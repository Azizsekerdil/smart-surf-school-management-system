from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import SurfLevel
from apps.locations.tests.factories import SurfSpotFactory
from apps.surf_conditions import services
from apps.surf_conditions.models import SurfCondition

from .factories import SurfConditionFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="api-manager", email="api-manager@example.com",
        password="pw-test-12345", role=Role.MANAGER,
    )


@pytest.fixture
def instructor(db):
    return User.objects.create_user(
        username="api-coach", email="api-coach@example.com",
        password="pw-test-12345", role=Role.SURF_INSTRUCTOR,
    )


def rows(response):
    payload = response.data
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_anonymous_access_is_rejected(api):
    assert api.get(reverse("surfcondition-list")).status_code in (401, 403)


def test_instructor_may_read_but_not_write(api, instructor):
    spot = SurfSpotFactory()
    SurfConditionFactory(spot=spot)
    api.force_authenticate(instructor)

    assert api.get(reverse("surfcondition-list")).status_code == 200
    response = api.post(
        reverse("surfcondition-list"),
        {"spot": spot.pk, "recorded_at": "2026-08-18T07:30:00Z", "wave_height_m": 1.0},
        format="json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_list_serialises_the_derived_values(api, manager):
    condition = SurfConditionFactory(wave_height_m=1.5, wind_speed_kmh=18.52)
    services.score_condition(condition)

    api.force_authenticate(manager)
    response = api.get(reverse("surfcondition-list"))
    assert response.status_code == 200

    row = rows(response)[0]
    assert row["wave_height_ft"] == pytest.approx(4.9, abs=0.05)
    assert row["wind_knots"] == pytest.approx(10.0, abs=0.05)
    assert row["recommended_wetsuit"]
    assert row["scores"]
    assert all(score["is_ai_generated"] is False for score in row["scores"])


def test_current_returns_404_when_nothing_is_stored(api, manager):
    SurfSpotFactory(is_primary=True)
    api.force_authenticate(manager)
    response = api.get(reverse("surfcondition-current"))
    assert response.status_code == 404
    assert response.data["error"]["type"] == "not_found"


def test_current_returns_the_latest_reading(api, manager):
    spot = SurfSpotFactory(is_primary=True)
    SurfConditionFactory(spot=spot, wave_height_m=1.3)

    api.force_authenticate(manager)
    response = api.get(reverse("surfcondition-current"), {"spot": spot.pk})
    assert response.status_code == 200
    assert response.data["wave_height_m"] == 1.3


def test_score_endpoint_rejects_an_unknown_level(api, manager):
    spot = SurfSpotFactory(is_primary=True)
    SurfConditionFactory(spot=spot)

    api.force_authenticate(manager)
    response = api.get(
        reverse("surfcondition-score"), {"spot": spot.pk, "level": "pro-surfer"}
    )
    assert response.status_code == 400
    assert response.data["error"]["type"] == "validation_error"


def test_score_endpoint_returns_a_computed_score(api, manager):
    spot = SurfSpotFactory(is_primary=True)
    condition = SurfConditionFactory(spot=spot)
    services.score_condition(condition)

    api.force_authenticate(manager)
    response = api.get(
        reverse("surfcondition-score"), {"spot": spot.pk, "level": SurfLevel.BEGINNER}
    )
    assert response.status_code == 200
    assert response.data["is_computed"] is True
    assert response.data["is_ai_generated"] is False
    assert response.data["score"]["level"] == SurfLevel.BEGINNER


def test_providers_endpoint_names_the_active_source(api, manager, monkeypatch):
    from apps.surf_conditions.providers import registry

    monkeypatch.setattr(registry, "health_report", lambda **kwargs: {})
    monkeypatch.setattr("apps.surf_conditions.api.health_report", lambda **kwargs: {})

    api.force_authenticate(manager)
    response = api.get(reverse("surfcondition-providers"))
    assert response.status_code == 200
    assert response.data["active"]
    assert response.data["attribution"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_manager_may_log_a_reading(api, manager):
    spot = SurfSpotFactory()
    api.force_authenticate(manager)
    response = api.post(
        reverse("surfcondition-list"),
        {
            "spot": spot.pk,
            "recorded_at": "2026-08-18T07:30:00Z",
            "wave_height_m": 0.9,
            "wind_speed_kmh": 12,
            "wind_direction_deg": 0,
        },
        format="json",
    )
    assert response.status_code == 201
    condition = SurfCondition.objects.get(spot=spot)
    assert condition.source == SurfCondition.Source.MANUAL
    assert condition.scores.count() > 0


def test_an_empty_reading_is_rejected(api, manager):
    spot = SurfSpotFactory()
    api.force_authenticate(manager)
    response = api.post(
        reverse("surfcondition-list"),
        {"spot": spot.pk, "recorded_at": "2026-08-18T07:30:00Z"},
        format="json",
    )
    assert response.status_code == 400


def test_a_gust_weaker_than_the_wind_is_rejected(api, manager):
    spot = SurfSpotFactory()
    api.force_authenticate(manager)
    response = api.post(
        reverse("surfcondition-list"),
        {
            "spot": spot.pk,
            "recorded_at": "2026-08-18T07:30:00Z",
            "wind_speed_kmh": 30,
            "wind_gust_kmh": 10,
        },
        format="json",
    )
    assert response.status_code == 400


def test_refresh_reports_an_unreachable_provider_as_503(api, manager, monkeypatch):
    spot = SurfSpotFactory()
    monkeypatch.setattr(services, "refresh_spot_conditions", lambda *a, **k: None)

    api.force_authenticate(manager)
    response = api.post(reverse("surfcondition-refresh"), {"spot": spot.pk}, format="json")
    assert response.status_code == 503
    assert response.data["error"]["type"] == "upstream_unavailable"


# ---------------------------------------------------------------------------
# Scores are read-only
# ---------------------------------------------------------------------------
def test_scores_cannot_be_written(api, manager):
    condition = SurfConditionFactory()
    services.score_condition(condition)

    api.force_authenticate(manager)
    response = api.post(
        reverse("surfscore-list"),
        {"condition": condition.pk, "level": SurfLevel.BEGINNER, "score": 100},
        format="json",
    )
    assert response.status_code in (403, 405)
