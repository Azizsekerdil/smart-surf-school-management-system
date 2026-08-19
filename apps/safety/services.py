"""Safety business rules.

What lives here
---------------
Everything that *decides*: whether a student may enter the water, whether a
spot is usable right now, what the dashboard says, and the human sign-off step
that turns an AI suggestion into an actual warning.

Two boundaries are deliberate
-----------------------------
1. **The AI is never the final authority.** :func:`is_spot_safe_now` reads the
   computed ``surf_conditions.SurfScore`` and *authoritative* warnings only. An
   AI-suggested warning that no member of staff has acknowledged is invisible to
   it, by design. :func:`acknowledge_warning` is the only door from "suggestion"
   to "warning", and it requires a named user holding ``safety.approve``.
2. **Modules written in parallel are probed, never assumed.** The
   ``surf_conditions`` app owns the shape of its score, so this module reads it
   through a small, documented set of candidate field names and degrades to an
   explicit "not verified" reason rather than inventing a number. Callers that
   already hold a score object pass it in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.accounts.constants import Role
from apps.accounts.permissions import require_capability
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import MAX_WIND_KMH, WAVE_HEIGHT_SUITABILITY, Severity, SurfLevel

from .models import (
    OPEN_INCIDENT_STATUSES,
    SEVERITY_RANK,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)

logger = logging.getLogger(__name__)

#: Roles told about every incident.
INCIDENT_NOTIFY_ROLES: tuple[str, ...] = (
    Role.MANAGER,
    Role.OPERATIONS_MANAGER,
    Role.HEAD_INSTRUCTOR,
)

#: Roles additionally told about a high or critical incident.
SERIOUS_INCIDENT_EXTRA_ROLES: tuple[str, ...] = (Role.LIFEGUARD, Role.SUPER_ADMIN)

# --- integration contract with apps.surf_conditions -------------------------
#: Field names ``SurfScore`` may use for the moment it was computed.
SCORE_TIME_FIELDS: tuple[str, ...] = ("computed_at", "scored_at", "created_at")
#: Field names ``SurfScore`` may use for the numeric 0–100 quality score.
SCORE_VALUE_FIELDS: tuple[str, ...] = (
    "score",
    "overall_score",
    "quality_score",
    "total_score",
)
#: Field names ``SurfScore`` may use for the hard safety verdict.
SCORE_VERDICT_FIELDS: tuple[str, ...] = ("safety_verdict", "verdict")
#: Methods ``SurfScore`` may expose for per-level suitability.
SCORE_LEVEL_METHODS: tuple[str, ...] = ("suits_level", "is_suitable_for", "suitable_for_level")
#: Verdict values that close the water.
BLOCKING_VERDICTS: frozenset[str] = frozenset({"no_go", "no-go", "nogo", "closed", "unsafe"})
#: Quality score below which conditions are worth flagging (domain bands: 0–24 poor).
POOR_SCORE_THRESHOLD = 25

# --- integration contract with a conditions reading -------------------------
#: Attribute/key names a conditions object may use for wave height in metres.
CONDITION_WAVE_FIELDS: tuple[str, ...] = (
    "wave_height_m",
    "wave_height",
    "significant_wave_height_m",
    "swell_height_m",
)
#: Attribute/key names a conditions object may use for wind speed in km/h.
#: Only unambiguous km/h spellings — a bare "wind_speed" could be knots.
CONDITION_WIND_FIELDS: tuple[str, ...] = ("wind_speed_kmh", "wind_speed_km_h", "wind_kmh")


class SafetyOperationError(ValidationError):
    """Raised when a safety operation would break an operational rule."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _read(source, names: tuple[str, ...]):
    """First non-``None`` value of *names* on *source* (object or mapping)."""
    if source is None:
        return None
    for name in names:
        if isinstance(source, dict):
            value = source.get(name)
        else:
            value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_model(app_label: str, model_name: str):
    """Return a model from another app, or ``None`` if that app is absent."""
    try:
        return django_apps.get_model(app_label, model_name)
    except LookupError:
        return None


def severity_ordering(field_name: str = "severity"):
    """Order by real seriousness, not by the alphabet.

    ``-severity`` would sort *medium* above *critical*, which on a safety screen
    is not a cosmetic problem.
    """
    return Case(
        *[
            When(**{field_name: value, "then": Value(rank)})
            for value, rank in SEVERITY_RANK.items()
        ],
        default=Value(0),
        output_field=IntegerField(),
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
@dataclass
class SafetyVerdict:
    """The answer to a yes/no safety question, with every reason spelled out.

    ``blocking`` stops the activity. ``warnings`` do not, but must be briefed.
    ``ok`` is simply "nothing blocking".
    """

    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def reasons(self) -> list[str]:
        """Everything a caller should show: blockers first, then cautions."""
        return [*self.blocking, *self.warnings]

    def as_tuple(self) -> tuple[bool, list[str]]:
        return self.ok, self.reasons

    def as_dict(self) -> dict:
        return {"ok": self.ok, "blocking": self.blocking, "warnings": self.warnings}


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
@transaction.atomic
def report_incident(
    *,
    occurred_at: datetime,
    incident_type: str,
    severity: str,
    description: str,
    spot=None,
    lesson=None,
    status: str | None = None,
    immediate_action: str = "",
    people=None,
    staff=None,
    reported_by=None,
    medical_attention_required: bool = False,
    emergency_services_called: bool = False,
    conditions_at_time: dict | None = None,
    photo=None,
    follow_up_required: bool = False,
    follow_up_due: date | None = None,
    request=None,
) -> SafetyIncident:
    """Record an incident, audit it, and tell the people who must know.

    The audit entry uses :attr:`AuditAction.SAFETY_INCIDENT`, which the audit
    module marks as sensitive and therefore never prunes.
    """
    incident = SafetyIncident(
        occurred_at=occurred_at,
        incident_type=incident_type,
        severity=severity,
        description=description,
        spot=spot,
        lesson=lesson,
        immediate_action=immediate_action,
        reported_by=reported_by,
        medical_attention_required=medical_attention_required,
        emergency_services_called=emergency_services_called,
        conditions_at_time=conditions_at_time or {},
        follow_up_required=follow_up_required,
        follow_up_due=follow_up_due,
        created_by=reported_by,
        updated_by=reported_by,
    )
    if status:
        incident.status = status
    if photo is not None:
        incident.photo = photo

    incident.full_clean(exclude=["incident_code"])
    incident.save()

    if people:
        incident.people_involved.set(people)
    if staff:
        incident.staff_involved.set(staff)

    record_audit(
        request,
        action=AuditAction.SAFETY_INCIDENT,
        instance=incident,
        user=reported_by,
        description=_("Incident %(code)s recorded: %(type)s (%(severity)s)")
        % {
            "code": incident.incident_code,
            "type": incident.get_incident_type_display(),
            "severity": incident.get_severity_display(),
        },
    )
    notify_incident(incident)
    return incident


def notify_incident(incident: SafetyIncident) -> None:
    """Tell managers about *incident*. Never raises — reporting comes first."""
    try:
        from apps.notifications.models import NotificationCategory, NotificationLevel
        from apps.notifications.services import notify_role
    except ImportError:  # pragma: no cover - notifications is always installed
        logger.warning("Notifications unavailable; incident %s not broadcast", incident.pk)
        return

    roles = list(INCIDENT_NOTIFY_ROLES)
    level = NotificationLevel.WARNING
    if incident.is_serious:
        roles += list(SERIOUS_INCIDENT_EXTRA_ROLES)
        level = NotificationLevel.ERROR

    where = incident.spot.name if incident.spot_id else _("no spot recorded")
    try:
        link = _incident_url(incident)
        notify_role(
            roles,
            NotificationCategory.SAFETY,
            title=_("%(severity)s incident: %(type)s")
            % {
                "severity": incident.get_severity_display(),
                "type": incident.get_incident_type_display(),
            },
            body=_("%(code)s at %(where)s — %(summary)s")
            % {
                "code": incident.incident_code,
                "where": where,
                "summary": (incident.description or "")[:280],
            },
            level=level,
            link_url=link,
            related=incident,
        )
    except Exception:  # noqa: BLE001 - a notification failure must not undo a report
        logger.exception("Could not broadcast incident %s", incident.pk)


def _incident_url(incident: SafetyIncident) -> str:
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse("safety:incident_detail", kwargs={"pk": incident.pk})
    except NoReverseMatch:  # pragma: no cover - the route exists
        return ""


@transaction.atomic
def review_incident(
    incident: SafetyIncident,
    *,
    user,
    root_cause: str = "",
    corrective_action: str = "",
    status: str | None = None,
    follow_up_required: bool | None = None,
    follow_up_due: date | None = None,
    request=None,
) -> SafetyIncident:
    """Close the loop on an incident: cause, fix and a named reviewer."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise SafetyOperationError(
            {"__all__": _("A review must be signed by a named member of staff.")}
        )
    require_capability(user, "safety.approve")

    incident.root_cause = root_cause or incident.root_cause
    incident.corrective_action = corrective_action or incident.corrective_action
    if status:
        incident.status = status
    if follow_up_required is not None:
        incident.follow_up_required = follow_up_required
        incident.follow_up_due = follow_up_due if follow_up_required else None
    incident.reviewed_by = user
    incident.reviewed_at = timezone.now()
    incident.updated_by = user

    incident.full_clean(exclude=["incident_code"])
    incident.save()

    record_audit(
        request,
        action=AuditAction.SAFETY_INCIDENT,
        instance=incident,
        user=user,
        description=_("Incident %(code)s reviewed by %(who)s")
        % {"code": incident.incident_code, "who": user.get_display_name()},
        changes={"status": [None, incident.status], "reviewed_by": [None, user.username]},
    )
    return incident


def days_since_last_incident(*, spot=None, minimum_severity: str | None = None) -> int | None:
    """Whole days since the last incident, or ``None`` if there has never been one.

    The tile this feeds is the one number a surf school actually talks about at
    the morning briefing.
    """
    queryset = SafetyIncident.objects.all()
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    if minimum_severity:
        floor = SEVERITY_RANK.get(minimum_severity, 0)
        allowed = [value for value, rank in SEVERITY_RANK.items() if rank >= floor]
        queryset = queryset.filter(severity__in=allowed)

    last = queryset.order_by("-occurred_at").values_list("occurred_at", flat=True).first()
    if last is None:
        return None
    return max(0, (timezone.now() - last).days)


# ---------------------------------------------------------------------------
# Restrictions
# ---------------------------------------------------------------------------
def active_restrictions_for(student, *, on: date | None = None) -> QuerySet[StudentRestriction]:
    """Restrictions in force for *student* on *on* (default today).

    Bookings and lessons call this through a lazy lookup, so it must stay a
    queryset: the caller decides whether to count, list or filter it further.
    """
    if student is None:
        return StudentRestriction.objects.none()
    day = on or timezone.localdate()
    student_id = getattr(student, "pk", student)
    return (
        StudentRestriction.objects.filter(student_id=student_id, is_active=True, starts_on__lte=day)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=day))
        .select_related("issued_by")
        .order_by("-cannot_surf", "-starts_on")
    )


def evaluate_student(student, condition=None, *, on: date | None = None) -> SafetyVerdict:
    """Full restriction assessment for one student against the conditions.

    ``condition`` may be a ``surf_conditions.SurfCondition`` instance or a plain
    mapping. A limit that cannot be checked because the reading is missing
    produces a *warning*, never a silent pass: the coach is told to confirm it
    on the beach.
    """
    verdict = SafetyVerdict()
    restrictions = list(active_restrictions_for(student, on=on))
    if not restrictions:
        return verdict

    wave = _float(_read(condition, CONDITION_WAVE_FIELDS)) if condition is not None else None
    wind = _float(_read(condition, CONDITION_WIND_FIELDS)) if condition is not None else None

    for restriction in restrictions:
        label = restriction.get_restriction_type_display()

        if restriction.cannot_surf:
            verdict.blocking.append(
                _("%(type)s restriction: this student must not enter the water. %(why)s")
                % {"type": label, "why": restriction.description}
            )
            continue

        if restriction.requires_supervision:
            verdict.warnings.append(
                _("%(type)s restriction: close supervision required. %(why)s")
                % {"type": label, "why": restriction.description}
            )

        if restriction.max_wave_height_m is not None:
            if wave is None:
                verdict.warnings.append(
                    _(
                        "Limited to waves of %(limit).1f m and no wave height is on "
                        "record — confirm the sea state before entering the water."
                    )
                    % {"limit": restriction.max_wave_height_m}
                )
            elif wave > restriction.max_wave_height_m:
                verdict.blocking.append(
                    _("Waves are %(actual).1f m; this student is limited to %(limit).1f m.")
                    % {"actual": wave, "limit": restriction.max_wave_height_m}
                )

        if restriction.max_wind_kmh is not None:
            if wind is None:
                verdict.warnings.append(
                    _(
                        "Limited to %(limit).0f km/h of wind and no wind reading is on "
                        "record — confirm before entering the water."
                    )
                    % {"limit": restriction.max_wind_kmh}
                )
            elif wind > restriction.max_wind_kmh:
                verdict.blocking.append(
                    _("Wind is %(actual).0f km/h; this student is limited to %(limit).0f km/h.")
                    % {"actual": wind, "limit": restriction.max_wind_kmh}
                )

    return verdict


def check_student_can_surf(student, condition=None) -> tuple[bool, list[str]]:
    """``(may_surf, reasons)`` for the booking and lesson conflict checks.

    ``reasons`` lists blockers first, then cautions, so a caller that only shows
    the list still shows the important line first. ``may_surf`` is ``False``
    only when something genuinely blocks.
    """
    return evaluate_student(student, condition).as_tuple()


@transaction.atomic
def deactivate_restriction(restriction: StudentRestriction, *, user=None, request=None):
    """Lift a restriction, keeping it on file."""
    if not restriction.is_active:
        return restriction
    restriction.is_active = False
    restriction.updated_by = user
    if restriction.ends_on is None:
        restriction.ends_on = timezone.localdate()
    restriction.full_clean()
    restriction.save()
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=restriction,
        user=user,
        description=_("Restriction lifted for %(student)s") % {"student": restriction.student},
        changes={"is_active": [True, False]},
    )
    return restriction


# ---------------------------------------------------------------------------
# Warnings — the human sign-off
# ---------------------------------------------------------------------------
def authoritative_warnings(*, spot=None, at: datetime | None = None) -> QuerySet[WeatherWarning]:
    """Warnings other modules may act on: active, in window, and human-owned.

    AI suggestions appear here **only** once acknowledged. This is the queryset
    the rest of the system should consume; the unacknowledged ones are for the
    safety screens alone.
    """
    moment = at or timezone.now()
    queryset = WeatherWarning.objects.filter(
        is_active=True, starts_at__lte=moment, ends_at__gte=moment
    ).filter(Q(ai_suggested=False) | Q(acknowledged_by__isnull=False))
    if spot is not None:
        queryset = queryset.filter(Q(spot__isnull=True) | Q(spot=spot))
    return (
        queryset.select_related("spot", "acknowledged_by")
        .annotate(severity_order=severity_ordering())
        .order_by("-severity_order", "-starts_at")
    )


def pending_ai_warnings(*, spot=None) -> QuerySet[WeatherWarning]:
    """AI suggestions still waiting for a member of staff to sign them off."""
    queryset = WeatherWarning.objects.filter(
        is_active=True, ai_suggested=True, acknowledged_by__isnull=True
    )
    if spot is not None:
        queryset = queryset.filter(Q(spot__isnull=True) | Q(spot=spot))
    return (
        queryset.select_related("spot")
        .annotate(severity_order=severity_ordering())
        .order_by("-severity_order", "-starts_at")
    )


@transaction.atomic
def acknowledge_warning(warning: WeatherWarning, user, *, request=None) -> WeatherWarning:
    """The human sign-off: turn an AI suggestion into a real warning.

    This is the only path from "AI Recommendation — awaiting staff confirmation"
    to a warning other modules obey. It demands an authenticated user holding
    ``safety.approve`` and stores who signed it and when.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise SafetyOperationError(
            {"__all__": _("A warning can only be confirmed by a named member of staff.")}
        )
    require_capability(user, "safety.approve")

    if warning.acknowledged_by_id is not None:
        return warning

    warning.acknowledged_by = user
    warning.acknowledged_at = timezone.now()
    warning.updated_by = user
    warning.full_clean()
    warning.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=warning,
        user=user,
        description=_("Weather warning “%(title)s” confirmed by %(who)s")
        % {"title": warning.title, "who": user.get_display_name()},
        changes={"acknowledged_by": [None, user.username]},
    )
    return warning


@transaction.atomic
def dismiss_warning(warning: WeatherWarning, user, *, request=None) -> WeatherWarning:
    """Reject a warning (typically an AI suggestion staff disagree with)."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise SafetyOperationError({"__all__": _("Sign in to dismiss a warning.")})
    require_capability(user, "safety.approve")

    if not warning.is_active:
        return warning
    warning.is_active = False
    warning.updated_by = user
    warning.full_clean()
    warning.save()
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=warning,
        user=user,
        description=_("Weather warning “%(title)s” dismissed by %(who)s")
        % {"title": warning.title, "who": user.get_display_name()},
        changes={"is_active": [True, False]},
    )
    return warning


# ---------------------------------------------------------------------------
# Lifeguard cover
# ---------------------------------------------------------------------------
def lifeguard_cover(spot, *, at: datetime | None = None) -> QuerySet[LifeguardAssignment]:
    """Confirmed shifts covering *spot* at *at* (default now)."""
    moment = at or timezone.now()
    local = timezone.localtime(moment)
    return (
        LifeguardAssignment.objects.filter(
            spot=spot,
            date=local.date(),
            is_confirmed=True,
            start_time__lte=local.time(),
            end_time__gte=local.time(),
        )
        .select_related("lifeguard", "spot")
        .order_by("start_time")
    )


def cover_today(*, spot=None) -> QuerySet[LifeguardAssignment]:
    """Every shift on today's roster, confirmed or not."""
    queryset = LifeguardAssignment.objects.filter(date=timezone.localdate())
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    return queryset.select_related("lifeguard", "spot").order_by("start_time", "spot__name")


def roster_for_week(start: date, *, spot=None) -> dict:
    """The weekly grid: seven days, each with its shifts.

    Returns ``{"days": [{"date": …, "assignments": [...]}, …], "start": …,
    "end": …}`` so the template does no query work of its own.
    """
    end = start + timedelta(days=6)
    queryset = LifeguardAssignment.objects.filter(date__gte=start, date__lte=end)
    if spot is not None:
        queryset = queryset.filter(spot=spot)
    assignments = list(
        queryset.select_related("lifeguard", "spot").order_by("date", "start_time")
    )

    buckets: dict[date, list[LifeguardAssignment]] = {
        start + timedelta(days=offset): [] for offset in range(7)
    }
    for assignment in assignments:
        buckets.setdefault(assignment.date, []).append(assignment)

    today = timezone.localdate()
    return {
        "start": start,
        "end": end,
        "days": [
            {
                "date": day,
                "is_today": day == today,
                "assignments": buckets.get(day, []),
                "confirmed_count": sum(1 for a in buckets.get(day, []) if a.is_confirmed),
            }
            for day in sorted(buckets)
        ],
        "total": len(assignments),
        "confirmed": sum(1 for a in assignments if a.is_confirmed),
    }


@transaction.atomic
def confirm_assignment(assignment: LifeguardAssignment, *, user=None, request=None):
    """Confirm a shift so it starts counting as cover."""
    if assignment.is_confirmed:
        return assignment
    assignment.is_confirmed = True
    assignment.updated_by = user
    assignment.full_clean()
    assignment.save()
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=assignment,
        user=user,
        description=_("Lifeguard shift confirmed: %(shift)s") % {"shift": assignment},
        changes={"is_confirmed": [False, True]},
    )
    return assignment


# ---------------------------------------------------------------------------
# Equipment checks
# ---------------------------------------------------------------------------
def overdue_equipment_checks(*, on: date | None = None) -> QuerySet[EquipmentSafetyCheck]:
    """Items whose **latest** check named a due date that has now passed.

    Only the latest check per item counts — a board inspected again yesterday is
    not overdue because a check from March said "recheck in April".
    ``distinct("field")`` is PostgreSQL-only, so the latest row per item is
    selected with a correlated subquery, which both back-ends run happily.
    """
    day = on or timezone.localdate()
    latest_for_item = (
        EquipmentSafetyCheck.objects.filter(equipment_id=OuterRef("equipment_id"))
        .order_by("-checked_at", "-id")
        .values("pk")[:1]
    )
    return (
        EquipmentSafetyCheck.objects.filter(next_check_due__lt=day)
        .filter(pk=Subquery(latest_for_item))
        .select_related("equipment", "checked_by")
        .order_by("next_check_due")
    )


def latest_check_for(equipment) -> EquipmentSafetyCheck | None:
    """The most recent safety check recorded against *equipment*."""
    return (
        EquipmentSafetyCheck.objects.filter(equipment=equipment)
        .select_related("checked_by")
        .order_by("-checked_at", "-id")
        .first()
    )


def failed_open_checks() -> QuerySet[EquipmentSafetyCheck]:
    """Failed checks where nothing has been recorded as done about it."""
    return (
        EquipmentSafetyCheck.objects.filter(passed=False)
        .filter(Q(action_taken="") | Q(action_taken__isnull=True))
        .select_related("equipment", "checked_by")
        .order_by("-checked_at")
    )


# ---------------------------------------------------------------------------
# Drills
# ---------------------------------------------------------------------------
def upcoming_drills(*, days: int = 30) -> QuerySet[EvacuationPlan]:
    """Active plans whose next drill falls inside the window, overdue first."""
    today = timezone.localdate()
    return (
        EvacuationPlan.objects.filter(
            is_active=True, next_drill_due__isnull=False, next_drill_due__lte=today + timedelta(days=days)
        )
        .select_related("spot")
        .order_by("next_drill_due")
    )


def overdue_drills() -> QuerySet[EvacuationPlan]:
    return (
        EvacuationPlan.objects.filter(is_active=True, next_drill_due__lt=timezone.localdate())
        .select_related("spot")
        .order_by("next_drill_due")
    )


# ---------------------------------------------------------------------------
# Is this spot safe right now?
# ---------------------------------------------------------------------------
@dataclass
class SurfScoreReading:
    """What the computed surf score says — or that there is not one."""

    available: bool = False
    score: float | None = None
    verdict: str | None = None
    recorded_at: datetime | None = None
    level_suitable: bool | None = None

    @property
    def is_blocking(self) -> bool:
        return bool(self.verdict and str(self.verdict).lower() in BLOCKING_VERDICTS)


def surf_score_for(spot, level: str, *, surf_score=None) -> SurfScoreReading:
    """Read the **computed** surf score for *spot* — never an AI opinion.

    ``surf_score`` lets a caller that already loaded the object pass it in.
    When the ``surf_conditions`` module has produced nothing for this spot the
    reading comes back unavailable, and :func:`is_spot_safe_now` says so out
    loud instead of assuming the water is fine.
    """
    obj = surf_score
    if obj is None:
        model = _get_model("surf_conditions", "SurfScore")
        if model is None:
            return SurfScoreReading()
        try:
            queryset = model.objects.filter(spot=spot)
            field_names = {f.name for f in model._meta.get_fields() if hasattr(f, "name")}
            for candidate in SCORE_TIME_FIELDS:
                if candidate in field_names:
                    queryset = queryset.order_by(f"-{candidate}")
                    break
            else:
                queryset = queryset.order_by("-id")
            obj = queryset.first()
        except Exception:  # noqa: BLE001 - a foreign schema must not break safety
            logger.exception("Could not read SurfScore for spot %s", getattr(spot, "pk", spot))
            return SurfScoreReading()

    if obj is None:
        return SurfScoreReading()

    level_suitable: bool | None = None
    for name in SCORE_LEVEL_METHODS:
        method = getattr(obj, name, None)
        if callable(method):
            try:
                level_suitable = bool(method(level))
            except Exception:  # noqa: BLE001
                level_suitable = None
            break

    return SurfScoreReading(
        available=True,
        score=_float(_read(obj, SCORE_VALUE_FIELDS)),
        verdict=_read(obj, SCORE_VERDICT_FIELDS),
        recorded_at=_read(obj, SCORE_TIME_FIELDS),
        level_suitable=level_suitable,
    )


def assess_spot(spot, level: str = SurfLevel.BEGINNER, *, surf_score=None, at=None) -> SafetyVerdict:
    """Everything that decides whether *spot* is usable for *level* right now.

    Inputs, in the order they are trusted:

    1. the spot's own state (archived, level range, critical hazards);
    2. the **computed** surf score, if the conditions module has one;
    3. **authoritative** weather warnings — AI suggestions are excluded until a
       person has confirmed them;
    4. open critical incidents at this spot;
    5. lifeguard cover.
    """
    verdict = SafetyVerdict()
    if spot is None:
        verdict.blocking.append(_("No surf spot was given."))
        return verdict

    moment = at or timezone.now()

    # --- 1. the spot itself ----------------------------------------------
    if not getattr(spot, "is_active", True):
        verdict.blocking.append(_("This spot is archived and not in service."))

    if hasattr(spot, "suits_level") and not spot.suits_level(level):
        verdict.blocking.append(
            _("%(spot)s does not accept %(level)s surfers.")
            % {"spot": spot.name, "level": dict(SurfLevel.choices).get(level, level)}
        )

    for hazard in spot.hazards.filter(is_active=True, severity=Severity.CRITICAL):
        verdict.blocking.append(_("Critical hazard: %(name)s.") % {"name": hazard.name})

    # --- 2. the computed score -------------------------------------------
    reading = surf_score_for(spot, level, surf_score=surf_score)
    if not reading.available:
        verdict.warnings.append(
            _("No computed surf score for this spot — assess the conditions on the beach.")
        )
    else:
        if reading.is_blocking:
            verdict.blocking.append(
                _("The conditions assessment for this spot returns “%(verdict)s”.")
                % {"verdict": reading.verdict}
            )
        if reading.level_suitable is False:
            verdict.warnings.append(
                _("Today's conditions are rated unsuitable for %(level)s surfers.")
                % {"level": dict(SurfLevel.choices).get(level, level)}
            )
        if reading.score is not None and reading.score < POOR_SCORE_THRESHOLD:
            verdict.warnings.append(
                _("Surf quality is scored %(score)s/100 — poor conditions for teaching.")
                % {"score": int(reading.score)}
            )

    # --- 3. authoritative warnings only ----------------------------------
    for warning in authoritative_warnings(spot=spot, at=moment):
        if warning.severity in (Severity.HIGH, Severity.CRITICAL):
            verdict.blocking.append(
                _("%(severity)s weather warning in force: %(title)s.")
                % {"severity": warning.get_severity_display(), "title": warning.title}
            )
        else:
            verdict.warnings.append(
                _("Weather warning: %(title)s (%(severity)s).")
                % {"title": warning.title, "severity": warning.get_severity_display()}
            )

    pending = pending_ai_warnings(spot=spot).count()
    if pending:
        verdict.warnings.append(
            ngettext(
                "%(count)s AI-suggested warning for this spot is awaiting staff "
                "confirmation and is not counted above.",
                "%(count)s AI-suggested warnings for this spot are awaiting staff "
                "confirmation and are not counted above.",
                pending,
            )
            % {"count": pending}
        )

    # --- 4. open critical incidents --------------------------------------
    recent_critical = SafetyIncident.objects.filter(
        spot=spot,
        severity=Severity.CRITICAL,
        status__in=OPEN_INCIDENT_STATUSES,
        occurred_at__gte=moment - timedelta(hours=24),
    ).order_by("-occurred_at")
    for incident in recent_critical:
        verdict.blocking.append(
            _("Critical incident %(code)s at this spot is still open.")
            % {"code": incident.incident_code}
        )

    # --- 5. lifeguard cover ----------------------------------------------
    if not lifeguard_cover(spot, at=moment).exists():
        if getattr(spot, "lifeguard_on_duty", False):
            verdict.warnings.append(
                _(
                    "This spot has a lifeguard service, but no confirmed shift covers "
                    "this moment. Check the roster before the group goes in."
                )
            )
        else:
            verdict.warnings.append(
                _(
                    "Unpatrolled spot with no lifeguard shift rostered — the school "
                    "provides its own water safety cover here."
                )
            )

    return verdict


def is_spot_safe_now(spot, level: str = SurfLevel.BEGINNER, *, surf_score=None) -> tuple[bool, list[str]]:
    """``(safe, reasons)`` for *spot* at *level*, right now.

    Backed by :func:`assess_spot`; the tuple form is what booking and lesson
    validation calls.
    """
    return assess_spot(spot, level, surf_score=surf_score).as_tuple()


def level_condition_limits(level: str) -> dict:
    """The school's own ceilings for a level — used on the briefing screens."""
    suitability = WAVE_HEIGHT_SUITABILITY.get(level)
    return {
        "max_wave_height_m": suitability[3] if suitability else None,
        "ideal_wave_range_m": (suitability[1], suitability[2]) if suitability else None,
        "max_wind_kmh": MAX_WIND_KMH.get(level),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def safety_dashboard_stats(start: datetime | None, end: datetime | None) -> dict:
    """Every number the safety dashboard shows, in one pass."""
    incidents = SafetyIncident.objects.all()
    if start:
        incidents = incidents.filter(occurred_at__gte=start)
    if end:
        incidents = incidents.filter(occurred_at__lte=end)

    type_labels = dict(SafetyIncident.IncidentType.choices)
    by_type = [
        {
            "value": row["incident_type"],
            "label": str(type_labels.get(row["incident_type"], row["incident_type"])),
            "count": row["count"],
        }
        for row in incidents.values("incident_type").annotate(count=Count("id")).order_by("-count")
    ]

    severity_labels = dict(Severity.choices)
    severity_counts = {
        row["severity"]: row["count"]
        for row in incidents.values("severity").annotate(count=Count("id"))
    }
    by_severity = [
        {
            "value": value,
            "label": str(severity_labels[value]),
            "count": severity_counts.get(value, 0),
        }
        for value in (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
    ]

    open_incidents = SafetyIncident.objects.filter(status__in=OPEN_INCIDENT_STATUSES)
    today = timezone.localdate()

    last_incident = SafetyIncident.objects.order_by("-occurred_at").first()

    return {
        "total": incidents.count(),
        "by_type": by_type,
        "by_severity": by_severity,
        "serious": incidents.filter(severity__in=(Severity.HIGH, Severity.CRITICAL)).count(),
        "medical": incidents.filter(medical_attention_required=True).count(),
        "emergency_services": incidents.filter(emergency_services_called=True).count(),
        "days_since_last_incident": days_since_last_incident(),
        "last_incident": last_incident,
        "open_incidents": open_incidents.count(),
        "open_follow_ups": open_incidents.filter(follow_up_required=True).count(),
        "overdue_follow_ups": open_incidents.filter(
            follow_up_required=True, follow_up_due__lt=today
        ).count(),
        "overdue_equipment_checks": overdue_equipment_checks().count(),
        "failed_checks": failed_open_checks().count(),
        "upcoming_drills": list(upcoming_drills()[:5]),
        "overdue_drills": overdue_drills().count(),
        "active_warnings": authoritative_warnings().count(),
        "pending_ai_warnings": pending_ai_warnings().count(),
        "active_restrictions": StudentRestriction.objects.filter(
            is_active=True, starts_on__lte=today
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .count(),
        "trend": incident_trend(start, end),
    }


def incident_trend(start: datetime | None, end: datetime | None, *, max_points: int = 90) -> dict:
    """Daily incident counts across the range, ready for Chart.js.

    Buckets are produced with ``TruncDate``, which both SQLite and PostgreSQL
    support, and the gaps are filled in Python so the line has no holes.
    """
    if start is None or end is None:
        first = SafetyIncident.objects.order_by("occurred_at").values_list(
            "occurred_at", flat=True
        ).first()
        start = start or first or timezone.now() - timedelta(days=29)
        end = end or timezone.now()

    start_day = timezone.localtime(start).date()
    end_day = timezone.localtime(end).date()
    if end_day < start_day:
        start_day, end_day = end_day, start_day

    span = (end_day - start_day).days + 1
    if span > max_points:
        start_day = end_day - timedelta(days=max_points - 1)
        span = max_points

    rows = (
        SafetyIncident.objects.filter(occurred_at__gte=start, occurred_at__lte=end)
        .annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(count=Count("id"), serious=Count("id", filter=Q(severity__in=(Severity.HIGH, Severity.CRITICAL))))
        .order_by("day")
    )
    totals = {row["day"]: row["count"] for row in rows if row["day"]}
    serious = {row["day"]: row["serious"] for row in rows if row["day"]}

    days = [start_day + timedelta(days=offset) for offset in range(span)]
    return {
        "labels": [day.strftime("%d.%m") for day in days],
        "counts": [totals.get(day, 0) for day in days],
        "serious": [serious.get(day, 0) for day in days],
    }
