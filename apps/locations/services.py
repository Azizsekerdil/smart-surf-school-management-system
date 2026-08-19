"""Business rules for surf locations.

Nothing in this module imports another business app. A spot does not know what
a lesson or a booking is — callers pass in how many students are already in the
water, and this module answers whether more may go in. That keeps the
dependency arrow pointing one way and makes the rules testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import (
    MAX_STUDENTS_PER_INSTRUCTOR,
    MAX_STUDENTS_PER_INSTRUCTOR_MINORS,
    SURF_LEVEL_ORDER,
    Severity,
    SurfLevel,
    TideState,
    WindType,
    level_rank,
)

from .models import SEVERITY_RANK, SurfSpot

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
#: Everything checked out — the group may go in the water at this spot.
VERDICT_GO = "go"
#: Usable, but a named risk must be briefed and managed by the coach.
VERDICT_CAUTION = "caution"
#: The school must not run this group at this spot.
VERDICT_NO_GO = "no_go"

VERDICT_LABELS: dict[str, object] = {
    VERDICT_GO: _lazy("Go"),
    VERDICT_CAUTION: _lazy("Go with caution"),
    VERDICT_NO_GO: _lazy("Do not use"),
}

#: Wind classes that hold the wave face up. Anything else is degrading it.
CLEAN_WIND_TYPES = (WindType.OFFSHORE, WindType.CROSS_OFFSHORE, WindType.GLASSY)

#: Beginner levels for which offshore wind is a documented hazard: it blows a
#: separated surfer, and anything inflatable, away from the shore.
OFFSHORE_RISK_LEVELS = (SurfLevel.FIRST_TIME, SurfLevel.BEGINNER)


class SpotOperationError(ValidationError):
    """Raised when an operation on a spot would break an operational rule."""


# ---------------------------------------------------------------------------
# Core lookups
# ---------------------------------------------------------------------------
def get_primary_spot() -> SurfSpot | None:
    """Return the school's default spot.

    Falls back to the first active spot so callers always get something usable
    even if an operator has archived the flagged spot outside the normal flow.
    """
    spot = SurfSpot.objects.filter(is_primary=True, is_active=True).first()
    if spot is not None:
        return spot
    return SurfSpot.objects.filter(is_active=True).order_by("name").first()


def spots_suitable_for_level(
    level: str, *, include_inactive: bool = False
) -> QuerySet[SurfSpot]:
    """Every spot whose accepted level range contains *level*.

    Implemented with two ``__in`` filters rather than a numeric annotation so the
    query stays identical on SQLite and PostgreSQL.
    """
    rank = level_rank(level)
    lower = [value for value, order in SURF_LEVEL_ORDER.items() if order <= rank]
    upper = [value for value, order in SURF_LEVEL_ORDER.items() if order >= rank]

    queryset = SurfSpot.objects.all() if include_inactive else SurfSpot.objects.filter(is_active=True)
    return queryset.filter(min_level__in=lower, max_level__in=upper).order_by(
        "-is_primary", "name"
    )


def classify_wind_for_spot(spot: SurfSpot, wind_dir_deg: float | None) -> str:
    """Classify a wind bearing relative to *spot*'s orientation.

    ``wind_dir_deg`` follows the meteorological convention: the direction the
    wind comes **from**.
    """
    if spot is None:
        return WindType.CROSS_SHORE
    return spot.classify_wind(wind_dir_deg)


def wind_is_clean_for_spot(spot: SurfSpot, wind_dir_deg: float | None) -> bool:
    """Does this wind groom the wave face rather than chop it up?"""
    return classify_wind_for_spot(spot, wind_dir_deg) in CLEAN_WIND_TYPES


# ---------------------------------------------------------------------------
# Hazards
# ---------------------------------------------------------------------------
def active_hazards(
    spot: SurfSpot,
    *,
    tide_state: str | None = None,
    minimum_severity: str | None = None,
) -> list:
    """Active hazards at *spot*, most serious first.

    ``tide_state`` narrows the list to hazards whose tide window is open. A
    missing or unknown tide never hides a hazard.
    """
    hazards = getattr(spot, "prefetched_active_hazards", None)
    if hazards is None:
        hazards = list(spot.hazards.filter(is_active=True))
    else:
        hazards = [hazard for hazard in hazards if hazard.is_active]

    if tide_state:
        hazards = [hazard for hazard in hazards if hazard.applies_at_tide(tide_state)]

    if minimum_severity:
        floor = SEVERITY_RANK.get(minimum_severity, 0)
        hazards = [hazard for hazard in hazards if hazard.severity_rank >= floor]

    return sorted(hazards, key=lambda hazard: (-hazard.severity_rank, hazard.name))


def blocking_hazards(spot: SurfSpot, *, tide_state: str | None = None) -> list:
    """Hazards severe enough to close the spot outright."""
    return [
        hazard
        for hazard in active_hazards(spot, tide_state=tide_state)
        if hazard.severity == Severity.CRITICAL
    ]


def worst_hazard_severity(spot: SurfSpot, *, tide_state: str | None = None) -> str | None:
    hazards = active_hazards(spot, tide_state=tide_state)
    return hazards[0].severity if hazards else None


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------
def remaining_capacity(spot: SurfSpot, occupied_students: int = 0) -> int:
    """How many more students this spot can take. Never negative."""
    return max(0, int(spot.capacity or 0) - max(0, int(occupied_students or 0)))


def max_group_size(spot: SurfSpot, level: str, *, has_minors: bool = False) -> int:
    """The largest group one instructor may supervise at this spot.

    The water-capacity of the break and the per-level supervision ratio are two
    independent ceilings; the smaller one wins. Under-18 groups always use the
    stricter minors ratio.
    """
    ratio = MAX_STUDENTS_PER_INSTRUCTOR.get(level, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)
    if has_minors:
        ratio = min(ratio, MAX_STUDENTS_PER_INSTRUCTOR_MINORS)
    return max(1, min(int(spot.capacity or 1), ratio))


# ---------------------------------------------------------------------------
# The operational question: may this group surf here, now?
# ---------------------------------------------------------------------------
@dataclass
class SpotAssessment:
    """The answer, with every reason spelled out for the coach's briefing."""

    spot: SurfSpot
    verdict: str = VERDICT_GO
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    remaining_capacity: int = 0
    recommended_group_size: int = 0
    wind_type: str | None = None

    @property
    def is_go(self) -> bool:
        return self.verdict == VERDICT_GO

    @property
    def is_blocked(self) -> bool:
        return self.verdict == VERDICT_NO_GO

    @property
    def verdict_label(self) -> object:
        return VERDICT_LABELS.get(self.verdict, self.verdict)

    def as_dict(self) -> dict:
        return {
            "spot": self.spot.pk,
            "verdict": self.verdict,
            "verdict_label": str(self.verdict_label),
            "blocking": self.blocking,
            "warnings": self.warnings,
            "notes": self.notes,
            "remaining_capacity": self.remaining_capacity,
            "recommended_group_size": self.recommended_group_size,
            "wind_type": self.wind_type,
        }


def assess_spot_for_group(
    spot: SurfSpot,
    *,
    level: str = SurfLevel.BEGINNER,
    group_size: int = 1,
    occupied_students: int = 0,
    has_minors: bool = False,
    tide_state: str | None = None,
    wind_direction_deg: float | None = None,
) -> SpotAssessment:
    """Decide whether *group_size* students at *level* may use *spot*.

    This is the gate every scheduling screen should call before it commits a
    group to a break. It never guesses: unknown inputs produce a warning, not a
    silent pass.
    """
    group_size = max(1, int(group_size or 1))
    assessment = SpotAssessment(
        spot=spot,
        remaining_capacity=remaining_capacity(spot, occupied_students),
        recommended_group_size=max_group_size(spot, level, has_minors=has_minors),
    )

    # --- is the spot even open? ------------------------------------------
    if not spot.is_active:
        assessment.blocking.append(_("This spot is archived and not in service."))

    # --- level fit --------------------------------------------------------
    if level_rank(level) < level_rank(spot.min_level):
        assessment.blocking.append(
            _("%(spot)s starts at %(min)s — this group is below that level.")
            % {"spot": spot.name, "min": dict(SurfLevel.choices).get(spot.min_level, spot.min_level)}
        )
    elif level_rank(level) > level_rank(spot.max_level):
        assessment.warnings.append(
            _("This break tops out at %(max)s and will under-serve the group.")
            % {"max": dict(SurfLevel.choices).get(spot.max_level, spot.max_level)}
        )

    # --- water capacity ---------------------------------------------------
    if group_size > assessment.remaining_capacity:
        assessment.blocking.append(
            _(
                "Over capacity: %(free)s of %(cap)s places free, %(asked)s requested."
            )
            % {
                "free": assessment.remaining_capacity,
                "cap": spot.capacity,
                "asked": group_size,
            }
        )
    elif assessment.remaining_capacity - group_size <= 2:
        assessment.notes.append(
            _("Spot will be at or near capacity (%(cap)s students).") % {"cap": spot.capacity}
        )

    # --- supervision ratio ------------------------------------------------
    if group_size > assessment.recommended_group_size:
        message = _(
            "Group of %(size)s exceeds the %(max)s-student ratio for this level; "
            "split the group or add an instructor."
        ) % {"size": group_size, "max": assessment.recommended_group_size}
        if has_minors:
            assessment.blocking.append(message)
        else:
            assessment.warnings.append(message)

    # --- hazards ----------------------------------------------------------
    hazards = active_hazards(spot, tide_state=tide_state)
    for hazard in hazards:
        if hazard.severity == Severity.CRITICAL:
            assessment.blocking.append(
                _("Critical hazard: %(hazard)s.") % {"hazard": hazard.name}
            )
        elif hazard.severity == Severity.HIGH:
            assessment.warnings.append(
                _("High hazard: %(hazard)s (%(window)s).")
                % {"hazard": hazard.name, "window": hazard.tide_window_display}
            )
        else:
            assessment.notes.append(
                _("%(hazard)s — %(severity)s.")
                % {"hazard": hazard.name, "severity": hazard.get_severity_display()}
            )

    high_or_worse = [h for h in hazards if h.severity_rank >= SEVERITY_RANK[Severity.HIGH]]

    # --- minors -----------------------------------------------------------
    if has_minors:
        if not spot.lifeguard_on_duty and high_or_worse:
            assessment.blocking.append(
                _(
                    "Under-18 group at an unpatrolled spot with an active high-severity "
                    "hazard. Move the group or arrange lifeguard cover."
                )
            )
        elif not spot.lifeguard_on_duty:
            assessment.warnings.append(
                _("No lifeguard service here — under-18 groups need extra water cover.")
            )

    # --- wind -------------------------------------------------------------
    if wind_direction_deg is not None:
        assessment.wind_type = classify_wind_for_spot(spot, wind_direction_deg)
        labels = dict(WindType.choices)
        if assessment.wind_type == WindType.ONSHORE:
            assessment.warnings.append(
                _("Onshore wind: choppy, disorganised waves and a shorebreak.")
            )
        if assessment.wind_type == WindType.OFFSHORE and level in OFFSHORE_RISK_LEVELS:
            assessment.warnings.append(
                _(
                    "Offshore wind pushes beginners and inflatables away from the beach. "
                    "Keep the group inside the break line."
                )
            )
        if assessment.wind_type != spot.ideal_wind:
            assessment.notes.append(
                _("Wind is %(actual)s; this break wants %(ideal)s.")
                % {
                    "actual": labels.get(assessment.wind_type, assessment.wind_type),
                    "ideal": labels.get(spot.ideal_wind, spot.ideal_wind),
                }
            )

    # --- tide -------------------------------------------------------------
    if tide_state and tide_state != TideState.UNKNOWN and tide_state != spot.ideal_tide:
        assessment.notes.append(
            _("Tide is %(actual)s; this break works best on %(ideal)s.")
            % {
                "actual": dict(TideState.choices).get(tide_state, tide_state),
                "ideal": dict(TideState.choices).get(spot.ideal_tide, spot.ideal_tide),
            }
        )

    # --- emergency readiness ---------------------------------------------
    if not spot.has_emergency_contact:
        assessment.warnings.append(
            _("No hospital and phone number recorded for this spot.")
        )

    if assessment.blocking:
        assessment.verdict = VERDICT_NO_GO
    elif assessment.warnings:
        assessment.verdict = VERDICT_CAUTION
    else:
        assessment.verdict = VERDICT_GO
    return assessment


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
@transaction.atomic
def set_primary_spot(spot: SurfSpot, *, request=None, user=None) -> SurfSpot:
    """Make *spot* the school's default, demoting whichever spot held the flag."""
    if not spot.is_active:
        raise SpotOperationError(
            {"is_primary": _("Reactivate this spot before making it the default.")}
        )

    previous = SurfSpot.objects.filter(is_primary=True).exclude(pk=spot.pk).first()
    SurfSpot.all_objects.filter(is_primary=True).exclude(pk=spot.pk).update(is_primary=False)
    SurfSpot.all_objects.filter(pk=spot.pk).update(is_primary=True)
    spot.is_primary = True

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=spot,
        user=user,
        description=_("%(spot)s set as the default surf spot") % {"spot": spot.name},
        changes={"is_primary": [previous.name if previous else None, spot.name]},
    )
    return spot


def can_archive_spot(spot: SurfSpot) -> tuple[bool, str]:
    """May this spot be taken out of service?

    A school with a single live spot cannot archive it — every downstream module
    resolves a default spot, and removing the last one leaves them with nothing.
    """
    remaining = SurfSpot.objects.filter(is_active=True).exclude(pk=spot.pk).count()
    if spot.is_active and remaining == 0:
        return False, str(
            _("This is the only active spot. Add another before archiving it.")
        )
    return True, ""


@transaction.atomic
def archive_spot(spot: SurfSpot, *, request=None, user=None) -> SurfSpot:
    """Soft-delete a spot, refusing when it is the last one standing."""
    allowed, reason = can_archive_spot(spot)
    if not allowed:
        raise SpotOperationError({"__all__": reason})

    spot.is_active = False
    spot.save(update_fields=["is_active", "updated_at"])
    spot.delete()
    record_audit(
        request,
        action=AuditAction.DELETE,
        instance=spot,
        user=user,
        description=_("Surf spot %(spot)s archived") % {"spot": spot.name},
    )
    return spot


@transaction.atomic
def set_hazard_active(hazard, *, is_active: bool, request=None, user=None):
    """Open or close a hazard, keeping it in the record either way."""
    if hazard.is_active == is_active:
        return hazard
    hazard.is_active = is_active
    hazard.full_clean()
    hazard.save(update_fields=["is_active", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=hazard,
        user=user,
        description=(
            _("Hazard %(name)s reopened at %(spot)s")
            if is_active
            else _("Hazard %(name)s cleared at %(spot)s")
        )
        % {"name": hazard.name, "spot": hazard.spot.name},
        changes={"is_active": [not is_active, is_active]},
    )
    return hazard


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def spot_overview_stats() -> dict:
    """Headline numbers for the locations list screen."""
    queryset = SurfSpot.objects.all()
    aggregates = queryset.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        lifeguarded=Count("id", filter=Q(is_active=True, lifeguard_on_duty=True)),
    )
    at_risk = (
        queryset.filter(
            is_active=True,
            hazards__is_active=True,
            hazards__severity__in=[Severity.HIGH, Severity.CRITICAL],
        )
        .distinct()
        .count()
    )
    aggregates["at_risk"] = at_risk
    aggregates["total_capacity"] = queryset.filter(is_active=True).aggregate(
        total=Coalesce(Sum("capacity"), Value(0))
    )["total"]
    return aggregates
