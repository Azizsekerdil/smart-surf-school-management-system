from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import Severity, SurfLevel, TideState, WindType
from apps.locations.models import SpotHazard, SurfSpot

from .factories import SpotHazardFactory, SurfSpotFactory

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


def test_anonymous_access_is_rejected(api):
    assert api.get(reverse("surfspot-list")).status_code in (401, 403)


def test_instructor_may_read_but_not_write(api, instructor):
    SurfSpotFactory(name="Alpha")
    api.force_authenticate(instructor)
    assert api.get(reverse("surfspot-list")).status_code == 200
    assert api.post(reverse("surfspot-list"), {"name": "Nope"}, format="json").status_code == 403


def test_list_serialises_derived_values(api, manager):
    spot = SurfSpotFactory(name="Alpha", beach_facing_deg=180.0)
    SpotHazardFactory(spot=spot, name="Lateral rip", severity=Severity.HIGH)
    api.force_authenticate(manager)

    response = api.get(reverse("surfspot-detail", args=[spot.pk]))
    assert response.status_code == 200
    body = response.json()
    assert body["facing_compass"] == "S"
    assert body["offshore_compass"] == "N"
    assert body["level_range"]
    assert "openstreetmap.org" in body["map_url"]
    assert [h["name"] for h in body["active_hazards"]] == ["Lateral rip"]


def test_create_spot_via_api(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        reverse("surfspot-list"),
        {
            "name": "API Break",
            "latitude": 36.5,
            "longitude": 30.5,
            "beach_facing_deg": 200,
            "break_type": "point_break",
            "bottom_type": "reef",
            "min_level": SurfLevel.INTERMEDIATE,
            "max_level": SurfLevel.COMPETITION,
            "ideal_tide": TideState.HIGH,
            "ideal_wind": WindType.OFFSHORE,
            "capacity": 12,
            "is_active": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    spot = SurfSpot.objects.get(name="API Break")
    assert spot.created_by == manager
    assert spot.code.startswith("SPOT")


def test_create_rejects_an_inverted_level_range(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        reverse("surfspot-list"),
        {
            "name": "Broken Range",
            "latitude": 36.5,
            "longitude": 30.5,
            "beach_facing_deg": 200,
            "min_level": SurfLevel.ADVANCED,
            "max_level": SurfLevel.BEGINNER,
        },
        format="json",
    )
    assert response.status_code == 400
    assert not SurfSpot.objects.filter(name="Broken Range").exists()


def test_primary_endpoint(api, manager):
    spot = SurfSpotFactory(name="Alpha")
    api.force_authenticate(manager)
    response = api.get(reverse("surfspot-primary"))
    assert response.status_code == 200
    assert response.json()["id"] == spot.pk


def test_primary_endpoint_without_any_spot(api, manager):
    api.force_authenticate(manager)
    assert api.get(reverse("surfspot-primary")).status_code == 404


def test_suitable_endpoint_filters_by_level(api, manager):
    SurfSpotFactory(name="Nursery", min_level=SurfLevel.FIRST_TIME, max_level=SurfLevel.BEGINNER)
    SurfSpotFactory(name="Slab", min_level=SurfLevel.ADVANCED, max_level=SurfLevel.COMPETITION)
    api.force_authenticate(manager)

    response = api.get(reverse("surfspot-suitable"), {"level": SurfLevel.FIRST_TIME})
    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["Nursery"]

    assert api.get(reverse("surfspot-suitable"), {"level": "nonsense"}).status_code == 400


def test_classify_wind_endpoint(api, manager):
    spot = SurfSpotFactory(beach_facing_deg=180.0)
    api.force_authenticate(manager)
    response = api.get(reverse("surfspot-classify-wind", args=[spot.pk]), {"direction": 0})
    assert response.status_code == 200
    assert response.json()["wind_type"] == WindType.OFFSHORE
    assert response.json()["is_clean"] is True

    assert (
        api.get(reverse("surfspot-classify-wind", args=[spot.pk]), {"direction": "x"}).status_code
        == 400
    )


def test_assess_endpoint_reports_a_no_go(api, manager):
    spot = SurfSpotFactory(capacity=6)
    SpotHazardFactory(spot=spot, name="Sewage outflow", severity=Severity.CRITICAL)
    api.force_authenticate(manager)
    response = api.get(
        reverse("surfspot-assess", args=[spot.pk]),
        {"level": SurfLevel.BEGINNER, "group_size": 4},
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "no_go"


def test_hazards_endpoint(api, manager):
    spot = SurfSpotFactory()
    SpotHazardFactory(spot=spot, name="Rocks", severity=Severity.CRITICAL)
    SpotHazardFactory(spot=spot, name="Litter", severity=Severity.LOW)
    api.force_authenticate(manager)
    response = api.get(reverse("surfspot-hazards", args=[spot.pk]))
    assert [row["name"] for row in response.json()] == ["Rocks", "Litter"]


def test_set_primary_requires_the_manage_capability(api, instructor):
    SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    api.force_authenticate(instructor)
    response = api.patch(
        reverse("surfspot-detail", args=[second.pk]), {"is_primary": True}, format="json"
    )
    assert response.status_code == 403


def test_set_primary_action_moves_the_flag(api, manager):
    first = SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    api.force_authenticate(manager)
    response = api.post(reverse("surfspot-set-primary", args=[second.pk]))
    assert response.status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert second.is_primary is True
    assert first.is_primary is False


def test_set_primary_action_is_denied_without_manage(api, instructor):
    SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    api.force_authenticate(instructor)
    assert api.post(reverse("surfspot-set-primary", args=[second.pk])).status_code == 403


def test_deleting_the_last_spot_returns_400(api, manager):
    spot = SurfSpotFactory()
    api.force_authenticate(manager)
    response = api.delete(reverse("surfspot-detail", args=[spot.pk]))
    assert response.status_code == 400
    assert SurfSpot.objects.filter(pk=spot.pk).exists()


def test_deleting_a_spot_soft_deletes_it(api, manager):
    first = SurfSpotFactory(name="Alpha")
    SurfSpotFactory(name="Bravo")
    api.force_authenticate(manager)
    assert api.delete(reverse("surfspot-detail", args=[first.pk])).status_code == 204
    assert SurfSpot.all_objects.get(pk=first.pk).is_deleted is True


def test_hazard_delete_only_deactivates(api, manager):
    hazard = SpotHazardFactory()
    api.force_authenticate(manager)
    assert api.delete(reverse("spothazard-detail", args=[hazard.pk])).status_code == 204
    hazard.refresh_from_db()
    assert hazard.is_active is False
    assert SpotHazard.objects.filter(pk=hazard.pk).exists()
