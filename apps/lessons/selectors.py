"""Read queries for the lesson screens.

Kept apart from :mod:`apps.lessons.services` because these never change state.
Every queryset here is joined and annotated so no list template can trigger an
N+1: ``booked`` is computed in SQL, never by walking the roster in Python.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta

from django.db.models import Count, Q, QuerySet

from apps.core.enums import LessonStatus

from .models import Lesson, LessonAttendance, LessonType

#: Annotation shared by every list/calendar query.
BOOKED_ANNOTATION = Count(
    "attendances",
    filter=Q(
        attendances__is_deleted=False,
        attendances__status__in=LessonAttendance.SEAT_TAKING_STATUSES,
    ),
    distinct=True,
)


def lesson_queryset() -> QuerySet[Lesson]:
    """Lessons with everything the list and detail screens display."""
    return (
        Lesson.objects.select_related("lesson_type", "spot", "instructor", "safety_checked_by")
        .prefetch_related("assistant_instructors")
        .annotate(booked=BOOKED_ANNOTATION)
    )


def roster_queryset(lesson: Lesson) -> QuerySet[LessonAttendance]:
    """The roster of one lesson, ordered so cancelled seats sink to the bottom."""
    return (
        LessonAttendance.objects.filter(lesson=lesson)
        .select_related(
            "lesson",
            "lesson__lesson_type",
            "student",
            "booking",
            "assigned_board",
            "assigned_wetsuit",
        )
        .order_by("status", "pk")
    )


def lessons_on(day: date_cls, instructor=None, spot=None) -> QuerySet[Lesson]:
    """Every lesson on one calendar day, in timetable order."""
    queryset = lesson_queryset().filter(date=day).order_by("start_time", "spot__name")
    if instructor is not None:
        queryset = queryset.filter(
            Q(instructor=instructor) | Q(assistant_instructors=instructor)
        ).distinct()
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    return queryset


def upcoming_lessons(limit: int = 10, instructor=None) -> QuerySet[Lesson]:
    """The next lessons still due to run."""
    from django.utils import timezone

    today = timezone.localdate()
    queryset = lesson_queryset().filter(
        date__gte=today,
        status__in=[LessonStatus.SCHEDULED, LessonStatus.CONFIRMED, LessonStatus.IN_PROGRESS],
    )
    if instructor is not None:
        queryset = queryset.filter(instructor=instructor)
    return queryset.order_by("date", "start_time")[:limit]


def day_summary(day: date_cls) -> dict:
    """Headline numbers for the day view."""
    lessons = list(lessons_on(day))
    seats = sum(lesson.capacity for lesson in lessons)
    booked = sum(lesson.booked for lesson in lessons)
    return {
        "date": day,
        "previous_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
        "lesson_count": len(lessons),
        "seats": seats,
        "booked": booked,
        "free_seats": max(0, seats - booked),
        "cancelled": sum(1 for lesson in lessons if lesson.status == LessonStatus.CANCELLED),
        "unbriefed": sum(
            1
            for lesson in lessons
            if not lesson.safety_briefing_done and lesson.status in
            {LessonStatus.SCHEDULED, LessonStatus.CONFIRMED, LessonStatus.IN_PROGRESS}
        ),
    }


def active_lesson_types() -> QuerySet[LessonType]:
    return LessonType.objects.filter(is_active=True).order_by("sort_order", "name")
