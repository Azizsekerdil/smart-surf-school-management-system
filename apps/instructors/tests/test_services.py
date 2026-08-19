"""Service rules: availability, ratios, certification currency and money."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import SurfLevel
from apps.instructors import services
from apps.instructors.models import AvailabilitySlot, Certification

from .factories import (
    AvailabilitySlotFactory,
    CertificationFactory,
    InstructorFactory,
    TimeOffFactory,
    UserFactory,
    fully_certified,
)

pytestmark = pytest.mark.django_db


def next_weekday(weekday: int = 0) -> dt.date:
    """The next occurrence of *weekday* (0 = Monday), today included."""
    today = timezone.localdate()
    return today + dt.timedelta(days=(weekday - today.weekday()) % 7)


@pytest.fixture
def bookable_instructor():
    instructor = fully_certified(InstructorFactory())
    AvailabilitySlotFactory(
        instructor=instructor,
        weekday=AvailabilitySlot.Weekday.MONDAY,
        start_time=dt.time(9, 0),
        end_time=dt.time(17, 0),
    )
    return instructor


class TestIsInstructorAvailable:
    def test_available_inside_a_published_slot(self, bookable_instructor):
        available, reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(10, 0), dt.time(12, 0)
        )
        assert available is True
        assert reason == ""

    def test_window_outside_the_slot_is_refused(self, bookable_instructor):
        available, reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(17, 30), dt.time(18, 30)
        )
        assert available is False
        assert reason

    def test_other_weekday_is_refused(self, bookable_instructor):
        available, _reason = services.is_instructor_available(
            bookable_instructor, next_weekday(2), dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False

    def test_inactive_instructor_is_refused(self, bookable_instructor):
        bookable_instructor.is_active = False
        bookable_instructor.save(update_fields=["is_active"])
        available, reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False
        assert reason

    def test_instructor_closed_for_bookings_is_refused(self, bookable_instructor):
        bookable_instructor.is_available_for_booking = False
        bookable_instructor.save(update_fields=["is_available_for_booking"])
        available, _reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False

    def test_inverted_window_is_refused(self, bookable_instructor):
        available, _reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(12, 0), dt.time(10, 0)
        )
        assert available is False

    def test_approved_time_off_blocks_the_day(self, bookable_instructor):
        monday = next_weekday(0)
        TimeOffFactory(
            instructor=bookable_instructor,
            start_date=monday,
            end_date=monday,
            is_approved=True,
        )
        available, reason = services.is_instructor_available(
            bookable_instructor, monday, dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False
        assert reason

    def test_unapproved_time_off_does_not_block(self, bookable_instructor):
        monday = next_weekday(0)
        TimeOffFactory(
            instructor=bookable_instructor,
            start_date=monday,
            end_date=monday,
            is_approved=False,
        )
        available, _reason = services.is_instructor_available(
            bookable_instructor, monday, dt.time(10, 0), dt.time(12, 0)
        )
        assert available is True

    def test_slot_outside_its_validity_window_does_not_count(self):
        instructor = InstructorFactory()
        monday = next_weekday(0)
        AvailabilitySlotFactory(
            instructor=instructor,
            weekday=AvailabilitySlot.Weekday.MONDAY,
            start_time=dt.time(9, 0),
            end_time=dt.time(17, 0),
            valid_until=monday - dt.timedelta(days=1),
        )
        available, _reason = services.is_instructor_available(
            instructor, monday, dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False

    def test_inactive_slot_does_not_count(self, bookable_instructor):
        bookable_instructor.availability_slots.update(is_active=False)
        available, _reason = services.is_instructor_available(
            bookable_instructor, next_weekday(0), dt.time(10, 0), dt.time(12, 0)
        )
        assert available is False


class TestAvailableInstructors:
    def test_returns_only_free_instructors(self, bookable_instructor):
        busy = InstructorFactory()  # no availability published at all
        monday = next_weekday(0)
        result = services.available_instructors(monday, dt.time(10, 0), dt.time(12, 0))
        assert bookable_instructor in result
        assert busy not in result

    def test_level_filter_excludes_lower_grades(self, bookable_instructor):
        bookable_instructor.max_level_taught = SurfLevel.BEGINNER
        bookable_instructor.save(update_fields=["max_level_taught"])
        result = services.available_instructors(
            next_weekday(0), dt.time(10, 0), dt.time(12, 0), level=SurfLevel.ADVANCED
        )
        assert bookable_instructor not in result

    def test_level_filter_keeps_higher_grades(self, bookable_instructor):
        result = services.available_instructors(
            next_weekday(0), dt.time(10, 0), dt.time(12, 0), level=SurfLevel.BEGINNER
        )
        assert bookable_instructor in result

    def test_approved_absence_removes_the_instructor(self, bookable_instructor):
        monday = next_weekday(0)
        TimeOffFactory(
            instructor=bookable_instructor,
            start_date=monday - dt.timedelta(days=1),
            end_date=monday + dt.timedelta(days=1),
            is_approved=True,
        )
        result = services.available_instructors(monday, dt.time(10, 0), dt.time(12, 0))
        assert bookable_instructor not in result

    def test_invalid_window_returns_nothing(self):
        result = services.available_instructors(
            next_weekday(0), dt.time(12, 0), dt.time(10, 0)
        )
        assert result.count() == 0


class TestRatios:
    def test_ceiling_follows_the_level_table(self):
        assert services.ratio_ceiling(SurfLevel.BEGINNER) == 8
        assert services.ratio_ceiling(SurfLevel.INTERMEDIATE) == 10

    def test_minors_tighten_the_ratio(self):
        instructor = InstructorFactory(max_students_per_lesson=10)
        assert services.max_students_for(instructor, SurfLevel.INTERMEDIATE) == 10
        assert (
            services.max_students_for(instructor, SurfLevel.INTERMEDIATE, has_minors=True) == 6
        )

    def test_personal_maximum_can_only_lower_the_limit(self):
        instructor = InstructorFactory(max_students_per_lesson=4)
        assert services.max_students_for(instructor, SurfLevel.BEGINNER) == 4

    def test_first_timer_ratio_is_stricter_than_beginner(self):
        instructor = InstructorFactory(max_students_per_lesson=8)
        assert services.max_students_for(instructor, SurfLevel.FIRST_TIME) == 6

    def test_can_teach_level_compares_ranks(self):
        instructor = InstructorFactory(max_level_taught=SurfLevel.BEGINNER)
        assert services.can_teach_level(instructor, SurfLevel.BEGINNER) is True
        assert services.can_teach_level(instructor, SurfLevel.ADVANCED) is False


class TestAssignmentBlockers:
    def test_fully_certified_instructor_has_no_blockers(self):
        instructor = fully_certified(InstructorFactory())
        assert services.assignment_blockers(instructor) == []

    def test_missing_rescue_award_is_a_blocker(self):
        instructor = InstructorFactory()
        CertificationFactory(instructor=instructor, kind=Certification.Kind.ISA_L1)
        assert services.assignment_blockers(instructor)

    def test_intermediate_requires_a_level_two_award(self):
        instructor = InstructorFactory(max_level_taught=SurfLevel.ADVANCED)
        today = timezone.localdate()
        for kind in (
            Certification.Kind.ISA_L1,
            Certification.Kind.LIFEGUARD,
            Certification.Kind.FIRST_AID,
        ):
            CertificationFactory(
                instructor=instructor,
                kind=kind,
                expires_on=today + dt.timedelta(days=300),
                is_verified=True,
            )
        blockers = services.assignment_blockers(instructor, level=SurfLevel.INTERMEDIATE)
        assert blockers

    def test_group_larger_than_the_ratio_is_a_blocker(self):
        instructor = fully_certified(InstructorFactory(max_students_per_lesson=6))
        blockers = services.assignment_blockers(
            instructor, level=SurfLevel.BEGINNER, student_count=8
        )
        assert blockers


class TestCertificationExpiry:
    def test_report_groups_by_instructor_and_sorts_by_urgency(self):
        today = timezone.localdate()
        urgent = InstructorFactory()
        CertificationFactory(
            instructor=urgent, expires_on=today + dt.timedelta(days=5)
        )
        later = InstructorFactory()
        CertificationFactory(
            instructor=later, expires_on=today + dt.timedelta(days=50)
        )
        InstructorFactory()  # nothing expiring

        report = services.check_certification_expiry(60)
        assert [entry["instructor"] for entry in report] == [urgent, later]
        assert report[0]["days_until_expiry"] == 5

    def test_already_expired_certificates_are_included_and_flagged(self):
        instructor = InstructorFactory()
        CertificationFactory(
            instructor=instructor, expires_on=timezone.localdate() - dt.timedelta(days=3)
        )
        report = services.check_certification_expiry(60)
        assert report[0]["has_expired"] is True

    def test_inactive_instructors_are_left_out(self):
        instructor = InstructorFactory(is_active=False)
        CertificationFactory(
            instructor=instructor, expires_on=timezone.localdate() + dt.timedelta(days=5)
        )
        assert services.check_certification_expiry(60) == []

    def test_verification_stamps_the_named_approver(self):
        certification = CertificationFactory(is_verified=False)
        approver = UserFactory()
        services.verify_certification(certification, approver)
        certification.refresh_from_db()
        assert certification.is_verified is True
        assert certification.verified_by == approver
        assert certification.verified_at is not None


class TestAvailabilitySlots:
    def test_overlapping_slot_is_refused(self):
        instructor = InstructorFactory()
        services.create_availability_slot(
            instructor, AvailabilitySlot.Weekday.MONDAY, dt.time(9, 0), dt.time(12, 0)
        )
        with pytest.raises(ValidationError):
            services.create_availability_slot(
                instructor, AvailabilitySlot.Weekday.MONDAY, dt.time(11, 0), dt.time(14, 0)
            )

    def test_adjacent_slots_are_allowed(self):
        instructor = InstructorFactory()
        services.create_availability_slot(
            instructor, AvailabilitySlot.Weekday.MONDAY, dt.time(9, 0), dt.time(12, 0)
        )
        services.create_availability_slot(
            instructor, AvailabilitySlot.Weekday.MONDAY, dt.time(12, 0), dt.time(15, 0)
        )
        assert instructor.availability_slots.count() == 2

    def test_inverted_slot_is_refused(self):
        instructor = InstructorFactory()
        with pytest.raises(ValidationError):
            services.create_availability_slot(
                instructor, AvailabilitySlot.Weekday.MONDAY, dt.time(15, 0), dt.time(9, 0)
            )

    def test_weekly_availability_returns_seven_days(self):
        instructor = InstructorFactory()
        AvailabilitySlotFactory(instructor=instructor)
        week = services.weekly_availability(instructor)
        assert len(week) == 7
        assert week[0]["active_minutes"] == 480


class TestTimeOff:
    def test_approval_stamps_the_approver(self):
        time_off = TimeOffFactory()
        approver = UserFactory()
        approved, affected = services.approve_time_off(time_off, approver)
        assert approved.is_approved is True
        assert approved.approved_by == approver
        assert affected == 0

    def test_approving_twice_is_a_no_op(self):
        time_off = TimeOffFactory(is_approved=True)
        _period, affected = services.approve_time_off(time_off, UserFactory())
        assert affected == 0

    def test_overlap_detection(self):
        instructor = InstructorFactory()
        today = timezone.localdate()
        TimeOffFactory(
            instructor=instructor, start_date=today, end_date=today + dt.timedelta(days=5)
        )
        clashing = services.overlapping_time_off(
            instructor, today + dt.timedelta(days=3), today + dt.timedelta(days=8)
        )
        assert clashing.count() == 1


class TestStatisticsAndPerformance:
    def test_rating_recalculation_is_zero_safe_without_lessons(self):
        instructor = InstructorFactory()
        assert services.recalculate_instructor_rating(instructor) == Decimal("0.00")
        instructor.refresh_from_db()
        assert instructor.rating_count == 0

    def test_performance_summary_is_zero_safe(self):
        instructor = InstructorFactory(commission_percent=Decimal("10.00"))
        today = timezone.localdate()
        summary = services.instructor_performance(
            instructor, today - dt.timedelta(days=30), today
        )
        assert summary["lessons_taught"] == 0
        assert summary["students_taught"] == 0
        assert summary["revenue_generated"] == Decimal("0.00")
        assert summary["commission_earned"] == Decimal("0.00")

    def test_performance_summary_swaps_a_reversed_period(self):
        instructor = InstructorFactory()
        today = timezone.localdate()
        summary = services.instructor_performance(
            instructor, today, today - dt.timedelta(days=30)
        )
        assert summary["period_start"] < summary["period_end"]

    def test_deleting_an_instructor_is_allowed_without_lessons(self):
        instructor = InstructorFactory()
        allowed, reason = services.can_delete_instructor(instructor)
        assert allowed is True
        assert reason == ""

    def test_booking_availability_toggle_is_idempotent(self):
        instructor = InstructorFactory(is_available_for_booking=True)
        services.set_booking_availability(instructor, False)
        instructor.refresh_from_db()
        assert instructor.is_available_for_booking is False
        services.set_booking_availability(instructor, False)
        instructor.refresh_from_db()
        assert instructor.is_available_for_booking is False
