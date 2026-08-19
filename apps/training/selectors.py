"""Read queries for the Training Center."""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from .models import TrainingCourse, TrainingLesson, TrainingProgress, TrainingStep


def active_courses() -> QuerySet[TrainingCourse]:
    """Every published course, with its step count annotated in one query."""
    return TrainingCourse.objects.filter(is_active=True).annotate(
        step_total=Count(
            "lessons__steps",
            filter=Q(lessons__is_deleted=False, lessons__steps__is_deleted=False),
            distinct=True,
        ),
        lesson_total=Count("lessons", filter=Q(lessons__is_deleted=False), distinct=True),
    )


def courses_for(user) -> QuerySet[TrainingCourse]:
    """Courses *user* is allowed to see.

    A course may name a ``required_capability``; if the user does not hold it,
    the course is hidden rather than shown and then refused — there is nothing
    to learn on a screen you cannot open.
    """
    queryset = active_courses()
    if user is None or not user.is_authenticated:
        return queryset.none()

    capabilities = user.get_capabilities()
    allowed = [
        course.pk
        for course in queryset
        if not course.required_capability or course.required_capability in capabilities
    ]
    return queryset.filter(pk__in=allowed)


def course_detail_queryset() -> QuerySet[TrainingCourse]:
    return active_courses().prefetch_related("lessons__steps")


def lessons_for(course: TrainingCourse) -> QuerySet[TrainingLesson]:
    return (
        TrainingLesson.objects.filter(course=course)
        .annotate(step_total=Count("steps", filter=Q(steps__is_deleted=False), distinct=True))
        .order_by("order")
    )


def steps_for(course: TrainingCourse) -> QuerySet[TrainingStep]:
    """Every step of a course in reading order."""
    return (
        TrainingStep.objects.filter(lesson__course=course)
        .select_related("lesson")
        .order_by("lesson__order", "order")
    )


def step_detail_queryset() -> QuerySet[TrainingStep]:
    return TrainingStep.objects.select_related("lesson", "lesson__course")


def progress_for(user, course: TrainingCourse) -> TrainingProgress | None:
    if user is None or not user.is_authenticated:
        return None
    return TrainingProgress.objects.filter(user=user, course=course).first()


def progress_map(user) -> dict[int, TrainingProgress]:
    """``{course_id: progress}`` for *user*, in one query."""
    if user is None or not user.is_authenticated:
        return {}
    return {
        record.course_id: record
        for record in TrainingProgress.objects.filter(user=user).select_related("course")
    }
