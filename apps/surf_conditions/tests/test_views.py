from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.locations.tests.factories import SurfSpotFactory
from apps.surf_conditions import services
from apps.surf_conditions.models import SurfCondition

from .factories import SurfConditionFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _local_stamp() -> str:
    """A datetime-local value in the recent past, whatever day the suite runs."""
    return (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager", email="manager@example.com", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def instructor(db):
    """Every authenticated user may *view* conditions; only managers may change."""
    return User.objects.create_user(
        username="coach", email="coach@example.com", password="pw-test-12345",
        role=Role.SURF_INSTRUCTOR,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("surf_conditions:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_an_instructor_may_read_the_dashboard(client, instructor):
    SurfSpotFactory(is_primary=True)
    client.force_login(instructor)
    assert client.get(reverse("surf_conditions:dashboard")).status_code == 200


def test_an_instructor_may_not_log_a_reading(client, instructor):
    client.force_login(instructor)
    assert client.get(reverse("surf_conditions:create")).status_code == 403


def test_an_instructor_may_not_force_a_refresh(client, instructor):
    spot = SurfSpotFactory()
    client.force_login(instructor)
    response = client.post(reverse("surf_conditions:refresh", args=[spot.pk]))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_renders_without_any_stored_reading(client, manager):
    SurfSpotFactory(is_primary=True)
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:dashboard"))
    assert response.status_code == 200
    assert response.context["condition"] is None


def test_dashboard_renders_with_no_spot_at_all(client, manager):
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:dashboard"))
    assert response.status_code == 200
    assert response.context["selected_spot"] is None


def test_dashboard_shows_the_computed_scores_and_the_attribution(client, manager):
    spot = SurfSpotFactory(is_primary=True)
    condition = SurfConditionFactory(spot=spot)
    services.score_condition(condition)

    client.force_login(manager)
    response = client.get(reverse("surf_conditions:dashboard"))
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["scores"]
    assert all(item["factors"] for item in response.context["scores"])
    # The attribution is a licence obligation and is never translated away.
    assert "Open-Meteo" in body


def test_dashboard_honours_the_spot_query_parameter(client, manager):
    SurfSpotFactory(name="First Beach", is_primary=True)
    second = SurfSpotFactory(name="Second Beach")
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:dashboard"), {"spot": second.pk})
    assert response.context["selected_spot"].pk == second.pk


# ---------------------------------------------------------------------------
# Spot panel — the contract with apps.locations
# ---------------------------------------------------------------------------
def test_the_spot_panel_url_exists_for_the_locations_module():
    spot_pk = 1
    assert reverse("surf_conditions:spot_panel", args=[spot_pk]).endswith(
        f"/spots/{spot_pk}/panel/"
    )


def test_spot_panel_renders_for_a_spot_without_readings(client, instructor):
    spot = SurfSpotFactory()
    client.force_login(instructor)
    response = client.get(reverse("surf_conditions:spot_panel", args=[spot.pk]))
    assert response.status_code == 200
    assert response.context["condition"] is None


def test_spot_panel_shows_the_stored_scores(client, instructor):
    spot = SurfSpotFactory()
    condition = SurfConditionFactory(spot=spot)
    services.score_condition(condition)
    client.force_login(instructor)
    response = client.get(reverse("surf_conditions:spot_panel", args=[spot.pk]))
    assert response.status_code == 200
    assert response.context["scores"]


# ---------------------------------------------------------------------------
# History & detail
# ---------------------------------------------------------------------------
def test_history_lists_observations_and_hides_forecast_rows(client, manager):
    spot = SurfSpotFactory()
    SurfConditionFactory(spot=spot, is_forecast=False)
    SurfConditionFactory(spot=spot, is_forecast=True)
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:history"), {"range": "all"})
    assert response.status_code == 200
    assert len(response.context["conditions"]) == 1


def test_detail_shows_the_factor_breakdown(client, manager):
    condition = SurfConditionFactory()
    services.score_condition(condition)
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:detail", args=[condition.pk]))
    assert response.status_code == 200
    rows = response.context["scores"]
    assert rows
    # Every level must arrive with the full weighted breakdown behind it.
    assert all(len(row["factors"]) == 6 for row in rows)


# ---------------------------------------------------------------------------
# Manual logging
# ---------------------------------------------------------------------------
def test_a_manager_can_log_a_reading_and_it_gets_scored(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.post(
        reverse("surf_conditions:create"),
        {
            "spot": spot.pk,
            "recorded_at": _local_stamp(),
            "wave_height_m": "0.8",
            "swell_period_s": "9.0",
            "wind_speed_kmh": "10",
            "wind_direction_deg": "0",
            "tide_state": "mid_rising",
            "water_temperature_c": "21.0",
        },
    )
    assert response.status_code == 302
    condition = SurfCondition.objects.get(spot=spot, source=SurfCondition.Source.MANUAL)
    assert condition.provider == "manual"
    assert condition.scores.count() > 0


def test_an_empty_reading_is_rejected(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.post(
        reverse("surf_conditions:create"),
        {"spot": spot.pk, "recorded_at": _local_stamp(), "tide_state": "mid_rising"},
    )
    assert response.status_code == 200
    assert response.context["form"].errors


def test_a_provider_reading_cannot_be_edited(client, manager):
    condition = SurfConditionFactory(source=SurfCondition.Source.PROVIDER)
    client.force_login(manager)
    response = client.get(reverse("surf_conditions:update", args=[condition.pk]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def test_refresh_reports_an_unreachable_provider_without_erroring(
    client, manager, monkeypatch
):
    spot = SurfSpotFactory()
    monkeypatch.setattr(services, "refresh_spot_conditions", lambda *a, **k: None)
    client.force_login(manager)
    response = client.post(reverse("surf_conditions:refresh", args=[spot.pk]))
    assert response.status_code == 302


def test_refresh_swaps_the_now_card_for_htmx(client, manager, monkeypatch):
    spot = SurfSpotFactory()
    condition = SurfConditionFactory(spot=spot)
    services.score_condition(condition)
    monkeypatch.setattr(services, "refresh_spot_conditions", lambda *a, **k: condition)

    client.force_login(manager)
    response = client.post(
        reverse("surf_conditions:refresh", args=[spot.pk]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert 'id="now-card"' in response.content.decode()
