"""Business rules for instructor scheduling, certification and performance.

Everything that answers "may this instructor be assigned?" lives here, because
lessons, bookings, camps and the safety module all need the *same* answer. A
rule duplicated in a view is a rule that will drift.

Cross-app model access is deliberately lazy (``apps.get_model``) and defensive:
this module must keep working before the lessons, finance and notifications apps
have data — or even tables — so a fresh installation can still publish
availability and record certifications.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import FieldError, ValidationError
from django.db import DatabaseError, models, transaction
from django.db.models import Avg, Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import (
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    LessonStatus,
    SurfLevel,
    level_rank,
)

from .models import (
    EXPIRY_WARNING_DAYS,
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

#: Field names the lessons app might plausibly use. Probed in order; the first
#: one that exists wins. Keeping this table here means the lessons app can be
#: written independently without a circular dependency.
LESSON_DATE_FIELDS = ("date", "scheduled_date", "lesson_date", "start_date", "session_date")
LESSON_START_FIELDS = ("start_time", "starts_at", "start")
LESSON_END_FIELDS = ("end_time", "ends_at", "end")
LESSON_INSTRUCTOR_FIELDS = ("instructor", "primary_instructor", "coach")
LESSON_INSTRUCTOR_M2M_FIELDS = ("instructors", "assistant_instructors")
LESSON_PRICE_FIELDS = ("total_price", "price", "total_amount", "amount", "revenue")
LESSON_STATUS_FIELD = "status"

#: Lesson statuses that no longer occupy the instructor's calendar.
NON_BLOCKING_LESSON_STATUSES = (LessonStatus.CANCELLED, LessonStatus.POSTPONED)
#: Lesson statuses that count as delivered work for performance reporting.
DELIVERED_LESSON_STATUSES = (LessonStatus.COMPLETED, LessonStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# Lazy cross-app helpers
# ---------------------------------------------------------------------------
def _get_model(app_label: str, model_name: str):
    """Return a model class, or ``None`` when the app is absent/not ready."""
    try:
        return apps.get_model(app_label, model_name)
    except Exception:  # noqa: BLE001 - LookupError, AppRegistryNotReady, ImproperlyConfigured
        return None


def _field_names(model) -> set[str]:
    return {field.name for field in model._meta.get_fields()}


def _first_present(names: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in names), None)


@dataclass(frozen=True)
class LessonSchema:
    """The subset of the lessons app's shape that this module depends on."""

    model: type
    date_field: str
    instructor_field: str | None
    instructor_is_m2m: bool
    start_field: str | None
    end_field: str | None
    status_field: str | None
    price_field: str | None


def _lesson_schema() -> LessonSchema | None:
    """Discover the lessons model layout, or ``None`` if it is unusable."""
    model = _get_model("lessons", "Lesson")
    if model is None:
        return None
    names = _field_names(model)
    date_field = _first_present(names, LESSON_DATE_FIELDS)
    if not date_field:
        return None

    instructor_field = _first_present(names, LESSON_INSTRUCTOR_FIELDS)
    instructor_is_m2m = False
    if instructor_field is None:
        instructor_field = _first_present(names, LESSON_INSTRUCTOR_M2M_FIELDS)
        instructor_is_m2m = instructor_field is not None
    if instructor_field is None:
        return None

    return LessonSchema(
        model=model,
        date_field=date_field,
        instructor_field=instructor_field,
        instructor_is_m2m=instructor_is_m2m,
        start_field=_first_present(names, LESSON_START_FIELDS),
        end_field=_first_present(names, LESSON_END_FIELDS),
        status_field=LESSON_STATUS_FIELD if LESSON_STATUS_FIELD in names else None,
        price_field=_first_present(names, LESSON_PRICE_FIELDS),
    )


def _lesson_base_queryset(schema: LessonSchema, instructor: Instructor):
    manager = getattr(schema.model, "objects", None)
    if manager is None:  # pragma: no cover - every Django model has a manager
        return None
    queryset = manager.filter(**{schema.instructor_field: instructor})
    if schema.status_field:
        queryset = queryset.exclude(
            **{f"{schema.status_field}__in": list(NON_BLOCKING_LESSON_STATUSES)}
        )
    return queryset


def _overlapping_lessons(
    instructor: Instructor, on_date: dt.date, start_time: dt.time, end_time: dt.time
):
    """Lessons that clash with ``[start_time, end_time)`` on ``on_date``.

    Returns ``None`` when the lessons app is absent or shaped differently than
    expected — the caller treats that as "no conflict", which is what lets this
    module ship before the lessons module exists.
    """
    schema = _lesson_schema()
    if schema is None:
        return None
    try:
        queryset = _lesson_base_queryset(schema, instructor)
        if queryset is None:
            return None
        queryset = queryset.filter(**{schema.date_field: on_date})
        if schema.start_field and schema.end_field:
            queryset = queryset.filter(
                **{
                    f"{schema.start_field}__lt": end_time,
                    f"{schema.end_field}__gt": start_time,
                }
            )
        elif schema.start_field:
            # No end time recorded: treat any lesson starting inside the window
            # as a clash rather than pretending the instructor is free.
            queryset = queryset.filter(
                **{
                    f"{schema.start_field}__gte": start_time,
                    f"{schema.start_field}__lt": end_time,
                }
            )
        return list(queryset[:5])
    except (DatabaseError, FieldError, TypeError, ValueError) as exc:
        logger.debug("Lesson conflict lookup unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def is_instructor_available(
    instructor: Instructor,
    date: dt.date,
    start_time: dt.time,
    end_time: dt.time,
) -> tuple[bool, str]:
    """Can *instructor* teach ``[start_time, end_time)`` on *date*?

    Returns ``(True, "")`` or ``(False, human_readable_reason)``. The reason is
    shown directly to the person trying to make the booking, so it always names
    the blocking fact rather than saying "unavailable".

    Checks, in the order a receptionist would make them:

    1. the instructor is employed and active,
    2. the instructor is open for bookings,
    3. the requested window is a real window,
    4. a published availability slot covers the window and is valid on *date*,
    5. no approved time off covers *date*,
    6. no lesson already occupies an overlapping window.
    """
    if instructor is None:
        return False, _("No instructor selected.")
    if not instructor.is_active:
        return False, _("%(name)s is not an active instructor.") % {"name": instructor.full_name}
    if not instructor.is_available_for_booking:
        return False, _("%(name)s is not open for bookings.") % {"name": instructor.full_name}
    if start_time is None or end_time is None or date is None:
        return False, _("A date, a start time and an end time are required.")
    if end_time <= start_time:
        return False, _("The end time must be after the start time.")

    slot = (
        instructor.availability_slots.filter(
            is_active=True,
            weekday=date.weekday(),
            start_time__lte=start_time,
            end_time__gte=end_time,
        )
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=date))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=date))
        .first()
    )
    if slot is None:
        return False, _(
            "%(name)s has no published availability on %(day)s between %(start)s and %(end)s."
        ) % {
            "name": instructor.full_name,
            "day": AvailabilitySlot.Weekday(date.weekday()).label,
            "start": start_time.strftime("%H:%M"),
            "end": end_time.strftime("%H:%M"),
        }

    time_off = instructor.time_off_periods.filter(
        is_approved=True, start_date__lte=date, end_date__gte=date
    ).first()
    if time_off is not None:
        return False, _("%(name)s is on approved %(reason)s until %(until)s.") % {
            "name": instructor.full_name,
            "reason": time_off.get_reason_display().lower(),
            "until": time_off.end_date.strftime("%d.%m.%Y"),
        }

    conflicts = _overlapping_lessons(instructor, date, start_time, end_time)
    if conflicts:
        return False, _("%(name)s already has %(count)s lesson(s) in that window.") % {
            "name": instructor.full_name,
            "count": len(conflicts),
        }

    return True, ""


def available_instructors(
    date: dt.date,
    start_time: dt.time,
    end_time: dt.time,
    level: str | None = None,
) -> models.QuerySet:
    """Instructors who can teach ``[start_time, end_time)`` on *date*.

    Returns a queryset (never a list) so callers can filter, order and paginate
    it. The cheap constraints are pushed into SQL; only the lesson-overlap check
    runs per instructor, over the already-narrowed set.
    """
    queryset = Instructor.objects.select_related("user").filter(
        is_active=True, is_available_for_booking=True
    )
    if date is None or start_time is None or end_time is None or end_time <= start_time:
        return queryset.none()

    if level:
        required = level_rank(level)
        allowed = [value for value, _label in SurfLevel.choices if level_rank(value) >= required]
        queryset = queryset.filter(max_level_taught__in=allowed)

    slot_match = (
        Q(availability_slots__is_active=True)
        & Q(availability_slots__weekday=date.weekday())
        & Q(availability_slots__start_time__lte=start_time)
        & Q(availability_slots__end_time__gte=end_time)
        & (
            Q(availability_slots__valid_from__isnull=True)
            | Q(availability_slots__valid_from__lte=date)
        )
        & (
            Q(availability_slots__valid_until__isnull=True)
            | Q(availability_slots__valid_until__gte=date)
        )
    )
    queryset = queryset.filter(slot_match).distinct()

    # Reverse lookups join raw rows, so soft-deleted absence must be excluded
    # explicitly — the manager filter does not apply across a join.
    queryset = queryset.exclude(
        time_off_periods__is_deleted=False,
        time_off_periods__is_approved=True,
        time_off_periods__start_date__lte=date,
        time_off_periods__end_date__gte=date,
    )

    free_ids = [
        instructor.pk
        for instructor in queryset
        if not _overlapping_lessons(instructor, date, start_time, end_time)
    ]
    return (
        Instructor.objects.select_related("user")
        .filter(pk__in=free_ids)
        .order_by("user__first_name", "user__last_name")
    )


def instructor_conflicts(
    instructor: Instructor, date: dt.date, start_time: dt.time, end_time: dt.time
) -> list[str]:
    """Every reason *instructor* cannot take the window, not just the first.

    Used by the assignment screens, where showing one blocker at a time turns
    scheduling into a guessing game.
    """
    reasons: list[str] = []
    if not instructor.is_active:
        reasons.append(_("The instructor is not active."))
    if not instructor.is_available_for_booking:
        reasons.append(_("The instructor is not open for bookings."))
    available, reason = is_instructor_available(instructor, date, start_time, end_time)
    if not available and reason and reason not in reasons:
        reasons.append(reason)
    return reasons


def weekly_availability(instructor: Instructor, on_date: dt.date | None = None) -> list[dict]:
    """The instructor's week as seven rows, ready for the editor grid."""
    reference = on_date or timezone.localdate()
    slots = list(instructor.availability_slots.all())
    week: list[dict] = []
    for value, label in AvailabilitySlot.Weekday.choices:
        day_slots = [slot for slot in slots if slot.weekday == value]
        week.append(
            {
                "weekday": value,
                "label": label,
                "slots": day_slots,
                "active_minutes": sum(
                    slot.duration_minutes
                    for slot in day_slots
                    if slot.is_active and slot.is_valid_on(reference)
                ),
            }
        )
    return week


@transaction.atomic
def create_availability_slot(
    instructor: Instructor,
    weekday: int,
    start_time: dt.time,
    end_time: dt.time,
    *,
    valid_from: dt.date | None = None,
    valid_until: dt.date | None = None,
    is_active: bool = True,
    request=None,
) -> AvailabilitySlot:
    """Publish a weekly availability window, refusing overlaps.

    Two overlapping slots on the same day mean the instructor appears free twice
    for the same hour, which quietly doubles their apparent capacity.
    """
    slot = AvailabilitySlot(
        instructor=instructor,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        valid_from=valid_from,
        valid_until=valid_until,
        is_active=is_active,
    )
    slot.full_clean()
    validate_availability_slot(slot)
    slot.save()
    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=instructor,
        description=_("Availability added: %(slot)s") % {"slot": slot},
    )
    return slot


def validate_availability_slot(slot: AvailabilitySlot) -> None:
    """Raise :class:`ValidationError` when *slot* overlaps another live slot."""
    clashes = AvailabilitySlot.objects.filter(
        instructor=slot.instructor,
        weekday=slot.weekday,
        start_time__lt=slot.end_time,
        end_time__gt=slot.start_time,
    ).exclude(pk=slot.pk)
    for other in clashes:
        if _validity_windows_overlap(slot, other):
            raise ValidationError(
                {
                    "start_time": [
                        _("This overlaps an existing slot (%(other)s).") % {"other": other}
                    ]
                }
            )


def _validity_windows_overlap(first: AvailabilitySlot, second: AvailabilitySlot) -> bool:
    first_start = first.valid_from or dt.date.min
    first_end = first.valid_until or dt.date.max
    second_start = second.valid_from or dt.date.min
    second_end = second.valid_until or dt.date.max
    return first_start <= second_end and second_start <= first_end


# ---------------------------------------------------------------------------
# Ratios and qualification gates
# ---------------------------------------------------------------------------
def can_teach_level(instructor: Instructor, level: str) -> bool:
    """Does the instructor's grade cover *level*?"""
    return level_rank(instructor.max_level_taught) >= level_rank(level)


def max_students_for(
    instructor: Instructor, level: str, *, has_minors: bool = False
) -> int:
    """The effective ratio ceiling for one instructor teaching *level*.

    The lowest of three limits wins: the safety ratio for the level, the
    stricter ratio for groups containing under-18s, and the instructor's own
    declared maximum. This is a safety constraint, not a preference.
    """
    limits = [
        int(instructor.max_students_per_lesson or 1),
        int(MAX_STUDENTS_PER_INSTRUCTOR.get(level, 8)),
    ]
    if has_minors:
        limits.append(int(MAX_STUDENTS_PER_INSTRUCTOR_MINORS))
    return max(1, min(limits))


def ratio_ceiling(max_level_taught: str) -> int:
    """The largest group the safety table allows for this grade of instructor.

    An instructor graded for beginners can never legitimately declare a personal
    maximum above the beginner ratio; one graded for intermediate may, because
    the intermediate ratio is higher.
    """
    rank = level_rank(max_level_taught)
    values = [
        value
        for key, value in MAX_STUDENTS_PER_INSTRUCTOR.items()
        if level_rank(key) <= rank
    ]
    return max(values) if values else 8


def assignment_blockers(
    instructor: Instructor,
    *,
    level: str | None = None,
    student_count: int | None = None,
    has_minors: bool = False,
) -> list[str]:
    """Hard reasons this instructor must not take this group.

    Certification currency is a legal gate, not a warning: an instructor without
    a current rescue award may not be put in the water with students.
    """
    blockers: list[str] = []
    if not instructor.is_active:
        blockers.append(_("The instructor is not active."))

    missing = instructor.missing_certification_groups
    if missing:
        blockers.append(
            _("Missing or expired certification: %(groups)s.")
            % {"groups": ", ".join(str(group) for group in missing)}
        )

    if level:
        if not can_teach_level(instructor, level):
            blockers.append(
                _("Qualified to teach up to %(max)s, but the group is %(level)s.")
                % {
                    "max": SurfLevel(instructor.max_level_taught).label,
                    "level": SurfLevel(level).label,
                }
            )
        if level_rank(level) >= level_rank(SurfLevel.INTERMEDIATE):
            senior = instructor.current_certifications().filter(
                kind__in=list(Certification.SENIOR_COACHING_KINDS)
            )
            if not senior.exists():
                blockers.append(
                    _("Intermediate level and above requires a current Level 2 coaching award.")
                )
        if student_count is not None:
            ceiling = max_students_for(instructor, level, has_minors=has_minors)
            if student_count > ceiling:
                blockers.append(
                    _("Group of %(count)s exceeds the maximum ratio of 1:%(max)s.")
                    % {"count": student_count, "max": ceiling}
                )
    return blockers


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------
def check_certification_expiry(days: int = EXPIRY_WARNING_DAYS) -> list[dict]:
    """Instructors holding certifications that expire within *days* days.

    Already-expired certifications are included as well — they are the most
    urgent case, and dropping them from the report is exactly how a lapsed
    rescue award goes unnoticed for a season.

    Returns ``[{"instructor": …, "certifications": [...], "soonest_expiry": date,
    "days_until_expiry": int, "has_expired": bool}, …]`` ordered by urgency.
    """
    today = timezone.localdate()
    horizon = today + dt.timedelta(days=max(0, int(days)))
    certifications = (
        Certification.objects.select_related("instructor", "instructor__user")
        .filter(
            instructor__is_active=True,
            instructor__is_deleted=False,
            expires_on__isnull=False,
            expires_on__lte=horizon,
        )
        .order_by("expires_on")
    )

    grouped: dict[int, dict] = {}
    for certification in certifications:
        entry = grouped.setdefault(
            certification.instructor_id,
            {
                "instructor": certification.instructor,
                "certifications": [],
                "soonest_expiry": certification.expires_on,
                "days_until_expiry": (certification.expires_on - today).days,
                "has_expired": False,
            },
        )
        entry["certifications"].append(certification)
        if certification.expires_on < entry["soonest_expiry"]:
            entry["soonest_expiry"] = certification.expires_on
            entry["days_until_expiry"] = (certification.expires_on - today).days
        if certification.is_expired:
            entry["has_expired"] = True

    return sorted(grouped.values(), key=lambda entry: entry["soonest_expiry"])


@transaction.atomic
def verify_certification(certification: Certification, user, request=None) -> Certification:
    """Record that a named staff member has seen the original certificate."""
    certification.is_verified = True
    certification.verified_by = user if getattr(user, "is_authenticated", False) else None
    certification.verified_at = timezone.now()
    certification.save(update_fields=["is_verified", "verified_by", "verified_at", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=certification,
        description=_("Certification %(name)s verified for %(instructor)s")
        % {"name": certification.name, "instructor": certification.instructor.full_name},
        changes={"is_verified": [False, True]},
    )
    return certification


# ---------------------------------------------------------------------------
# Time off
# ---------------------------------------------------------------------------
def overlapping_time_off(
    instructor: Instructor,
    start_date: dt.date,
    end_date: dt.date,
    exclude_pk: int | None = None,
) -> models.QuerySet:
    queryset = TimeOff.objects.filter(
        instructor=instructor, start_date__lte=end_date, end_date__gte=start_date
    )
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset


def lessons_during_time_off(time_off: TimeOff) -> int:
    """How many lessons are already scheduled inside the requested absence.

    Approving absence over booked lessons is legitimate — somebody must then
    reassign them — but it must never happen silently.
    """
    schema = _lesson_schema()
    if schema is None:
        return 0
    try:
        queryset = _lesson_base_queryset(schema, time_off.instructor)
        if queryset is None:
            return 0
        return queryset.filter(
            **{
                f"{schema.date_field}__gte": time_off.start_date,
                f"{schema.date_field}__lte": time_off.end_date,
            }
        ).count()
    except (DatabaseError, FieldError, TypeError, ValueError) as exc:
        logger.debug("Lesson lookup during time off unavailable: %s", exc)
        return 0


@transaction.atomic
def approve_time_off(time_off: TimeOff, user, request=None) -> tuple[TimeOff, int]:
    """Approve an absence and report how many lessons it collides with."""
    if time_off.is_approved:
        return time_off, 0
    affected = lessons_during_time_off(time_off)
    time_off.is_approved = True
    time_off.approved_by = user if getattr(user, "is_authenticated", False) else None
    time_off.approved_at = timezone.now()
    time_off.save(update_fields=["is_approved", "approved_by", "approved_at", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=time_off,
        description=_("Time off approved for %(instructor)s (%(start)s – %(end)s)")
        % {
            "instructor": time_off.instructor.full_name,
            "start": time_off.start_date,
            "end": time_off.end_date,
        },
        changes={"is_approved": [False, True]},
    )
    return time_off, affected


# ---------------------------------------------------------------------------
# Ratings, statistics and performance
# ---------------------------------------------------------------------------
#: Places a customer-facing rating of an instructor might be stored, probed in
#: order: ``(app_label, model_name, rating_field, path_to_instructor)``.
RATING_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("lessons", "LessonAttendance", "instructor_rating", "lesson__instructor"),
    ("lessons", "LessonAttendance", "rating", "lesson__instructor"),
    ("lessons", "Lesson", "instructor_rating", "instructor"),
    ("bookings", "Booking", "instructor_rating", "instructor"),
)


def _rating_aggregate(instructor: Instructor) -> tuple[Decimal, int]:
    """Weighted mean and count of every rating recorded for *instructor*."""
    total_sum = Decimal("0")
    total_count = 0
    seen: set[tuple[str, str]] = set()

    for app_label, model_name, rating_field, path in RATING_SOURCES:
        model = _get_model(app_label, model_name)
        if model is None or rating_field not in _field_names(model):
            continue
        key = (f"{app_label}.{model_name}", rating_field)
        if key in seen:
            continue
        seen.add(key)
        try:
            result = model.objects.filter(
                **{path: instructor, f"{rating_field}__isnull": False}
            ).aggregate(average=Avg(rating_field), count=Count("pk"))
        except (DatabaseError, FieldError, TypeError, ValueError) as exc:
            logger.debug("Rating source %s unavailable: %s", key, exc)
            continue
        count = int(result.get("count") or 0)
        average = result.get("average")
        if count and average is not None:
            total_sum += Decimal(str(average)) * count
            total_count += count

    if not total_count:
        return ZERO, 0
    return (total_sum / total_count).quantize(Decimal("0.01")), total_count


def recalculate_instructor_rating(instructor: Instructor) -> Decimal:
    """Refresh ``rating_average`` / ``rating_count`` from the rating sources.

    Ratings live wherever the feedback was captured (lesson attendance today,
    possibly bookings later), so the aggregate is rebuilt rather than
    incremented — an incremented counter drifts the moment a rating is edited.
    """
    average, count = _rating_aggregate(instructor)
    if instructor.rating_average != average or instructor.rating_count != count:
        instructor.rating_average = average
        instructor.rating_count = count
        instructor.save(update_fields=["rating_average", "rating_count", "updated_at"])
    return average


def recalculate_lesson_total(instructor: Instructor) -> int:
    """Refresh ``total_lessons_taught`` from delivered lessons."""
    schema = _lesson_schema()
    total = 0
    if schema is not None:
        try:
            queryset = _lesson_base_queryset(schema, instructor)
            if queryset is not None:
                if schema.status_field:
                    queryset = queryset.filter(
                        **{f"{schema.status_field}__in": list(DELIVERED_LESSON_STATUSES)}
                    )
                total = queryset.count()
        except (DatabaseError, FieldError, TypeError, ValueError) as exc:
            logger.debug("Lesson total lookup unavailable: %s", exc)
            total = instructor.total_lessons_taught
    if total != instructor.total_lessons_taught:
        instructor.total_lessons_taught = total
        instructor.save(update_fields=["total_lessons_taught", "updated_at"])
    return total


def refresh_instructor_statistics(instructor: Instructor) -> dict:
    """Recompute every denormalised counter on the profile."""
    return {
        "rating_average": recalculate_instructor_rating(instructor),
        "total_lessons_taught": recalculate_lesson_total(instructor),
    }


def _sum_decimal(queryset, field: str) -> Decimal:
    result = queryset.aggregate(
        total=Coalesce(Sum(field), Value(ZERO), output_field=DecimalField(max_digits=14, decimal_places=2))
    )
    return Decimal(str(result.get("total") or ZERO)).quantize(Decimal("0.01"))


def _commission_from_records(instructor: Instructor, start: dt.date, end: dt.date) -> Decimal | None:
    """Actual commission booked in finance, when that app is populated."""
    model = _get_model("finance", "CommissionRecord")
    if model is None:
        return None
    names = _field_names(model)
    instructor_field = _first_present(names, ("instructor", "earned_by", "staff"))
    amount_field = _first_present(names, ("amount", "commission_amount", "total"))
    date_field = _first_present(names, ("date", "earned_on", "period_start", "created_at"))
    if not (instructor_field and amount_field and date_field):
        return None
    try:
        queryset = model.objects.filter(
            **{
                instructor_field: instructor,
                f"{date_field}__gte": start,
                f"{date_field}__lte": end,
            }
        )
        if not queryset.exists():
            return None
        return _sum_decimal(queryset, amount_field)
    except (DatabaseError, FieldError, TypeError, ValueError) as exc:
        logger.debug("Commission lookup unavailable: %s", exc)
        return None


def _students_taught(schema: LessonSchema, instructor: Instructor, start: dt.date, end: dt.date) -> int:
    """Distinct students seen in the period, falling back to attendance rows."""
    model = _get_model("lessons", "LessonAttendance")
    if model is None:
        return 0
    names = _field_names(model)
    if "lesson" not in names:
        return 0
    student_field = _first_present(names, ("student", "participant"))
    try:
        queryset = model.objects.filter(
            **{
                f"lesson__{schema.instructor_field}": instructor,
                f"lesson__{schema.date_field}__gte": start,
                f"lesson__{schema.date_field}__lte": end,
            }
        )
        if student_field:
            return queryset.values(student_field).distinct().count()
        return queryset.count()
    except (DatabaseError, FieldError, TypeError, ValueError) as exc:
        logger.debug("Attendance lookup unavailable: %s", exc)
        return 0


def instructor_performance(instructor: Instructor, start: dt.date, end: dt.date) -> dict:
    """Delivery and earnings summary for one instructor over ``[start, end]``.

    Every figure is zero-safe: an empty or absent lessons/finance app yields
    zeros rather than an exception, so the profile screen works on day one.
    """
    if start and end and end < start:
        start, end = end, start

    summary = {
        "period_start": start,
        "period_end": end,
        "lessons_taught": 0,
        "lessons_cancelled": 0,
        "students_taught": 0,
        "hours_taught": Decimal("0.00"),
        "average_rating": instructor.rating_average or ZERO,
        "revenue_generated": ZERO,
        "commission_earned": ZERO,
        "commission_percent": instructor.commission_percent or ZERO,
        "is_estimated_commission": True,
    }

    schema = _lesson_schema()
    if schema is not None and start and end:
        try:
            base = _lesson_base_queryset(schema, instructor)
            if base is not None:
                period = base.filter(
                    **{
                        f"{schema.date_field}__gte": start,
                        f"{schema.date_field}__lte": end,
                    }
                )
                delivered = period
                if schema.status_field:
                    delivered = period.filter(
                        **{f"{schema.status_field}__in": list(DELIVERED_LESSON_STATUSES)}
                    )
                summary["lessons_taught"] = delivered.count()
                summary["students_taught"] = _students_taught(schema, instructor, start, end)
                if schema.price_field:
                    summary["revenue_generated"] = _sum_decimal(delivered, schema.price_field)
                if schema.status_field:
                    cancelled_queryset = schema.model.objects.filter(
                        **{
                            schema.instructor_field: instructor,
                            f"{schema.date_field}__gte": start,
                            f"{schema.date_field}__lte": end,
                            f"{schema.status_field}": LessonStatus.CANCELLED,
                        }
                    )
                    summary["lessons_cancelled"] = cancelled_queryset.count()
        except (DatabaseError, FieldError, TypeError, ValueError) as exc:
            logger.debug("Performance lookup unavailable: %s", exc)

    booked_commission = _commission_from_records(instructor, start, end) if start and end else None
    if booked_commission is not None:
        summary["commission_earned"] = booked_commission
        summary["is_estimated_commission"] = False
    else:
        percent = Decimal(str(instructor.commission_percent or 0))
        summary["commission_earned"] = (
            summary["revenue_generated"] * percent / Decimal("100")
        ).quantize(Decimal("0.01"))

    reviews = PerformanceReview.objects.filter(
        instructor=instructor, period_end__gte=start, period_start__lte=end
    )
    summary["review_count"] = reviews.count()
    summary["review_average"] = (
        Decimal(str(reviews.aggregate(value=Avg("overall_score"))["value"])).quantize(
            Decimal("0.01")
        )
        if summary["review_count"]
        else ZERO
    )
    return summary


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def can_delete_instructor(instructor: Instructor) -> tuple[bool, str]:
    """Refuse deletion while future work is still assigned.

    Deleting an instructor who is on tomorrow's schedule leaves a lesson with
    nobody in the water. Deactivation is the correct action in that case.
    """
    schema = _lesson_schema()
    if schema is not None:
        try:
            base = _lesson_base_queryset(schema, instructor)
            if base is not None:
                upcoming = base.filter(
                    **{f"{schema.date_field}__gte": timezone.localdate()}
                ).count()
                if upcoming:
                    return False, _(
                        "%(name)s still has %(count)s upcoming lesson(s). "
                        "Reassign them first, or deactivate the profile instead."
                    ) % {"name": instructor.full_name, "count": upcoming}
        except (DatabaseError, FieldError, TypeError, ValueError) as exc:
            logger.debug("Upcoming lesson lookup unavailable: %s", exc)
    return True, ""


@transaction.atomic
def set_booking_availability(
    instructor: Instructor, is_available: bool, request=None
) -> Instructor:
    """Take an instructor in or out of the booking pool."""
    if instructor.is_available_for_booking == is_available:
        return instructor
    previous = instructor.is_available_for_booking
    instructor.is_available_for_booking = is_available
    instructor.save(update_fields=["is_available_for_booking", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=instructor,
        description=(
            _("%(name)s opened for bookings")
            if is_available
            else _("%(name)s withdrawn from the booking pool")
        )
        % {"name": instructor.full_name},
        changes={"is_available_for_booking": [previous, is_available]},
    )
    return instructor
