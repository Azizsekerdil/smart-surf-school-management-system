"""Model-level behaviour: codes, money arithmetic and cross-field validation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, WaitlistEntry
from apps.core.enums import BookingStatus, PaymentStatus

from .factories import BookingFactory, WaitlistEntryFactory, build_customer, build_lesson

pytestmark = pytest.mark.django_db


def test_booking_code_is_sequential_and_prefixed():
    first = BookingFactory()
    second = BookingFactory()
    assert first.booking_code.startswith("BK")
    assert len(first.booking_code) == 8  # BK + 6 digits
    assert second.booking_code > first.booking_code


def test_str_shows_code_and_customer():
    booking = BookingFactory()
    assert booking.booking_code in str(booking)
    assert str(booking.customer) in str(booking)


def test_recalculate_totals_applies_discount_per_seat():
    booking = BookingFactory(
        participants=3, unit_price=Decimal("50.00"), discount_amount=Decimal("10.00")
    )
    booking.recalculate_totals()
    assert booking.total_amount == Decimal("140.00")
    assert booking.payment_status == PaymentStatus.UNPAID


def test_payment_status_tracks_paid_amount():
    booking = BookingFactory(participants=2, unit_price=Decimal("40.00"))
    booking.recalculate_totals()
    assert booking.total_amount == Decimal("80.00")

    booking.paid_amount = Decimal("30.00")
    booking.recalculate_totals()
    assert booking.payment_status == PaymentStatus.PARTIAL
    assert booking.balance_due == Decimal("50.00")

    booking.paid_amount = Decimal("80.00")
    booking.recalculate_totals()
    assert booking.payment_status == PaymentStatus.PAID
    assert booking.is_paid is True
    assert booking.balance_due == Decimal("0.00")


def test_cancelled_booking_owes_only_the_cancellation_fee():
    booking = BookingFactory(participants=2, unit_price=Decimal("60.00"))
    booking.status = BookingStatus.CANCELLED
    booking.cancellation_fee = Decimal("30.00")
    booking.recalculate_totals()
    assert booking.total_amount == Decimal("30.00")


def test_total_never_goes_negative():
    booking = BookingFactory(unit_price=Decimal("20.00"), discount_amount=Decimal("50.00"))
    booking.recalculate_totals()
    assert booking.total_amount == Decimal("0.00")


def test_free_cancellation_window():
    soon = BookingFactory(lesson=build_lesson(start=timezone.now() + timedelta(hours=3)))
    later = BookingFactory(lesson=build_lesson(start=timezone.now() + timedelta(days=5)))
    assert soon.is_cancellable_free is False
    assert later.is_cancellable_free is True


def test_lifecycle_flags():
    booking = BookingFactory(status=BookingStatus.CONFIRMED)
    assert booking.is_active is True
    assert booking.can_cancel is True
    assert booking.can_check_in is True

    booking.status = BookingStatus.COMPLETED
    assert booking.is_active is False
    assert booking.can_cancel is False


def test_lesson_booking_requires_a_lesson_and_a_student():
    booking = Booking(
        booking_type=Booking.BookingType.LESSON, customer=build_customer(), participants=1
    )
    with pytest.raises(ValidationError) as error:
        booking.clean()
    assert "lesson" in error.value.message_dict
    assert "student" in error.value.message_dict


def test_a_booking_cannot_be_both_a_lesson_and_a_camp():
    booking = BookingFactory.build(customer=build_customer())
    booking.surf_camp_id = 1
    booking.lesson_id = 1
    with pytest.raises(ValidationError) as error:
        booking.clean()
    assert "surf_camp" in error.value.message_dict


def test_cancelled_booking_must_carry_a_reason():
    booking = BookingFactory()
    booking.status = BookingStatus.CANCELLED
    with pytest.raises(ValidationError) as error:
        booking.clean()
    assert "cancellation_reason" in error.value.message_dict


def test_waitlist_position_increments_per_session():
    lesson = build_lesson()
    first = WaitlistEntryFactory(lesson=lesson)
    second = WaitlistEntryFactory(lesson=lesson)
    assert (first.position, second.position) == (1, 2)
    assert first.is_waiting is True


def test_waitlist_entry_needs_a_target():
    entry = WaitlistEntry(customer=build_customer(), participants=1)
    with pytest.raises(ValidationError):
        entry.clean()
