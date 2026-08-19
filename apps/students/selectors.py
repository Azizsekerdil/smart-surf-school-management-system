"""Read queries for the student screens.

Lesson history is owned by ``apps.lessons``. It is read through the app registry
so this module keeps working — showing an empty history — in a deployment where
the lessons module holds no rows for a student.
"""

from __future__ import annotations

from django.apps import apps as django_apps

from apps.customers.selectors import first_existing_field, model_has_field, optional_model

from .models import SkillAssessment, Student

LESSON_ROW_LIMIT = 25


def student_list(
    *,
    search: str = "",
    level: str = "",
    instructor: str = "",
    status: str = "",
    needs_assessment: bool = False,
):
    """The filtered student queryset behind the list screen and the API."""
    queryset = Student.objects.select_related("customer", "preferred_instructor")

    if search:
        queryset = queryset.search(search)
    if level:
        queryset = queryset.at_level(level)
    if instructor:
        queryset = queryset.filter(preferred_instructor_id=instructor)
    if status == "active":
        queryset = queryset.active()
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    if needs_assessment:
        queryset = queryset.filter(assessments__isnull=True)
    return queryset.distinct()


def assessments_for(student, limit: int | None = None):
    queryset = SkillAssessment.objects.filter(student=student).select_related("instructor")
    return list(queryset[:limit]) if limit else list(queryset)


def instructor_choices() -> list[tuple[int, str]]:
    """(pk, label) for every selectable instructor, or ``[]`` when unavailable."""
    Instructor = optional_model("instructors", "Instructor")
    if Instructor is None:
        return []
    manager = getattr(Instructor, "objects", None) or Instructor._default_manager
    queryset = manager.all()
    if model_has_field(Instructor, "is_active"):
        queryset = queryset.filter(is_active=True)
    return [(obj.pk, str(obj)) for obj in queryset[:500]]


def lesson_history(student, limit: int = LESSON_ROW_LIMIT) -> list:
    """Attendance rows for this student, most recent first.

    Prefers ``lessons.LessonAttendance`` (the per-student row) and falls back to
    ``lessons.Lesson`` when the module models attendance differently.
    """
    Attendance = optional_model("lessons", "LessonAttendance")
    if model_has_field(Attendance, "student"):
        order_field = first_existing_field(
            Attendance, ("lesson__start_at", "attended_on", "date", "created_at")
        )
        queryset = _manager(Attendance).filter(student=student)
        if model_has_field(Attendance, "lesson"):
            queryset = queryset.select_related("lesson")
        if order_field:
            queryset = queryset.order_by(f"-{order_field}")
        return list(queryset[:limit])

    Lesson = optional_model("lessons", "Lesson")
    if model_has_field(Lesson, "students"):
        order_field = first_existing_field(Lesson, ("start_at", "date", "created_at"))
        queryset = _manager(Lesson).filter(students=student)
        if order_field:
            queryset = queryset.order_by(f"-{order_field}")
        return list(queryset[:limit])
    return []


def _manager(model):
    return getattr(model, "objects", None) or model._default_manager


def upcoming_lessons(student, limit: int = 5) -> list:
    """Scheduled future lessons, so the desk can see what is already booked."""
    from django.utils import timezone

    Attendance = optional_model("lessons", "LessonAttendance")
    if not model_has_field(Attendance, "student"):
        return []
    if not model_has_field(Attendance, "lesson"):
        return []
    Lesson = optional_model("lessons", "Lesson")
    start_field = first_existing_field(Lesson, ("start_at", "date"))
    if not start_field:
        return []
    queryset = (
        _manager(Attendance)
        .filter(student=student, **{f"lesson__{start_field}__gte": timezone.now() if start_field == "start_at" else timezone.localdate()})
        .select_related("lesson")
        .order_by(f"lesson__{start_field}")
    )
    return list(queryset[:limit])


def student_restrictions(student) -> list:
    """Active safety restrictions, read lazily from the safety module."""
    try:
        Restriction = django_apps.get_model("safety", "StudentRestriction")
    except (LookupError, ValueError):
        return []
    if not model_has_field(Restriction, "student"):
        return []
    queryset = _manager(Restriction).filter(student=student)
    if model_has_field(Restriction, "is_active"):
        queryset = queryset.filter(is_active=True)
    return list(queryset[:10])
