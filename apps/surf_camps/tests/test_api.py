"""REST API behaviour: capability gating and the service-backed actions."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role

from ..models import CampStatus, ParticipantStatus
from .factories import CampParticipantFactory, SurfCampFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="api-manager",
        email="api-manager@example.test",
        password="a-long-test-password",
        role=Role.MANAGER,
    )


@pytest.fixture
def instructor_user(db):
    """A surf instructor: may read camps, may not change them."""
    return User.objects.create_user(
        username="api-instructor",
        email="api-instructor@example.test",
        password="a-long-test-password",
        role=Role.SURF_INSTRUCTOR,
    )


def _student():
    from apps.students.tests.factories import StudentFactory

    return StudentFactory()


def test_camp_list_requires_authentication(api):
    response = api.get(reverse("surfcamp-list"))
    assert response.status_code in (401, 403)


def test_camp_list_returns_occupancy(api, manager):
    camp = SurfCampFactory(capacity=4)
    CampParticipantFactory(camp=camp)
    api.force_authenticate(manager)

    response = api.get(reverse("surfcamp-list"))

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["participant_count"] == 1
    assert row["available_places"] == 3
    assert row["is_full"] is False


def test_instructor_may_read_but_not_write(api, instructor_user):
    from apps.locations.tests.factories import SurfSpotFactory

    api.force_authenticate(instructor_user)
    assert api.get(reverse("surfcamp-list")).status_code == 200

    start = timezone.localdate() + timedelta(days=20)
    response = api.post(
        reverse("surfcamp-list"),
        {
            "name": "Unauthorised camp",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=5)).isoformat(),
            "spot": SurfSpotFactory().pk,
            "capacity": 8,
            "price": "500.00",
        },
        format="json",
    )
    assert response.status_code == 403


def test_add_participant_action_enforces_capacity(api, manager):
    camp = SurfCampFactory(capacity=1)
    api.force_authenticate(manager)

    first = api.post(
        reverse("surfcamp-add-participant", kwargs={"pk": camp.pk}),
        {"student": _student().pk, "room_type": "shared"},
        format="json",
    )
    assert first.status_code == 201

    second = api.post(
        reverse("surfcamp-add-participant", kwargs={"pk": camp.pk}),
        {"student": _student().pk, "room_type": "shared"},
        format="json",
    )
    assert second.status_code == 400
    assert camp.participant_count == 1


def test_finance_action_returns_the_summary(api, manager):
    camp = SurfCampFactory(capacity=5, price=Decimal("300.00"))
    CampParticipantFactory(camp=camp, amount_paid=Decimal("100.00"))
    api.force_authenticate(manager)

    response = api.get(reverse("surfcamp-finance", kwargs={"pk": camp.pk}))

    assert response.status_code == 200
    assert Decimal(str(response.data["expected_revenue"])) == Decimal("300.00")
    assert Decimal(str(response.data["outstanding"])) == Decimal("200.00")


def test_generate_programme_action(api, manager):
    camp = SurfCampFactory()
    api.force_authenticate(manager)

    response = api.post(
        reverse("surfcamp-generate-programme", kwargs={"pk": camp.pk}), {}, format="json"
    )

    assert response.status_code == 200
    assert response.data["activities_created"] > 0
    assert camp.days.count() == camp.duration_days


def test_roster_action_rejects_a_bad_date(api, manager):
    camp = SurfCampFactory()
    api.force_authenticate(manager)

    response = api.get(reverse("surfcamp-roster", kwargs={"pk": camp.pk}), {"date": "13-13-13"})

    assert response.status_code == 400


def test_cancel_action_releases_every_place(api, manager):
    camp = SurfCampFactory(capacity=5)
    CampParticipantFactory.create_batch(2, camp=camp)
    api.force_authenticate(manager)

    response = api.post(
        reverse("surfcamp-cancel", kwargs={"pk": camp.pk}),
        {"reason": "Not enough bookings"},
        format="json",
    )

    camp.refresh_from_db()
    assert response.status_code == 200
    assert camp.status == CampStatus.CANCELLED
    assert camp.participant_count == 0


def test_participant_check_in_flow(api, manager):
    today = timezone.localdate()
    camp = SurfCampFactory(start_date=today, end_date=today + timedelta(days=6))
    participant = CampParticipantFactory(camp=camp)
    api.force_authenticate(manager)

    response = api.post(
        reverse("campparticipant-check-in", kwargs={"pk": participant.pk}), {}, format="json"
    )

    participant.refresh_from_db()
    assert response.status_code == 200
    assert participant.status == ParticipantStatus.ARRIVED


def test_deleting_a_participant_cancels_instead_of_erasing(api, manager):
    participant = CampParticipantFactory()
    api.force_authenticate(manager)

    response = api.delete(reverse("campparticipant-detail", kwargs={"pk": participant.pk}))

    participant.refresh_from_db()
    assert response.status_code == 204
    assert participant.status == ParticipantStatus.CANCELLED
