"""Model-level rules: derived values, safety ratios and validation."""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import (
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    LessonStatus,
    SurfLevel,
)

from ..models import Lesson, LessonType, ratio_limit, student_is_minor
from .factories import LessonTypeFactory


# ---------------------------------------------------------------------------
# Pure functions — no database needed
# ---------------------------------------------------------------------------
def test_ratio_limit_uses_the_level_table():
    assert ratio_limit(SurfLevel.BEGINNER) == MAX_STUDENTS_PER_INSTRUCTOR[SurfLevel.BEGINNER]
    assert ratio_limit(SurfLevel.INTERMEDIATE) == MAX_STUDENTS_PER_INSTRUCTOR[
        SurfLevel.INTERMEDIATE
    ]


def test_ratio_limit_scales_with_instructors():
    single = ratio_limit(SurfLevel.BEGINNER, instructor_count=1)
    assert ratio_limit(SurfLevel.BEGINNER, instructor_count=3) == single * 3


def test_ratio_limit_tightens_for_minors():
    assert (
        ratio_limit(SurfLevel.INTERMEDIATE, has_minors=True) == MAX_STUDENTS_PER_INSTRUCTOR_MINORS
    )
    # The minors ceiling never *raises* a stricter level limit.
    assert ratio_limit(SurfLevel.COMPETITION, has_minors=True) <= MAX_STUDENTS_PER_INSTRUCTOR[
        SurfLevel.COMPETITION
    ]


class _FakeStudent:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_student_is_minor_from_birth_date():
    today = timezone.localdate()
    child = _FakeStudent(date_of_birth=today.replace(year=today.year - 12))
    adult = _FakeStudent(date_of_birth=today.replace(year=today.year - 30))
    assert student_is_minor(child) is True
    assert student_is_minor(adult) is False


def test_student_is_minor_without_any_birth_information():
    assert student_is_minor(_FakeStudent()) is False


# ---------------------------------------------------------------------------
# LessonType
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_lesson_type_str_and_allowed_levels():
    lesson_type = LessonTypeFactory(
        code="BEG", name="Beginner", min_level=SurfLevel.FIRST_TIME, max_level=SurfLevel.INTERMEDIATE
    )
    assert str(lesson_type) == "BEG — Beginner"
    assert lesson_type.allowed_levels == [
        SurfLevel.FIRST_TIME,
        SurfLevel.BEGINNER,
        SurfLevel.ADVANCED_BEGINNER,
        SurfLevel.INTERMEDIATE,
    ]
    assert lesson_type.accepts_level(SurfLevel.BEGINNER) is True
    assert lesson_type.accepts_level(SurfLevel.ADVANCED) is False


@pytest.mark.django_db
def test_lesson_type_is_private():
    assert LessonTypeFactory(category=LessonType.Category.PRIVATE).is_private is True
    assert LessonTypeFactory(max_students=1).is_private is True
    assert LessonTypeFactory(max_students=8).is_private is False


@pytest.mark.django_db
def test_lesson_type_price_is_exact_decimal_arithmetic():
    lesson_type = LessonTypeFactory(
        min_students=1, base_price=Decimal("750.00"), price_per_extra_student=Decimal("500.00")
    )
    assert lesson_type.price_for(1) == Decimal("750.00")
    assert lesson_type.price_for(4) == Decimal("2250.00")
    assert lesson_type.price_for(0) == Decimal("750.00")


@pytest.mark.django_db
def test_lesson_type_age_band():
    lesson_type = LessonTypeFactory(min_age=8, max_age=17)
    assert lesson_type.accepts_age(12) is True
    assert lesson_type.accepts_age(7) is False
    assert lesson_type.accepts_age(18) is False
    assert lesson_type.accepts_age(None) is True


@pytest.mark.django_db
def test_lesson_type_rejects_inverted_bands():
    lesson_type = LessonTypeFactory.build(min_students=6, max_students=4)
    with pytest.raises(ValidationError) as excinfo:
        lesson_type.clean()
    assert "min_students" in excinfo.value.message_dict

    inverted = LessonTypeFactory.build(
        min_level=SurfLevel.ADVANCED, max_level=SurfLevel.BEGINNER
    )
    with pytest.raises(ValidationError) as excinfo:
        inverted.clean()
    assert "max_level" in excinfo.value.message_dict


# ---------------------------------------------------------------------------
# Lesson
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_lesson_code_is_generated_sequentially(lesson):
    assert lesson.lesson_code.startswith("LSN")
    assert len(lesson.lesson_code) == 8

    second = Lesson.objects.create(
        lesson_type=lesson.lesson_type,
        spot=lesson.spot,
        instructor=lesson.instructor,
        date=lesson.date + timedelta(days=1),
        start_time=time(14, 0),
        end_time=time(16, 0),
        capacity=4,
    )
    assert second.lesson_code != lesson.lesson_code


@pytest.mark.django_db
def test_lesson_duration_and_str(lesson):
    assert lesson.duration_minutes == 120
    assert lesson.lesson_code in str(lesson)


@pytest.mark.django_db
def test_lesson_seat_counters_ignore_cancelled_seats(lesson, student, another_student):
    from ..models import LessonAttendance

    LessonAttendance.objects.create(lesson=lesson, student=student)
    LessonAttendance.objects.create(
        lesson=lesson, student=another_student, status=LessonAttendance.Status.CANCELLED
    )
    assert lesson.booked_count == 1
    assert lesson.available_seats == lesson.capacity - 1
    assert lesson.is_full is False


@pytest.mark.django_db
def test_lesson_price_prefers_the_override(lesson):
    assert lesson.price == lesson.lesson_type.base_price
    lesson.price_override = Decimal("999.00")
    assert lesson.price == Decimal("999.00")


@pytest.mark.django_db
def test_lesson_is_past(lesson, past_lesson):
    assert past_lesson.is_past is True
    assert lesson.is_past is False


@pytest.mark.django_db
def test_lesson_clean_rejects_reversed_times(lesson):
    lesson.end_time = time(9, 0)
    with pytest.raises(ValidationError) as excinfo:
        lesson.clean()
    assert "end_time" in excinfo.value.message_dict


@pytest.mark.django_db
def test_lesson_clean_rejects_capacity_above_the_lesson_type(lesson):
    lesson.capacity = lesson.lesson_type.max_students + 1
    with pytest.raises(ValidationError) as excinfo:
        lesson.clean()
    assert "capacity" in excinfo.value.message_dict


@pytest.mark.django_db
def test_lesson_clean_rejects_capacity_above_the_safety_ratio(lesson):
    lesson.lesson_type.max_students = 40
    lesson.lesson_type.save()
    lesson.capacity = ratio_limit(lesson.lesson_type.max_level) + 1
    with pytest.raises(ValidationError) as excinfo:
        lesson.clean()
    assert "capacity" in excinfo.value.message_dict


@pytest.mark.django_db
def test_lesson_clean_requires_a_cancellation_reason(lesson):
    lesson.status = LessonStatus.CANCELLED
    with pytest.raises(ValidationError) as excinfo:
        lesson.clean()
    assert "cancellation_reason" in excinfo.value.message_dict


@pytest.mark.django_db
def test_required_ratio_ok_reflects_the_roster(lesson, student, another_student):
    from ..models import LessonAttendance

    lesson.capacity = 8
    lesson.save()
    LessonAttendance.objects.create(lesson=lesson, student=student)
    LessonAttendance.objects.create(lesson=lesson, student=another_student)
    assert lesson.required_ratio_ok is True


@pytest.mark.django_db
def test_attendance_unique_per_student(lesson, student):
    from django.db import IntegrityError

    from ..models import LessonAttendance

    LessonAttendance.objects.create(lesson=lesson, student=student)
    with pytest.raises(IntegrityError):
        LessonAttendance.objects.create(lesson=lesson, student=student)
