"""View behaviour: access control, the HTMX participant panel and the printable roster."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import SurfLevel

from ..models import CampStatus, ParticipantStatus
from .factories import CampParticipantFactory, SurfCampFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="camp-manager",
        email="manager@example.test",
        password="a-long-test-password",
        role=Role.MANAGER,
    )


@pytest.fixture
def maintenance(db):
    """A staff member with no surf_camps capability at all."""
    return User.objects.create_user(
        username="camp-maintenance",
        email="maintenance@example.test",
        password="a-long-test-password",
        role=Role.MAINTENANCE_STAFF,
    )


@pytest.fixture
def reception(db):
    """Reception may look at camps but not change them."""
    return User.objects.create_user(
        username="camp-reception",
        email="reception@example.test",
        password="a-long-test-password",
        role=Role.RECEPTION,
    )


def _student():
    from apps.students.tests.factories import StudentFactory

    return StudentFactory()


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def test_list_requires_authentication(client):
    response = client.get(reverse("surf_camps:list"))
    assert response.status_code == 302


def test_list_is_denied_without_the_capability(client, maintenance):
    client.force_login(maintenance)
    response = client.get(reverse("surf_camps:list"))
    assert response.status_code == 403


def test_reception_cannot_create_a_camp(client, reception):
    client.force_login(reception)
    response = client.get(reverse("surf_camps:create"))
    assert response.status_code == 403


def test_reception_cannot_add_a_participant(client, reception):
    camp = SurfCampFactory()
    client.force_login(reception)
    response = client.post(
        reverse("surf_camps:participant_create", kwargs={"pk": camp.pk}),
        {"student": _student().pk, "room_type": "shared", "amount_paid": "0"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def test_list_returns_200(client, manager):
    SurfCampFactory.create_batch(3)
    client.force_login(manager)
    response = client.get(reverse("surf_camps:list"))
    assert response.status_code == 200
    assert "camps" in response.context


def test_list_search_filters_by_name(client, manager):
    SurfCampFactory(name="Alacati Beginner Week")
    SurfCampFactory(name="Advanced Coaching Retreat")
    client.force_login(manager)

    response = client.get(reverse("surf_camps:list"), {"q": "Beginner", "period": "all"})

    names = [camp.name for camp in response.context["camps"]]
    assert names == ["Alacati Beginner Week"]


def test_detail_returns_200_with_the_four_panels(client, manager):
    camp = SurfCampFactory()
    CampParticipantFactory(camp=camp)
    client.force_login(manager)

    response = client.get(reverse("surf_camps:detail", kwargs={"pk": camp.pk}))

    assert response.status_code == 200
    assert response.context["finance"]["participants"] == 1
    assert "staffing" in response.context
    assert "roster" in response.context


def test_roster_renders_for_a_chosen_day(client, manager):
    today = timezone.localdate()
    camp = SurfCampFactory(start_date=today, end_date=today + timedelta(days=6))
    CampParticipantFactory(camp=camp)
    client.force_login(manager)

    response = client.get(
        reverse("surf_camps:roster", kwargs={"pk": camp.pk}), {"date": today.isoformat()}
    )

    assert response.status_code == 200
    assert response.context["roster"]["present_count"] == 1


def test_roster_falls_back_when_the_date_is_nonsense(client, manager):
    camp = SurfCampFactory()
    client.force_login(manager)

    response = client.get(
        reverse("surf_camps:roster", kwargs={"pk": camp.pk}), {"date": "not-a-date"}
    )

    assert response.status_code == 200
    assert response.context["roster_date"] == camp.start_date


def test_participant_export_is_csv(client, manager):
    camp = SurfCampFactory()
    CampParticipantFactory(camp=camp, room_number="12")
    client.force_login(manager)

    response = client.get(reverse("surf_camps:export", kwargs={"pk": camp.pk}))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_create_camp_builds_the_days(client, manager):
    from apps.locations.tests.factories import SurfSpotFactory

    spot = SurfSpotFactory()
    client.force_login(manager)
    start = timezone.localdate() + timedelta(days=30)

    response = client.post(
        reverse("surf_camps:create"),
        {
            "name": "Spring Progression Week",
            "code": "",
            "description": "",
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
            "spot": spot.pk,
            "capacity": "8",
            "min_participants": "4",
            "min_level": SurfLevel.BEGINNER,
            "max_level": SurfLevel.INTERMEDIATE,
            "price": "900.00",
            "deposit_amount": "250.00",
            "single_room_supplement": "0.00",
            "accommodation_name": "Surf House",
            "accommodation_address": "",
            "meal_plan": "",
            "transfer_pickup_point": "",
            "transfer_notes": "",
            "status": CampStatus.DRAFT,
            "is_active": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    camp = response.context["camp"]
    assert camp.name == "Spring Progression Week"
    assert camp.days.count() == 7


def test_add_participant_over_htmx_returns_the_panel(client, manager):
    camp = SurfCampFactory(capacity=2)
    student = _student()
    client.force_login(manager)

    response = client.post(
        reverse("surf_camps:participant_create", kwargs={"pk": camp.pk}),
        {"student": student.pk, "room_type": "shared", "amount_paid": "0"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert b"participants-panel" in response.content
    assert camp.participant_count == 1


def test_add_participant_over_capacity_shows_an_error_not_a_crash(client, manager):
    camp = SurfCampFactory(capacity=1)
    CampParticipantFactory(camp=camp)
    client.force_login(manager)

    response = client.post(
        reverse("surf_camps:participant_create", kwargs={"pk": camp.pk}),
        {"student": _student().pk, "room_type": "shared", "amount_paid": "0"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert response.context["panel_error"]
    assert camp.participant_count == 1


def test_remove_participant_frees_the_place(client, manager):
    camp = SurfCampFactory(capacity=2)
    participant = CampParticipantFactory(camp=camp)
    client.force_login(manager)

    response = client.post(
        reverse("surf_camps:participant_remove", kwargs={"pk": participant.pk}),
        {"reason": "Cancelled by the customer"},
        HTTP_HX_REQUEST="true",
    )

    participant.refresh_from_db()
    assert response.status_code == 200
    assert participant.status == ParticipantStatus.CANCELLED
    assert camp.available_places == 2


def test_publish_requires_the_approve_capability(client, reception):
    camp = SurfCampFactory(status=CampStatus.DRAFT)
    client.force_login(reception)

    response = client.post(reverse("surf_camps:publish", kwargs={"pk": camp.pk}))

    assert response.status_code == 403
    camp.refresh_from_db()
    assert camp.status == CampStatus.DRAFT


def test_publish_moves_a_draft_camp(client, manager):
    camp = SurfCampFactory(status=CampStatus.DRAFT, price=Decimal("500.00"))
    client.force_login(manager)

    client.post(reverse("surf_camps:publish", kwargs={"pk": camp.pk}))

    camp.refresh_from_db()
    assert camp.status == CampStatus.PUBLISHED


def test_generate_programme_action(client, manager):
    camp = SurfCampFactory()
    client.force_login(manager)

    client.post(reverse("surf_camps:generate_programme", kwargs={"pk": camp.pk}))

    assert camp.days.count() == camp.duration_days
    assert all(day.activities.exists() for day in camp.days.all())


def test_camp_with_participants_cannot_be_deleted(client, manager):
    camp = SurfCampFactory()
    CampParticipantFactory(camp=camp)
    client.force_login(manager)

    client.post(reverse("surf_camps:delete", kwargs={"pk": camp.pk}))

    camp.refresh_from_db()
    assert camp.is_deleted is False
