"""The rules that keep the school safe and solvent."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.bookings import services
from apps.bookings.models import Booking
from apps.core.enums import BookingStatus, PaymentStatus, SurfLevel

from .factories import (
    BookingFactory,
    WaitlistEntryFactory,
    build_camp,
    build_customer,
    build_lesson,
    build_lesson_type,
    build_student,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def test_create_booking_writes_a_booking_and_an_audit_entry():
    customer = build_customer()
    student = build_student(customer=customer)
    lesson = build_lesson()

    booking = services.create_booking(
        customer, Booking.BookingType.LESSON, lesson=lesson, student=student
    )

    assert booking.pk is not None
    assert booking.status == BookingStatus.PENDING
    assert booking.total_amount == Decimal("50.00")
    assert AuditLog.objects.filter(
        action=AuditAction.BOOKING_CHANGE, object_id=str(booking.pk)
    ).exists()


def test_create_booking_takes_the_price_from_the_lesson():
    customer = build_customer()
    booking = services.create_booking(
        customer,
        Booking.BookingType.LESSON,
        lesson=build_lesson(price=Decimal("75.00")),
        student=build_student(customer=customer),
    )
    assert booking.unit_price == Decimal("75.00")


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------
def test_a_full_lesson_is_refused():
    lesson = build_lesson(max_students=2)
    BookingFactory(lesson=lesson, participants=2, status=BookingStatus.CONFIRMED)

    customer = build_customer()
    problems = services.check_booking_conflicts(
        lesson=lesson, student=build_student(customer=customer), participants=1
    )
    assert problems
    assert any("fully booked" in problem.lower() for problem in problems)

    with pytest.raises(services.BookingConflictError):
        services.create_booking(
            customer,
            Booking.BookingType.LESSON,
            lesson=lesson,
            student=build_student(customer=customer),
        )


def test_partial_availability_reports_the_remaining_seats():
    lesson = build_lesson(max_students=4)
    BookingFactory(lesson=lesson, participants=3, status=BookingStatus.CONFIRMED)
    problems = services.check_booking_conflicts(
        lesson=lesson, student=build_student(), participants=2
    )
    assert any("Only 1" in problem for problem in problems)


def test_the_same_student_cannot_be_booked_twice_on_one_lesson():
    lesson = build_lesson(max_students=8)
    booking = BookingFactory(lesson=lesson, status=BookingStatus.CONFIRMED)
    problems = services.check_booking_conflicts(lesson=lesson, student=booking.student)
    assert any(booking.booking_code in problem for problem in problems)


def test_a_student_cannot_be_in_two_places_at_once():
    start = timezone.now() + timedelta(days=2)
    first = BookingFactory(
        lesson=build_lesson(start=start), status=BookingStatus.CONFIRMED
    )
    overlapping = build_lesson(start=start + timedelta(minutes=30))

    problems = services.check_booking_conflicts(lesson=overlapping, student=first.student)
    assert any(first.booking_code in problem for problem in problems)


def test_a_student_below_the_lesson_level_is_refused():
    lesson_type = build_lesson_type(
        min_level=SurfLevel.INTERMEDIATE, max_level=SurfLevel.ADVANCED
    )
    lesson = build_lesson(lesson_type=lesson_type)
    student = build_student(level=SurfLevel.FIRST_TIME)

    problems = services.check_booking_conflicts(lesson=lesson, student=student)
    assert any("level" in problem.lower() for problem in problems)


def test_the_instructor_ratio_caps_the_group():
    """One coach, first-timer level: six students is the ceiling."""
    lesson_type = build_lesson_type(
        min_level=SurfLevel.FIRST_TIME, max_level=SurfLevel.BEGINNER
    )
    lesson = build_lesson(lesson_type=lesson_type, max_students=20)
    BookingFactory(lesson=lesson, participants=6, status=BookingStatus.CONFIRMED)

    problems = services.check_booking_conflicts(
        lesson=lesson, student=build_student(level=SurfLevel.FIRST_TIME), participants=1
    )
    assert any("ratio" in problem.lower() for problem in problems)


def test_one_minor_pulls_the_whole_group_to_the_stricter_ratio():
    lesson_type = build_lesson_type(
        min_level=SurfLevel.INTERMEDIATE, max_level=SurfLevel.ADVANCED
    )
    lesson = build_lesson(lesson_type=lesson_type, max_students=20)
    BookingFactory(
        lesson=lesson,
        participants=6,
        status=BookingStatus.CONFIRMED,
        student=build_student(level=SurfLevel.INTERMEDIATE, age=30),
    )

    adult = build_student(level=SurfLevel.INTERMEDIATE, age=30)
    minor = build_student(level=SurfLevel.INTERMEDIATE, age=13)

    # Ten intermediates per coach is fine …
    assert not [
        problem
        for problem in services.check_booking_conflicts(lesson=lesson, student=adult)
        if "ratio" in problem.lower() or "Under-18" in problem
    ]
    # … but a single under-18 drops the ceiling to six.
    minor_problems = services.check_booking_conflicts(lesson=lesson, student=minor)
    assert any("Under-18" in problem for problem in minor_problems)


def test_a_lesson_in_the_past_cannot_be_booked():
    lesson = build_lesson(start=timezone.now() - timedelta(hours=2))
    problems = services.check_booking_conflicts(lesson=lesson, student=build_student())
    assert any("started" in problem for problem in problems)


def test_a_lesson_without_an_instructor_cannot_be_booked():
    lesson = build_lesson(instructor=None)
    problems = services.check_booking_conflicts(lesson=lesson, student=build_student())
    assert any("instructor" in problem.lower() for problem in problems)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------
def test_confirm_then_check_in_then_complete():
    booking = BookingFactory(lesson=build_lesson(start=timezone.now() + timedelta(days=1)))
    services.confirm_booking(booking)
    assert booking.status == BookingStatus.CONFIRMED
    assert booking.confirmed_at is not None

    # Check-in is refused for a session that is not today.
    with pytest.raises(services.BookingTransitionError):
        services.check_in_booking(booking)

    services.check_in_booking(booking, force=True)
    assert booking.status == BookingStatus.CHECKED_IN

    services.complete_booking(booking)
    assert booking.status == BookingStatus.COMPLETED


def test_a_completed_booking_cannot_be_confirmed_again():
    booking = BookingFactory(status=BookingStatus.COMPLETED)
    with pytest.raises(services.BookingTransitionError):
        services.confirm_booking(booking)


def test_cancelling_frees_the_seat_and_charges_the_policy_fee():
    lesson = build_lesson(start=timezone.now() + timedelta(hours=3), max_students=4)
    booking = BookingFactory(
        lesson=lesson,
        status=BookingStatus.CONFIRMED,
        participants=2,
        unit_price=Decimal("50.00"),
    )
    assert services.seats_available(lesson=lesson) == 2

    services.cancel_booking(booking, "Customer requested it")

    assert booking.status == BookingStatus.CANCELLED
    assert booking.cancellation_fee == Decimal("50.00")  # 50% of 100.00, inside 24h
    assert booking.total_amount == Decimal("50.00")
    assert services.seats_available(lesson=lesson) == 4
    assert AuditLog.objects.filter(action=AuditAction.BOOKING_CANCEL).exists()


def test_cancelling_outside_the_notice_window_is_free():
    booking = BookingFactory(
        lesson=build_lesson(start=timezone.now() + timedelta(days=4)),
        status=BookingStatus.CONFIRMED,
    )
    services.cancel_booking(booking, "Changed plans")
    assert booking.cancellation_fee == Decimal("0.00")
    assert booking.total_amount == Decimal("0.00")


def test_a_fee_override_is_honoured():
    booking = BookingFactory(
        lesson=build_lesson(start=timezone.now() + timedelta(hours=2)),
        status=BookingStatus.CONFIRMED,
    )
    services.cancel_booking(booking, "Storm — goodwill", fee=Decimal("0.00"))
    assert booking.cancellation_fee == Decimal("0.00")


def test_cancelling_promotes_the_first_person_waiting():
    lesson = build_lesson(max_students=1, start=timezone.now() + timedelta(days=3))
    booking = BookingFactory(lesson=lesson, status=BookingStatus.CONFIRMED)
    entry = WaitlistEntryFactory(lesson=lesson)

    services.cancel_booking(booking, "No longer needed")

    entry.refresh_from_db()
    assert entry.is_converted is True
    assert entry.converted_booking is not None
    assert entry.converted_booking.status == BookingStatus.PENDING


def test_no_show_keeps_the_charge():
    booking = BookingFactory(status=BookingStatus.CONFIRMED, unit_price=Decimal("45.00"))
    services.mark_no_show(booking)
    assert booking.status == BookingStatus.NO_SHOW
    assert booking.total_amount == Decimal("45.00")


def test_registering_a_payment_updates_the_balance():
    booking = BookingFactory(unit_price=Decimal("60.00"))
    services.register_payment(booking, Decimal("20.00"))
    assert booking.payment_status == PaymentStatus.PARTIAL
    services.register_payment(booking, Decimal("40.00"))
    assert booking.payment_status == PaymentStatus.PAID
    assert booking.balance_due == Decimal("0.00")


def test_a_zero_payment_is_rejected():
    booking = BookingFactory()
    with pytest.raises(services.BookingTransitionError):
        services.register_payment(booking, Decimal("0.00"))


# ---------------------------------------------------------------------------
# Waiting list & calendar
# ---------------------------------------------------------------------------
def test_the_waiting_list_does_not_duplicate_a_customer():
    lesson = build_lesson()
    customer = build_customer()
    student = build_student(customer=customer)
    first = services.add_to_waitlist(customer, lesson=lesson, student=student)
    again = services.add_to_waitlist(customer, lesson=lesson, student=student)
    assert first.pk == again.pk


def test_calendar_events_report_capacity():
    lesson = build_lesson(max_students=8, start=timezone.now() + timedelta(days=1))
    BookingFactory(lesson=lesson, participants=4, status=BookingStatus.CONFIRMED)

    events = services.booking_calendar_events(
        timezone.now(), timezone.now() + timedelta(days=2)
    )
    lesson_events = [event for event in events if event["object_id"] == lesson.pk]
    assert lesson_events
    assert lesson_events[0]["capacity_label"] == "4/8"
    assert lesson_events[0]["fill_state"] == "free"


def test_daily_schedule_flags_unconfirmed_bookings():
    start = timezone.now() + timedelta(hours=4)
    booking = BookingFactory(lesson=build_lesson(start=start), status=BookingStatus.PENDING)
    schedule = services.daily_schedule(timezone.localtime(start).date())
    assert schedule["totals"]["participants"] >= booking.participants
    assert any("awaiting confirmation" in str(alert) for alert in schedule["alerts"])


def test_camp_bookings_respect_capacity():
    camp = build_camp(capacity=1)
    customer = build_customer()
    services.create_booking(
        customer,
        Booking.BookingType.CAMP,
        camp=camp,
        student=build_student(customer=customer),
    )
    with pytest.raises(services.BookingConflictError):
        services.create_booking(
            build_customer(), Booking.BookingType.CAMP, camp=camp, student=build_student()
        )
