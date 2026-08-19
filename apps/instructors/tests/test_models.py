"""Model-level rules: codes, validation, expiry and computed scores."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.instructors.models import AvailabilitySlot, Certification, Instructor, TimeOff

from .factories import (
    AvailabilitySlotFactory,
    CertificationFactory,
    InstructorFactory,
    PerformanceReviewFactory,
    TimeOffFactory,
    fully_certified,
)

pytestmark = pytest.mark.django_db


class TestInstructor:
    def test_code_is_generated_and_sequential(self):
        first = InstructorFactory()
        second = InstructorFactory()
        assert first.instructor_code == "INS0001"
        assert second.instructor_code == "INS0002"

    def test_str_contains_name_and_code(self):
        instructor = InstructorFactory()
        assert instructor.instructor_code in str(instructor)
        assert instructor.full_name in str(instructor)

    def test_full_name_falls_back_to_the_user_display_name(self):
        instructor = InstructorFactory()
        assert instructor.full_name == instructor.user.get_display_name()

    def test_future_hire_date_is_rejected(self):
        instructor = InstructorFactory.build(
            hire_date=timezone.localdate() + dt.timedelta(days=1)
        )
        with pytest.raises(ValidationError) as error:
            instructor.clean()
        assert "hire_date" in error.value.message_dict

    def test_has_valid_certifications_requires_every_group(self):
        instructor = InstructorFactory()
        CertificationFactory(instructor=instructor, kind=Certification.Kind.ISA_L1)
        assert instructor.has_valid_certifications is False

        fully_certified(instructor)
        assert instructor.has_valid_certifications is True
        assert instructor.missing_certification_groups == []

    def test_expired_rescue_award_invalidates_the_instructor(self):
        instructor = fully_certified(InstructorFactory())
        rescue = instructor.certifications.get(kind=Certification.Kind.LIFEGUARD)
        rescue.expires_on = timezone.localdate() - dt.timedelta(days=1)
        rescue.save(update_fields=["expires_on"])
        assert instructor.has_valid_certifications is False

    def test_expiring_certifications_uses_the_sixty_day_window(self):
        instructor = InstructorFactory()
        soon = CertificationFactory(
            instructor=instructor,
            expires_on=timezone.localdate() + dt.timedelta(days=30),
        )
        CertificationFactory(
            instructor=instructor,
            kind=Certification.Kind.CPR,
            expires_on=timezone.localdate() + dt.timedelta(days=120),
        )
        assert list(instructor.expiring_certifications) == [soon]

    def test_soft_delete_hides_the_row_from_the_default_manager(self):
        instructor = InstructorFactory()
        instructor.delete()
        assert Instructor.objects.filter(pk=instructor.pk).exists() is False
        assert Instructor.all_objects.filter(pk=instructor.pk).exists() is True


class TestCertification:
    def test_expiry_helpers(self):
        certification = CertificationFactory(
            expires_on=timezone.localdate() + dt.timedelta(days=10)
        )
        assert certification.is_expired is False
        assert certification.days_until_expiry == 10
        assert certification.is_expiring_soon is True
        assert certification.status == Certification.Status.EXPIRING

    def test_expired_status_wins_over_unverified(self):
        certification = CertificationFactory(
            is_verified=False, expires_on=timezone.localdate() - dt.timedelta(days=1)
        )
        assert certification.status == Certification.Status.EXPIRED

    def test_unverified_certification_is_not_current(self):
        certification = CertificationFactory(is_verified=False)
        assert certification.is_current is False
        assert certification.status == Certification.Status.UNVERIFIED

    def test_expiry_before_issue_is_rejected(self):
        certification = CertificationFactory.build(
            issued_on=timezone.localdate(),
            expires_on=timezone.localdate() - dt.timedelta(days=1),
        )
        with pytest.raises(ValidationError) as error:
            certification.clean()
        assert "expires_on" in error.value.message_dict

    def test_ordering_puts_the_soonest_expiry_first(self):
        instructor = InstructorFactory()
        late = CertificationFactory(
            instructor=instructor, expires_on=timezone.localdate() + dt.timedelta(days=300)
        )
        early = CertificationFactory(
            instructor=instructor,
            kind=Certification.Kind.CPR,
            expires_on=timezone.localdate() + dt.timedelta(days=30),
        )
        never = CertificationFactory(
            instructor=instructor, kind=Certification.Kind.SAFEGUARDING, expires_on=None
        )
        assert list(instructor.certifications.all()) == [early, late, never]


class TestAvailabilitySlot:
    def test_end_before_start_is_rejected(self):
        slot = AvailabilitySlotFactory.build(
            start_time=dt.time(14, 0), end_time=dt.time(12, 0)
        )
        with pytest.raises(ValidationError) as error:
            slot.clean()
        assert "end_time" in error.value.message_dict

    def test_validity_window(self):
        today = timezone.localdate()
        slot = AvailabilitySlotFactory(
            valid_from=today, valid_until=today + dt.timedelta(days=30)
        )
        assert slot.is_valid_on(today) is True
        assert slot.is_valid_on(today - dt.timedelta(days=1)) is False
        assert slot.is_valid_on(today + dt.timedelta(days=31)) is False

    def test_covers_requires_full_containment(self):
        slot = AvailabilitySlotFactory(start_time=dt.time(9, 0), end_time=dt.time(12, 0))
        assert slot.covers(dt.time(10, 0), dt.time(11, 0)) is True
        assert slot.covers(dt.time(10, 0), dt.time(13, 0)) is False

    def test_str_is_readable(self):
        slot = AvailabilitySlotFactory(
            weekday=AvailabilitySlot.Weekday.FRIDAY,
            start_time=dt.time(9, 30),
            end_time=dt.time(11, 0),
        )
        assert "09:30" in str(slot) and "11:00" in str(slot)


class TestTimeOff:
    def test_end_before_start_is_rejected(self):
        time_off = TimeOffFactory.build(
            start_date=timezone.localdate(),
            end_date=timezone.localdate() - dt.timedelta(days=1),
        )
        with pytest.raises(ValidationError) as error:
            time_off.clean()
        assert "end_date" in error.value.message_dict

    def test_total_days_is_inclusive(self):
        today = timezone.localdate()
        time_off = TimeOffFactory(start_date=today, end_date=today + dt.timedelta(days=2))
        assert time_off.total_days == 3
        assert time_off.is_current is True
        assert time_off.covers(today + dt.timedelta(days=1)) is True

    def test_reason_choices_are_stable(self):
        assert TimeOff.Reason.SICK in dict(TimeOff.Reason.choices)


class TestPerformanceReview:
    def test_overall_score_is_the_mean_of_the_five_criteria(self):
        review = PerformanceReviewFactory(
            teaching_quality=4, punctuality=4, safety=5, communication=4, teamwork=3
        )
        assert review.overall_score == Decimal("4.00")

    def test_score_out_of_range_is_rejected(self):
        review = PerformanceReviewFactory.build(teaching_quality=9)
        with pytest.raises(ValidationError) as error:
            review.clean()
        assert "teaching_quality" in error.value.message_dict

    def test_period_end_before_start_is_rejected(self):
        review = PerformanceReviewFactory.build(
            period_start=timezone.localdate(),
            period_end=timezone.localdate() - dt.timedelta(days=1),
        )
        with pytest.raises(ValidationError) as error:
            review.clean()
        assert "period_end" in error.value.message_dict
