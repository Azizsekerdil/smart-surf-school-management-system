"""Business rules for scheduling, staffing and running lessons.

Everything that can refuse an operation lives here: double-booked instructors,
spot capacity, safety ratios, level and age gates, equipment already out, and
the state machine around cancellation and completion. Views orchestrate; this
module decides.

Integration seams with apps that land separately are all *lazy* (imported
inside the function that needs them) and degrade to lesson-local checks if the
other module is not present yet, so a partially built system still refuses the
unsafe operations it can see.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date as date_cls
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import UNAVAILABLE_EQUIPMENT_STATUSES, EquipmentStatus, LessonStatus

from .models import (
    CLOSED_LESSON_STATUSES,
    LIVE_LESSON_STATUSES,
    Lesson,
    LessonAttendance,
    LessonType,
    ratio_limit,
    student_is_minor,
    student_level,
)

logger = logging.getLogger("apps.lessons")

#: Attributes a SurfSpot may use to express how many people may be in the water.
_SPOT_CAPACITY_ATTRS = ("max_lesson_capacity", "max_capacity", "capacity", "max_students")

#: Attributes the students app may use for the "lessons taken" counter.
_STUDENT_LESSON_COUNTERS = ("total_lessons", "lessons_completed", "lesson_count")
#: Attributes the students app may use for accumulated water time, in hours.
_STUDENT_HOUR_COUNTERS = ("total_hours", "hours_in_water", "total_lesson_hours")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _overlaps(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    """True when two same-day time windows intersect (touching is fine)."""
    return start_a < end_b and end_a > start_b


def _value(source: Any, key: str, default=None):
    """Read *key* from a dict or an object."""
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _spot_capacity(spot) -> int | None:
    for attribute in _SPOT_CAPACITY_ATTRS:
        value = getattr(spot, attribute, None)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _instructor_label(instructor) -> str:
    for attribute in ("get_display_name", "full_name", "display_name", "name"):
        value = getattr(instructor, attribute, None)
        if callable(value):
            try:
                return str(value())
            except TypeError:
                continue
        if value:
            return str(value)
    return str(instructor)


def _external_instructor_availability(
    instructor, on_date: date_cls, start_time: time, end_time: time
) -> tuple[bool, str]:
    """Ask the instructors module whether *instructor* may work this window.

    Returns ``(available, reason)``. When the instructors module is not
    installed, or exposes a signature we do not recognise, the answer is
    "available" and the lesson-local conflict checks below still apply.
    """
    try:
        from apps.instructors.services import is_instructor_available
    except Exception:  # noqa: BLE001 - the module may not have landed yet
        return True, ""

    attempts = (
        lambda: is_instructor_available(instructor, on_date, start_time, end_time),
        lambda: is_instructor_available(
            instructor=instructor, date=on_date, start_time=start_time, end_time=end_time
        ),
        lambda: is_instructor_available(instructor, on_date),
    )
    for attempt in attempts:
        try:
            result = attempt()
        except TypeError:
            continue
        except Exception:  # noqa: BLE001 - never let an integration break scheduling
            logger.exception("is_instructor_available raised; treating as available")
            return True, ""
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), str(result[1] or "")
        return bool(result), ""
    return True, ""


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
def check_lesson_conflicts(lesson_or_kwargs, exclude_pk: int | None = None) -> list[str]:
    """Return every human-readable reason this lesson cannot run as described.

    Accepts either a :class:`~apps.lessons.models.Lesson` instance (saved or
    not) or a mapping of the same field names, so the create form can ask for
    conflicts while the user is still typing.
    """
    source = lesson_or_kwargs
    conflicts: list[str] = []

    lesson_type: LessonType | None = _value(source, "lesson_type")
    spot = _value(source, "spot")
    on_date: date_cls | None = _value(source, "date")
    start_time: time | None = _value(source, "start_time")
    end_time: time | None = _value(source, "end_time")
    instructor = _value(source, "instructor")
    capacity = _value(source, "capacity") or 0
    status = _value(source, "status") or LessonStatus.SCHEDULED

    assistants = _value(source, "assistant_instructors") or []
    if hasattr(assistants, "all"):
        assistants = list(assistants.all()) if getattr(source, "pk", None) else []
    assistants = [a for a in assistants if a is not None]

    if exclude_pk is None:
        exclude_pk = getattr(source, "pk", None)

    # --- basic shape ----------------------------------------------------
    if not (on_date and start_time and end_time):
        return conflicts  # not enough information yet; the form reports missing fields
    if end_time <= start_time:
        conflicts.append(_("The end time must be later than the start time."))
        return conflicts

    if lesson_type is not None and not lesson_type.is_active:
        conflicts.append(
            _("Lesson type “%(name)s” is archived and cannot be scheduled.")
            % {"name": lesson_type.name}
        )

    if spot is not None and getattr(spot, "is_active", True) is False:
        conflicts.append(_("Surf spot “%(name)s” is not active.") % {"name": spot})

    if status in LIVE_LESSON_STATUSES and on_date < timezone.localdate():
        conflicts.append(_("This lesson is scheduled in the past."))

    # --- capacity vs. product ------------------------------------------
    if lesson_type is not None and capacity:
        if capacity > lesson_type.max_students:
            conflicts.append(
                _("%(type)s allows at most %(max)s students; %(asked)s requested.")
                % {
                    "type": lesson_type.name,
                    "max": lesson_type.max_students,
                    "asked": capacity,
                }
            )
        instructor_count = 1 + len(assistants)
        ceiling = ratio_limit(lesson_type.max_level, instructor_count=instructor_count)
        if capacity > ceiling:
            conflicts.append(
                _(
                    "Safety ratio: %(instructors)s instructor(s) may supervise at most "
                    "%(max)s students at %(level)s level, not %(asked)s."
                )
                % {
                    "instructors": instructor_count,
                    "max": ceiling,
                    "level": lesson_type.get_max_level_display(),
                    "asked": capacity,
                }
            )
    # --- staffing -------------------------------------------------------
    overlapping = Lesson.objects.filter(date=on_date, status__in=LIVE_LESSON_STATUSES)
    if exclude_pk:
        overlapping = overlapping.exclude(pk=exclude_pk)
    overlapping = list(
        overlapping.select_related("spot", "lesson_type").prefetch_related("assistant_instructors")
    )

    seen: set[int] = set()
    for member in [instructor, *assistants]:
        if member is None or getattr(member, "pk", None) is None:
            continue
        if member.pk in seen:
            conflicts.append(
                _("%(name)s is listed twice on this lesson.")
                % {"name": _instructor_label(member)}
            )
            continue
        seen.add(member.pk)

        for other in overlapping:
            if not _overlaps(start_time, end_time, other.start_time, other.end_time):
                continue
            others_staff = {other.instructor_id} | {
                a.pk for a in other.assistant_instructors.all()
            }
            if member.pk in others_staff:
                conflicts.append(
                    _("%(name)s already teaches %(code)s at %(time)s.")
                    % {
                        "name": _instructor_label(member),
                        "code": other.lesson_code,
                        "time": other.time_label,
                    }
                )

        available, reason = _external_instructor_availability(
            member, on_date, start_time, end_time
        )
        if not available:
            conflicts.append(
                _("%(name)s is not available then%(reason)s.")
                % {
                    "name": _instructor_label(member),
                    "reason": f" ({reason})" if reason else "",
                }
            )

    # --- spot -----------------------------------------------------------
    if spot is not None:
        limit = _spot_capacity(spot)
        if limit:
            in_water = int(capacity or 0)
            for other in overlapping:
                if other.spot_id == getattr(spot, "pk", None) and _overlaps(
                    start_time, end_time, other.start_time, other.end_time
                ):
                    in_water += int(other.capacity)
            if in_water > limit:
                conflicts.append(
                    _(
                        "%(spot)s holds %(limit)s people in the water; this slot would "
                        "have %(total)s."
                    )
                    % {"spot": spot, "limit": limit, "total": in_water}
                )

    return conflicts


def check_lesson_warnings(lesson_or_kwargs, exclude_pk: int | None = None) -> list[str]:
    """Return advisory notes that do not block scheduling.

    Kept separate from :func:`check_lesson_conflicts` on purpose: a conflict
    refuses the save, a warning is the operations desk's judgement call.
    """
    source = lesson_or_kwargs
    warnings: list[str] = []

    lesson_type: LessonType | None = _value(source, "lesson_type")
    on_date: date_cls | None = _value(source, "date")
    start_time: time | None = _value(source, "start_time")
    end_time: time | None = _value(source, "end_time")
    instructor = _value(source, "instructor")
    capacity = int(_value(source, "capacity") or 0)

    if exclude_pk is None:
        exclude_pk = getattr(source, "pk", None)
    if not (on_date and start_time and end_time and end_time > start_time):
        return warnings

    planned = int(
        (
            datetime.combine(date_cls.min, end_time) - datetime.combine(date_cls.min, start_time)
        ).total_seconds()
        // 60
    )

    if lesson_type is not None:
        if lesson_type.duration_minutes and planned != int(lesson_type.duration_minutes):
            warnings.append(
                _("Slot is %(planned)s minutes; %(type)s normally runs %(expected)s minutes.")
                % {
                    "planned": planned,
                    "type": lesson_type.name,
                    "expected": lesson_type.duration_minutes,
                }
            )
        if capacity and capacity < lesson_type.min_students:
            warnings.append(
                _("Capacity %(capacity)s is below the %(min)s students this lesson needs to run.")
                % {"capacity": capacity, "min": lesson_type.min_students}
            )
        if lesson_type.max_age is not None and lesson_type.max_age < 18 and capacity:
            minors_ceiling = ratio_limit(lesson_type.max_level, has_minors=True)
            if capacity > minors_ceiling:
                warnings.append(
                    _("This is a minors' lesson: consider a second instructor above %(max)s students.")
                    % {"max": minors_ceiling}
                )

    if instructor is not None and getattr(instructor, "pk", None):
        same_day = Lesson.objects.filter(
            date=on_date, instructor=instructor, status__in=LIVE_LESSON_STATUSES
        )
        if exclude_pk:
            same_day = same_day.exclude(pk=exclude_pk)
        for other in same_day:
            gap_before = (
                datetime.combine(date_cls.min, start_time)
                - datetime.combine(date_cls.min, other.end_time)
            ).total_seconds() / 60
            gap_after = (
                datetime.combine(date_cls.min, other.start_time)
                - datetime.combine(date_cls.min, end_time)
            ).total_seconds() / 60
            gap = gap_before if gap_before >= 0 else gap_after
            if 0 <= gap < 30:
                warnings.append(
                    _("Only %(gap)s minutes between this lesson and %(code)s for %(name)s.")
                    % {
                        "gap": int(gap),
                        "code": other.lesson_code,
                        "name": _instructor_label(instructor),
                    }
                )
    return warnings


# ---------------------------------------------------------------------------
# Creation & scheduling
# ---------------------------------------------------------------------------
@transaction.atomic
def create_lesson(
    *,
    lesson_type: LessonType,
    spot,
    date: date_cls,
    start_time: time,
    end_time: time | None = None,
    instructor,
    assistant_instructors: Iterable | None = None,
    capacity: int | None = None,
    status: str = LessonStatus.SCHEDULED,
    price_override: Decimal | None = None,
    notes: str = "",
    internal_notes: str = "",
    user=None,
    request=None,
) -> Lesson:
    """Create a lesson after proving it can safely run.

    Raises :class:`django.core.exceptions.ValidationError` listing every
    conflict found — instructor already teaching, instructor unavailable, spot
    over capacity, group larger than the safety ratio allows.
    """
    assistants = list(assistant_instructors or [])

    if end_time is None:
        end = datetime.combine(date, start_time) + lesson_type.duration
        end_time = end.time()

    if capacity is None:
        capacity = suggest_capacity(
            lesson_type, instructor_count=1 + len(assistants), has_minors=False
        )

    proposal = {
        "lesson_type": lesson_type,
        "spot": spot,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "instructor": instructor,
        "assistant_instructors": assistants,
        "capacity": capacity,
        "status": status,
    }
    conflicts = check_lesson_conflicts(proposal)
    if conflicts:
        raise ValidationError(conflicts)

    lesson = Lesson(
        lesson_type=lesson_type,
        spot=spot,
        date=date,
        start_time=start_time,
        end_time=end_time,
        instructor=instructor,
        capacity=capacity,
        status=status,
        price_override=price_override,
        notes=notes,
        internal_notes=internal_notes,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        updated_by=user if getattr(user, "is_authenticated", False) else None,
    )
    lesson.full_clean(exclude=["lesson_code"])
    lesson.save()
    if assistants:
        lesson.assistant_instructors.set(assistants)

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=lesson,
        user=user,
        description=_("Lesson %(code)s scheduled for %(date)s %(time)s")
        % {"code": lesson.lesson_code, "date": lesson.date, "time": lesson.time_label},
    )
    return lesson


def suggest_capacity(
    lesson_type: LessonType, instructor_count: int = 1, has_minors: bool = False
) -> int:
    """The largest group this product may run with the given staffing.

    Never exceeds the product's own ``max_students``, and never exceeds the
    safety ratio for its hardest level (tightened further when minors attend).
    """
    ceiling = ratio_limit(
        lesson_type.max_level, instructor_count=instructor_count, has_minors=has_minors
    )
    return max(1, min(int(lesson_type.max_students), ceiling))


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------
@transaction.atomic
def add_student_to_lesson(
    lesson: Lesson, student, booking=None, *, user=None, request=None
) -> LessonAttendance:
    """Put *student* on *lesson*, refusing every unsafe or impossible case."""
    if lesson.status in CLOSED_LESSON_STATUSES:
        raise ValidationError(
            _("Lesson %(code)s is %(status)s; its roster is closed.")
            % {"code": lesson.lesson_code, "status": lesson.get_status_display().lower()}
        )

    existing = (
        LessonAttendance.all_objects.filter(lesson=lesson, student=student)
        .order_by("pk")
        .first()
    )
    if existing is not None and not existing.is_deleted and existing.takes_seat:
        raise ValidationError(
            _("%(student)s is already on this lesson.") % {"student": student}
        )

    # --- capacity & ratio ----------------------------------------------
    booked = lesson.booked_count
    if booked >= lesson.capacity:
        raise ValidationError(
            _("Lesson %(code)s is full (%(booked)s of %(capacity)s seats taken).")
            % {"code": lesson.lesson_code, "booked": booked, "capacity": lesson.capacity}
        )

    minor = student_is_minor(student, lesson.date)
    ceiling = ratio_limit(
        lesson.lesson_type.max_level,
        instructor_count=lesson.instructor_count,
        has_minors=minor or lesson.has_minors,
    )
    if booked + 1 > ceiling:
        raise ValidationError(
            _(
                "Safety ratio would be breached: %(instructors)s instructor(s) may "
                "supervise at most %(max)s students%(minors)s."
            )
            % {
                "instructors": lesson.instructor_count,
                "max": ceiling,
                "minors": _(" when minors attend") if (minor or lesson.has_minors) else "",
            }
        )

    # --- level & age ----------------------------------------------------
    level = student_level(student)
    if not lesson.lesson_type.accepts_level(level):
        raise ValidationError(
            _("%(type)s is taught from %(min)s to %(max)s; %(student)s is %(level)s.")
            % {
                "type": lesson.lesson_type.name,
                "min": lesson.lesson_type.get_min_level_display(),
                "max": lesson.lesson_type.get_max_level_display(),
                "student": student,
                "level": level,
            }
        )

    age = _student_age(student, lesson.date)
    if not lesson.lesson_type.accepts_age(age):
        raise ValidationError(
            _("%(student)s is %(age)s; %(type)s accepts ages %(min)s–%(max)s.")
            % {
                "student": student,
                "age": age,
                "type": lesson.lesson_type.name,
                "min": lesson.lesson_type.min_age if lesson.lesson_type.min_age else "—",
                "max": lesson.lesson_type.max_age if lesson.lesson_type.max_age else "—",
            }
        )

    _check_student_restrictions(student, lesson)

    # --- write ----------------------------------------------------------
    if existing is not None:
        existing.is_deleted = False
        existing.deleted_at = None
        existing.status = LessonAttendance.Status.REGISTERED
        existing.checked_in_at = None
        if booking is not None:
            existing.booking = booking
        if getattr(user, "is_authenticated", False):
            existing.updated_by = user
        existing.save()
        attendance = existing
    else:
        attendance = LessonAttendance(
            lesson=lesson,
            student=student,
            booking=booking,
            status=LessonAttendance.Status.REGISTERED,
            created_by=user if getattr(user, "is_authenticated", False) else None,
            updated_by=user if getattr(user, "is_authenticated", False) else None,
        )
        attendance.full_clean(exclude=["lesson", "student", "booking"])
        attendance.save()

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=lesson,
        user=user,
        description=_("%(student)s added to lesson %(code)s")
        % {"student": student, "code": lesson.lesson_code},
    )
    return attendance


@transaction.atomic
def remove_student_from_lesson(
    lesson: Lesson, student, *, reason: str = "", user=None, request=None
) -> LessonAttendance:
    """Take *student* off *lesson*.

    The row is kept and marked cancelled rather than deleted: the seat history
    matters for refunds, for no-show statistics and for the unique constraint
    that stops the same student being added twice.
    """
    attendance = LessonAttendance.objects.filter(lesson=lesson, student=student).first()
    if attendance is None:
        raise ValidationError(
            _("%(student)s is not on lesson %(code)s.")
            % {"student": student, "code": lesson.lesson_code}
        )
    if attendance.status == LessonAttendance.Status.ATTENDED:
        raise ValidationError(
            _("%(student)s has already been marked as attended and cannot be removed.")
            % {"student": student}
        )
    if lesson.status == LessonStatus.COMPLETED:
        raise ValidationError(
            _("Lesson %(code)s is completed; its roster is closed.")
            % {"code": lesson.lesson_code}
        )

    release_equipment_from_attendance(attendance, user=user)
    attendance.status = LessonAttendance.Status.CANCELLED
    attendance.checked_in_at = None
    if reason:
        attendance.instructor_notes = (
            f"{attendance.instructor_notes}\n{reason}".strip()
            if attendance.instructor_notes
            else reason
        )
    if getattr(user, "is_authenticated", False):
        attendance.updated_by = user
    attendance.save()

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=lesson,
        user=user,
        description=_("%(student)s removed from lesson %(code)s")
        % {"student": student, "code": lesson.lesson_code},
    )
    return attendance


@transaction.atomic
def check_in_student(attendance: LessonAttendance, *, user=None, request=None) -> LessonAttendance:
    """Mark a student present at the beach.

    Refuses if the lesson is cancelled, if the safety briefing has not been
    signed off by a named staff member, or if the kit the lesson type requires
    has not been handed out — those are the two checks that actually protect
    the student, and skipping them under morning pressure is exactly what an
    operations system exists to prevent.
    """
    lesson = attendance.lesson
    if lesson.status == LessonStatus.CANCELLED:
        raise ValidationError(_("This lesson was cancelled."))
    if attendance.status == LessonAttendance.Status.CANCELLED:
        raise ValidationError(_("This student was removed from the lesson."))
    if attendance.status in {
        LessonAttendance.Status.CHECKED_IN,
        LessonAttendance.Status.ATTENDED,
    }:
        return attendance
    if not lesson.safety_briefing_done:
        raise ValidationError(
            _("Record the safety briefing before checking students in.")
        )
    if not attendance.equipment_ready:
        raise ValidationError(
            _("Assign the required equipment to %(student)s before check-in.")
            % {"student": attendance.student}
        )

    attendance.status = LessonAttendance.Status.CHECKED_IN
    attendance.checked_in_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        attendance.updated_by = user
    attendance.save(update_fields=["status", "checked_in_at", "updated_by", "updated_at"])

    if lesson.status in {LessonStatus.SCHEDULED, LessonStatus.CONFIRMED}:
        lesson.status = LessonStatus.IN_PROGRESS
        lesson.save(update_fields=["status", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=lesson,
        user=user,
        description=_("%(student)s checked in for %(code)s")
        % {"student": attendance.student, "code": lesson.lesson_code},
    )
    return attendance


@transaction.atomic
def mark_no_show(attendance: LessonAttendance, *, user=None, request=None) -> LessonAttendance:
    """Record that a registered student never turned up."""
    if attendance.status == LessonAttendance.Status.ATTENDED:
        raise ValidationError(_("This student has already been marked as attended."))
    release_equipment_from_attendance(attendance, user=user)
    attendance.status = LessonAttendance.Status.NO_SHOW
    if getattr(user, "is_authenticated", False):
        attendance.updated_by = user
    attendance.save()
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=attendance.lesson,
        user=user,
        description=_("%(student)s marked as a no-show for %(code)s")
        % {"student": attendance.student, "code": attendance.lesson.lesson_code},
    )
    return attendance


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@transaction.atomic
def complete_lesson(
    lesson: Lesson,
    *,
    mark_unchecked_as_no_show: bool = False,
    user=None,
    request=None,
) -> Lesson:
    """Close a lesson out: attendance, student progress and equipment, in one go."""
    if lesson.status == LessonStatus.CANCELLED:
        raise ValidationError(_("A cancelled lesson cannot be completed."))
    if lesson.status == LessonStatus.COMPLETED:
        return lesson
    now = timezone.localtime()
    if lesson.date > now.date():
        raise ValidationError(_("A lesson in the future cannot be completed."))

    attendances = list(
        lesson.attendances.filter(
            is_deleted=False,
            status__in=[
                LessonAttendance.Status.REGISTERED,
                LessonAttendance.Status.CHECKED_IN,
            ],
        ).select_related("student")
    )

    attended = 0
    for attendance in attendances:
        if (
            mark_unchecked_as_no_show
            and attendance.status == LessonAttendance.Status.REGISTERED
        ):
            attendance.status = LessonAttendance.Status.NO_SHOW
        else:
            attendance.status = LessonAttendance.Status.ATTENDED
            attended += 1
            _bump_student_progress(attendance.student, lesson)
        if getattr(user, "is_authenticated", False):
            attendance.updated_by = user
        attendance.save()
        release_equipment_from_attendance(attendance, user=user)

    lesson.status = LessonStatus.COMPLETED
    if getattr(user, "is_authenticated", False):
        lesson.updated_by = user
    lesson.save(update_fields=["status", "updated_by", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=lesson,
        user=user,
        description=_("Lesson %(code)s completed with %(count)s attending student(s)")
        % {"code": lesson.lesson_code, "count": attended},
    )
    return lesson


@transaction.atomic
def cancel_lesson(lesson: Lesson, reason: str, user=None, *, request=None) -> Lesson:
    """Cancel a lesson, its roster and any equipment held for it."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("A cancellation reason is required."))
    if lesson.status == LessonStatus.COMPLETED:
        raise ValidationError(_("A completed lesson cannot be cancelled."))
    if lesson.status == LessonStatus.CANCELLED:
        return lesson

    attendances = list(
        lesson.attendances.filter(
            is_deleted=False, status__in=LessonAttendance.SEAT_TAKING_STATUSES
        ).select_related("student", "booking")
    )
    affected_bookings = []
    for attendance in attendances:
        release_equipment_from_attendance(attendance, user=user)
        attendance.status = LessonAttendance.Status.CANCELLED
        attendance.checked_in_at = None
        if getattr(user, "is_authenticated", False):
            attendance.updated_by = user
        attendance.save()
        if attendance.booking_id and attendance.booking not in affected_bookings:
            affected_bookings.append(attendance.booking)

    lesson.status = LessonStatus.CANCELLED
    lesson.cancellation_reason = reason
    lesson.cancelled_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        lesson.updated_by = user
    lesson.save(
        update_fields=[
            "status",
            "cancellation_reason",
            "cancelled_at",
            "updated_by",
            "updated_at",
        ]
    )

    _flag_bookings_for_cancellation(affected_bookings, lesson, reason, user=user, request=request)

    record_audit(
        request,
        action=AuditAction.BOOKING_CANCEL,
        instance=lesson,
        user=user,
        description=_("Lesson %(code)s cancelled: %(reason)s")
        % {"code": lesson.lesson_code, "reason": reason},
        changes={"status": [LessonStatus.SCHEDULED, LessonStatus.CANCELLED]},
    )
    return lesson


@transaction.atomic
def mark_safety_check(lesson: Lesson, user, *, request=None) -> Lesson:
    """Record that a named staff member delivered the safety briefing.

    A safety sign-off is always attributable to a person — never to the system
    and never to the AI.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise ValidationError(_("A signed-in staff member must confirm the safety briefing."))
    if lesson.status == LessonStatus.CANCELLED:
        raise ValidationError(_("This lesson was cancelled."))

    lesson.safety_briefing_done = True
    lesson.safety_checked_by = user
    lesson.safety_checked_at = timezone.now()
    lesson.updated_by = user
    lesson.save(
        update_fields=[
            "safety_briefing_done",
            "safety_checked_by",
            "safety_checked_at",
            "updated_by",
            "updated_at",
        ]
    )
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=lesson,
        user=user,
        description=_("Safety briefing confirmed for %(code)s") % {"code": lesson.lesson_code},
    )
    return lesson


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------
def _equipment_model():
    try:
        return django_apps.get_model("equipment", "Equipment")
    except LookupError:
        return None


def _equipment_conflict(item, attendance: LessonAttendance) -> str | None:
    """Return a reason *item* cannot be handed to this attendance, or None."""
    if item is None:
        return None

    status = getattr(item, "status", None)
    if status in UNAVAILABLE_EQUIPMENT_STATUSES:
        return _("%(item)s is %(status)s and cannot be issued.") % {
            "item": item,
            "status": getattr(item, "get_status_display", lambda: status)(),
        }

    lesson = attendance.lesson
    clash = (
        LessonAttendance.objects.filter(
            lesson__date=lesson.date,
            lesson__status__in=LIVE_LESSON_STATUSES,
            status__in=LessonAttendance.SEAT_TAKING_STATUSES,
        )
        .filter(Q(assigned_board=item) | Q(assigned_wetsuit=item))
        .exclude(pk=attendance.pk)
        .select_related("lesson", "student")
    )
    for other in clash:
        if _overlaps(
            lesson.start_time, lesson.end_time, other.lesson.start_time, other.lesson.end_time
        ):
            return _("%(item)s is already out with %(student)s on %(code)s.") % {
                "item": item,
                "student": other.student,
                "code": other.lesson.lesson_code,
            }
    return None


def _set_equipment_status(item, status: str) -> None:
    """Move a piece of equipment to *status*, if the equipment app is present."""
    if item is None or not hasattr(item, "status"):
        return
    if item.status == status:
        return
    item.status = status
    try:
        item.save(update_fields=["status", "updated_at"])
    except Exception:  # noqa: BLE001 - field set differs; fall back to a full save
        item.save()


@transaction.atomic
def assign_equipment_to_attendance(
    attendance: LessonAttendance, board=None, wetsuit=None, *, user=None, request=None
) -> LessonAttendance:
    """Hand a board and/or a wetsuit to a student for this lesson.

    Refuses kit that is in maintenance, damaged, lost or retired, and kit that
    is already out on an overlapping lesson.
    """
    if attendance.lesson.status == LessonStatus.CANCELLED:
        raise ValidationError(_("This lesson was cancelled."))
    if attendance.status == LessonAttendance.Status.CANCELLED:
        raise ValidationError(_("This student was removed from the lesson."))

    errors = [
        message
        for message in (
            _equipment_conflict(board, attendance),
            _equipment_conflict(wetsuit, attendance),
        )
        if message
    ]
    if errors:
        raise ValidationError(errors)

    previous_board, previous_wetsuit = attendance.assigned_board, attendance.assigned_wetsuit

    if board is not None and board != previous_board:
        _set_equipment_status(previous_board, EquipmentStatus.AVAILABLE)
        attendance.assigned_board = board
        _set_equipment_status(board, EquipmentStatus.IN_LESSON)
    if wetsuit is not None and wetsuit != previous_wetsuit:
        _set_equipment_status(previous_wetsuit, EquipmentStatus.AVAILABLE)
        attendance.assigned_wetsuit = wetsuit
        _set_equipment_status(wetsuit, EquipmentStatus.IN_LESSON)

    if getattr(user, "is_authenticated", False):
        attendance.updated_by = user
    attendance.save()

    record_audit(
        request,
        action=AuditAction.RENTAL_OUT,
        instance=attendance.lesson,
        user=user,
        description=_("Equipment issued to %(student)s for %(code)s")
        % {"student": attendance.student, "code": attendance.lesson.lesson_code},
        changes={
            "assigned_board": [previous_board, attendance.assigned_board],
            "assigned_wetsuit": [previous_wetsuit, attendance.assigned_wetsuit],
        },
    )
    return attendance


def release_equipment_from_attendance(
    attendance: LessonAttendance, *, user=None, request=None
) -> LessonAttendance:
    """Return the board and wetsuit to the pool when a seat ends."""
    board, wetsuit = attendance.assigned_board, attendance.assigned_wetsuit
    if board is None and wetsuit is None:
        return attendance
    for item in (board, wetsuit):
        if item is not None and getattr(item, "status", None) == EquipmentStatus.IN_LESSON:
            _set_equipment_status(item, EquipmentStatus.AVAILABLE)
    attendance.assigned_board = None
    attendance.assigned_wetsuit = None
    if getattr(user, "is_authenticated", False):
        attendance.updated_by = user
    attendance.save(
        update_fields=["assigned_board", "assigned_wetsuit", "updated_by", "updated_at"]
    )
    return attendance


def available_equipment(lesson: Lesson):
    """Equipment that is free for the whole of *lesson*'s time window.

    Returns an empty list when the equipment module is not installed, so the
    roster screen still renders.
    """
    Equipment = _equipment_model()
    if Equipment is None:
        return []
    queryset = Equipment.objects.exclude(status__in=UNAVAILABLE_EQUIPMENT_STATUSES)
    busy = (
        LessonAttendance.objects.filter(
            lesson__date=lesson.date,
            lesson__status__in=LIVE_LESSON_STATUSES,
            status__in=LessonAttendance.SEAT_TAKING_STATUSES,
            lesson__start_time__lt=lesson.end_time,
            lesson__end_time__gt=lesson.start_time,
        )
        .values_list("assigned_board_id", "assigned_wetsuit_id")
    )
    taken = {pk for pair in busy for pk in pair if pk}
    if taken:
        queryset = queryset.exclude(pk__in=taken)
    return queryset.order_by("pk")


# ---------------------------------------------------------------------------
# Conditions snapshot
# ---------------------------------------------------------------------------
#: Fields copied from a SurfCondition reading into the frozen snapshot.
_SNAPSHOT_FIELDS = (
    "wave_height_m",
    "swell_height_m",
    "swell_period_s",
    "swell_direction_deg",
    "wind_speed_kmh",
    "wind_direction_deg",
    "wind_type",
    "tide_state",
    "tide_height_m",
    "water_temperature_c",
    "air_temperature_c",
    "beach_flag",
    "surf_score",
)


def capture_conditions_snapshot(lesson: Lesson, *, user=None, request=None) -> dict:
    """Freeze the surf conditions for this lesson.

    The snapshot is legal evidence if an incident is investigated later, so it
    is written once and never recomputed. Returns the stored dictionary; an
    empty dictionary means no reading was available for the spot.
    """
    try:
        SurfCondition = django_apps.get_model("surf_conditions", "SurfCondition")
    except LookupError:
        return {}

    reading = (
        SurfCondition.objects.filter(spot=lesson.spot)
        .order_by("-recorded_at" if _has_field(SurfCondition, "recorded_at") else "-created_at")
        .first()
    )
    if reading is None:
        return {}

    snapshot: dict[str, Any] = {
        "captured_at": timezone.now().isoformat(),
        "captured_by": getattr(user, "username", "") or "",
        "spot": str(lesson.spot),
        "source": f"{SurfCondition._meta.label}:{reading.pk}",
    }
    for field in _SNAPSHOT_FIELDS:
        value = getattr(reading, field, None)
        if value is None:
            continue
        snapshot[field] = float(value) if isinstance(value, Decimal) else value

    lesson.conditions_snapshot = snapshot
    if getattr(user, "is_authenticated", False):
        lesson.updated_by = user
    lesson.save(update_fields=["conditions_snapshot", "updated_by", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=lesson,
        user=user,
        description=_("Surf conditions captured for %(code)s") % {"code": lesson.lesson_code},
    )
    return snapshot


def _has_field(model, name: str) -> bool:
    return any(field.name == name for field in model._meta.get_fields())


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
def lessons_for_calendar(
    start: date_cls, end: date_cls, instructor=None, spot=None, viewer=None
) -> list[dict]:
    """Lessons between *start* and *end* as plain dicts for the calendar UI.

    Pass *viewer* (the requesting user) so the feed obeys the same row-level
    ownership rule as the lesson list. Without it the calendar would hand a
    customer or student every lesson in the school, which is the exact leak
    ``apps.accounts.scoping`` exists to close. ``viewer=None`` keeps the
    unfiltered behaviour for internal callers such as management commands.
    """
    from apps.accounts.scoping import OWN, scope_queryset

    queryset = (
        Lesson.objects.filter(date__gte=start, date__lte=end)
        .select_related("lesson_type", "spot", "instructor")
        .annotate(
            booked=Count(
                "attendances",
                filter=Q(
                    attendances__is_deleted=False,
                    attendances__status__in=LessonAttendance.SEAT_TAKING_STATUSES,
                ),
                distinct=True,
            )
        )
        .order_by("date", "start_time")
    )
    if instructor is not None:
        queryset = queryset.filter(
            Q(instructor=instructor) | Q(assistant_instructors=instructor)
        ).distinct()
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    if viewer is not None:
        queryset = scope_queryset(
            queryset,
            viewer,
            access=OWN,
            lookups=("attendances__student__customer__user",),
        )

    events: list[dict] = []
    for lesson in queryset:
        events.append(
            {
                "id": lesson.pk,
                "code": lesson.lesson_code,
                "title": f"{lesson.lesson_type.name} · {lesson.spot}",
                "lesson_type": lesson.lesson_type.name,
                "category": lesson.lesson_type.category,
                "date": lesson.date.isoformat(),
                "start": lesson.start_time.strftime("%H:%M"),
                "end": lesson.end_time.strftime("%H:%M"),
                "starts_at": lesson.starts_at.isoformat(),
                "ends_at": lesson.ends_at.isoformat(),
                "colour": lesson.lesson_type.colour,
                "status": lesson.status,
                "status_label": str(lesson.get_status_display()),
                "instructor": _instructor_label(lesson.instructor),
                "instructor_id": lesson.instructor_id,
                "spot": str(lesson.spot),
                "spot_id": lesson.spot_id,
                "booked": lesson.booked,
                "capacity": lesson.capacity,
                "available": max(0, lesson.capacity - lesson.booked),
                "is_full": lesson.booked >= lesson.capacity,
                "safety_briefing_done": lesson.safety_briefing_done,
            }
        )
    return events


# ---------------------------------------------------------------------------
# Cross-app integration seams
# ---------------------------------------------------------------------------
def _student_age(student, on_date: date_cls | None = None) -> int | None:
    reference = on_date or timezone.localdate()
    born = getattr(student, "date_of_birth", None) or getattr(student, "birth_date", None)
    if born:
        return (
            reference.year - born.year - ((reference.month, reference.day) < (born.month, born.day))
        )
    age = getattr(student, "age", None)
    return age if isinstance(age, int) else None


def _check_student_restrictions(student, lesson: Lesson) -> None:
    """Refuse the booking when the safety module has restricted this student."""
    try:
        StudentRestriction = django_apps.get_model("safety", "StudentRestriction")
    except LookupError:
        return
    queryset = StudentRestriction.objects.filter(student=student)
    if _has_field(StudentRestriction, "is_active"):
        queryset = queryset.filter(is_active=True)
    restriction = queryset.first()
    if restriction is not None:
        raise ValidationError(
            _("%(student)s has an active safety restriction: %(reason)s")
            % {"student": student, "reason": restriction}
        )


def _bump_student_progress(student, lesson: Lesson) -> None:
    """Advance the student's lesson counters after a completed lesson."""
    if student is None:
        return
    updated: list[str] = []

    for attribute in _STUDENT_LESSON_COUNTERS:
        if hasattr(student, attribute):
            setattr(student, attribute, (getattr(student, attribute) or 0) + 1)
            updated.append(attribute)
            break

    hours = Decimal(lesson.duration_minutes) / Decimal("60")
    for attribute in _STUDENT_HOUR_COUNTERS:
        if hasattr(student, attribute):
            current = getattr(student, attribute) or 0
            try:
                setattr(student, attribute, (Decimal(current) + hours).quantize(Decimal("0.01")))
            except (TypeError, ValueError):
                setattr(student, attribute, float(current) + float(hours))
            updated.append(attribute)
            break

    if hasattr(student, "last_lesson_date"):
        current = student.last_lesson_date
        if current is None or current < lesson.date:
            student.last_lesson_date = lesson.date
            updated.append("last_lesson_date")

    if not updated:
        return
    if hasattr(student, "updated_at"):
        updated.append("updated_at")
    try:
        student.save(update_fields=updated)
    except Exception:  # noqa: BLE001 - unexpected field set; fall back to a full save
        student.save()


def _flag_bookings_for_cancellation(
    bookings: Iterable, lesson: Lesson, reason: str, *, user=None, request=None
) -> None:
    """Tell the bookings module that a lesson it sold has been cancelled.

    The bookings app owns what "cancelled lesson" means for a booking (refund,
    reschedule, credit), so lessons never rewrites a booking's status itself:
    it calls ``apps.bookings.services.handle_lesson_cancelled`` when present and
    always leaves an audit trail so nothing is silently lost.
    """
    bookings = [booking for booking in bookings if booking is not None]
    if not bookings:
        return

    handler = None
    try:
        from apps.bookings import services as booking_services

        handler = getattr(booking_services, "handle_lesson_cancelled", None)
    except Exception:  # noqa: BLE001 - the bookings module may not have landed yet
        handler = None

    for booking in bookings:
        if callable(handler):
            try:
                handler(booking, lesson=lesson, reason=reason, user=user)
            except Exception:  # noqa: BLE001 - a booking hook must not block cancellation
                logger.exception("handle_lesson_cancelled failed for booking %s", booking.pk)
        record_audit(
            request,
            action=AuditAction.BOOKING_CANCEL,
            instance=booking,
            user=user,
            description=_("Lesson %(code)s cancelled — booking needs attention: %(reason)s")
            % {"code": lesson.lesson_code, "reason": reason},
        )
