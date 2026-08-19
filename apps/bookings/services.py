"""Booking business rules.

Everything that decides *whether a booking may exist* and *what happens when it
changes state* lives here. Views orchestrate, models validate a single row,
services own the multi-step rules that span the schedule, the roster and the
waiting list.

The three rules that keep a surf school out of trouble
------------------------------------------------------
1. **Seats are finite.** Every seat check runs inside a transaction that locks
   the lesson row, so two receptionists selling the last place at the same
   moment cannot both succeed.
2. **A person can only be in one place.** A student with a confirmed booking
   that overlaps the requested window is refused, whichever lesson it is on.
3. **Ratios are safety limits, not preferences.** Group size is capped by the
   instructor count multiplied by the per-level maximum, and by the stricter
   minors maximum as soon as one participant is under 18.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import FieldError, ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    BookingSource,
    BookingStatus,
    LessonStatus,
    PaymentStatus,
    SurfLevel,
    level_rank,
)
from apps.core.models import SystemSetting
from apps.core.utils import to_decimal

from .models import (
    FREE_CANCELLATION_HOURS,
    Booking,
    WaitlistEntry,
    as_aware,
    as_end_of_day,
    camp_capacity,
    camp_window,
    is_minor,
    lesson_capacity,
    lesson_colour,
    lesson_end,
    lesson_level_range,
    lesson_start,
    lesson_target_level,
    student_age,
)

logger = logging.getLogger("apps.bookings")

ZERO = Decimal("0.00")

#: Statuses a lesson may not be booked into.
UNBOOKABLE_LESSON_STATUSES = {LessonStatus.CANCELLED, LessonStatus.POSTPONED}

#: Badge palette used by the calendar and the tables.
STATUS_COLOURS = {
    BookingStatus.DRAFT: "slate",
    BookingStatus.PENDING: "amber",
    BookingStatus.CONFIRMED: "sky",
    BookingStatus.CHECKED_IN: "violet",
    BookingStatus.COMPLETED: "emerald",
    BookingStatus.CANCELLED: "rose",
    BookingStatus.NO_SHOW: "rose",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class BookingError(Exception):
    """Base class for refused booking operations."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or [message]


class BookingConflictError(BookingError):
    """The requested booking breaks one or more operational or safety rules."""

    def __init__(self, errors: list[str]):
        super().__init__(errors[0] if errors else _("The booking cannot be made."), errors)


class BookingTransitionError(BookingError):
    """The booking is not in a state that allows the requested transition."""


# ---------------------------------------------------------------------------
# Small infrastructure helpers
# ---------------------------------------------------------------------------
def get_model(app_label: str, model_name: str):
    """Return a model from another app, or ``None`` when it is unavailable.

    Used instead of a module-level import so this app can be loaded, tested and
    reasoned about without importing half the project.
    """
    try:
        return django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return None


#: Internal alias kept for readability inside this module.
_get_model = get_model


def _safe_filter(queryset, **lookups):
    """Apply *lookups*, ignoring any that the target model does not support.

    Lets this module query sibling apps by their documented field names without
    turning a rename into a 500 on the calendar.
    """
    if not lookups:
        return queryset
    try:
        filtered = queryset.filter(**lookups)
        # Force evaluation of the query construction, not the rows.
        str(filtered.query)
        return filtered
    except (FieldError, ValueError, TypeError):
        return queryset


def _concrete_field_names(model) -> set[str]:
    return {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}


def _assignable(model, data: dict) -> dict:
    """Keep only the keys that exist as concrete fields on *model*."""
    names = _concrete_field_names(model)
    return {key: value for key, value in data.items() if key in names}


def _set_if_valid(instance, field_name: str, value) -> bool:
    """Set ``field_name`` when it exists and *value* is an allowed choice."""
    try:
        field = instance._meta.get_field(field_name)
    except Exception:  # noqa: BLE001 - field simply is not there
        return False
    choices = getattr(field, "choices", None)
    if choices and value not in {choice[0] for choice in choices}:
        return False
    setattr(instance, field_name, value)
    return True


def policy_int(key: str, default: int) -> int:
    """Read an operator-tunable integer policy from the system settings."""
    try:
        value = SystemSetting.get(key, default)
        return int(value)
    except (TypeError, ValueError):
        return default


def policy_decimal(key: str, default: Decimal) -> Decimal:
    try:
        return to_decimal(SystemSetting.get(key, default), default)
    except Exception:  # noqa: BLE001 - configuration must never break operations
        return default


# ---------------------------------------------------------------------------
# Seat accounting
# ---------------------------------------------------------------------------
def seats_taken(lesson=None, camp=None, exclude_booking=None) -> int:
    """Seats currently held by active bookings on a lesson or camp."""
    if lesson is None and camp is None:
        return 0
    queryset = Booking.objects.filter(status__in=ACTIVE_BOOKING_STATUSES)
    if lesson is not None:
        queryset = queryset.filter(lesson=lesson)
    else:
        queryset = queryset.filter(surf_camp=camp)
    if exclude_booking is not None and exclude_booking.pk:
        queryset = queryset.exclude(pk=exclude_booking.pk)
    total = queryset.aggregate(
        seats=Coalesce(Sum("participants"), Value(0), output_field=IntegerField())
    )["seats"]
    return int(total or 0)


def seats_available(lesson=None, camp=None, exclude_booking=None) -> int:
    """Free seats, never negative."""
    capacity = lesson_capacity(lesson) if lesson is not None else camp_capacity(camp)
    return max(0, capacity - seats_taken(lesson=lesson, camp=camp, exclude_booking=exclude_booking))


def seats_map(lessons) -> dict[int, int]:
    """``{lesson_id: seats_taken}`` for a batch of lessons — avoids N+1."""
    ids = [lesson.pk for lesson in lessons]
    if not ids:
        return {}
    rows = (
        Booking.objects.filter(lesson_id__in=ids, status__in=ACTIVE_BOOKING_STATUSES)
        .values("lesson_id")
        .annotate(seats=Coalesce(Sum("participants"), Value(0), output_field=IntegerField()))
    )
    return {row["lesson_id"]: int(row["seats"] or 0) for row in rows}


def camp_seats_map(camps) -> dict[int, int]:
    ids = [camp.pk for camp in camps]
    if not ids:
        return {}
    rows = (
        Booking.objects.filter(surf_camp_id__in=ids, status__in=ACTIVE_BOOKING_STATUSES)
        .values("surf_camp_id")
        .annotate(seats=Coalesce(Sum("participants"), Value(0), output_field=IntegerField()))
    )
    return {row["surf_camp_id"]: int(row["seats"] or 0) for row in rows}


def instructor_count(lesson) -> int:
    """Number of coaches in the water for this lesson."""
    count = 1 if getattr(lesson, "instructor_id", None) else 0
    for attribute in ("assistant_instructors", "additional_instructors", "instructors"):
        related = getattr(lesson, attribute, None)
        if related is not None and hasattr(related, "count"):
            try:
                count += int(related.count())
            except Exception:  # noqa: BLE001, S112 - unsaved instance or missing table; one bad row must not abort the batch  # nosec B112
                continue
            break
    return count


def ratio_limit(lesson, includes_minor: bool = False) -> tuple[int, int, int]:
    """Return ``(max_students, per_instructor, instructors)`` for a lesson.

    Per-instructor maxima come from :mod:`apps.core.enums`, which encodes the
    governing-body ratios. A single under-18 participant pulls the whole group
    down to the stricter minors ratio — that is deliberate: the supervision
    burden is set by the least experienced, youngest person in the water.
    """
    level = lesson_target_level(lesson)
    per_instructor = int(MAX_STUDENTS_PER_INSTRUCTOR.get(level, 8))
    if includes_minor:
        per_instructor = min(per_instructor, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)
    coaches = instructor_count(lesson)
    return per_instructor * coaches, per_instructor, coaches


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
def _student_active_bookings(student, exclude_booking=None, around=None):
    """Bookings that would put *student* in the water at the same time."""
    queryset = (
        Booking.objects.filter(
            student=student,
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN],
        )
        .select_related("lesson", "surf_camp")
        .exclude(lesson__isnull=True, surf_camp__isnull=True)
    )
    if exclude_booking is not None and exclude_booking.pk:
        queryset = queryset.exclude(pk=exclude_booking.pk)
    if around is not None:
        day = timezone.localtime(around).date()
        window = Q(lesson__start_time__date__range=(day - timedelta(days=1), day + timedelta(days=1)))
        window |= Q(surf_camp__isnull=False)
        try:
            narrowed = queryset.filter(window)
            str(narrowed.query)
            return narrowed
        except (FieldError, ValueError, TypeError):
            return queryset
    return queryset


def _overlaps(start_a, end_a, start_b, end_b) -> bool:
    if not all([start_a, end_a, start_b, end_b]):
        return False
    return start_a < end_b and start_b < end_a


def student_restrictions(student) -> list[str]:
    """Active safety restrictions blocking *student* from the water.

    Looked up lazily: if the safety module has no restriction rows for this
    student — the normal case — the student is allowed.
    """
    if student is None:
        return []
    model = _get_model("safety", "StudentRestriction")
    if model is None:
        return []
    try:
        queryset = model.objects.filter(student=student)
        queryset = _safe_filter(queryset, is_active=True)
        today = timezone.localdate()
        try:
            queryset = queryset.filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
            str(queryset.query)
        except (FieldError, ValueError, TypeError):
            pass
        rows = list(queryset[:10])
    except Exception:  # noqa: BLE001 - a missing table must not block reception
        logger.warning("Could not read safety restrictions for student %s", student, exc_info=True)
        return []

    messages: list[str] = []
    for row in rows:
        blocks = getattr(row, "blocks_water_activity", None)
        if blocks is False:
            continue
        reason = (
            getattr(row, "reason", "")
            or getattr(row, "description", "")
            or str(row)
        )
        messages.append(
            _("Safety restriction on %(student)s: %(reason)s")
            % {"student": student, "reason": reason}
        )
    return messages


def instructor_conflicts(lesson) -> list[str]:
    """Ask the instructors module whether the assigned coach can work this slot."""
    instructor = getattr(lesson, "instructor", None)
    if instructor is None:
        return []
    start, end = lesson_start(lesson), lesson_end(lesson)
    if start is None or end is None:
        return []
    try:
        from apps.instructors import services as instructor_services
    except ImportError:
        return []

    checker = getattr(instructor_services, "check_instructor_availability", None)
    if callable(checker):
        try:
            result = checker(instructor, start, end, exclude_lesson=lesson)
        except TypeError:
            try:
                result = checker(instructor, start, end)
            except Exception:  # noqa: BLE001
                logger.warning("Instructor availability check failed", exc_info=True)
                return []
        except Exception:  # noqa: BLE001
            logger.warning("Instructor availability check failed", exc_info=True)
            return []
        if isinstance(result, (list, tuple)):
            return [str(item) for item in result]
        if result is False:
            return [
                _("%(instructor)s is not available at this time.") % {"instructor": instructor}
            ]
        return []

    is_available = getattr(instructor_services, "is_instructor_available", None)
    if callable(is_available):
        try:
            if not is_available(instructor, start, end):
                return [
                    _("%(instructor)s is not available at this time.")
                    % {"instructor": instructor}
                ]
        except Exception:  # noqa: BLE001
            logger.warning("Instructor availability check failed", exc_info=True)
    return []


def check_booking_conflicts(
    *,
    booking_type: str = Booking.BookingType.LESSON,
    lesson=None,
    camp=None,
    student=None,
    participants: int = 1,
    customer=None,
    exclude_booking=None,
) -> list[str]:
    """Return every reason this booking must not be made.

    An empty list means "go". Each message is written for a receptionist to read
    aloud to a customer standing at the desk, not for a developer.
    """
    problems: list[str] = []
    participants = max(1, int(participants or 1))

    if customer is not None and getattr(customer, "is_blacklisted", False):
        problems.append(
            _("%(customer)s is blocked from booking. A manager must clear this first.")
            % {"customer": customer}
        )

    if booking_type == Booking.BookingType.LESSON:
        problems.extend(
            _check_lesson_conflicts(
                lesson=lesson,
                student=student,
                participants=participants,
                exclude_booking=exclude_booking,
            )
        )
    elif booking_type == Booking.BookingType.CAMP:
        problems.extend(
            _check_camp_conflicts(
                camp=camp,
                student=student,
                participants=participants,
                exclude_booking=exclude_booking,
            )
        )

    problems.extend(student_restrictions(student))
    return problems


def _check_lesson_conflicts(*, lesson, student, participants, exclude_booking) -> list[str]:
    problems: list[str] = []

    if lesson is None:
        return [_("Choose the lesson this booking is for.")]

    # --- the lesson itself must be bookable --------------------------------
    status = getattr(lesson, "status", None)
    if status in UNBOOKABLE_LESSON_STATUSES:
        problems.append(
            _("%(lesson)s is %(status)s and cannot take bookings.")
            % {"lesson": lesson, "status": status}
        )
    if getattr(lesson, "is_deleted", False):
        problems.append(_("This lesson has been removed from the schedule."))

    start, end = lesson_start(lesson), lesson_end(lesson)
    now = timezone.now()
    if start is None:
        problems.append(_("This lesson has no scheduled time yet, so it cannot be booked."))
    elif start <= now:
        problems.append(
            _("%(lesson)s started at %(when)s. Book a later session.")
            % {"lesson": lesson, "when": timezone.localtime(start).strftime("%d.%m.%Y %H:%M")}
        )

    # --- seats --------------------------------------------------------------
    capacity = lesson_capacity(lesson)
    taken = seats_taken(lesson=lesson, exclude_booking=exclude_booking)
    free = max(0, capacity - taken)
    if capacity <= 0:
        problems.append(_("This lesson has no seats configured."))
    elif participants > free:
        if free == 0:
            problems.append(
                _("%(lesson)s is fully booked (%(taken)s/%(capacity)s). Offer the waiting list.")
                % {"lesson": lesson, "taken": taken, "capacity": capacity}
            )
        else:
            problems.append(
                _("Only %(free)s of %(capacity)s seats are left; %(wanted)s were requested.")
                % {"free": free, "capacity": capacity, "wanted": participants}
            )

    # --- the student --------------------------------------------------------
    if student is None:
        problems.append(_("Choose the student who will attend this lesson."))
        return problems

    duplicate = Booking.objects.filter(
        lesson=lesson, student=student, status__in=ACTIVE_BOOKING_STATUSES
    )
    if exclude_booking is not None and exclude_booking.pk:
        duplicate = duplicate.exclude(pk=exclude_booking.pk)
    existing = duplicate.first()
    if existing is not None:
        problems.append(
            _("%(student)s is already booked on this lesson (%(code)s).")
            % {"student": student, "code": existing.booking_code}
        )

    if start and end:
        for other in _student_active_bookings(student, exclude_booking, around=start):
            if other.lesson_id and other.lesson_id == getattr(lesson, "pk", None):
                continue
            if _overlaps(start, end, other.scheduled_start, other.scheduled_end):
                problems.append(
                    _(
                        "%(student)s already has booking %(code)s for %(activity)s at that time."
                    )
                    % {
                        "student": student,
                        "code": other.booking_code,
                        "activity": other.activity_label,
                    }
                )
                break

    # --- level --------------------------------------------------------------
    problems.extend(_check_level(lesson, student))

    # --- instructor ---------------------------------------------------------
    problems.extend(instructor_conflicts(lesson))

    # --- group size and supervision ratio ----------------------------------
    problems.extend(
        _check_ratio(
            lesson=lesson,
            student=student,
            participants=participants,
            taken=taken,
            exclude_booking=exclude_booking,
        )
    )
    return problems


def _check_level(lesson, student) -> list[str]:
    minimum, maximum = lesson_level_range(lesson)
    level = getattr(student, "level", None) or getattr(student, "surf_level", None)
    if not level or (not minimum and not maximum):
        return []
    labels = dict(SurfLevel.choices)
    rank = level_rank(level)
    if minimum and rank < level_rank(minimum):
        return [
            _(
                "%(student)s is at %(level)s level; this session starts at %(required)s. "
                "Choose a session that matches their level."
            )
            % {
                "student": student,
                "level": labels.get(level, level),
                "required": labels.get(minimum, minimum),
            }
        ]
    if maximum and rank > level_rank(maximum):
        return [
            _(
                "%(student)s is at %(level)s level; this session is capped at %(required)s. "
                "They would be under-challenged and take a place from someone who needs it."
            )
            % {
                "student": student,
                "level": labels.get(level, level),
                "required": labels.get(maximum, maximum),
            }
        ]
    return []


def _check_ratio(*, lesson, student, participants, taken, exclude_booking) -> list[str]:
    problems: list[str] = []
    projected = taken + participants

    roster = (
        Booking.objects.filter(lesson=lesson, status__in=ACTIVE_BOOKING_STATUSES)
        .select_related("student")
    )
    if exclude_booking is not None and exclude_booking.pk:
        roster = roster.exclude(pk=exclude_booking.pk)
    group_has_minor = is_minor(student) or any(
        is_minor(booking.student) for booking in roster if booking.student_id
    )

    limit, per_instructor, coaches = ratio_limit(lesson, includes_minor=group_has_minor)

    if coaches == 0:
        problems.append(
            _("No instructor is assigned to %(lesson)s yet, so it cannot take bookings.")
            % {"lesson": lesson}
        )
        return problems

    if projected > limit:
        if group_has_minor:
            problems.append(
                _(
                    "Under-18 groups are limited to %(per)s students per instructor. "
                    "With %(coaches)s instructor(s) this session may hold %(limit)s; "
                    "this booking would make %(projected)s."
                )
                % {
                    "per": per_instructor,
                    "coaches": coaches,
                    "limit": limit,
                    "projected": projected,
                }
            )
        else:
            problems.append(
                _(
                    "Safety ratio exceeded: %(per)s students per instructor at this level. "
                    "With %(coaches)s instructor(s) the maximum is %(limit)s; "
                    "this booking would make %(projected)s."
                )
                % {
                    "per": per_instructor,
                    "coaches": coaches,
                    "limit": limit,
                    "projected": projected,
                }
            )
    if student is not None and is_minor(student) and student_age(student) is not None:
        age = student_age(student)
        minimum_age = policy_int("bookings.minimum_age", 6)
        if age < minimum_age:
            problems.append(
                _("%(student)s is %(age)s; the school's minimum age is %(minimum)s.")
                % {"student": student, "age": age, "minimum": minimum_age}
            )
    return problems


def _check_camp_conflicts(*, camp, student, participants, exclude_booking) -> list[str]:
    problems: list[str] = []
    if camp is None:
        return [_("Choose the surf camp this booking is for.")]

    status = getattr(camp, "status", None)
    if status in {"cancelled", LessonStatus.CANCELLED}:
        problems.append(
            _("%(camp)s has been cancelled and cannot take bookings.") % {"camp": camp}
        )

    start, end = camp_window(camp)
    now = timezone.now()
    if start is None:
        problems.append(_("This surf camp has no dates yet, so it cannot be booked."))
    elif start <= now:
        problems.append(
            _("%(camp)s started on %(when)s. Book a later camp.")
            % {"camp": camp, "when": timezone.localtime(start).strftime("%d.%m.%Y")}
        )

    capacity = camp_capacity(camp)
    taken = seats_taken(camp=camp, exclude_booking=exclude_booking)
    free = max(0, capacity - taken)
    if capacity <= 0:
        problems.append(_("This surf camp has no places configured."))
    elif participants > free:
        if free == 0:
            problems.append(
                _("%(camp)s is fully booked (%(taken)s/%(capacity)s). Offer the waiting list.")
                % {"camp": camp, "taken": taken, "capacity": capacity}
            )
        else:
            problems.append(
                _("Only %(free)s of %(capacity)s places are left; %(wanted)s were requested.")
                % {"free": free, "capacity": capacity, "wanted": participants}
            )

    if student is None:
        return problems

    duplicate = Booking.objects.filter(
        surf_camp=camp, student=student, status__in=ACTIVE_BOOKING_STATUSES
    )
    if exclude_booking is not None and exclude_booking.pk:
        duplicate = duplicate.exclude(pk=exclude_booking.pk)
    existing = duplicate.first()
    if existing is not None:
        problems.append(
            _("%(student)s is already booked on this camp (%(code)s).")
            % {"student": student, "code": existing.booking_code}
        )

    if start and end:
        for other in _student_active_bookings(student, exclude_booking):
            if other.surf_camp_id and other.surf_camp_id == getattr(camp, "pk", None):
                continue
            if _overlaps(start, end, other.scheduled_start, other.scheduled_end):
                problems.append(
                    _("%(student)s already has booking %(code)s for %(activity)s in those dates.")
                    % {
                        "student": student,
                        "code": other.booking_code,
                        "activity": other.activity_label,
                    }
                )
                break

    problems.extend(_check_level(camp, student))
    return problems


# ---------------------------------------------------------------------------
# Roster synchronisation with the lessons / camps modules
# ---------------------------------------------------------------------------
def _sync_lesson_attendance(booking) -> None:
    """Create or refresh the roster row for a lesson booking."""
    model = _get_model("lessons", "LessonAttendance")
    if model is None or not booking.lesson_id or not booking.student_id:
        return
    data = _assignable(
        model,
        {
            "lesson": booking.lesson,
            "student": booking.student,
            "booking": booking,
            "participants": booking.participants,
        },
    )
    lookup = {key: data.pop(key) for key in ("lesson", "student") if key in data}
    if len(lookup) < 2:
        return
    try:
        model.objects.update_or_create(defaults=data, **lookup)
    except Exception:  # noqa: BLE001 - never lose the booking over a roster row
        logger.exception("Could not synchronise lesson attendance for %s", booking.booking_code)


def _sync_camp_participant(booking) -> None:
    model = _get_model("surf_camps", "CampParticipant")
    if model is None or not booking.surf_camp_id or not booking.student_id:
        return
    data = _assignable(
        model,
        {
            "surf_camp": booking.surf_camp,
            "camp": booking.surf_camp,
            "student": booking.student,
            "booking": booking,
        },
    )
    lookup = {}
    for key in ("surf_camp", "camp"):
        if key in data:
            lookup[key] = data.pop(key)
            break
    if "student" in data:
        lookup["student"] = data.pop("student")
    if len(lookup) < 2:
        return
    try:
        model.objects.update_or_create(defaults=data, **lookup)
    except Exception:  # noqa: BLE001
        logger.exception("Could not synchronise camp participant for %s", booking.booking_code)


def _roster_rows(booking):
    """Yield the roster rows belonging to *booking* across the other modules."""
    for app_label, model_name in (("lessons", "LessonAttendance"), ("surf_camps", "CampParticipant")):
        model = _get_model(app_label, model_name)
        if model is None or "booking" not in _concrete_field_names(model):
            continue
        try:
            yield from model.objects.filter(booking=booking)
        except Exception:  # noqa: BLE001
            logger.warning("Could not read %s rows for %s", model_name, booking.booking_code)


def _release_roster(booking) -> None:
    """Remove the roster rows so the seat is genuinely free again."""
    for row in list(_roster_rows(booking)):
        try:
            row.delete()
        except Exception:  # noqa: BLE001
            logger.exception("Could not release roster row for %s", booking.booking_code)


def _mark_roster(booking, *, status_value: str, present: bool | None = None) -> None:
    """Reflect a booking transition on the roster rows."""
    for row in list(_roster_rows(booking)):
        changed = _set_if_valid(row, "status", status_value)
        if present is not None:
            for field_name in ("is_present", "attended", "has_attended"):
                try:
                    row._meta.get_field(field_name)
                except Exception:  # noqa: BLE001, S112 - one bad row must not abort the batch  # nosec B112
                    continue
                setattr(row, field_name, present)
                changed = True
                break
        if present:
            for field_name in ("checked_in_at", "arrived_at"):
                try:
                    row._meta.get_field(field_name)
                except Exception:  # noqa: BLE001, S112 - one bad row must not abort the batch  # nosec B112
                    continue
                setattr(row, field_name, timezone.now())
                changed = True
                break
        if changed:
            try:
                row.save()
            except Exception:  # noqa: BLE001
                logger.exception("Could not update roster row for %s", booking.booking_code)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def default_unit_price(*, lesson=None, camp=None) -> Decimal:
    """Rack rate for one seat, taken from the lesson or the camp."""
    for source, names in (
        (lesson, ("price", "price_per_person", "base_price")),
        (getattr(lesson, "lesson_type", None), ("base_price", "price")),
        (camp, ("price_per_person", "price", "base_price")),
    ):
        if source is None:
            continue
        for name in names:
            value = getattr(source, name, None)
            if value not in (None, ""):
                return to_decimal(value)
    return ZERO


def cancellation_fee_for(booking, at=None) -> Decimal:
    """The fee this cancellation would attract under the school's policy.

    Free outside the notice window; a percentage of the booking total inside it;
    the full amount once the session has started.
    """
    total = to_decimal(booking.unit_price) * max(1, int(booking.participants or 1)) - to_decimal(
        booking.discount_amount
    )
    total = max(ZERO, total)
    start = booking.scheduled_start
    if start is None:
        return ZERO

    at = at or timezone.now()
    notice_hours = policy_int("bookings.free_cancellation_hours", FREE_CANCELLATION_HOURS)
    hours_left = (start - at).total_seconds() / 3600.0

    if hours_left >= notice_hours:
        return ZERO
    if hours_left <= 0:
        percent = policy_decimal("bookings.no_show_fee_percent", Decimal("100"))
    else:
        percent = policy_decimal("bookings.late_cancellation_fee_percent", Decimal("50"))
    return to_decimal(total * percent / Decimal("100"))


# ---------------------------------------------------------------------------
# Booking lifecycle
# ---------------------------------------------------------------------------
@transaction.atomic
def create_booking(
    customer,
    booking_type: str = Booking.BookingType.LESSON,
    *,
    lesson=None,
    camp=None,
    student=None,
    participants: int = 1,
    source: str = BookingSource.WALK_IN,
    user=None,
    request=None,
    unit_price=None,
    discount_amount=ZERO,
    special_requests: str = "",
    internal_notes: str = "",
    status: str = BookingStatus.PENDING,
) -> Booking:
    """Create a booking and put the student on the roster.

    Raises :class:`BookingConflictError` when any operational or safety rule
    would be broken. The lesson row is locked for the duration so two
    simultaneous sales cannot both take the last seat.
    """
    participants = max(1, int(participants or 1))

    if lesson is not None and getattr(lesson, "pk", None):
        lesson = _lock_lesson(lesson)
    if camp is not None and getattr(camp, "pk", None):
        camp = _lock_camp(camp)

    problems = check_booking_conflicts(
        booking_type=booking_type,
        lesson=lesson,
        camp=camp,
        student=student,
        participants=participants,
        customer=customer,
    )
    if problems:
        raise BookingConflictError(problems)

    price = to_decimal(unit_price) if unit_price is not None else default_unit_price(
        lesson=lesson, camp=camp
    )

    booking = Booking(
        booking_type=booking_type,
        customer=customer,
        student=student,
        lesson=lesson if booking_type == Booking.BookingType.LESSON else None,
        surf_camp=camp if booking_type == Booking.BookingType.CAMP else None,
        status=status,
        participants=participants,
        unit_price=price,
        discount_amount=to_decimal(discount_amount),
        source=source,
        booked_at=timezone.now(),
        special_requests=special_requests or "",
        internal_notes=internal_notes or "",
        created_by=user if getattr(user, "is_authenticated", False) else None,
        updated_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if status == BookingStatus.CONFIRMED:
        booking.confirmed_at = timezone.now()
    booking.recalculate_totals()
    booking.full_clean(exclude=["booking_code"])
    booking.save()

    if booking.lesson_id:
        _sync_lesson_attendance(booking)
    elif booking.surf_camp_id:
        _sync_camp_participant(booking)

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=booking,
        user=user,
        description=_("Booking %(code)s created for %(customer)s — %(activity)s (%(seats)s seat(s))")
        % {
            "code": booking.booking_code,
            "customer": customer,
            "activity": booking.activity_label,
            "seats": participants,
        },
        changes={
            "status": [None, booking.status],
            "participants": [None, participants],
            "total_amount": [None, str(booking.total_amount)],
        },
    )
    return booking


def _lock_lesson(lesson):
    """Re-read the lesson ``FOR UPDATE`` so seat maths is race-free."""
    model = lesson.__class__
    try:
        return model.objects.select_for_update().get(pk=lesson.pk)
    except Exception:  # noqa: BLE001 - SQLite/dev or an unmanaged model
        return lesson


def _lock_camp(camp):
    model = camp.__class__
    try:
        return model.objects.select_for_update().get(pk=camp.pk)
    except Exception:  # noqa: BLE001
        return camp


@transaction.atomic
def confirm_booking(booking: Booking, *, user=None, request=None) -> Booking:
    """Move a draft/pending booking to confirmed, re-checking availability."""
    if not booking.can_confirm:
        raise BookingTransitionError(
            _("Booking %(code)s is %(status)s and cannot be confirmed.")
            % {"code": booking.booking_code, "status": booking.get_status_display()}
        )

    problems = check_booking_conflicts(
        booking_type=booking.booking_type,
        lesson=booking.lesson,
        camp=booking.surf_camp,
        student=booking.student,
        participants=booking.participants,
        customer=booking.customer,
        exclude_booking=booking,
    )
    if problems:
        raise BookingConflictError(problems)

    previous = booking.status
    booking.status = BookingStatus.CONFIRMED
    booking.confirmed_at = timezone.now()
    booking.updated_by = user if getattr(user, "is_authenticated", False) else booking.updated_by
    booking.recalculate_totals()
    booking.save(
        update_fields=[
            "status",
            "confirmed_at",
            "updated_by",
            "total_amount",
            "payment_status",
            "updated_at",
        ]
    )
    _mark_roster(booking, status_value="confirmed")

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=booking,
        user=user,
        description=_("Booking %(code)s confirmed") % {"code": booking.booking_code},
        changes={"status": [previous, booking.status]},
    )
    return booking


@transaction.atomic
def check_in_booking(booking: Booking, *, user=None, request=None, force: bool = False) -> Booking:
    """Mark the customer as arrived at the beach."""
    if not booking.can_check_in:
        raise BookingTransitionError(
            _("Only confirmed bookings can be checked in; %(code)s is %(status)s.")
            % {"code": booking.booking_code, "status": booking.get_status_display()}
        )

    start = booking.scheduled_start
    if start is not None and not force:
        today = timezone.localdate()
        if timezone.localtime(start).date() != today:
            raise BookingTransitionError(
                _("%(code)s is scheduled for %(date)s, not today.")
                % {
                    "code": booking.booking_code,
                    "date": timezone.localtime(start).strftime("%d.%m.%Y"),
                }
            )

    previous = booking.status
    booking.status = BookingStatus.CHECKED_IN
    booking.updated_by = user if getattr(user, "is_authenticated", False) else booking.updated_by
    booking.save(update_fields=["status", "updated_by", "updated_at"])
    _mark_roster(booking, status_value="checked_in", present=True)

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=booking,
        user=user,
        description=_("Booking %(code)s checked in") % {"code": booking.booking_code},
        changes={"status": [previous, booking.status]},
    )
    return booking


@transaction.atomic
def complete_booking(booking: Booking, *, user=None, request=None) -> Booking:
    """Close a booking after the session has been delivered."""
    if not booking.can_complete:
        raise BookingTransitionError(
            _("Booking %(code)s is %(status)s and cannot be completed.")
            % {"code": booking.booking_code, "status": booking.get_status_display()}
        )

    previous = booking.status
    booking.status = BookingStatus.COMPLETED
    booking.updated_by = user if getattr(user, "is_authenticated", False) else booking.updated_by
    booking.recalculate_totals()
    booking.save(
        update_fields=["status", "updated_by", "total_amount", "payment_status", "updated_at"]
    )
    _mark_roster(booking, status_value="completed", present=True)

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=booking,
        user=user,
        description=_("Booking %(code)s completed") % {"code": booking.booking_code},
        changes={"status": [previous, booking.status]},
    )
    return booking


@transaction.atomic
def cancel_booking(
    booking: Booking,
    reason: str,
    user=None,
    fee=None,
    *,
    request=None,
    promote_waitlist: bool = True,
) -> Booking:
    """Cancel a booking, free the seat and offer it to the waiting list.

    ``fee`` overrides the automatic policy fee — reception needs that for
    goodwill exceptions, and the override is written to the audit log.
    """
    if not booking.can_cancel:
        raise BookingTransitionError(
            _("Booking %(code)s is %(status)s and cannot be cancelled.")
            % {"code": booking.booking_code, "status": booking.get_status_display()}
        )
    reason = (reason or "").strip()
    if not reason:
        raise BookingTransitionError(_("Record why the booking was cancelled."))

    automatic_fee = cancellation_fee_for(booking)
    applied_fee = automatic_fee if fee is None else to_decimal(fee)
    applied_fee = max(ZERO, applied_fee)

    previous_status = booking.status
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = timezone.now()
    booking.cancellation_reason = reason
    booking.cancellation_fee = applied_fee
    booking.updated_by = user if getattr(user, "is_authenticated", False) else booking.updated_by
    booking.recalculate_totals()
    booking.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancellation_reason",
            "cancellation_fee",
            "updated_by",
            "total_amount",
            "payment_status",
            "updated_at",
        ]
    )

    _release_roster(booking)

    changes = {
        "status": [previous_status, booking.status],
        "cancellation_fee": [str(automatic_fee), str(applied_fee)],
    }
    record_audit(
        request,
        action=AuditAction.BOOKING_CANCEL,
        instance=booking,
        user=user,
        description=_("Booking %(code)s cancelled — %(reason)s (fee %(fee)s)")
        % {"code": booking.booking_code, "reason": reason, "fee": applied_fee},
        changes=changes,
    )

    if promote_waitlist:
        promote_from_waitlist(
            lesson=booking.lesson, camp=booking.surf_camp, user=user, request=request
        )
    return booking


@transaction.atomic
def mark_no_show(booking: Booking, *, user=None, request=None, fee=None) -> Booking:
    """Record that the customer never turned up.

    A no-show keeps the full charge by default — the seat was held and the
    instructor waited — but the amount stays operator-tunable.
    """
    if not booking.can_mark_no_show:
        raise BookingTransitionError(
            _("Booking %(code)s is %(status)s and cannot be marked as a no-show.")
            % {"code": booking.booking_code, "status": booking.get_status_display()}
        )

    previous = booking.status
    booking.status = BookingStatus.NO_SHOW
    if fee is not None:
        booking.discount_amount = ZERO
        booking.unit_price = to_decimal(fee) / max(1, int(booking.participants or 1))
    booking.updated_by = user if getattr(user, "is_authenticated", False) else booking.updated_by
    booking.recalculate_totals()
    booking.save(
        update_fields=[
            "status",
            "unit_price",
            "discount_amount",
            "updated_by",
            "total_amount",
            "payment_status",
            "updated_at",
        ]
    )
    _mark_roster(booking, status_value="no_show", present=False)

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=booking,
        user=user,
        description=_("Booking %(code)s marked as a no-show") % {"code": booking.booking_code},
        changes={"status": [previous, booking.status]},
    )
    return booking


@transaction.atomic
def register_payment(booking: Booking, amount, *, user=None, request=None) -> Booking:
    """Record money received against a booking.

    :mod:`apps.finance` owns payments; this keeps the operational balance shown
    on the desk screens in step with them.
    """
    amount = to_decimal(amount)
    if amount <= ZERO:
        raise BookingTransitionError(_("The payment amount must be greater than zero."))

    before = to_decimal(booking.paid_amount)
    booking.paid_amount = before + amount
    booking.recalculate_totals()
    booking.save(update_fields=["paid_amount", "total_amount", "payment_status", "updated_at"])

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=booking,
        user=user,
        description=_("Payment of %(amount)s recorded against %(code)s")
        % {"amount": amount, "code": booking.booking_code},
        changes={"paid_amount": [str(before), str(booking.paid_amount)]},
    )
    return booking


# ---------------------------------------------------------------------------
# Waiting list
# ---------------------------------------------------------------------------
@transaction.atomic
def add_to_waitlist(
    customer,
    *,
    lesson=None,
    camp=None,
    student=None,
    participants: int = 1,
    note: str = "",
    user=None,
    request=None,
) -> WaitlistEntry:
    """Queue a customer for a seat that is currently sold out."""
    if lesson is None and camp is None:
        raise BookingTransitionError(_("Choose the lesson or the surf camp being waited for."))

    duplicate = WaitlistEntry.objects.filter(customer=customer, is_converted=False)
    duplicate = duplicate.filter(lesson=lesson) if lesson is not None else duplicate.filter(
        surf_camp=camp
    )
    if student is not None:
        duplicate = duplicate.filter(student=student)
    existing = duplicate.first()
    if existing is not None:
        return existing

    queryset = WaitlistEntry.objects.filter(lesson=lesson) if lesson is not None else (
        WaitlistEntry.objects.filter(surf_camp=camp)
    )
    entry = WaitlistEntry(
        lesson=lesson,
        surf_camp=camp,
        customer=customer,
        student=student,
        participants=max(1, int(participants or 1)),
        position=queryset.count() + 1,
        note=note or "",
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    entry.full_clean()
    entry.save()

    record_audit(
        request,
        action=AuditAction.BOOKING_CHANGE,
        instance=entry,
        user=user,
        description=_("%(customer)s added to the waiting list for %(target)s (position %(pos)s)")
        % {"customer": customer, "target": lesson or camp, "pos": entry.position},
    )
    return entry


@transaction.atomic
def promote_from_waitlist(*, lesson=None, camp=None, user=None, request=None) -> Booking | None:
    """Give a freed seat to the first person still waiting.

    The promoted entry becomes a **pending** booking: the seat is held, but
    reception still has to reach the customer and confirm. Entries that can no
    longer be honoured (their student is now double-booked, the level no longer
    matches) are skipped rather than silently failing.
    """
    if lesson is None and camp is None:
        return None

    queryset = WaitlistEntry.objects.filter(is_converted=False).select_related(
        "customer", "student"
    )
    queryset = queryset.filter(lesson=lesson) if lesson is not None else queryset.filter(
        surf_camp=camp
    )

    for entry in queryset.order_by("position", "requested_at")[:10]:
        booking_type = (
            Booking.BookingType.LESSON if lesson is not None else Booking.BookingType.CAMP
        )
        try:
            booking = create_booking(
                entry.customer,
                booking_type,
                lesson=lesson,
                camp=camp,
                student=entry.student,
                participants=entry.participants,
                source=BookingSource.RETURNING,
                user=user,
                request=request,
                status=BookingStatus.PENDING,
                internal_notes=_("Promoted from the waiting list (position %(pos)s).")
                % {"pos": entry.position},
            )
        except (BookingConflictError, ValidationError):
            continue

        entry.is_converted = True
        entry.converted_booking = booking
        entry.is_notified = True
        entry.notified_at = timezone.now()
        entry.save(
            update_fields=[
                "is_converted",
                "converted_booking",
                "is_notified",
                "notified_at",
                "updated_at",
            ]
        )
        record_audit(
            request,
            action=AuditAction.BOOKING_CHANGE,
            instance=booking,
            user=user,
            description=_("Waiting-list entry #%(pos)s promoted into booking %(code)s")
            % {"pos": entry.position, "code": booking.booking_code},
        )
        return booking
    return None


def waitlist_for(*, lesson=None, camp=None):
    queryset = WaitlistEntry.objects.filter(is_converted=False).select_related(
        "customer", "student"
    )
    if lesson is not None:
        return queryset.filter(lesson=lesson).order_by("position", "requested_at")
    if camp is not None:
        return queryset.filter(surf_camp=camp).order_by("position", "requested_at")
    return queryset.none()


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
def _lessons_in_window(start, end, filters: dict | None = None):
    """Lessons scheduled between *start* and *end*.

    ``lessons.Lesson`` splits the schedule across a ``DateField`` and a
    ``TimeField``. Filtering ``start_time__gte`` with a *datetime* against a
    TimeField matches nothing at all — silently, with no error — which emptied
    the whole booking calendar. So when the model has that shape the query runs
    over the date range and the exact window is applied in Python via
    :func:`lesson_start`, which combines the two fields correctly.

    Deployments whose Lesson carries a single datetime column still work through
    the fallback loop below.
    """
    model = _get_model("lessons", "Lesson")
    if model is None:
        return []
    filters = filters or {}

    def _apply_common(queryset):
        queryset = queryset.select_related(*_existing_related(model, "lesson_type", "instructor"))
        if filters.get("lesson_type"):
            queryset = _safe_filter(queryset, lesson_type_id=filters["lesson_type"])
        if filters.get("instructor"):
            queryset = _safe_filter(queryset, instructor_id=filters["instructor"])
        if filters.get("location"):
            queryset = _safe_filter(queryset, location_id=filters["location"])
        return queryset

    field_map = {f.name: f for f in model._meta.get_fields() if getattr(f, "concrete", False)}
    date_field = field_map.get("date")
    time_field = field_map.get("start_time")
    if date_field is not None and time_field is not None and (
        time_field.get_internal_type() == "TimeField"
    ):
        try:
            queryset = model.objects.filter(
                date__gte=timezone.localtime(start).date(),
                date__lte=timezone.localtime(end).date(),
            ).order_by("date", "start_time")
            rows = list(_apply_common(queryset)[:1200])
        except Exception:  # noqa: BLE001 - table not migrated yet
            logger.warning("Could not read lessons for the calendar", exc_info=True)
            return []

        # The date filter is deliberately generous at both ends; trim it to the
        # real window now that date and time can be combined.
        precise = []
        for lesson in rows:
            moment = lesson_start(lesson)
            if moment is None or (start <= moment < end):
                precise.append(lesson)
        return precise[:600]

    for field in ("start_time", "start_at", "starts_at", "scheduled_start"):
        try:
            queryset = model.objects.filter(
                **{f"{field}__gte": start, f"{field}__lt": end}
            ).order_by(field)
            str(queryset.query)
        except (FieldError, ValueError, TypeError):
            continue

        queryset = _apply_common(queryset)
        try:
            return list(queryset[:600])
        except Exception:  # noqa: BLE001 - table not migrated yet
            logger.warning("Could not read lessons for the calendar", exc_info=True)
            return []
    return []


def _existing_related(model, *names) -> list[str]:
    fields = _concrete_field_names(model)
    return [name for name in names if name in fields]


def _camps_in_window(start, end, filters: dict | None = None):
    model = _get_model("surf_camps", "SurfCamp")
    if model is None:
        return []
    start_date = timezone.localtime(start).date()
    end_date = timezone.localtime(end).date()
    try:
        queryset = model.objects.filter(start_date__lt=end_date, end_date__gte=start_date)
        str(queryset.query)
    except (FieldError, ValueError, TypeError):
        return []
    try:
        return list(queryset.order_by("start_date")[:200])
    except Exception:  # noqa: BLE001
        logger.warning("Could not read surf camps for the calendar", exc_info=True)
        return []


def _fill_state(booked: int, capacity: int) -> str:
    """How full a session is — drives the dot colour on the calendar."""
    if capacity <= 0:
        return "unknown"
    if booked > capacity:
        return "over"
    if booked >= capacity:
        return "full"
    if capacity - booked <= 2:
        return "tight"
    return "free"


def booking_calendar_events(start, end, filters: dict | None = None) -> list[dict]:
    """Return the schedule between *start* and *end* as calendar events.

    One event per lesson (with its seat count), plus one band per surf camp.
    Lessons are the unit the calendar draws because that is what has a colour, a
    capacity and an instructor — bookings hang off them.
    """
    filters = filters or {}
    start = as_aware(start)
    end = as_aware(end)
    if start is None or end is None:
        return []

    events: list[dict] = []
    lessons = _lessons_in_window(start, end, filters)
    taken = seats_map(lessons)

    status_filter = filters.get("status") or ""
    only_available = bool(filters.get("only_available"))
    query = (filters.get("q") or "").strip().lower()

    for lesson in lessons:
        lesson_id = lesson.pk
        booked = taken.get(lesson_id, 0)
        capacity = lesson_capacity(lesson)
        lesson_status = str(getattr(lesson, "status", "") or "scheduled")
        if status_filter and lesson_status != status_filter:
            continue
        if only_available and booked >= capacity:
            continue
        title = str(lesson)
        if query and query not in title.lower():
            continue

        event_start = lesson_start(lesson)
        event_end = lesson_end(lesson)
        if event_start is None:
            continue
        local_start = timezone.localtime(event_start)
        local_end = timezone.localtime(event_end) if event_end else local_start

        events.append(
            {
                "id": f"lesson-{lesson_id}",
                "kind": "lesson",
                "object_id": lesson_id,
                "title": title,
                "start": event_start,
                "end": event_end,
                "date": local_start.date(),
                "start_label": local_start.strftime("%H:%M"),
                "end_label": local_end.strftime("%H:%M"),
                "start_minutes": local_start.hour * 60 + local_start.minute,
                "duration_minutes": max(
                    30, int((local_end - local_start).total_seconds() // 60) or 60
                ),
                "colour": lesson_colour(lesson),
                "status": lesson_status,
                "booked": booked,
                "capacity": capacity,
                "capacity_label": f"{booked}/{capacity}",
                "is_full": capacity > 0 and booked >= capacity,
                "is_overbooked": capacity > 0 and booked > capacity,
                "free_seats": max(0, capacity - booked),
                "fill_state": _fill_state(booked, capacity),
                "instructor": str(getattr(lesson, "instructor", "") or ""),
                "has_instructor": bool(getattr(lesson, "instructor_id", None)),
                "object": lesson,
            }
        )

    if not filters.get("lesson_type") and not filters.get("instructor"):
        camps = _camps_in_window(start, end, filters)
        camp_taken = camp_seats_map(camps)
        for camp in camps:
            camp_start, camp_end = camp_window(camp)
            if camp_start is None:
                continue
            booked = camp_taken.get(camp.pk, 0)
            capacity = camp_capacity(camp)
            events.append(
                {
                    "id": f"camp-{camp.pk}",
                    "kind": "camp",
                    "object_id": camp.pk,
                    "title": str(camp),
                    "start": camp_start,
                    "end": camp_end,
                    "date": timezone.localtime(camp_start).date(),
                    "start_label": timezone.localtime(camp_start).strftime("%d.%m"),
                    "end_label": timezone.localtime(camp_end).strftime("%d.%m")
                    if camp_end
                    else "",
                    "start_minutes": 0,
                    "duration_minutes": 0,
                    "colour": "#8b5cf6",
                    "status": str(getattr(camp, "status", "") or "scheduled"),
                    "booked": booked,
                    "capacity": capacity,
                    "capacity_label": f"{booked}/{capacity}",
                    "is_full": capacity > 0 and booked >= capacity,
                    "is_overbooked": capacity > 0 and booked > capacity,
                    "free_seats": max(0, capacity - booked),
                    "fill_state": _fill_state(booked, capacity),
                    "instructor": "",
                    "has_instructor": True,
                    "object": camp,
                }
            )

    events.sort(key=lambda item: (item["start"], item["title"]))
    return events


def daily_schedule(day: date_cls, filters: dict | None = None) -> dict:
    """Everything the beach team needs for one day, in one structure."""
    if isinstance(day, datetime):
        day = timezone.localtime(as_aware(day)).date()
    start = as_aware(day)
    end = as_end_of_day(day)

    events = booking_calendar_events(start, end, filters)
    lesson_ids = [event["object_id"] for event in events if event["kind"] == "lesson"]
    camp_ids = [event["object_id"] for event in events if event["kind"] == "camp"]

    bookings = list(
        Booking.objects.filter(
            Q(lesson_id__in=lesson_ids) | Q(surf_camp_id__in=camp_ids)
        )
        .exclude(status=BookingStatus.CANCELLED)
        .select_related("customer", "student")
        .order_by("booking_code")
    )

    by_lesson: dict[int, list[Booking]] = {}
    by_camp: dict[int, list[Booking]] = {}
    for booking in bookings:
        if booking.lesson_id:
            by_lesson.setdefault(booking.lesson_id, []).append(booking)
        elif booking.surf_camp_id:
            by_camp.setdefault(booking.surf_camp_id, []).append(booking)

    alerts: list[str] = []
    participants = 0
    checked_in = 0
    expected_revenue = ZERO
    outstanding = ZERO

    for event in events:
        rows = (by_lesson if event["kind"] == "lesson" else by_camp).get(
            event["object_id"], []
        )
        event["bookings"] = rows
        event["waitlist_count"] = (
            waitlist_for(lesson=event["object"]).count()
            if event["kind"] == "lesson"
            else waitlist_for(camp=event["object"]).count()
        )
        event["pending_count"] = sum(1 for b in rows if b.status == BookingStatus.PENDING)
        event["checked_in_count"] = sum(
            1 for b in rows if b.status == BookingStatus.CHECKED_IN
        )
        event["unpaid_count"] = sum(
            1
            for b in rows
            if b.payment_status in {PaymentStatus.UNPAID, PaymentStatus.PARTIAL, PaymentStatus.OVERDUE}
        )

        participants += sum(b.participants for b in rows if b.is_active)
        checked_in += event["checked_in_count"]
        for booking in rows:
            expected_revenue += to_decimal(booking.total_amount)
            outstanding += max(ZERO, booking.balance_due)

        if event["kind"] == "lesson" and not event["has_instructor"]:
            alerts.append(
                _("%(lesson)s has no instructor assigned.") % {"lesson": event["title"]}
            )
        if event["is_overbooked"]:
            alerts.append(
                _("%(lesson)s is overbooked (%(label)s).")
                % {"lesson": event["title"], "label": event["capacity_label"]}
            )
        if event["pending_count"]:
            alerts.append(
                _("%(lesson)s has %(n)s booking(s) still awaiting confirmation.")
                % {"lesson": event["title"], "n": event["pending_count"]}
            )
        if event["waitlist_count"] and event["free_seats"]:
            alerts.append(
                _("%(lesson)s has %(n)s people waiting and %(free)s free seat(s).")
                % {
                    "lesson": event["title"],
                    "n": event["waitlist_count"],
                    "free": event["free_seats"],
                }
            )

    return {
        "date": day,
        "events": events,
        "lessons": [event for event in events if event["kind"] == "lesson"],
        "camps": [event for event in events if event["kind"] == "camp"],
        "bookings": bookings,
        "alerts": alerts,
        "totals": {
            "sessions": len(events),
            "participants": participants,
            "checked_in": checked_in,
            "expected_revenue": expected_revenue,
            "outstanding": outstanding,
        },
    }


# ---------------------------------------------------------------------------
# Calendar grid construction
# ---------------------------------------------------------------------------
#: First and last hour drawn on the week/day time grid.
GRID_START_HOUR = 6
GRID_END_HOUR = 22
#: Pixels per hour — the templates use this to place events.
PIXELS_PER_HOUR = 56


def period_bounds(view: str, anchor: date_cls, week_start: int = 0) -> tuple[date_cls, date_cls]:
    """First and last **date** covered by a calendar view."""
    if view == "day":
        return anchor, anchor
    if view == "week":
        offset = (anchor.weekday() - week_start) % 7
        first = anchor - timedelta(days=offset)
        return first, first + timedelta(days=6)
    # month: whole weeks so the grid is rectangular
    first_of_month = anchor.replace(day=1)
    if first_of_month.month == 12:
        next_month = first_of_month.replace(year=first_of_month.year + 1, month=1)
    else:
        next_month = first_of_month.replace(month=first_of_month.month + 1)
    last_of_month = next_month - timedelta(days=1)
    grid_start = first_of_month - timedelta(days=(first_of_month.weekday() - week_start) % 7)
    trailing = (6 - ((last_of_month.weekday() - week_start) % 7))
    grid_end = last_of_month + timedelta(days=trailing)
    return grid_start, grid_end


def shift_period(view: str, anchor: date_cls, direction: int) -> date_cls:
    """The anchor date one period earlier (-1) or later (+1)."""
    if view == "day":
        return anchor + timedelta(days=direction)
    if view == "week":
        return anchor + timedelta(days=7 * direction)
    if direction < 0:
        first = anchor.replace(day=1)
        return (first - timedelta(days=1)).replace(day=1)
    if anchor.month == 12:
        return anchor.replace(year=anchor.year + 1, month=1, day=1)
    return anchor.replace(month=anchor.month + 1, day=1)


def build_calendar(view: str, anchor: date_cls, filters: dict | None = None) -> dict:
    """Assemble the data the calendar templates render.

    Month → a rectangular grid of weeks. Week/day → a time grid with events
    positioned by minute, so a 09:00 lesson visibly sits above an 11:00 one.
    """
    view = view if view in {"month", "week", "day"} else "month"
    grid_start, grid_end = period_bounds(view, anchor)
    events = booking_calendar_events(as_aware(grid_start), as_end_of_day(grid_end), filters)

    by_date: dict[date_cls, list[dict]] = {}
    for event in events:
        by_date.setdefault(event["date"], []).append(event)
        # A camp spans days: show it on every day it covers inside the window.
        if event["kind"] == "camp" and event["end"]:
            cursor = max(event["date"], grid_start)
            last = min(timezone.localtime(event["end"]).date(), grid_end)
            while cursor <= last:
                if cursor != event["date"]:
                    by_date.setdefault(cursor, []).append(event)
                cursor += timedelta(days=1)

    today = timezone.localdate()
    context: dict = {
        "view": view,
        "anchor": anchor,
        "grid_start": grid_start,
        "grid_end": grid_end,
        "today": today,
        "events": events,
        "hours": list(range(GRID_START_HOUR, GRID_END_HOUR)),
        "pixels_per_hour": PIXELS_PER_HOUR,
        "grid_height": (GRID_END_HOUR - GRID_START_HOUR) * PIXELS_PER_HOUR,
        "previous": shift_period(view, anchor, -1),
        "next": shift_period(view, anchor, 1),
    }

    if view == "month":
        weeks: list[list[dict]] = []
        cursor = grid_start
        while cursor <= grid_end:
            week: list[dict] = []
            for _index in range(7):
                day_events = sorted(by_date.get(cursor, []), key=lambda item: item["start"])
                week.append(
                    {
                        "date": cursor,
                        "in_period": cursor.month == anchor.month,
                        "is_today": cursor == today,
                        "is_past": cursor < today,
                        "events": day_events[:4],
                        "extra": max(0, len(day_events) - 4),
                        "total_booked": sum(item["booked"] for item in day_events),
                    }
                )
                cursor += timedelta(days=1)
            weeks.append(week)
        context["weeks"] = weeks
        return context

    days: list[dict] = []
    cursor = grid_start
    while cursor <= grid_end:
        positioned = []
        for event in sorted(by_date.get(cursor, []), key=lambda item: item["start"]):
            if event["kind"] == "camp":
                positioned.append({**event, "top": 0, "height": 0, "all_day": True})
                continue
            minutes_from_grid = event["start_minutes"] - GRID_START_HOUR * 60
            top = max(0, round(minutes_from_grid * PIXELS_PER_HOUR / 60))
            height = max(28, round(event["duration_minutes"] * PIXELS_PER_HOUR / 60))
            positioned.append({**event, "top": top, "height": height, "all_day": False})
        days.append(
            {
                "date": cursor,
                "in_period": True,
                "is_today": cursor == today,
                "is_past": cursor < today,
                "events": [item for item in positioned if not item["all_day"]],
                "all_day_events": [item for item in positioned if item["all_day"]],
            }
        )
        cursor += timedelta(days=1)
    context["days"] = days
    if view == "day":
        context["schedule"] = daily_schedule(anchor, filters)
    return context


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
def booking_timeline(booking: Booking) -> list[dict]:
    """The life story of a booking, from the audit log plus its own timestamps."""
    from django.contrib.contenttypes.models import ContentType

    from apps.audit.models import AuditLog

    entries: list[dict] = [
        {
            "at": booking.booked_at,
            "label": _("Booked"),
            "detail": _("Created via %(source)s") % {"source": booking.get_source_display()},
            "icon": "calendar-days",
            "colour": "slate",
        }
    ]
    if booking.confirmed_at:
        entries.append(
            {
                "at": booking.confirmed_at,
                "label": _("Confirmed"),
                "detail": "",
                "icon": "circle-check",
                "colour": "sky",
            }
        )
    if booking.cancelled_at:
        entries.append(
            {
                "at": booking.cancelled_at,
                "label": _("Cancelled"),
                "detail": booking.cancellation_reason,
                "icon": "circle-x",
                "colour": "rose",
            }
        )

    try:
        content_type = ContentType.objects.get_for_model(Booking)
        logs = (
            AuditLog.objects.filter(content_type=content_type, object_id=str(booking.pk))
            .select_related("user")
            .order_by("created_at")[:60]
        )
        for log in logs:
            entries.append(
                {
                    "at": log.created_at,
                    "label": log.get_action_display(),
                    "detail": log.description,
                    "user": log.username,
                    "changes": log.changes,
                    "icon": "history",
                    "colour": "slate",
                }
            )
    except Exception:  # noqa: BLE001 - the timeline is informational
        logger.warning("Could not read the audit trail for %s", booking.booking_code)

    entries.sort(key=lambda item: item["at"] or timezone.now())
    return entries


def booking_summary(queryset) -> dict:
    """Headline numbers for the list screen."""
    aggregate = queryset.aggregate(
        seats=Coalesce(Sum("participants"), Value(0), output_field=IntegerField()),
        outstanding=Coalesce(
            Sum("total_amount") - Sum("paid_amount"), Value(ZERO), output_field=DecimalField()
        ),
    )
    totals = queryset.values("status").annotate(count=Count("id"))
    by_status = {row["status"]: row["count"] for row in totals}
    return {
        "seats": aggregate["seats"] or 0,
        "outstanding": to_decimal(aggregate["outstanding"]),
        "by_status": by_status,
        "pending": by_status.get(BookingStatus.PENDING, 0),
        "confirmed": by_status.get(BookingStatus.CONFIRMED, 0),
        "checked_in": by_status.get(BookingStatus.CHECKED_IN, 0),
        "cancelled": by_status.get(BookingStatus.CANCELLED, 0),
    }


def available_lessons(*, search: str = "", day: date_cls | None = None, limit: int = 25):
    """Lessons that still have a free seat — powers the booking form picker."""
    now = timezone.now()
    start = max(as_aware(day), now) if day else now
    end = as_end_of_day(day) if day else now + timedelta(days=60)
    if end <= start:
        return []
    lessons = _lessons_in_window(start, end, {})
    taken = seats_map(lessons)

    results = []
    needle = (search or "").strip().lower()
    for lesson in lessons:
        capacity = lesson_capacity(lesson)
        booked = taken.get(lesson.pk, 0)
        if capacity <= 0 or booked >= capacity:
            continue
        if str(getattr(lesson, "status", "")) in UNBOOKABLE_LESSON_STATUSES:
            continue
        label = str(lesson)
        if needle and needle not in label.lower():
            continue
        local_start = timezone.localtime(lesson_start(lesson))
        results.append(
            {
                "id": lesson.pk,
                "label": label,
                "when": local_start.strftime("%d.%m.%Y %H:%M"),
                "date": local_start.date(),
                "free": capacity - booked,
                "capacity": capacity,
                "booked": booked,
                "capacity_label": f"{booked}/{capacity}",
                "colour": lesson_colour(lesson),
                "instructor": str(getattr(lesson, "instructor", "") or ""),
                "price": default_unit_price(lesson=lesson),
                "object": lesson,
            }
        )
        if len(results) >= limit:
            break
    return results


def search_customers(term: str, limit: int = 12):
    """Customer lookup for the booking form's HTMX search box."""
    model = _get_model("customers", "Customer")
    term = (term or "").strip()
    if model is None or len(term) < 2:
        return []
    condition = Q()
    for field in ("first_name", "last_name", "email", "phone", "customer_code"):
        if field in _concrete_field_names(model):
            condition |= Q(**{f"{field}__icontains": term})
    if not condition:
        return []
    try:
        return list(model.objects.filter(condition)[:limit])
    except Exception:  # noqa: BLE001
        logger.warning("Customer search failed", exc_info=True)
        return []


def students_for_customer(customer, limit: int = 25):
    """Students this customer books for (themselves, their children, a group)."""
    model = _get_model("students", "Student")
    if model is None or customer is None:
        return []
    try:
        queryset = model.objects.all()
        if "customer" in _concrete_field_names(model):
            queryset = queryset.filter(customer=customer)
        return list(queryset[:limit])
    except Exception:  # noqa: BLE001
        logger.warning("Student lookup failed", exc_info=True)
        return []
