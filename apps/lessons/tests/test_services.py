"""Business rules: conflicts, roster safety, completion and cancellation."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import LessonStatus, SurfLevel

from ..models import Lesson, LessonAttendance
from ..services import (
    add_student_to_lesson,
    cancel_lesson,
    check_in_student,
    check_lesson_conflicts,
    check_lesson_warnings,
    complete_lesson,
    create_lesson,
    lessons_for_calendar,
    mark_no_show,
    mark_safety_check,
    remove_student_from_lesson,
    suggest_capacity,
)
from .conftest import make_student
from .factories import LessonTypeFactory


# ---------------------------------------------------------------------------
# Capacity suggestion
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_suggest_capacity_never_exceeds_the_product_maximum():
    lesson_type = LessonTypeFactory(max_students=4, max_level=SurfLevel.BEGINNER)
    assert suggest_capacity(lesson_type, instructor_count=3) == 4


@pytest.mark.django_db
def test_suggest_capacity_respects_the_minors_ceiling():
    lesson_type = LessonTypeFactory(max_students=20, max_level=SurfLevel.INTERMEDIATE)
    adults = suggest_capacity(lesson_type, instructor_count=1, has_minors=False)
    minors = suggest_capacity(lesson_type, instructor_count=1, has_minors=True)
    assert minors < adults


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_conflicts_for_a_clean_slot(lesson_type, spot, instructor):
    conflicts = check_lesson_conflicts(
        {
            "lesson_type": lesson_type,
            "spot": spot,
            "date": timezone.localdate() + timedelta(days=2),
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "instructor": instructor,
            "assistant_instructors": [],
            "capacity": 6,
            "status": LessonStatus.SCHEDULED,
        }
    )
    assert conflicts == []


@pytest.mark.django_db
def test_double_booked_instructor_is_a_conflict(lesson, lesson_type, spot, instructor):
    conflicts = check_lesson_conflicts(
        {
            "lesson_type": lesson_type,
            "spot": spot,
            "date": lesson.date,
            "start_time": time(11, 0),  # overlaps 10:00-12:00
            "end_time": time(13, 0),
            "instructor": instructor,
            "assistant_instructors": [],
            "capacity": 4,
            "status": LessonStatus.SCHEDULED,
        }
    )
    assert any(lesson.lesson_code in message for message in conflicts)


@pytest.mark.django_db
def test_back_to_back_slots_do_not_conflict(lesson, lesson_type, spot, instructor):
    conflicts = check_lesson_conflicts(
        {
            "lesson_type": lesson_type,
            "spot": spot,
            "date": lesson.date,
            "start_time": time(12, 0),  # starts exactly when the other ends
            "end_time": time(14, 0),
            "instructor": instructor,
            "assistant_instructors": [],
            "capacity": 4,
            "status": LessonStatus.SCHEDULED,
        }
    )
    assert conflicts == []


@pytest.mark.django_db
def test_capacity_beyond_the_safety_ratio_is_a_conflict(lesson_type, spot, instructor):
    lesson_type.max_students = 30
    lesson_type.save()
    conflicts = check_lesson_conflicts(
        {
            "lesson_type": lesson_type,
            "spot": spot,
            "date": timezone.localdate() + timedelta(days=2),
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "instructor": instructor,
            "assistant_instructors": [],
            "capacity": 30,
            "status": LessonStatus.SCHEDULED,
        }
    )
    assert any("ratio" in message.lower() for message in conflicts)


@pytest.mark.django_db
def test_lesson_in_the_past_is_a_conflict(lesson_type, spot, instructor):
    conflicts = check_lesson_conflicts(
        {
            "lesson_type": lesson_type,
            "spot": spot,
            "date": timezone.localdate() - timedelta(days=1),
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "instructor": instructor,
            "assistant_instructors": [],
            "capacity": 4,
            "status": LessonStatus.SCHEDULED,
        }
    )
    assert any("past" in message.lower() for message in conflicts)


@pytest.mark.django_db
def test_duration_mismatch_is_a_warning_not_a_conflict(lesson_type, spot, instructor):
    proposal = {
        "lesson_type": lesson_type,  # 120-minute product
        "spot": spot,
        "date": timezone.localdate() + timedelta(days=2),
        "start_time": time(10, 0),
        "end_time": time(11, 0),
        "instructor": instructor,
        "assistant_instructors": [],
        "capacity": 4,
        "status": LessonStatus.SCHEDULED,
    }
    assert check_lesson_conflicts(proposal) == []
    assert check_lesson_warnings(proposal)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_lesson_derives_the_end_time_and_capacity(lesson_type, spot, instructor):
    created = create_lesson(
        lesson_type=lesson_type,
        spot=spot,
        date=timezone.localdate() + timedelta(days=3),
        start_time=time(9, 0),
        instructor=instructor,
    )
    assert created.end_time == time(11, 0)
    assert created.capacity == suggest_capacity(lesson_type)
    assert created.lesson_code


@pytest.mark.django_db
def test_create_lesson_refuses_a_double_booking(lesson, lesson_type, spot, instructor):
    with pytest.raises(ValidationError):
        create_lesson(
            lesson_type=lesson_type,
            spot=spot,
            date=lesson.date,
            start_time=time(10, 30),
            end_time=time(12, 30),
            instructor=instructor,
        )


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_add_student_registers_a_seat(lesson, student):
    attendance = add_student_to_lesson(lesson, student)
    assert attendance.status == LessonAttendance.Status.REGISTERED
    assert lesson.booked_count == 1


@pytest.mark.django_db
def test_add_student_refuses_a_duplicate(lesson, student):
    add_student_to_lesson(lesson, student)
    with pytest.raises(ValidationError):
        add_student_to_lesson(lesson, student)


@pytest.mark.django_db
def test_add_student_refuses_to_overbook(lesson, student):
    lesson.capacity = 1
    lesson.save()
    add_student_to_lesson(lesson, student)
    with pytest.raises(ValidationError) as excinfo:
        add_student_to_lesson(lesson, make_student())
    assert "full" in " ".join(excinfo.value.messages).lower()


@pytest.mark.django_db
def test_add_student_refuses_a_level_outside_the_band(lesson):
    advanced = make_student(level=SurfLevel.ADVANCED)
    if getattr(advanced, "level", None) != SurfLevel.ADVANCED and getattr(
        advanced, "surf_level", None
    ) != SurfLevel.ADVANCED:
        pytest.skip("the students app does not expose a surf level attribute")
    with pytest.raises(ValidationError):
        add_student_to_lesson(lesson, advanced)


@pytest.mark.django_db
def test_add_student_refuses_to_breach_the_minors_ratio(lesson, lesson_type):
    """Six minors is the ceiling for one instructor; the seventh is refused."""
    lesson_type.max_students = 20
    lesson_type.save()
    lesson.capacity = 12
    lesson.save()

    minors = [make_student(age=12) for _ in range(7)]
    if not getattr(minors[0], "date_of_birth", None) and not getattr(
        minors[0], "birth_date", None
    ):
        pytest.skip("the students app does not expose a birth date")

    for child in minors[:6]:
        add_student_to_lesson(lesson, child)
    with pytest.raises(ValidationError) as excinfo:
        add_student_to_lesson(lesson, minors[6])
    assert "ratio" in " ".join(excinfo.value.messages).lower()


@pytest.mark.django_db
def test_remove_student_keeps_the_row_and_frees_the_seat(lesson, student):
    add_student_to_lesson(lesson, student)
    attendance = remove_student_from_lesson(lesson, student, reason="Changed their mind")
    assert attendance.status == LessonAttendance.Status.CANCELLED
    assert lesson.booked_count == 0
    assert LessonAttendance.objects.filter(lesson=lesson, student=student).count() == 1


@pytest.mark.django_db
def test_removed_student_can_be_added_back(lesson, student):
    add_student_to_lesson(lesson, student)
    remove_student_from_lesson(lesson, student)
    attendance = add_student_to_lesson(lesson, student)
    assert attendance.status == LessonAttendance.Status.REGISTERED
    assert LessonAttendance.objects.filter(lesson=lesson, student=student).count() == 1


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_check_in_requires_the_safety_briefing(lesson, student, manager_user):
    attendance = add_student_to_lesson(lesson, student)
    with pytest.raises(ValidationError) as excinfo:
        check_in_student(attendance, user=manager_user)
    assert "briefing" in " ".join(excinfo.value.messages).lower()


@pytest.mark.django_db
def test_check_in_succeeds_once_briefed_and_kitted(lesson, student, manager_user):
    lesson.lesson_type.requires_board = False
    lesson.lesson_type.requires_wetsuit = False
    lesson.lesson_type.save()
    attendance = add_student_to_lesson(lesson, student)
    mark_safety_check(lesson, manager_user)
    lesson.refresh_from_db()
    attendance.refresh_from_db()

    check_in_student(attendance, user=manager_user)
    attendance.refresh_from_db()
    lesson.refresh_from_db()
    assert attendance.status == LessonAttendance.Status.CHECKED_IN
    assert attendance.checked_in_at is not None
    assert lesson.status == LessonStatus.IN_PROGRESS


@pytest.mark.django_db
def test_safety_check_requires_a_named_person(lesson):
    with pytest.raises(ValidationError):
        mark_safety_check(lesson, None)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_complete_lesson_marks_attendance_and_bumps_the_student(past_lesson, student):
    add_student_to_lesson(past_lesson, student)
    before = getattr(student, "total_lessons", None)

    complete_lesson(past_lesson)

    past_lesson.refresh_from_db()
    attendance = LessonAttendance.objects.get(lesson=past_lesson, student=student)
    assert past_lesson.status == LessonStatus.COMPLETED
    assert attendance.status == LessonAttendance.Status.ATTENDED

    student.refresh_from_db()
    if before is not None:
        assert student.total_lessons == before + 1
    if hasattr(student, "last_lesson_date"):
        assert student.last_lesson_date == past_lesson.date


@pytest.mark.django_db
def test_complete_lesson_can_mark_unchecked_students_as_no_shows(past_lesson, student):
    add_student_to_lesson(past_lesson, student)
    complete_lesson(past_lesson, mark_unchecked_as_no_show=True)
    attendance = LessonAttendance.objects.get(lesson=past_lesson, student=student)
    assert attendance.status == LessonAttendance.Status.NO_SHOW


@pytest.mark.django_db
def test_a_future_lesson_cannot_be_completed(lesson):
    with pytest.raises(ValidationError):
        complete_lesson(lesson)


@pytest.mark.django_db
def test_no_show_can_be_recorded_directly(lesson, student):
    attendance = add_student_to_lesson(lesson, student)
    mark_no_show(attendance)
    attendance.refresh_from_db()
    assert attendance.status == LessonAttendance.Status.NO_SHOW
    assert lesson.booked_count == 0


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cancel_lesson_requires_a_reason(lesson):
    with pytest.raises(ValidationError):
        cancel_lesson(lesson, "   ")


@pytest.mark.django_db
def test_cancel_lesson_releases_every_seat(lesson, student, another_student, manager_user):
    add_student_to_lesson(lesson, student)
    add_student_to_lesson(lesson, another_student)

    cancel_lesson(lesson, "Red flag — beach closed", user=manager_user)

    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.CANCELLED
    assert lesson.cancelled_at is not None
    assert lesson.cancellation_reason == "Red flag — beach closed"
    assert lesson.booked_count == 0
    assert (
        LessonAttendance.objects.filter(
            lesson=lesson, status=LessonAttendance.Status.CANCELLED
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_a_completed_lesson_cannot_be_cancelled(past_lesson):
    complete_lesson(past_lesson)
    with pytest.raises(ValidationError):
        cancel_lesson(past_lesson, "Too late")


@pytest.mark.django_db
def test_a_cancelled_lesson_refuses_new_students(lesson, student):
    cancel_lesson(lesson, "Storm warning")
    lesson.refresh_from_db()
    with pytest.raises(ValidationError):
        add_student_to_lesson(lesson, student)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_lessons_for_calendar_returns_serialisable_events(lesson, student):
    add_student_to_lesson(lesson, student)
    events = lessons_for_calendar(lesson.date, lesson.date)
    assert len(events) == 1
    event = events[0]
    assert event["code"] == lesson.lesson_code
    assert event["booked"] == 1
    assert event["capacity"] == lesson.capacity
    assert event["start"] == "10:00"
    assert event["colour"] == lesson.lesson_type.colour


@pytest.mark.django_db
def test_lessons_for_calendar_filters_by_instructor(lesson, other_instructor):
    assert lessons_for_calendar(lesson.date, lesson.date, instructor=other_instructor) == []


@pytest.mark.django_db
def test_calendar_ignores_lessons_outside_the_window(lesson):
    assert (
        lessons_for_calendar(lesson.date + timedelta(days=5), lesson.date + timedelta(days=6))
        == []
    )


@pytest.mark.django_db
def test_soft_deleted_lessons_disappear_from_the_calendar(lesson):
    lesson.delete()
    assert lessons_for_calendar(lesson.date, lesson.date) == []
    assert Lesson.all_objects.filter(pk=lesson.pk).exists()
