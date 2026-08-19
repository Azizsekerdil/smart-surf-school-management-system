from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.core.enums import Severity, SurfLevel
from apps.locations.models import SpotHazard, SurfSpot

from .factories import SpotHazardFactory, SurfSpotFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager", email="manager@example.com", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def instructor(db):
    """Surf instructors may view locations but never change them."""
    return User.objects.create_user(
        username="coach", email="coach@example.com", password="pw-test-12345",
        role=Role.SURF_INSTRUCTOR,
    )


@pytest.fixture
def outsider(db):
    """A customer holds no ``locations.*`` capability at all."""
    return User.objects.create_user(
        username="guest", email="guest@example.com", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_list_requires_authentication(client):
    response = client.get(reverse("locations:list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_customer_cannot_see_locations(client, outsider):
    client.force_login(outsider)
    assert client.get(reverse("locations:list")).status_code == 403


def test_instructor_cannot_create_a_spot(client, instructor):
    client.force_login(instructor)
    assert client.get(reverse("locations:create")).status_code == 403


def test_instructor_can_view_the_list(client, instructor):
    SurfSpotFactory(name="Alpha")
    client.force_login(instructor)
    assert client.get(reverse("locations:list")).status_code == 200


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
def test_list_renders_spots(client, manager):
    SurfSpotFactory(name="Kabak Bay")
    client.force_login(manager)
    response = client.get(reverse("locations:list"))
    assert response.status_code == 200
    assert b"Kabak Bay" in response.content


def test_list_search_filters_rows(client, manager):
    SurfSpotFactory(name="Kabak Bay")
    SurfSpotFactory(name="Cold Point")
    client.force_login(manager)
    response = client.get(reverse("locations:list"), {"q": "Kabak"})
    assert b"Kabak Bay" in response.content
    assert b"Cold Point" not in response.content


def test_list_level_filter_uses_the_level_range(client, manager):
    SurfSpotFactory(
        name="Nursery", min_level=SurfLevel.FIRST_TIME, max_level=SurfLevel.BEGINNER
    )
    SurfSpotFactory(
        name="Slab", min_level=SurfLevel.ADVANCED, max_level=SurfLevel.COMPETITION
    )
    client.force_login(manager)
    response = client.get(reverse("locations:list"), {"level": SurfLevel.FIRST_TIME})
    assert b"Nursery" in response.content
    assert b"Slab" not in response.content


def test_list_table_view_renders(client, manager):
    SurfSpotFactory(name="Kabak Bay")
    client.force_login(manager)
    response = client.get(reverse("locations:list"), {"view": "table"})
    assert response.status_code == 200
    assert response.context["view_mode"] == "table"


def test_list_htmx_request_returns_the_partial(client, manager):
    SurfSpotFactory(name="Kabak Bay")
    client.force_login(manager)
    response = client.get(reverse("locations:list"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert "locations/partials/spot_results.html" in [t.name for t in response.templates]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
def test_detail_renders_hazards_and_map_link(client, manager):
    spot = SurfSpotFactory(name="Kabak Bay")
    SpotHazardFactory(spot=spot, name="Lateral rip", severity=Severity.HIGH)
    client.force_login(manager)
    response = client.get(reverse("locations:detail", args=[spot.pk]))
    assert response.status_code == 200
    assert b"Lateral rip" in response.content
    assert b"openstreetmap.org" in response.content


def test_detail_shows_a_level_matrix(client, manager):
    spot = SurfSpotFactory(min_level=SurfLevel.BEGINNER, max_level=SurfLevel.INTERMEDIATE)
    client.force_login(manager)
    response = client.get(reverse("locations:detail", args=[spot.pk]))
    matrix = {row["value"]: row["suitable"] for row in response.context["level_matrix"]}
    assert matrix[SurfLevel.BEGINNER] is True
    assert matrix[SurfLevel.ADVANCED] is False


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
def _spot_payload(**overrides) -> dict:
    payload = {
        "name": "New Break",
        "description": "",
        "latitude": "36.5",
        "longitude": "30.5",
        "altitude": "",
        "beach_facing_deg": "180",
        "break_type": "beach_break",
        "bottom_type": "sand",
        "min_level": SurfLevel.FIRST_TIME,
        "max_level": SurfLevel.ADVANCED,
        "ideal_tide": "mid_rising",
        "ideal_wind": "offshore",
        "ideal_swell_direction_deg": "200",
        "capacity": "20",
        "is_active": "on",
        "parking_info": "",
        "access_notes": "",
        "lifeguard_on_duty": "on",
        "nearest_hospital": "Coastal State Hospital",
        "nearest_hospital_phone": "+90 555 123 45 67",
        "emergency_notes": "",
    }
    payload.update(overrides)
    return payload


def test_manager_can_create_a_spot(client, manager):
    client.force_login(manager)
    response = client.post(reverse("locations:create"), _spot_payload(), follow=True)
    assert response.status_code == 200
    spot = SurfSpot.objects.get(name="New Break")
    assert spot.created_by == manager
    assert spot.code.startswith("SPOT")
    assert spot.is_primary is True  # first spot in the school


def test_create_rejects_an_inverted_level_range(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("locations:create"),
        _spot_payload(min_level=SurfLevel.ADVANCED, max_level=SurfLevel.BEGINNER),
    )
    assert response.status_code == 200
    assert "max_level" in response.context["form"].errors
    assert not SurfSpot.objects.filter(name="New Break").exists()


def test_create_requires_a_hospital_for_an_unpatrolled_spot(client, manager):
    client.force_login(manager)
    payload = _spot_payload(nearest_hospital="", nearest_hospital_phone="")
    payload.pop("lifeguard_on_duty")
    response = client.post(reverse("locations:create"), payload)
    assert "nearest_hospital" in response.context["form"].errors


def test_update_changes_the_spot(client, manager):
    spot = SurfSpotFactory(name="Old Name")
    client.force_login(manager)
    client.post(
        reverse("locations:update", args=[spot.pk]),
        _spot_payload(name="Renamed Break"),
        follow=True,
    )
    spot.refresh_from_db()
    assert spot.name == "Renamed Break"
    assert spot.updated_by == manager


# ---------------------------------------------------------------------------
# Primary flag, archiving and hazards
# ---------------------------------------------------------------------------
def test_set_primary_moves_the_flag(client, manager):
    first = SurfSpotFactory(name="Alpha")
    second = SurfSpotFactory(name="Bravo")
    client.force_login(manager)
    client.post(reverse("locations:set_primary", args=[second.pk]))
    first.refresh_from_db()
    second.refresh_from_db()
    assert second.is_primary is True
    assert first.is_primary is False


def test_set_primary_rejects_get(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    assert client.get(reverse("locations:set_primary", args=[spot.pk])).status_code == 405


def test_archiving_the_last_spot_is_refused(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.post(reverse("locations:delete", args=[spot.pk]), follow=True)
    assert response.status_code == 200
    assert SurfSpot.objects.filter(pk=spot.pk).exists()


def test_archiving_a_spot_soft_deletes_it(client, manager):
    first = SurfSpotFactory(name="Alpha")
    SurfSpotFactory(name="Bravo")
    client.force_login(manager)
    client.post(reverse("locations:delete", args=[first.pk]), follow=True)
    assert not SurfSpot.objects.filter(pk=first.pk).exists()
    assert SurfSpot.all_objects.filter(pk=first.pk, is_deleted=True).exists()


def test_hazard_create_attaches_to_the_spot(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    client.post(
        reverse("locations:hazard_create", args=[spot.pk]),
        {
            "name": "Submerged rocks",
            "severity": Severity.HIGH,
            "description": "Exposed below mid tide.",
            "is_active": "on",
            "applies_from_tide": "low",
            "applies_to_tide": "mid_rising",
        },
        follow=True,
    )
    hazard = SpotHazard.objects.get(name="Submerged rocks")
    assert hazard.spot == spot
    assert hazard.applies_at_tide("low") is True


def test_critical_hazard_requires_a_description(client, manager):
    spot = SurfSpotFactory()
    client.force_login(manager)
    response = client.post(
        reverse("locations:hazard_create", args=[spot.pk]),
        {
            "name": "Sewage outflow",
            "severity": Severity.CRITICAL,
            "description": "",
            "is_active": "on",
            "applies_from_tide": "",
            "applies_to_tide": "",
        },
    )
    assert "description" in response.context["form"].errors
    assert not SpotHazard.objects.filter(name="Sewage outflow").exists()


def test_hazard_toggle_clears_and_reopens(client, manager):
    hazard = SpotHazardFactory()
    client.force_login(manager)
    client.post(reverse("locations:hazard_toggle", args=[hazard.pk]))
    hazard.refresh_from_db()
    assert hazard.is_active is False

    client.post(reverse("locations:hazard_toggle", args=[hazard.pk]))
    hazard.refresh_from_db()
    assert hazard.is_active is True


def test_instructor_cannot_toggle_a_hazard(client, instructor):
    hazard = SpotHazardFactory()
    client.force_login(instructor)
    assert client.post(reverse("locations:hazard_toggle", args=[hazard.pk])).status_code == 403
