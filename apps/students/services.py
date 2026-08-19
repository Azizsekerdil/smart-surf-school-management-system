"""Student business rules.

Two rules matter operationally and both live here rather than in a view:

* recording an assessment is the *only* way a student's level changes, and the
  change is audited against the instructor who made the call;
* lesson counters are advanced by the lessons module through
  :func:`register_lesson_completion`, so no screen has to recount attendance.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import diff_instances, record_audit
from apps.core.enums import SurfLevel, level_rank
from apps.customers import services as customer_services
from apps.customers.models import Customer

from .models import NON_SWIMMER_MAX_LEVEL, SKILL_FIELDS, SkillAssessment, Student

logger = logging.getLogger("apps.students")

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
@transaction.atomic
def create_student(customer: Customer, *, actor=None, request=None, **fields) -> Student:
    """Attach a student profile to an existing customer."""
    if Student.all_objects.filter(customer=customer).exists():
        raise ValidationError(
            _("%(name)s already has a student profile.") % {"name": customer.full_name}
        )

    student = Student(customer=customer, **fields)
    if actor is not None and getattr(actor, "is_authenticated", False):
        student.created_by = actor
        student.updated_by = actor
    student.full_clean(exclude=["student_code"])
    student.save()

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=student,
        user=actor,
        description=_("Student %(code)s created at level %(level)s")
        % {"code": student.student_code, "level": student.get_surf_level_display()},
    )
    return student


@transaction.atomic
def create_student_with_customer(
    *,
    first_name: str,
    last_name: str,
    actor=None,
    request=None,
    customer_fields: dict | None = None,
    allow_duplicate: bool = False,
    **student_fields,
) -> Student:
    """Register a brand-new person: customer record plus student profile.

    One transaction, so a rejected customer (duplicate contact details, missing
    guardian for a minor) never leaves a half-built student behind.
    """
    customer = customer_services.create_customer(
        first_name=first_name,
        last_name=last_name,
        actor=actor,
        request=request,
        allow_duplicate=allow_duplicate,
        **(customer_fields or {}),
    )
    return create_student(customer, actor=actor, request=request, **student_fields)


@transaction.atomic
def update_student(student: Student, *, actor=None, request=None, **fields) -> Student:
    before = Student.all_objects.get(pk=student.pk)
    for name, value in fields.items():
        setattr(student, name, value)
    if actor is not None and getattr(actor, "is_authenticated", False):
        student.updated_by = actor
    student.full_clean(exclude=["student_code"])
    student.save()

    changes = diff_instances(before, student)
    if changes:
        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=student,
            user=actor,
            changes=changes,
            description=_("Student %(code)s updated") % {"code": student.student_code},
        )
    return student


@transaction.atomic
def set_active(student: Student, active: bool, *, actor=None, request=None) -> Student:
    if student.is_active == active:
        return student
    student.is_active = active
    if actor is not None and getattr(actor, "is_authenticated", False):
        student.updated_by = actor
    student.save(update_fields=["is_active", "updated_by", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=student,
        user=actor,
        changes={"is_active": [not active, active]},
        description=(
            _("Student %(code)s reactivated") if active else _("Student %(code)s archived")
        )
        % {"code": student.student_code},
    )
    return student


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------
@transaction.atomic
def record_assessment(
    student: Student,
    *,
    paddling: int,
    popup: int,
    positioning: int,
    wave_reading: int,
    safety: int,
    instructor=None,
    assessed_on=None,
    level_after: str | None = None,
    notes: str = "",
    next_focus: str = "",
    actor=None,
    request=None,
) -> SkillAssessment:
    """Store an assessment and apply the level it concludes.

    ``level_before`` is always taken from the student as they stand now, so two
    assessments recorded out of order cannot corrupt the ladder. When
    ``level_after`` differs, the student's level is updated in the same
    transaction and the change is audited against the assessing instructor.
    """
    assessed_on = assessed_on or timezone.localdate()
    level_before = student.surf_level
    level_after = level_after or level_before

    # Promoting a student who cannot swim past the whitewater ceiling would let
    # the booking screen put them out of their depth. Refuse before writing.
    if (
        level_after != level_before
        and not student.can_swim
        and level_rank(level_after) > level_rank(NON_SWIMMER_MAX_LEVEL)
    ):
        raise ValidationError(
            {
                "level_after": _(
                    "Confirm this student can swim before moving them above “%(level)s”."
                )
                % {"level": SurfLevel(NON_SWIMMER_MAX_LEVEL).label}
            }
        )

    assessment = SkillAssessment(
        student=student,
        instructor=instructor,
        assessed_on=assessed_on,
        level_before=level_before,
        level_after=level_after,
        paddling=paddling,
        popup=popup,
        positioning=positioning,
        wave_reading=wave_reading,
        safety=safety,
        notes=notes,
        next_focus=next_focus,
    )
    if actor is not None and getattr(actor, "is_authenticated", False):
        assessment.created_by = actor
        assessment.updated_by = actor
    assessment.full_clean()
    assessment.save()

    updated_fields = ["updated_at"]
    if student.last_lesson_date is None or assessed_on > student.last_lesson_date:
        student.last_lesson_date = assessed_on
        updated_fields.append("last_lesson_date")

    if level_after != level_before:
        student.surf_level = level_after
        updated_fields.append("surf_level")

    if actor is not None and getattr(actor, "is_authenticated", False):
        student.updated_by = actor
        updated_fields.append("updated_by")
    student.save(update_fields=sorted(set(updated_fields)))

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=assessment,
        user=actor,
        description=_("Assessment recorded for %(code)s (average %(avg)s)")
        % {"code": student.student_code, "avg": assessment.average_score},
    )
    if level_after != level_before:
        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=student,
            user=actor,
            changes={"surf_level": [level_before, level_after]},
            description=_("Level changed from %(before)s to %(after)s by assessment")
            % {
                "before": SurfLevel(level_before).label,
                "after": SurfLevel(level_after).label,
            },
        )
        logger.info(
            "Student %s moved %s -> %s", student.student_code, level_before, level_after
        )
    return assessment


def suggested_level_after(student: Student, scores: dict[str, int]) -> str:
    """Suggest the level an assessment implies. Advisory only — a coach decides.

    The rule mirrors how coaches actually promote: every competency at 4 or
    better, and safety awareness never below 4, because a student who cannot
    read a rip does not move up regardless of how well they pop up.
    """
    values = [int(scores.get(name) or 0) for name in SKILL_FIELDS]
    if not values:
        return student.surf_level
    safety_score = int(scores.get("safety") or 0)
    order = [choice for choice, _label in SurfLevel.choices]
    index = order.index(student.surf_level) if student.surf_level in order else 0

    if min(values) >= 4 and safety_score >= 4 and index < len(order) - 1:
        return order[index + 1]
    if min(values) <= 1 and index > 0:
        return order[index - 1]
    return student.surf_level


def student_progress_series(student: Student) -> list[dict]:
    """Chronological series for the progress chart.

    One point per assessment: the five competencies, their average, and the
    level rank after the assessment so the chart can show the ladder alongside
    the scores.
    """
    series: list[dict] = []
    assessments = SkillAssessment.objects.filter(student=student).order_by(
        "assessed_on", "created_at"
    )
    for assessment in assessments:
        point = {
            "date": assessment.assessed_on.isoformat(),
            "average": assessment.average_score,
            "level": assessment.level_after,
            "level_label": str(SurfLevel(assessment.level_after).label),
            "level_rank": level_rank(assessment.level_after),
        }
        point.update({name: getattr(assessment, name) for name in SKILL_FIELDS})
        series.append(point)
    return series


# ---------------------------------------------------------------------------
# Counters — called by the lessons module
# ---------------------------------------------------------------------------
@transaction.atomic
def register_lesson_completion(
    student: Student,
    *,
    hours: Decimal | float | int = 0,
    on_date=None,
    count: int = 1,
    actor=None,
    request=None,
) -> Student:
    """Advance the student's lesson counters after a completed lesson.

    The lessons module calls this once per completed attendance. It also stamps
    the customer's visit dates, so the CRM screens stay accurate without
    scanning every module.
    """
    on_date = on_date or timezone.localdate()
    if hasattr(on_date, "date"):
        on_date = on_date.date()

    student.total_lessons = max((student.total_lessons or 0) + count, 0)
    student.total_hours = (student.total_hours or ZERO) + Decimal(str(hours or 0))
    if student.last_lesson_date is None or on_date > student.last_lesson_date:
        student.last_lesson_date = on_date
    student.save(
        update_fields=["total_lessons", "total_hours", "last_lesson_date", "updated_at"]
    )

    customer_services.register_visit(student.customer, on_date)
    return student


def recount_lessons(student: Student) -> Student:
    """Rebuild the counters from the lessons module, when one is installed.

    Used after a data correction. Falls back to leaving the counters untouched
    if the lessons module does not expose per-student attendance.
    """
    from .selectors import lesson_history

    rows = lesson_history(student, limit=10_000)
    if not rows:
        return student

    total_hours = ZERO
    last_date = None
    for row in rows:
        duration = getattr(row, "duration_hours", None)
        if duration is None:
            lesson = getattr(row, "lesson", None)
            duration = getattr(lesson, "duration_hours", None)
        if duration:
            total_hours += Decimal(str(duration))
        for attribute in ("attended_on", "date"):
            value = getattr(row, attribute, None)
            if value:
                value = value.date() if hasattr(value, "date") else value
                last_date = value if last_date is None else max(last_date, value)
                break

    student.total_lessons = len(rows)
    student.total_hours = total_hours
    if last_date:
        student.last_lesson_date = last_date
    student.save(
        update_fields=["total_lessons", "total_hours", "last_lesson_date", "updated_at"]
    )
    return student
