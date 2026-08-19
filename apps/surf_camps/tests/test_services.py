"""Service-level behaviour: the rules that decide who joins a camp and what it earns."""

from __future__ import annotations

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import SurfLevel

from .. import services
from ..models import (
    ActivityType,
    CampActivity,
    CampStatus,
    ParticipantStatus,
    RoomType,
)
from .factories import (
    CampActivityFactory,
    CampDayFactory,
    CampParticipantFactory,
    SurfCampFactory,
)

pytestmark = pytest.mark.django_db


def _student(**kwargs):
    """A student built by the students module's own factory."""
    from apps.students.tests.factories import StudentFactory

    return StudentFactory(**kwargs)


# ---------------------------------------------------------------------------
# Days
# ---------------------------------------------------------------------------
def test_create_camp_with_days_builds_one_day_per_date():
    camp = SurfCampFactory()
    services.create_camp_with_days(camp)

    assert camp.days.count() == camp.duration_days
    assert list(camp.days.values_list("day_number", flat=True)) == list(
        range(1, camp.duration_days + 1)
    )


def test_create_camp_with_days_is_idempotent():
    camp = SurfCampFactory()
    services.create_camp_with_days(camp)
    services.create_camp_with_days(camp)

    assert camp.days.count() == camp.duration_days


def test_shortening_a_camp_drops_only_empty_days():
    camp = SurfCampFactory()
    services.create_camp_with_days(camp)
    last_day = camp.days.order_by("-date").first()
    CampActivityFactory(camp_day=last_day)

    camp.end_date = camp.end_date - timedelta(days=2)
    camp.save(update_fields=["end_date"])
    services.create_camp_with_days(camp)

    # The planned day survives; the empty one in between does not.
    assert camp.days.filter(pk=last_day.pk).exists() is True
    assert camp.days.count() == camp.duration_days + 1


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
def test_add_participant_registers_a_place():
    camp = SurfCampFactory(capacity=5)
    participant = services.add_participant(camp, _student())

    assert participant.status == ParticipantStatus.REGISTERED
    assert camp.participant_count == 1


def test_add_participant_refuses_to_overbook():
    camp = SurfCampFactory(capacity=1)
    services.add_participant(camp, _student())

    with pytest.raises(ValidationError):
        services.add_participant(camp, _student())


def test_add_participant_refuses_a_duplicate():
    camp = SurfCampFactory(capacity=5)
    student = _student()
    services.add_participant(camp, student)

    with pytest.raises(ValidationError):
        services.add_participant(camp, student)


def test_add_participant_refuses_a_cancelled_camp():
    camp = SurfCampFactory(status=CampStatus.CANCELLED)
    with pytest.raises(ValidationError):
        services.add_participant(camp, _student())


def test_add_participant_reinstates_a_cancelled_place():
    camp = SurfCampFactory(capacity=2)
    student = _student()
    participant = services.add_participant(camp, student)
    services.remove_participant(participant, reason="Changed plans")
    assert camp.participant_count == 0

    again = services.add_participant(camp, student)

    assert again.pk == participant.pk
    assert again.status == ParticipantStatus.REGISTERED
    assert camp.participant_count == 1


def test_remove_participant_frees_the_place_and_keeps_the_row():
    camp = SurfCampFactory(capacity=1)
    participant = services.add_participant(camp, _student())
    services.remove_participant(participant, reason="Illness")

    participant.refresh_from_db()
    assert participant.status == ParticipantStatus.CANCELLED
    assert participant.cancellation_reason == "Illness"
    assert camp.available_places == 1


def test_remove_participant_twice_is_refused():
    participant = CampParticipantFactory(status=ParticipantStatus.CANCELLED)
    with pytest.raises(ValidationError):
        services.remove_participant(participant)


def test_check_in_before_the_camp_starts_is_refused():
    camp = SurfCampFactory(
        start_date=timezone.localdate() + timedelta(days=5),
        end_date=timezone.localdate() + timedelta(days=11),
    )
    participant = CampParticipantFactory(camp=camp)

    with pytest.raises(ValidationError):
        services.set_participant_status(participant, ParticipantStatus.ARRIVED)


def test_check_out_requires_a_check_in():
    today = timezone.localdate()
    camp = SurfCampFactory(start_date=today, end_date=today + timedelta(days=6))
    participant = CampParticipantFactory(camp=camp, status=ParticipantStatus.CONFIRMED)

    with pytest.raises(ValidationError):
        services.set_participant_status(participant, ParticipantStatus.DEPARTED)

    services.set_participant_status(participant, ParticipantStatus.ARRIVED)
    services.set_participant_status(participant, ParticipantStatus.DEPARTED)
    participant.refresh_from_db()
    assert participant.status == ParticipantStatus.DEPARTED


def test_cancelling_a_camp_cancels_every_place():
    camp = SurfCampFactory(capacity=5)
    CampParticipantFactory.create_batch(3, camp=camp)

    services.cancel_camp(camp, reason="Storm")

    camp.refresh_from_db()
    assert camp.status == CampStatus.CANCELLED
    assert camp.participant_count == 0
    assert camp.participants.filter(status=ParticipantStatus.CANCELLED).count() == 3


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def test_refresh_camp_status_marks_a_full_camp():
    camp = SurfCampFactory(capacity=1, status=CampStatus.PUBLISHED)
    CampParticipantFactory(camp=camp)

    services.refresh_camp_status(camp)

    assert camp.status == CampStatus.FULL


def test_refresh_camp_status_never_touches_a_draft():
    camp = SurfCampFactory(capacity=1, status=CampStatus.DRAFT)
    CampParticipantFactory(camp=camp)

    services.refresh_camp_status(camp)

    assert camp.status == CampStatus.DRAFT


def test_publish_requires_a_price():
    camp = SurfCampFactory(status=CampStatus.DRAFT, price=Decimal("0.00"))
    with pytest.raises(ValidationError):
        services.publish_camp(camp)


def test_publish_builds_the_days():
    camp = SurfCampFactory(status=CampStatus.DRAFT)
    services.publish_camp(camp)

    assert camp.status == CampStatus.PUBLISHED
    assert camp.days.count() == camp.duration_days


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
def test_generate_default_programme_fills_every_day():
    camp = SurfCampFactory()
    created = services.generate_default_programme(camp)

    assert created > 0
    assert camp.days.count() == camp.duration_days
    for day in camp.days.all():
        assert day.activities.exists()

    first_day = camp.days.order_by("date").first()
    last_day = camp.days.order_by("-date").first()
    assert first_day.activities.filter(activity_type=ActivityType.TRANSFER).exists()
    assert last_day.activities.filter(activity_type=ActivityType.TRANSFER).exists()
    assert CampActivity.objects.filter(
        camp_day__camp=camp, activity_type=ActivityType.SURF_LESSON
    ).exists()


def test_generate_default_programme_leaves_edited_days_alone():
    camp = SurfCampFactory()
    services.create_camp_with_days(camp)
    day = camp.days.order_by("date").first()
    CampActivityFactory(camp_day=day, title="Hand written session")

    services.generate_default_programme(camp)

    assert day.activities.count() == 1
    assert day.activities.first().title == "Hand written session"


def test_generate_default_programme_can_replace():
    camp = SurfCampFactory()
    services.create_camp_with_days(camp)
    day = camp.days.order_by("date").first()
    CampActivityFactory(camp_day=day, title="Hand written session")

    services.generate_default_programme(camp, replace=True)

    assert day.activities.filter(title="Hand written session").exists() is False


def test_instructor_cannot_be_double_booked():
    from apps.instructors.tests.factories import InstructorFactory

    instructor = InstructorFactory()
    day = CampDayFactory()
    CampActivityFactory(
        camp_day=day, instructor=instructor, start_time=time(9, 0), end_time=time(11, 0)
    )

    clash = CampActivity(
        camp_day=day,
        instructor=instructor,
        start_time=time(10, 0),
        end_time=time(12, 0),
        title="Second lesson",
        activity_type=ActivityType.SURF_LESSON,
    )
    with pytest.raises(ValidationError):
        services.save_activity(clash)


def test_back_to_back_activities_do_not_clash():
    from apps.instructors.tests.factories import InstructorFactory

    instructor = InstructorFactory()
    day = CampDayFactory()
    CampActivityFactory(
        camp_day=day, instructor=instructor, start_time=time(9, 0), end_time=time(11, 0)
    )

    following = CampActivity(
        camp_day=day,
        instructor=instructor,
        start_time=time(11, 0),
        end_time=time(13, 0),
        title="Second lesson",
        activity_type=ActivityType.SURF_LESSON,
    )
    services.save_activity(following)

    assert following.pk is not None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_financial_summary_is_exact_decimal_arithmetic():
    camp = SurfCampFactory(
        capacity=10,
        price=Decimal("850.00"),
        deposit_amount=Decimal("200.00"),
        single_room_supplement=Decimal("150.00"),
    )
    CampParticipantFactory(camp=camp, room_type=RoomType.SHARED, amount_paid=Decimal("200.00"))
    CampParticipantFactory(camp=camp, room_type=RoomType.SINGLE, amount_paid=Decimal("1000.00"))

    summary = services.camp_financial_summary(camp)

    assert summary["participants"] == 2
    assert summary["expected_revenue"] == Decimal("1850.00")
    assert summary["collected"] == Decimal("1200.00")
    assert summary["outstanding"] == Decimal("650.00")
    assert summary["potential_revenue"] == Decimal("8500.00")
    assert summary["available_places"] == 8


def test_staffing_summary_applies_the_level_ratio():
    camp = SurfCampFactory(capacity=20, min_level=SurfLevel.BEGINNER)
    CampParticipantFactory.create_batch(9, camp=camp)

    staffing = services.camp_staffing_summary(camp)

    assert staffing["ratio"] == 8
    assert staffing["required_instructors"] == 2
    assert staffing["is_understaffed"] is True


def test_daily_roster_lists_the_people_on_site():
    camp = SurfCampFactory(
        start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=6)
    )
    services.generate_default_programme(camp)
    CampParticipantFactory(camp=camp, dietary_requirements="Vegetarian")
    CampParticipantFactory(camp=camp, medical_notes="Asthma — inhaler in the bag")

    roster = services.camp_daily_roster(camp, camp.start_date)

    assert roster["present_count"] == 2
    assert len(roster["dietary"]) == 1
    assert len(roster["medical"]) == 1
    assert roster["activities"]
    assert roster["date"] == camp.start_date


def test_camp_alerts_flag_an_understaffed_camp():
    camp = SurfCampFactory(capacity=20, min_level=SurfLevel.FIRST_TIME)
    CampParticipantFactory.create_batch(7, camp=camp)

    # Seven first-timers at 6:1 need two instructors and the camp has none.
    assert "error" in [alert["level"] for alert in services.camp_alerts(camp)]


def test_camp_alerts_are_quiet_for_a_healthy_camp():
    camp = SurfCampFactory(capacity=20, min_participants=1)
    assert [alert for alert in services.camp_alerts(camp) if alert["level"] == "error"] == []


def test_student_age_helper_handles_a_missing_birthday():
    class Plain:
        pass

    assert services.student_age(Plain()) is None
    assert services.is_minor(Plain()) is False


def test_student_age_helper_reads_a_date_of_birth():
    class Teenager:
        date_of_birth = date(timezone.localdate().year - 15, 1, 1)

    assert services.student_age(Teenager()) in (14, 15)
    assert services.is_minor(Teenager()) is True
