"""Model-level behaviour: validation, derived numbers and place accounting."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.core.enums import SurfLevel

from ..models import CampParticipant, CampStatus, ParticipantStatus, RoomType, SurfCamp
from .factories import CampActivityFactory, CampParticipantFactory, SurfCampFactory

pytestmark = pytest.mark.django_db


def test_str_and_code_are_generated():
    camp = SurfCampFactory(name="Autumn Camp")
    assert camp.code.startswith("CAMP")
    assert camp.name in str(camp)


def test_codes_are_sequential():
    first = SurfCampFactory()
    second = SurfCampFactory()
    assert first.code != second.code


def test_duration_and_nights():
    camp = SurfCampFactory(
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timedelta(days=6),
    )
    assert camp.duration_days == 7
    assert camp.nights == 6
    assert len(camp.date_list()) == 7


def test_end_date_before_start_is_rejected():
    camp = SurfCampFactory.build(
        start_date=timezone.localdate(),
        end_date=timezone.localdate() - timedelta(days=1),
    )
    with pytest.raises(ValidationError) as error:
        camp.clean()
    assert "end_date" in error.value.message_dict


def test_capacity_must_be_positive():
    camp = SurfCampFactory.build(capacity=0)
    with pytest.raises(ValidationError) as error:
        camp.clean()
    assert "capacity" in error.value.message_dict


def test_deposit_cannot_exceed_price():
    camp = SurfCampFactory.build(price=Decimal("100.00"), deposit_amount=Decimal("120.00"))
    with pytest.raises(ValidationError) as error:
        camp.clean()
    assert "deposit_amount" in error.value.message_dict


def test_level_range_must_be_ordered():
    camp = SurfCampFactory.build(
        min_level=SurfLevel.ADVANCED, max_level=SurfLevel.BEGINNER
    )
    with pytest.raises(ValidationError) as error:
        camp.clean()
    assert "max_level" in error.value.message_dict


def test_capacity_cannot_drop_below_booked_places():
    camp = SurfCampFactory(capacity=3)
    CampParticipantFactory.create_batch(2, camp=camp)
    camp.capacity = 1
    with pytest.raises(ValidationError) as error:
        camp.clean()
    assert "capacity" in error.value.message_dict


def test_cancelled_place_frees_capacity():
    camp = SurfCampFactory(capacity=2)
    first = CampParticipantFactory(camp=camp)
    CampParticipantFactory(camp=camp)
    assert camp.available_places == 0
    assert camp.is_full is True

    first.status = ParticipantStatus.CANCELLED
    first.save(update_fields=["status"])

    assert camp.available_places == 1
    assert camp.is_full is False


def test_total_revenue_includes_single_room_supplement():
    camp = SurfCampFactory(price=Decimal("500.00"), single_room_supplement=Decimal("100.00"))
    CampParticipantFactory(camp=camp, room_type=RoomType.SHARED)
    CampParticipantFactory(camp=camp, room_type=RoomType.SINGLE)

    assert camp.total_revenue == Decimal("1100.00")


def test_accepts_level_respects_the_advertised_range():
    camp = SurfCampFactory(min_level=SurfLevel.BEGINNER, max_level=SurfLevel.INTERMEDIATE)
    assert camp.accepts_level(SurfLevel.BEGINNER) is True
    assert camp.accepts_level(SurfLevel.INTERMEDIATE) is True
    assert camp.accepts_level(SurfLevel.FIRST_TIME) is False
    assert camp.accepts_level(SurfLevel.ADVANCED) is False


def test_is_running_and_is_upcoming():
    today = timezone.localdate()
    running = SurfCampFactory(start_date=today - timedelta(days=1), end_date=today + timedelta(days=1))
    upcoming = SurfCampFactory(start_date=today + timedelta(days=10), end_date=today + timedelta(days=16))

    assert running.is_running is True
    assert running.is_upcoming is False
    assert upcoming.is_upcoming is True
    assert upcoming.is_running is False


def test_cancelled_camp_is_neither_running_nor_upcoming():
    today = timezone.localdate()
    camp = SurfCampFactory(
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=1),
        status=CampStatus.CANCELLED,
    )
    assert camp.is_running is False
    assert camp.is_upcoming is False


def test_participant_balance_and_payment_state():
    camp = SurfCampFactory(price=Decimal("400.00"), single_room_supplement=Decimal("100.00"))
    participant = CampParticipantFactory(
        camp=camp, room_type=RoomType.SINGLE, amount_paid=Decimal("200.00")
    )

    assert participant.total_price == Decimal("500.00")
    assert participant.balance_due == Decimal("300.00")
    assert participant.is_fully_paid is False
    assert participant.payment_state == "partial"

    participant.amount_paid = Decimal("500.00")
    assert participant.is_fully_paid is True
    assert participant.payment_state == "paid"


def test_participant_departure_before_arrival_is_rejected():
    participant = CampParticipantFactory.build(
        arrival_datetime=timezone.now(),
        departure_datetime=timezone.now() - timedelta(days=1),
    )
    with pytest.raises(ValidationError) as error:
        participant.clean()
    assert "departure_datetime" in error.value.message_dict


def test_participant_is_on_site_uses_camp_dates_when_no_flights():
    camp = SurfCampFactory()
    participant = CampParticipantFactory(camp=camp)

    assert participant.is_on_site(camp.start_date) is True
    assert participant.is_on_site(camp.end_date) is True
    assert participant.is_on_site(camp.start_date - timedelta(days=1)) is False


def test_a_student_cannot_hold_two_places_on_one_camp():
    participant = CampParticipantFactory()
    with pytest.raises(IntegrityError):
        CampParticipant.objects.create(camp=participant.camp, student=participant.student)


def test_activity_end_must_follow_start():
    activity = CampActivityFactory.build()
    activity.start_time = activity.end_time
    with pytest.raises(ValidationError) as error:
        activity.clean()
    assert "end_time" in error.value.message_dict


def test_activity_duration_and_overlap():
    day_activity = CampActivityFactory()
    other = CampActivityFactory.build(
        camp_day=day_activity.camp_day,
        start_time=day_activity.start_time,
        end_time=day_activity.end_time,
    )
    assert day_activity.duration_minutes == 120
    assert day_activity.overlaps(other) is True
    assert day_activity.is_water_activity is True


def test_camp_day_must_fall_inside_the_camp():
    camp = SurfCampFactory()
    from ..models import CampDay

    day = CampDay(camp=camp, date=camp.end_date + timedelta(days=3), day_number=99)
    with pytest.raises(ValidationError) as error:
        day.clean()
    assert "date" in error.value.message_dict


def test_soft_deleted_camp_disappears_from_the_default_manager():
    camp = SurfCampFactory()
    camp.delete()
    assert SurfCamp.objects.filter(pk=camp.pk).exists() is False
    assert SurfCamp.all_objects.filter(pk=camp.pk).exists() is True
