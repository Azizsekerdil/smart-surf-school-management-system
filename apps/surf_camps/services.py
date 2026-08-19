"""Business rules for surf camps.

Everything that decides something lives here: whether a student may join a camp,
what the default programme looks like, what a camp is worth, who is on site on a
given day, and whether the school has enough instructors in the water.

Views orchestrate, models validate, services decide.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import (
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    SurfLevel,
)

from .models import (
    ACTIVE_PARTICIPANT_STATUSES,
    CLOSED_CAMP_STATUSES,
    ActivityType,
    CampActivity,
    CampDay,
    CampParticipant,
    CampStatus,
    ParticipantStatus,
    RoomType,
    SurfCamp,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)

#: Age below which the stricter supervision ratio applies.
MINOR_AGE = 18


# ---------------------------------------------------------------------------
# Tolerant readers for data owned by other modules
# ---------------------------------------------------------------------------
def student_level(student) -> str | None:
    """Return a student's surf level.

    The students module owns the field; this reads whichever of the accepted
    names it exposes so a camp registration never crashes on a naming
    difference. Returns ``None`` when the student carries no level yet, in which
    case the level gate is skipped rather than guessed.
    """
    for attribute in ("current_level", "level", "surf_level", "skill_level"):
        value = getattr(student, attribute, None)
        if value:
            return str(value)
    return None


def student_age(student, on_day: date | None = None) -> int | None:
    """Return the student's age in whole years, or ``None`` when unknown."""
    age = getattr(student, "age", None)
    if isinstance(age, int):
        return age

    born = None
    for attribute in ("date_of_birth", "birth_date", "birthday"):
        candidate = getattr(student, attribute, None)
        if isinstance(candidate, datetime):
            candidate = candidate.date()
        if isinstance(candidate, date):
            born = candidate
            break
    if born is None:
        return None

    today = on_day or timezone.localdate()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def is_minor(student) -> bool:
    age = student_age(student)
    return age is not None and age < MINOR_AGE


# ---------------------------------------------------------------------------
# Camp lifecycle
# ---------------------------------------------------------------------------
@transaction.atomic
def create_camp_with_days(camp: SurfCamp) -> list[CampDay]:
    """Ensure one :class:`CampDay` exists for every date the camp spans.

    Safe to call again after the dates change: missing days are created,
    day numbers are renumbered, and days that fell outside the new range are
    removed only when they carry no programme (an emptied day is disposable, a
    planned one is not — that would silently destroy a schedule).
    """
    if not camp.start_date or not camp.end_date:
        raise ValidationError(_("The camp needs a start and an end date before days are built."))

    wanted = camp.date_list()
    existing = {day.date: day for day in camp.days.all()}
    created: list[CampDay] = []

    for index, day_date in enumerate(wanted, start=1):
        day = existing.get(day_date)
        if day is None:
            day = CampDay(
                camp=camp,
                date=day_date,
                day_number=index,
                created_by=camp.updated_by or camp.created_by,
                updated_by=camp.updated_by or camp.created_by,
            )
            day.full_clean(exclude=["camp"])
            day.save()
            created.append(day)
        elif day.day_number != index:
            day.day_number = index
            day.save(update_fields=["day_number", "updated_at"])

    stale = [day for day_date, day in existing.items() if day_date not in set(wanted)]
    for day in stale:
        if day.activities.exists():
            logger.warning(
                "Camp %s: day %s is outside the camp dates but keeps its programme.",
                camp.code,
                day.date,
            )
            continue
        day.delete()

    return created


@transaction.atomic
def publish_camp(camp: SurfCamp, request=None) -> SurfCamp:
    """Move a camp from draft to published after checking it is sellable."""
    problems: list[str] = []
    if camp.status == CampStatus.CANCELLED:
        problems.append(_("A cancelled camp cannot be published."))
    if camp.status == CampStatus.COMPLETED:
        problems.append(_("A completed camp cannot be published."))
    if (camp.price or ZERO) <= ZERO:
        problems.append(_("Set a price before publishing the camp."))
    if not camp.capacity:
        problems.append(_("Set a capacity before publishing the camp."))
    if camp.end_date and camp.end_date < timezone.localdate():
        problems.append(_("This camp is already in the past."))
    if problems:
        raise ValidationError(problems)

    create_camp_with_days(camp)
    camp.status = CampStatus.FULL if camp.is_full else CampStatus.PUBLISHED
    camp.save(update_fields=["status", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=camp,
        description=_("Camp %(code)s published") % {"code": camp.code},
        changes={"status": [CampStatus.DRAFT, camp.status]},
    )
    return camp


@transaction.atomic
def cancel_camp(camp: SurfCamp, reason: str = "", request=None) -> SurfCamp:
    """Call the camp off and cancel every place on it.

    Participants keep their rows so the money already taken stays traceable;
    only their status changes.
    """
    if camp.status == CampStatus.CANCELLED:
        raise ValidationError(_("This camp is already cancelled."))
    if camp.status == CampStatus.COMPLETED:
        raise ValidationError(_("A completed camp cannot be cancelled."))

    previous = camp.status
    affected = list(camp.active_participants())
    for participant in affected:
        participant.status = ParticipantStatus.CANCELLED
        participant.cancellation_reason = (reason or str(_("Camp cancelled")))[:200]
        participant.save(update_fields=["status", "cancellation_reason", "updated_at"])

    camp.status = CampStatus.CANCELLED
    camp.save(update_fields=["status", "updated_at"])

    record_audit(
        request,
        action=AuditAction.BOOKING_CANCEL,
        instance=camp,
        description=_("Camp %(code)s cancelled (%(count)s places released): %(reason)s")
        % {"code": camp.code, "count": len(affected), "reason": reason or "—"},
        changes={"status": [previous, CampStatus.CANCELLED]},
    )
    return camp


def refresh_camp_status(camp: SurfCamp) -> SurfCamp:
    """Keep the camp status in step with its dates and occupancy.

    Draft and cancelled camps are never touched automatically — those are
    deliberate human decisions.
    """
    if camp.status in (CampStatus.DRAFT, CampStatus.CANCELLED):
        return camp

    today = timezone.localdate()
    target = camp.status

    if camp.end_date and camp.end_date < today:
        target = CampStatus.COMPLETED
    elif camp.start_date and camp.end_date and camp.start_date <= today <= camp.end_date:
        target = CampStatus.RUNNING
    elif camp.is_full:
        target = CampStatus.FULL
    else:
        target = CampStatus.PUBLISHED

    if target != camp.status:
        camp.status = target
        camp.save(update_fields=["status", "updated_at"])
    return camp


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------
@transaction.atomic
def add_participant(
    camp: SurfCamp,
    student,
    booking=None,
    request=None,
    **details,
) -> CampParticipant:
    """Register *student* on *camp*.

    Raises :class:`~django.core.exceptions.ValidationError` when the camp is
    closed, already full, the student is already on it, or the student's level
    is outside the camp's advertised range. A previously cancelled place on the
    same camp is reinstated rather than duplicated, which keeps the
    one-place-per-student constraint intact.
    """
    if camp.status in CLOSED_CAMP_STATUSES:
        raise ValidationError(
            _("Camp %(code)s is %(status)s and cannot take registrations.")
            % {"code": camp.code, "status": camp.get_status_display().lower()}
        )
    if not camp.is_active:
        raise ValidationError(_("This camp is archived and cannot take registrations."))
    if camp.end_date and camp.end_date < timezone.localdate():
        raise ValidationError(_("This camp has already finished."))

    existing = CampParticipant.objects.filter(camp=camp, student=student).first()
    if existing and existing.status in ACTIVE_PARTICIPANT_STATUSES:
        raise ValidationError(
            _("%(student)s is already registered on this camp.") % {"student": student}
        )

    if camp.available_places <= 0:
        raise ValidationError(
            _("Camp %(code)s is full (%(capacity)s places). Add capacity or use the waitlist.")
            % {"code": camp.code, "capacity": camp.capacity}
        )

    level = student_level(student)
    if level and not camp.accepts_level(level):
        raise ValidationError(
            _("This camp is for %(range)s. %(student)s is registered as %(level)s.")
            % {
                "range": camp.level_label,
                "student": student,
                "level": _level_label(level),
            }
        )

    allowed_fields = {
        "room_number",
        "room_type",
        "roommate_preference",
        "arrival_datetime",
        "departure_datetime",
        "arrival_flight",
        "departure_flight",
        "needs_transfer",
        "dietary_requirements",
        "medical_notes",
        "t_shirt_size",
        "amount_paid",
        "deposit_paid",
    }
    payload = {key: value for key, value in details.items() if key in allowed_fields}

    actor = getattr(request, "user", None)
    actor = actor if getattr(actor, "is_authenticated", False) else None

    if existing is not None:
        participant = existing
        participant.status = ParticipantStatus.REGISTERED
        participant.cancellation_reason = ""
        participant.booking = booking or participant.booking
        for key, value in payload.items():
            setattr(participant, key, value)
    else:
        participant = CampParticipant(camp=camp, student=student, booking=booking, **payload)
        participant.created_by = actor

    participant.updated_by = actor
    participant.full_clean(exclude=["camp", "student", "booking", "created_by", "updated_by"])
    participant.save()

    refresh_camp_status(camp)

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=participant,
        description=_("%(student)s registered on camp %(code)s")
        % {"student": student, "code": camp.code},
    )
    return participant


@transaction.atomic
def remove_participant(participant: CampParticipant, reason: str = "", request=None) -> CampParticipant:
    """Cancel a place and free it for someone else.

    The row is kept (never hard-deleted) because money and history hang off it.
    """
    if participant.status == ParticipantStatus.CANCELLED:
        raise ValidationError(_("This place is already cancelled."))

    previous = participant.status
    participant.status = ParticipantStatus.CANCELLED
    participant.cancellation_reason = (reason or "")[:200]
    participant.save(update_fields=["status", "cancellation_reason", "updated_at"])

    refresh_camp_status(participant.camp)

    record_audit(
        request,
        action=AuditAction.BOOKING_CANCEL,
        instance=participant,
        description=_("%(student)s removed from camp %(code)s: %(reason)s")
        % {
            "student": participant.student,
            "code": participant.camp.code,
            "reason": reason or "—",
        },
        changes={"status": [previous, ParticipantStatus.CANCELLED]},
    )
    return participant


@transaction.atomic
def set_participant_status(
    participant: CampParticipant, status: str, request=None
) -> CampParticipant:
    """Move a participant along the arrival → departure flow.

    Check-in is refused for a cancelled place and for a camp that has not
    started, so an accidental early check-in cannot hide a real no-show.
    """
    if status not in ParticipantStatus.values:
        raise ValidationError(_("Unknown participant status."))
    if status == ParticipantStatus.CANCELLED:
        return remove_participant(participant, request=request)

    if participant.status == ParticipantStatus.CANCELLED:
        raise ValidationError(
            _("This place is cancelled. Register the student again to reinstate it.")
        )

    camp = participant.camp
    today = timezone.localdate()
    if status == ParticipantStatus.ARRIVED and camp.start_date and today < camp.start_date:
        raise ValidationError(
            _("The camp starts on %(date)s — nobody can be checked in yet.")
            % {"date": camp.start_date}
        )
    if status == ParticipantStatus.DEPARTED and participant.status != ParticipantStatus.ARRIVED:
        raise ValidationError(_("Only a checked-in participant can be checked out."))

    previous = participant.status
    participant.status = status
    participant.save(update_fields=["status", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=participant,
        description=_("%(student)s on camp %(code)s is now %(status)s")
        % {
            "student": participant.student,
            "code": camp.code,
            "status": participant.get_status_display(),
        },
        changes={"status": [previous, status]},
    )
    return participant


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ActivityTemplate:
    start: time
    end: time
    title: str
    activity_type: str
    with_instructor: bool = False


def _programme_for_day(
    camp: SurfCamp, day: CampDay, position: str, index: int
) -> list[ActivityTemplate]:
    """Return the default activity list for one day.

    *position* is ``"first"``, ``"last"``, ``"only"`` or ``"middle"``. Arrival
    and departure days are deliberately light: people are travelling.
    """
    meals = camp.includes_meals
    transfer = camp.includes_transfer

    arrival = ActivityTemplate(
        time(14, 0),
        time(15, 30),
        _("Airport transfer and check-in") if transfer else _("Arrival and check-in"),
        ActivityType.TRANSFER if transfer else ActivityType.OTHER,
    )
    departure = ActivityTemplate(
        time(11, 0),
        time(12, 30),
        _("Check-out and airport transfer") if transfer else _("Check-out"),
        ActivityType.TRANSFER if transfer else ActivityType.OTHER,
    )
    briefing = ActivityTemplate(
        time(16, 0), time(17, 0), _("Welcome briefing and safety talk"), ActivityType.THEORY, True
    )
    welcome_dinner = ActivityTemplate(
        time(19, 30), time(21, 0), _("Welcome dinner"), ActivityType.SOCIAL
    )

    if position == "only":
        plan = [
            ActivityTemplate(
                time(9, 0), time(9, 45), _("Welcome and safety briefing"), ActivityType.THEORY, True
            ),
            ActivityTemplate(
                time(10, 0), time(12, 0), _("Surf lesson"), ActivityType.SURF_LESSON, True
            ),
        ]
        if meals:
            plan.append(ActivityTemplate(time(12, 30), time(13, 30), _("Lunch"), ActivityType.MEAL))
        plan.append(
            ActivityTemplate(
                time(14, 30), time(16, 30), _("Afternoon surf session"), ActivityType.SURF_LESSON, True
            )
        )
        return plan

    if position == "first":
        plan = [arrival, briefing]
        if meals:
            plan.append(welcome_dinner)
        return plan

    if position == "last":
        plan: list[ActivityTemplate] = []
        if meals:
            plan.append(ActivityTemplate(time(8, 0), time(9, 0), _("Breakfast"), ActivityType.MEAL))
        plan.append(
            ActivityTemplate(
                time(9, 15), time(10, 45), _("Farewell surf session"), ActivityType.SURF_LESSON, True
            )
        )
        plan.append(departure)
        return plan

    # A normal camp day.
    plan = [
        ActivityTemplate(time(7, 30), time(8, 15), _("Sunrise yoga"), ActivityType.YOGA),
    ]
    if meals:
        plan.append(ActivityTemplate(time(8, 30), time(9, 15), _("Breakfast"), ActivityType.MEAL))
    plan.append(
        ActivityTemplate(
            time(9, 30), time(11, 30), _("Morning surf lesson"), ActivityType.SURF_LESSON, True
        )
    )
    if meals:
        plan.append(ActivityTemplate(time(12, 30), time(13, 30), _("Lunch"), ActivityType.MEAL))
    if index % 2 == 0:
        plan.append(
            ActivityTemplate(
                time(14, 0), time(15, 0), _("Video analysis"), ActivityType.VIDEO_ANALYSIS, True
            )
        )
    else:
        plan.append(
            ActivityTemplate(
                time(14, 0), time(15, 0), _("Theory: waves and safety"), ActivityType.THEORY, True
            )
        )
    plan.append(
        ActivityTemplate(
            time(15, 30), time(17, 30), _("Afternoon surf session"), ActivityType.SURF_LESSON, True
        )
    )
    plan.append(ActivityTemplate(time(17, 30), time(19, 0), _("Free time"), ActivityType.FREE_TIME))
    if meals:
        plan.append(ActivityTemplate(time(19, 30), time(20, 30), _("Dinner"), ActivityType.MEAL))
    return plan


@transaction.atomic
def generate_default_programme(camp: SurfCamp, replace: bool = False, request=None) -> int:
    """Fill every camp day with a sensible default schedule.

    Days that already carry activities are left alone unless *replace* is set,
    so regenerating never destroys a schedule someone has edited.
    Returns the number of activities created.
    """
    create_camp_with_days(camp)

    days = list(camp.days.all().order_by("date"))
    if not days:
        return 0

    total = len(days)
    created = 0
    for index, day in enumerate(days):
        if day.activities.exists():
            if not replace:
                continue
            day.activities.all().delete()

        if total == 1:
            position = "only"
        elif index == 0:
            position = "first"
        elif index == total - 1:
            position = "last"
        else:
            position = "middle"

        if not day.title:
            day.title = {
                "only": _("Camp day"),
                "first": _("Arrival day"),
                "last": _("Departure day"),
            }.get(position, _("Day %(n)s") % {"n": day.day_number})
            day.save(update_fields=["title", "updated_at"])

        for template in _programme_for_day(camp, day, position, index):
            CampActivity.objects.create(
                camp_day=day,
                start_time=template.start,
                end_time=template.end,
                title=str(template.title)[:150],
                activity_type=template.activity_type,
                instructor=camp.lead_instructor if template.with_instructor else None,
                location=str(day.effective_spot) if template.activity_type in
                (ActivityType.SURF_LESSON,) and day.effective_spot else "",
                created_by=camp.updated_by or camp.created_by,
                updated_by=camp.updated_by or camp.created_by,
            )
            created += 1

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=camp,
        description=_("Default programme generated for camp %(code)s (%(count)s activities)")
        % {"code": camp.code, "count": created},
    )
    return created


def activity_conflicts(activity: CampActivity) -> list[CampActivity]:
    """Other activities that would double-book this activity's instructor.

    An instructor cannot be in two places at once, and camps overlap in season —
    so the check spans every camp, not just this one.
    """
    if not activity.instructor_id or not activity.camp_day_id:
        return []

    same_day = (
        CampActivity.objects.filter(
            instructor_id=activity.instructor_id,
            camp_day__date=activity.camp_day.date,
        )
        .exclude(pk=activity.pk)
        .select_related("camp_day", "camp_day__camp")
    )
    return [other for other in same_day if activity.overlaps(other)]


@transaction.atomic
def save_activity(activity: CampActivity, request=None) -> CampActivity:
    """Validate and persist an activity, refusing an instructor double-booking."""
    activity.full_clean(exclude=["camp_day", "instructor", "lesson", "created_by", "updated_by"])

    clashes = activity_conflicts(activity)
    if clashes:
        first = clashes[0]
        raise ValidationError(
            _("%(instructor)s is already booked %(time)s on %(date)s (%(title)s).")
            % {
                "instructor": activity.instructor,
                "time": first.time_label,
                "date": first.camp_day.date,
                "title": first.title,
            }
        )

    activity.save()
    return activity


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def camp_financial_summary(camp: SurfCamp) -> dict:
    """Money view of one camp: sold, collected, outstanding, and the gap to full.

    ``collected`` reflects what the camp desk has recorded against each place;
    Finance remains the system of record for the underlying payments.
    """
    active = camp.active_participants()
    totals = active.aggregate(
        people=Count("id"),
        singles=Count("id", filter=Q(room_type=RoomType.SINGLE)),
        collected=Coalesce(Sum("amount_paid"), Value(ZERO), output_field=MONEY),
        deposits_missing=Count("id", filter=Q(deposit_paid=False)),
    )

    people = totals["people"] or 0
    singles = totals["singles"] or 0
    price = camp.price or ZERO
    supplement = camp.single_room_supplement or ZERO

    expected = (price * people) + (supplement * singles)
    collected = totals["collected"] or ZERO
    outstanding = expected - collected
    potential = price * (camp.capacity or 0)

    return {
        "capacity": camp.capacity or 0,
        "participants": people,
        "available_places": max((camp.capacity or 0) - people, 0),
        "occupancy_rate": round((people / camp.capacity) * 100, 1) if camp.capacity else 0.0,
        "min_participants": camp.min_participants or 0,
        "reaches_minimum": people >= (camp.min_participants or 0),
        "price": price,
        "deposit_amount": camp.deposit_amount or ZERO,
        "single_room_supplement": supplement,
        "single_rooms": singles,
        "supplement_revenue": supplement * singles,
        "expected_revenue": expected,
        "collected": collected,
        "outstanding": outstanding if outstanding > ZERO else ZERO,
        "overpaid": -outstanding if outstanding < ZERO else ZERO,
        "deposits_outstanding": totals["deposits_missing"] or 0,
        "deposit_expected": (camp.deposit_amount or ZERO) * people,
        "potential_revenue": potential,
        "revenue_gap": max(potential - expected, ZERO),
        "average_per_participant": (expected / people).quantize(Decimal("0.01"))
        if people
        else ZERO,
        "collection_rate": round(float(collected / expected) * 100, 1)
        if expected > ZERO
        else 0.0,
    }


def camp_staffing_summary(camp: SurfCamp) -> dict:
    """Instructor cover for the water sessions, including the minors rule.

    Ratios come from :mod:`apps.core.enums` and are a safety constraint. Under-18
    groups use the stricter ratio for the whole group — you cannot supervise a
    mixed group at two different ratios in the same line-up.
    """
    participants = list(
        camp.active_participants().select_related("student")
    )
    minors = [p for p in participants if is_minor(p.student)]

    ratio = MAX_STUDENTS_PER_INSTRUCTOR.get(camp.min_level, 8)
    if minors:
        ratio = min(ratio, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)

    head_count = len(participants)
    required = -(-head_count // ratio) if ratio else 0  # ceiling division
    assigned_ids = set(camp.instructors.values_list("id", flat=True))
    if camp.lead_instructor_id:
        assigned_ids.add(camp.lead_instructor_id)
    assigned = len(assigned_ids)

    return {
        "participants": head_count,
        "minors": len(minors),
        "ratio": ratio,
        "required_instructors": required,
        "assigned_instructors": assigned,
        "is_understaffed": assigned < required,
        "shortfall": max(required - assigned, 0),
    }


def camp_daily_roster(camp: SurfCamp, on_date: date) -> dict:
    """Everything the school needs on the beach for one day of a camp."""
    day = camp.days.filter(date=on_date).select_related("spot", "camp__spot").first()

    activities = []
    if day is not None:
        activities = list(
            day.activities.select_related("instructor", "lesson").order_by("start_time", "id")
        )

    participants = [
        participant
        for participant in camp.active_participants().select_related("student", "booking")
        if participant.is_on_site(on_date)
    ]

    arrivals = [
        participant
        for participant in camp.active_participants().select_related("student")
        if participant.arrival_datetime
        and timezone.localtime(participant.arrival_datetime).date() == on_date
    ]
    departures = [
        participant
        for participant in camp.active_participants().select_related("student")
        if participant.departure_datetime
        and timezone.localtime(participant.departure_datetime).date() == on_date
    ]

    instructors = []
    seen: set[int] = set()
    for activity in activities:
        if activity.instructor_id and activity.instructor_id not in seen:
            seen.add(activity.instructor_id)
            instructors.append(activity.instructor)

    return {
        "camp": camp,
        "date": on_date,
        "day": day,
        "spot": (day.effective_spot if day else camp.spot),
        "activities": activities,
        "water_sessions": [a for a in activities if a.is_water_activity],
        "participants": participants,
        "present_count": len(participants),
        "minors": [p for p in participants if is_minor(p.student)],
        "arrivals": arrivals,
        "departures": departures,
        "transfers": [p for p in arrivals + departures if p.needs_transfer],
        "dietary": [p for p in participants if p.dietary_requirements.strip()],
        "medical": [p for p in participants if p.has_medical_flag],
        "unpaid": [p for p in participants if not p.is_fully_paid],
        "instructors": instructors,
        "staffing": camp_staffing_summary(camp),
    }


def camp_alerts(camp: SurfCamp) -> list[dict]:
    """Operational warnings shown at the top of the camp screen.

    Each entry is ``{"level": "warning"|"error"|"info", "message": str}``.
    """
    alerts: list[dict] = []
    today = timezone.localdate()

    if camp.status == CampStatus.CANCELLED:
        alerts.append({"level": "error", "message": _("This camp is cancelled.")})
        return alerts

    staffing = camp_staffing_summary(camp)
    if staffing["is_understaffed"]:
        alerts.append(
            {
                "level": "error",
                "message": _(
                    "Understaffed: %(participants)s participants at a %(ratio)s:1 ratio need "
                    "%(required)s instructors, %(assigned)s assigned."
                )
                % {
                    "participants": staffing["participants"],
                    "ratio": staffing["ratio"],
                    "required": staffing["required_instructors"],
                    "assigned": staffing["assigned_instructors"],
                },
            }
        )
    if staffing["minors"]:
        alerts.append(
            {
                "level": "info",
                "message": _("%(count)s participants are under 18 — the %(ratio)s:1 ratio applies.")
                % {"count": staffing["minors"], "ratio": MAX_STUDENTS_PER_INSTRUCTOR_MINORS},
            }
        )

    if camp.is_full:
        alerts.append({"level": "info", "message": _("Every place is sold.")})

    if not camp.reaches_minimum and camp.status != CampStatus.DRAFT:
        alerts.append(
            {
                "level": "warning",
                "message": _(
                    "Only %(count)s of the %(minimum)s participants needed to run this camp."
                )
                % {"count": camp.participant_count, "minimum": camp.min_participants},
            }
        )

    if not camp.days.exists() and camp.status != CampStatus.DRAFT:
        alerts.append(
            {"level": "warning", "message": _("The programme is empty — generate the camp days.")}
        )

    if camp.includes_accommodation and not camp.accommodation_name:
        alerts.append(
            {
                "level": "warning",
                "message": _("Accommodation is included but no property is recorded."),
            }
        )

    if camp.includes_transfer and not camp.transfer_pickup_point:
        alerts.append(
            {"level": "warning", "message": _("Transfer is included but no pick-up point is set.")}
        )

    finance = camp_financial_summary(camp)
    if finance["outstanding"] > ZERO and camp.start_date and camp.start_date <= today + timedelta(
        days=7
    ):
        alerts.append(
            {
                "level": "warning",
                "message": _("%(amount)s is still outstanding and the camp starts within a week.")
                % {"amount": finance["outstanding"]},
            }
        )

    missing_rooms = camp.active_participants().filter(room_number="").count()
    if camp.includes_accommodation and missing_rooms and camp.is_running:
        alerts.append(
            {
                "level": "warning",
                "message": _("%(count)s participants have no room allocated.") % {"count": missing_rooms},
            }
        )

    return alerts


def _level_label(level: str) -> str:
    try:
        return str(SurfLevel(level).label)
    except ValueError:
        return str(level)
