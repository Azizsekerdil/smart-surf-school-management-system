"""Business rules for the equipment fleet.

Everything that decides something lives here: what "available" means, which
board a rider should be given, whether a status change is legal, and how a CSV
import is validated. Views orchestrate, this module decides.

Cross-app coupling
------------------
Rentals are looked up **lazily** through the app registry. The equipment module
must keep working before ``apps.rentals`` exists and must not import it at
module level. The field names it hopes to find are listed in
:data:`_RENTAL_START_PATHS` / :data:`_RENTAL_END_PATHS`; every one of them is
optional and the code degrades to a coarser answer instead of crashing.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.csv_safety import safe_csv_writer
from apps.core.enums import (
    UNAVAILABLE_EQUIPMENT_STATUSES,
    EquipmentCondition,
    EquipmentStatus,
    SurfLevel,
    level_rank,
    recommended_board_volume,
    recommended_wetsuit,
)
from apps.core.utils import next_sequential_code, to_decimal

from .models import (
    ASSET_CODE_PREFIX,
    ASSET_CODE_WIDTH,
    OPERATING_HOURS_PER_DAY,
    Equipment,
    EquipmentCategory,
)

# ---------------------------------------------------------------------------
# Category taxonomy
# ---------------------------------------------------------------------------
#: The standard tree every surf school starts from. ``(code, name, parent_code,
#: icon, sort_order)``. Seeded through :func:`ensure_default_categories`, never
#: through a data migration, so an operator can re-run it after pruning.
DEFAULT_CATEGORIES: tuple[tuple[str, str, str | None, str, int], ...] = (
    ("surfboard", "Surfboard", None, "waves", 10),
    ("longboard", "Longboard", "surfboard", "waves", 11),
    ("shortboard", "Shortboard", "surfboard", "waves", 12),
    ("softboard", "Softboard", "surfboard", "waves", 13),
    ("sup", "SUP", None, "anchor", 20),
    ("wetsuit", "Wetsuit", None, "umbrella", 30),
    ("leash", "Leash", None, "anchor", 40),
    ("fin", "Fin", None, "triangle-alert", 50),
    ("boots", "Boots", None, "package", 60),
    ("gloves", "Gloves", None, "package", 70),
    ("helmet", "Helmet", None, "shield-check", 80),
    ("life_jacket", "Life Jacket", None, "life-buoy", 90),
    ("camera", "Camera", None, "camera", 100),
    ("other", "Other Equipment", None, "package", 999),
)

#: Categories whose items are boards for the purposes of the size recommender.
BOARD_CATEGORY_CODES: tuple[str, ...] = (
    "surfboard",
    "longboard",
    "shortboard",
    "softboard",
    "sup",
)

#: Soft-construction boards. Handing a beginner a hard board is a compliance
#: failure, not a preference (Surfing England surf-school scheme).
SOFT_BOARD_CATEGORY_CODES: tuple[str, ...] = ("softboard",)

#: Levels that must be given a soft-top board.
SOFT_BOARD_REQUIRED_LEVELS: tuple[str, ...] = (
    SurfLevel.FIRST_TIME,
    SurfLevel.BEGINNER,
    SurfLevel.ADVANCED_BEGINNER,
)

WETSUIT_CATEGORY_CODES: tuple[str, ...] = ("wetsuit",)

#: Recommended soft-top length band (cm) by rider weight (kg):
#: ``(weight_from, weight_to, length_min_cm, length_max_cm)``.
#: 213 cm = 7'0", 229 cm = 7'6", 244 cm = 8'0", 274 cm = 9'0".
SOFTBOARD_LENGTH_BANDS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 65.0, 213, 229),
    (65.0, 75.0, 213, 229),
    (75.0, 85.0, 229, 244),
    (85.0, 999.0, 244, 274),
)

#: Water temperature thresholds (°C) for neoprene accessories. Below these the
#: item stops being a nicety and becomes an inventory obligation.
BOOTS_BELOW_C = 15.0
GLOVES_BELOW_C = 13.0
HOOD_BELOW_C = 10.0


# ---------------------------------------------------------------------------
# Rentals integration (lazy, optional)
# ---------------------------------------------------------------------------
RENTAL_ITEM_LABEL = "rentals.RentalItem"

#: Where a rental item's window may live, in order of preference.
_RENTAL_START_PATHS = ("start_at", "starts_at", "rental__start_at", "rental__starts_at")
_RENTAL_END_PATHS = (
    "due_at",
    "end_at",
    "ends_at",
    "rental__due_at",
    "rental__end_at",
    "rental__ends_at",
)
_RENTAL_RETURNED_PATHS = ("returned_at", "rental__returned_at")
_RENTAL_STATUS_PATHS = ("status", "rental__status")

#: Rental states in which the item is back on the rack.
CLOSED_RENTAL_STATUSES: tuple[str, ...] = (
    "draft",
    "cancelled",
    "returned",
    "completed",
    "closed",
)

#: Equipment statuses meaning "physically out of the racks right now".
IN_CIRCULATION_STATUSES: tuple[str, ...] = (
    EquipmentStatus.RENTED,
    EquipmentStatus.IN_LESSON,
    EquipmentStatus.RESERVED,
)


def _get_model(label: str):
    """Return a model class by label, or ``None`` if that app is not installed."""
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _path_exists(model, path: str) -> bool:
    """True when every segment of an ORM lookup path resolves on *model*."""
    current = model
    segments = path.split("__")
    for index, segment in enumerate(segments):
        if current is None:
            return False
        try:
            model_field = current._meta.get_field(segment)
        except (FieldDoesNotExist, AttributeError):
            return False
        if index < len(segments) - 1:
            current = getattr(model_field, "related_model", None)
    return True


def _first_path(model, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if _path_exists(model, candidate):
            return candidate
    return None


def busy_equipment_ids(start=None, end=None) -> set[int]:
    """Ids of items committed to a rental overlapping ``[start, end)``.

    Returns an empty set when the rentals app is not installed — zero rows means
    "nothing is booked", which is the correct answer for a school that has not
    started renting yet.
    """
    rental_item = _get_model(RENTAL_ITEM_LABEL)
    if rental_item is None or not _path_exists(rental_item, "equipment"):
        return set()

    if start is None and end is None:
        start = end = timezone.now()
    elif start is None:
        start = end
    elif end is None:
        end = start

    queryset = rental_item._default_manager.all()

    start_path = _first_path(rental_item, _RENTAL_START_PATHS)
    end_path = _first_path(rental_item, _RENTAL_END_PATHS)
    if start_path and end_path:
        # Half-open overlap: an item due back at 10:00 is free for a 10:00 pickup.
        queryset = queryset.filter(**{f"{start_path}__lt": end, f"{end_path}__gt": start})

    returned_path = _first_path(rental_item, _RENTAL_RETURNED_PATHS)
    if returned_path:
        queryset = queryset.filter(**{f"{returned_path}__isnull": True})

    status_path = _first_path(rental_item, _RENTAL_STATUS_PATHS)
    if status_path:
        queryset = queryset.exclude(**{f"{status_path}__in": CLOSED_RENTAL_STATUSES})

    return set(queryset.values_list("equipment_id", flat=True))


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def ensure_default_categories() -> tuple[int, int]:
    """Create any missing standard category. Returns ``(created, untouched)``.

    Idempotent: existing categories are never overwritten, so a school that
    renamed "Softboard" to "Foamie" keeps its name.
    """
    created = 0
    untouched = 0
    lookup: dict[str, EquipmentCategory] = {
        category.code: category for category in EquipmentCategory.objects.all()
    }
    for code, name, parent_code, icon, sort_order in DEFAULT_CATEGORIES:
        if code in lookup:
            untouched += 1
            continue
        parent = lookup.get(parent_code) if parent_code else None
        category = EquipmentCategory(
            code=code, name=name, parent=parent, icon=icon, sort_order=sort_order
        )
        category.full_clean()
        category.save()
        lookup[code] = category
        created += 1
    return created, untouched


def category_ids_including_children(category) -> list[int]:
    """The category and every descendant — filters must include sub-categories."""
    if category is None:
        return []
    if isinstance(category, EquipmentCategory):
        return category.descendant_ids
    lookup = "pk" if str(category).isdigit() else "code"
    resolved = EquipmentCategory.objects.filter(**{lookup: category}).first()
    return resolved.descendant_ids if resolved else []


# ---------------------------------------------------------------------------
# Asset codes
# ---------------------------------------------------------------------------
def generate_asset_code(category=None) -> str:
    """Return the next free ``EQ00001`` asset code.

    The series is deliberately global rather than per-category: a scanned label
    has to identify an item on its own, without the scanner first knowing which
    category it belongs to. ``category`` is accepted so a school that later
    wants per-category series only has to change this one function.
    """
    del category  # the series is global by design; see the docstring
    return next_sequential_code(
        Equipment, "asset_code", ASSET_CODE_PREFIX, width=ASSET_CODE_WIDTH
    )


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def available_equipment(
    category=None,
    start=None,
    end=None,
    level: str | None = None,
    rider_weight_kg=None,
) -> QuerySet[Equipment]:
    """Items that may be handed out for the window ``[start, end)``.

    Excludes
    * anything in :data:`~apps.core.enums.UNAVAILABLE_EQUIPMENT_STATUSES`
      (maintenance, damaged, lost, retired),
    * anything whose condition is unusable,
    * anything already committed to an overlapping rental,
    * and — when the window includes *now* — anything physically out of the
      racks (rented, in a lesson, reserved).

    ``level`` and ``rider_weight_kg`` narrow the result to gear the person is
    actually allowed to use.
    """
    queryset = (
        Equipment.objects.select_related("category")
        .exclude(status__in=UNAVAILABLE_EQUIPMENT_STATUSES)
        .exclude(condition=EquipmentCondition.UNUSABLE)
        .filter(retired_at__isnull=True)
    )

    if category is not None:
        ids = category_ids_including_children(category)
        queryset = queryset.filter(category_id__in=ids) if ids else queryset.none()

    if level:
        rank = level_rank(level)
        allowed_min = [value for value in SurfLevel.values if level_rank(value) <= rank]
        allowed_max = [value for value in SurfLevel.values if level_rank(value) >= rank]
        queryset = queryset.filter(
            suitable_min_level__in=allowed_min, suitable_max_level__in=allowed_max
        )

    weight = _as_float(rider_weight_kg)
    if weight is not None:
        queryset = queryset.filter(
            Q(min_rider_weight_kg__isnull=True) | Q(min_rider_weight_kg__lte=weight),
            Q(max_rider_weight_kg__isnull=True) | Q(max_rider_weight_kg__gte=weight),
        )

    now = timezone.now()
    if start is None or start <= now:
        # The window covers the present, so gear that is out right now is out.
        queryset = queryset.exclude(status__in=IN_CIRCULATION_STATUSES)

    busy = busy_equipment_ids(start, end)
    if busy:
        queryset = queryset.exclude(pk__in=busy)

    return queryset


def is_available_for(equipment: Equipment, start=None, end=None) -> bool:
    """Whether one specific item is free for a window."""
    return available_equipment(start=start, end=end).filter(pk=equipment.pk).exists()


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
@dataclass
class BoardRecommendation:
    """The board to hand over, and why."""

    equipment: Equipment | None
    target_volume_litres: float | None
    recommended_length_cm: tuple[int, int] | None
    reasoning: str
    alternatives: list[Equipment] = field(default_factory=list)
    soft_top_required: bool = False


@dataclass
class WetsuitRecommendation:
    """The suit to hand over, plus the accessories that are not optional."""

    equipment: Equipment | None
    thickness: str
    recommendation: str
    required_accessories: list[str]
    reasoning: str
    alternatives: list[Equipment] = field(default_factory=list)


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rider_weight_of(student_or_weight) -> float | None:
    """Accept a Student, a Customer, a number or a numeric string."""
    direct = _as_float(student_or_weight)
    if direct is not None:
        return direct
    for attribute in ("weight_kg", "weight", "body_weight_kg"):
        value = _as_float(getattr(student_or_weight, attribute, None))
        if value:
            return value
    return None


def _length_band_for(weight_kg: float | None) -> tuple[int, int] | None:
    if weight_kg is None:
        return None
    for low, high, length_min, length_max in SOFTBOARD_LENGTH_BANDS:
        if low <= weight_kg < high:
            return (length_min, length_max)
    return None


def recommend_board(student_or_weight, level: str) -> BoardRecommendation:
    """Pick the closest available board for a rider.

    Volume target comes from
    :func:`~apps.core.enums.recommended_board_volume`; the length band is the
    secondary sanity check the rental-fleet research calls for. For first-time,
    beginner and advanced-beginner riders a **soft-top is mandatory** — that is
    a regulatory constraint, so the filter is hard, not a preference.
    """
    weight = rider_weight_of(student_or_weight)
    level = level or SurfLevel.FIRST_TIME
    target = recommended_board_volume(weight, level)
    band = _length_band_for(weight)
    soft_required = level in SOFT_BOARD_REQUIRED_LEVELS

    category_codes = SOFT_BOARD_CATEGORY_CODES if soft_required else BOARD_CATEGORY_CODES
    category_ids: list[int] = []
    for code in category_codes:
        category = EquipmentCategory.objects.filter(code=code).first()
        if category:
            category_ids.extend(category.descendant_ids)

    level_label = str(SurfLevel(level).label) if level in SurfLevel.values else str(level)

    if not category_ids:
        return BoardRecommendation(
            equipment=None,
            target_volume_litres=target,
            recommended_length_cm=band,
            reasoning=_(
                "No board categories are set up yet. Load the standard categories "
                "first, then add boards to the fleet."
            ),
            soft_top_required=soft_required,
        )

    candidates = list(
        available_equipment(level=level, rider_weight_kg=weight)
        .filter(category_id__in=category_ids)
        .order_by("asset_code")[:200]
    )

    if not candidates:
        reasoning = (
            _(
                "No soft-top board is free for a %(level)s rider right now. A "
                "soft-top is mandatory at this level, so a hard board is not a "
                "legal substitute."
            )
            % {"level": level_label}
            if soft_required
            else _("No board is free for a %(level)s rider right now.")
            % {"level": level_label}
        )
        return BoardRecommendation(
            equipment=None,
            target_volume_litres=target,
            recommended_length_cm=band,
            reasoning=reasoning,
            soft_top_required=soft_required,
        )

    def score(item: Equipment) -> tuple[float, float, str]:
        volume_gap = (
            abs(float(item.volume_litres) - target)
            if (item.volume_litres is not None and target is not None)
            else 999.0
        )
        length_gap = 0.0
        if band and item.length_cm is not None:
            length = float(item.length_cm)
            if length < band[0]:
                length_gap = band[0] - length
            elif length > band[1]:
                length_gap = length - band[1]
        elif band:
            length_gap = 50.0  # unknown length: rank below boards we can verify
        return (volume_gap, length_gap, item.asset_code)

    ranked = sorted(candidates, key=score)
    best = ranked[0]

    details: list[str] = []
    if weight:
        details.append(_("rider %(weight)s kg") % {"weight": f"{weight:g}"})
    details.append(_("level %(level)s") % {"level": level_label})
    if target:
        details.append(_("target volume %(volume)s L") % {"volume": f"{target:g}"})
    if band:
        details.append(
            _("length band %(low)s–%(high)s cm") % {"low": band[0], "high": band[1]}
        )

    reasoning = _("%(code)s chosen for %(details)s.") % {
        "code": best.asset_code,
        "details": ", ".join(str(part) for part in details),
    }
    if best.volume_litres is not None and target:
        reasoning += " " + _("It floats %(volume)s L, %(gap)s L from the target.") % {
            "volume": f"{float(best.volume_litres):g}",
            "gap": f"{abs(float(best.volume_litres) - target):.1f}",
        }
    elif best.volume_litres is None:
        reasoning += " " + _("Volume is not recorded for this board — verify by eye.")
    if soft_required:
        reasoning += " " + _(
            "Soft-top only: mandatory for first-timers, beginners and low improvers."
        )

    return BoardRecommendation(
        equipment=best,
        target_volume_litres=target,
        recommended_length_cm=band,
        reasoning=str(reasoning),
        alternatives=ranked[1:5],
        soft_top_required=soft_required,
    )


def _torso_mm(thickness: str) -> float | None:
    """``"4/3"`` -> ``4.0``; ``"3"`` -> ``3.0``; anything else -> ``None``."""
    match = re.search(r"(\d+(?:\.\d+)?)", thickness or "")
    return float(match.group(1)) if match else None


def required_wetsuit_accessories(water_temp_c) -> list[str]:
    """Neoprene accessories that stop being optional at this temperature."""
    temperature = _as_float(water_temp_c)
    if temperature is None:
        return []
    accessories: list[str] = []
    if temperature < BOOTS_BELOW_C:
        accessories.append(str(_("Boots")))
    if temperature < GLOVES_BELOW_C:
        accessories.append(str(_("Gloves")))
    if temperature < HOOD_BELOW_C:
        accessories.append(str(_("Hood")))
    return accessories


def recommend_wetsuit(water_temp_c, size: str) -> WetsuitRecommendation:
    """Pick an available suit of *size* warm enough for the water.

    The thickness table lives in :mod:`apps.core.enums`. When the exact
    thickness is not in stock the search steps **warmer**, never colder: a
    student standing still in waist-deep water gets cold faster than the table
    predicts.
    """
    recommendation = recommended_wetsuit(_as_float(water_temp_c))
    thickness = ""
    match = re.search(r"(\d+/\d+)", recommendation)
    if match:
        thickness = match.group(1)
    accessories = required_wetsuit_accessories(water_temp_c)

    category_ids: list[int] = []
    for code in WETSUIT_CATEGORY_CODES:
        category = EquipmentCategory.objects.filter(code=code).first()
        if category:
            category_ids.extend(category.descendant_ids)

    if not category_ids:
        return WetsuitRecommendation(
            equipment=None,
            thickness=thickness,
            recommendation=recommendation,
            required_accessories=accessories,
            reasoning=str(
                _("No wetsuit category exists yet — load the standard categories first.")
            ),
        )

    queryset = available_equipment().filter(category_id__in=category_ids)
    if size:
        queryset = queryset.filter(size_label__iexact=size.strip())
    candidates = list(queryset.order_by("asset_code")[:200])

    if not candidates:
        return WetsuitRecommendation(
            equipment=None,
            thickness=thickness,
            recommendation=recommendation,
            required_accessories=accessories,
            reasoning=str(
                _("No wetsuit in size %(size)s is free right now.") % {"size": size or "—"}
            ),
        )

    target_mm = _torso_mm(thickness)

    def score(item: Equipment) -> tuple[int, float, str]:
        item_mm = _torso_mm(item.wetsuit_thickness)
        if target_mm is None or item_mm is None:
            return (2, 0.0, item.asset_code)
        if item_mm >= target_mm:
            return (0, item_mm - target_mm, item.asset_code)  # warm enough
        return (1, target_mm - item_mm, item.asset_code)  # too thin, last resort

    ranked = sorted(candidates, key=score)
    best = ranked[0]

    reasoning = _(
        "%(temp)s °C water calls for %(recommendation)s. %(code)s (%(thickness)s) "
        "in size %(size)s is the closest suit in stock."
    ) % {
        "temp": f"{_as_float(water_temp_c):g}" if _as_float(water_temp_c) is not None else "—",
        "recommendation": recommendation,
        "code": best.asset_code,
        "thickness": best.wetsuit_thickness or _("thickness not recorded"),
        "size": best.size_label or "—",
    }
    best_mm = _torso_mm(best.wetsuit_thickness)
    if target_mm is not None and best_mm is not None and best_mm < target_mm:
        reasoning += " " + str(
            _("Warning: this suit is thinner than recommended — shorten the session.")
        )
    if accessories:
        reasoning += " " + str(
            _("Required accessories: %(list)s.") % {"list": ", ".join(accessories)}
        )

    return WetsuitRecommendation(
        equipment=best,
        thickness=thickness,
        recommendation=recommendation,
        required_accessories=accessories,
        reasoning=str(reasoning),
        alternatives=ranked[1:5],
    )


# ---------------------------------------------------------------------------
# Status changes
# ---------------------------------------------------------------------------
#: Legal moves of the equipment state machine.
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    EquipmentStatus.AVAILABLE: (
        EquipmentStatus.RESERVED,
        EquipmentStatus.RENTED,
        EquipmentStatus.IN_LESSON,
        EquipmentStatus.MAINTENANCE,
        EquipmentStatus.DAMAGED,
        EquipmentStatus.LOST,
        EquipmentStatus.RETIRED,
    ),
    EquipmentStatus.RESERVED: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.RENTED,
        EquipmentStatus.IN_LESSON,
        EquipmentStatus.MAINTENANCE,
        EquipmentStatus.DAMAGED,
        EquipmentStatus.LOST,
    ),
    EquipmentStatus.RENTED: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.MAINTENANCE,
        EquipmentStatus.DAMAGED,
        EquipmentStatus.LOST,
    ),
    EquipmentStatus.IN_LESSON: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.MAINTENANCE,
        EquipmentStatus.DAMAGED,
        EquipmentStatus.LOST,
    ),
    EquipmentStatus.MAINTENANCE: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.DAMAGED,
        EquipmentStatus.LOST,
        EquipmentStatus.RETIRED,
    ),
    EquipmentStatus.DAMAGED: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.MAINTENANCE,
        EquipmentStatus.LOST,
        EquipmentStatus.RETIRED,
    ),
    EquipmentStatus.LOST: (
        EquipmentStatus.AVAILABLE,
        EquipmentStatus.RETIRED,
    ),
    EquipmentStatus.RETIRED: (EquipmentStatus.AVAILABLE,),
}

#: Moves an operator must justify in writing.
STATUS_REASON_REQUIRED: tuple[str, ...] = (
    EquipmentStatus.MAINTENANCE,
    EquipmentStatus.DAMAGED,
    EquipmentStatus.LOST,
    EquipmentStatus.RETIRED,
)


def allowed_next_statuses(equipment: Equipment) -> tuple[str, ...]:
    """Statuses this item may legally move to."""
    return STATUS_TRANSITIONS.get(equipment.status, ())


@transaction.atomic
def change_status(
    equipment: Equipment,
    new_status: str,
    user=None,
    reason: str = "",
    request=None,
) -> Equipment:
    """Move an item through its state machine and audit the move.

    Refuses the moves that cause real operational damage: marking a board
    available while a customer still has it, retiring something that is out on
    a rental, and putting unusable gear back into circulation.
    """
    reason = (reason or "").strip()
    old_status = equipment.status

    if new_status not in dict(EquipmentStatus.choices):
        raise ValidationError({"status": _("Unknown status.")})

    if new_status == old_status:
        return equipment

    if new_status not in STATUS_TRANSITIONS.get(old_status, ()):
        raise ValidationError(
            {
                "status": _("“%(old)s” cannot become “%(new)s”.")
                % {
                    "old": EquipmentStatus(old_status).label,
                    "new": EquipmentStatus(new_status).label,
                }
            }
        )

    if new_status in STATUS_REASON_REQUIRED and not reason:
        raise ValidationError({"reason": _("Give a reason for this change.")})

    if new_status == EquipmentStatus.AVAILABLE:
        if equipment.condition == EquipmentCondition.UNUSABLE:
            raise ValidationError(
                {
                    "status": _(
                        "This item is recorded as unusable. Update its condition "
                        "after repair before returning it to service."
                    )
                }
            )
        if equipment.pk in busy_equipment_ids():
            raise ValidationError(
                {
                    "status": _(
                        "A customer still has this item on an open rental. Check it "
                        "back in first."
                    )
                }
            )

    if new_status in (
        EquipmentStatus.RETIRED,
        EquipmentStatus.MAINTENANCE,
    ) and old_status in (EquipmentStatus.RENTED, EquipmentStatus.IN_LESSON):
        raise ValidationError(
            {
                "status": _(
                    "The item is out with a customer. Take it back before "
                    "retiring or servicing it."
                )
            }
        )

    equipment.status = new_status
    if new_status == EquipmentStatus.RETIRED:
        equipment.retired_at = timezone.now()
        equipment.retired_reason = reason[:250]
    elif old_status == EquipmentStatus.RETIRED:
        equipment.retired_at = None
        equipment.retired_reason = ""

    if user is not None and getattr(user, "is_authenticated", False):
        equipment.updated_by = user

    equipment.full_clean()
    equipment.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=equipment,
        user=user,
        changes={"status": [old_status, new_status]},
        description=_("%(code)s: %(old)s → %(new)s. %(reason)s")
        % {
            "code": equipment.asset_code,
            "old": EquipmentStatus(old_status).label,
            "new": EquipmentStatus(new_status).label,
            "reason": reason,
        },
    )
    return equipment


def archive_equipment(equipment: Equipment, user=None, request=None) -> Equipment:
    """Soft-delete an item, refusing while it is still in someone's hands.

    Equipment rows are referenced by rentals, lessons and maintenance history,
    so they are never destroyed — archiving hides them from every list while the
    history stays intact.
    """
    if equipment.status in IN_CIRCULATION_STATUSES:
        raise ValidationError(
            {
                "status": _(
                    "This item is out with a customer or a lesson. Check it back in "
                    "before archiving it."
                )
            }
        )
    if equipment.pk in busy_equipment_ids():
        raise ValidationError(
            {"status": _("An open rental still references this item.")}
        )

    if user is not None and getattr(user, "is_authenticated", False):
        equipment.updated_by = user
        equipment.save(update_fields=["updated_by", "updated_at"])
    equipment.delete()
    record_audit(
        request,
        action=AuditAction.DELETE,
        instance=equipment,
        user=user,
        description=_("Equipment %(code)s archived") % {"code": equipment.asset_code},
    )
    return equipment


def register_rental_usage(equipment: Equipment, hours, user=None, request=None) -> Equipment:
    """Add one completed rental to an item's lifetime counters.

    Exposed here so the rentals module has a single, audited place to update the
    figures that feed the utilisation report.
    """
    increment = to_decimal(hours)
    if increment < 0:
        raise ValidationError({"hours": _("Rental hours cannot be negative.")})
    equipment.total_rentals = (equipment.total_rentals or 0) + 1
    equipment.total_rental_hours = (equipment.total_rental_hours or Decimal("0.00")) + increment
    if user is not None and getattr(user, "is_authenticated", False):
        equipment.updated_by = user
    equipment.save(
        update_fields=["total_rentals", "total_rental_hours", "updated_by", "updated_at"]
    )
    record_audit(
        request,
        action=AuditAction.RENTAL_RETURN,
        instance=equipment,
        user=user,
        description=_("%(code)s used for %(hours)s h")
        % {"code": equipment.asset_code, "hours": f"{increment:.2f}"},
    )
    return equipment


# ---------------------------------------------------------------------------
# Utilisation
# ---------------------------------------------------------------------------
def _window_days(start, end) -> int:
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    if not start_date or not end_date:
        return 0
    return max((end_date - start_date).days + 1, 1)


def utilisation_report(start=None, end=None, category=None) -> list[dict]:
    """Per-item usage over a window, busiest first.

    When the rentals module is installed the figures come from actual rental
    rows; before that they fall back to the item's lifetime counters and the row
    is flagged ``is_lifetime`` so the screen can say so rather than pretend.
    """
    if end is None:
        end = timezone.now()
    if start is None:
        start = end - timedelta(days=30)

    days = _window_days(start, end)
    capacity = Decimal(days) * OPERATING_HOURS_PER_DAY

    queryset = Equipment.objects.select_related("category")
    if category is not None:
        ids = category_ids_including_children(category)
        queryset = queryset.filter(category_id__in=ids) if ids else queryset.none()

    per_item_hours: dict[int, Decimal] = {}
    per_item_count: dict[int, int] = {}
    is_lifetime = True

    rental_item = _get_model(RENTAL_ITEM_LABEL)
    if rental_item is not None and _path_exists(rental_item, "equipment"):
        start_path = _first_path(rental_item, _RENTAL_START_PATHS)
        end_path = _first_path(rental_item, _RENTAL_END_PATHS)
        if start_path and end_path:
            is_lifetime = False
            rental_rows = rental_item._default_manager.filter(
                **{f"{start_path}__lt": end, f"{end_path}__gt": start}
            )
            status_path = _first_path(rental_item, _RENTAL_STATUS_PATHS)
            if status_path:
                # A cancelled or never-confirmed rental never occupied the item.
                rental_rows = rental_rows.exclude(
                    **{f"{status_path}__in": ("draft", "cancelled")}
                )
            for equipment_id, item_start, item_end in rental_rows.values_list(
                "equipment_id", start_path, end_path
            ):
                if not item_start or not item_end:
                    continue
                overlap_start = max(item_start, start)
                overlap_end = min(item_end, end)
                hours = Decimal(
                    max((overlap_end - overlap_start).total_seconds(), 0) / 3600
                ).quantize(Decimal("0.01"))
                per_item_hours[equipment_id] = per_item_hours.get(
                    equipment_id, Decimal("0.00")
                ) + hours
                per_item_count[equipment_id] = per_item_count.get(equipment_id, 0) + 1

    report: list[dict] = []
    for item in queryset:
        if is_lifetime:
            hours = Decimal(item.total_rental_hours or 0)
            count = item.total_rentals or 0
            percent = item.utilisation_rate
        else:
            hours = per_item_hours.get(item.pk, Decimal("0.00"))
            count = per_item_count.get(item.pk, 0)
            percent = (
                min((hours / capacity) * Decimal("100"), Decimal("100.00")).quantize(
                    Decimal("0.01")
                )
                if capacity > 0
                else None
            )
        report.append(
            {
                "equipment": item,
                "asset_code": item.asset_code,
                "name": item.name,
                "category": item.category.name,
                "status": item.status,
                "rentals": count,
                "hours": hours,
                "utilisation_percent": percent,
                "revenue_per_day": item.rental_price_daily,
                "is_lifetime": is_lifetime,
                "window_days": days,
            }
        )

    report.sort(key=lambda row: (row["utilisation_percent"] or Decimal("0")), reverse=True)
    return report


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------
#: Columns the importer understands. ``name`` and ``category_code`` are required;
#: ``asset_code`` turns the row into an update of an existing item.
IMPORT_COLUMNS: tuple[str, ...] = (
    "asset_code",
    "category_code",
    "name",
    "brand",
    "model",
    "serial_number",
    "size_label",
    "length_cm",
    "width_cm",
    "thickness_cm",
    "volume_litres",
    "wetsuit_thickness",
    "suitable_min_level",
    "suitable_max_level",
    "min_rider_weight_kg",
    "max_rider_weight_kg",
    "purchase_date",
    "purchase_price",
    "current_value",
    "supplier",
    "status",
    "condition",
    "storage_location",
    "is_rentable",
    "is_lesson_stock",
    "rental_price_hourly",
    "rental_price_daily",
    "rental_price_weekly",
    "deposit_amount",
    "notes",
)

REQUIRED_IMPORT_COLUMNS: tuple[str, ...] = ("category_code", "name")

_DECIMAL_COLUMNS = (
    "length_cm",
    "width_cm",
    "thickness_cm",
    "volume_litres",
    "min_rider_weight_kg",
    "max_rider_weight_kg",
)
_MONEY_COLUMNS = (
    "purchase_price",
    "current_value",
    "rental_price_hourly",
    "rental_price_daily",
    "rental_price_weekly",
    "deposit_amount",
)
_BOOLEAN_COLUMNS = ("is_rentable", "is_lesson_stock")
_TRUE_VALUES = {"1", "true", "yes", "y", "evet", "x", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "hayır", "hayir", "off", ""}

#: Maximum rows accepted in one upload — keeps the preview in the session small.
MAX_IMPORT_ROWS = 500


@dataclass
class RowOutcome:
    line_number: int
    action: str  # "create" | "update" | "error"
    asset_code: str
    name: str
    category: str
    message: str = ""
    data: dict = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.action == "error"


@dataclass
class ImportResult:
    rows: list[RowOutcome] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    errors: int = 0
    dry_run: bool = True

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def has_importable_rows(self) -> bool:
        return (self.created + self.updated) > 0


def parse_csv_file(uploaded_file) -> tuple[list[dict], str]:
    """Read an uploaded CSV into normalised dictionaries.

    Returns ``(rows, error_message)``; ``error_message`` is empty on success.
    Handles the Excel-on-Windows realities: a UTF-8 BOM and a semicolon
    delimiter in Turkish locales.
    """
    try:
        raw = uploaded_file.read()
    except (OSError, ValueError):
        return [], str(_("The file could not be read."))
    if isinstance(raw, bytes):
        for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            return [], str(_("The file is not readable text. Save it as UTF-8 CSV."))
    else:
        text = raw

    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return [], str(_("The file has no header row."))

    headers = [(name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames]
    missing = [column for column in REQUIRED_IMPORT_COLUMNS if column not in headers]
    if missing:
        return [], str(
            _("Missing required column(s): %(columns)s.") % {"columns": ", ".join(missing)}
        )

    rows: list[dict] = []
    for raw_row in reader:
        row = {}
        for key, value in raw_row.items():
            if key is None:
                continue
            column = key.strip().lower().replace(" ", "_")
            if column in IMPORT_COLUMNS:
                row[column] = (value or "").strip()
        if any(row.values()):
            rows.append(row)
        if len(rows) >= MAX_IMPORT_ROWS:
            break
    return rows, ""


def import_template_csv() -> str:
    """The blank template an operator downloads before filling in the fleet."""
    buffer = io.StringIO()
    writer = safe_csv_writer(buffer, lineterminator="\n")
    writer.writerow(IMPORT_COLUMNS)
    writer.writerow(
        [
            "",
            "softboard",
            "Soft-top 8'0",
            "Demo Boards",
            "Fun Model",
            "SN-100234",
            "8'0\"",
            "244",
            "56",
            "8",
            "68.00",
            "",
            "first_time",
            "advanced_beginner",
            "40",
            "95",
            "2025-04-01",
            "9500.00",
            "7200.00",
            "Demo Boards Distribution",
            "available",
            "good",
            "Container A / Rack 3",
            "yes",
            "yes",
            "150.00",
            "600.00",
            "3000.00",
            "500.00",
            "Lesson fleet board",
        ]
    )
    return buffer.getvalue()


def _coerce_row(row: dict) -> tuple[dict, list[str]]:
    """Convert raw CSV strings into model values. Returns ``(values, errors)``."""
    values: dict = {}
    errors: list[str] = []

    values["name"] = row.get("name", "").strip()
    if not values["name"]:
        errors.append(str(_("Name is required.")))

    for column in ("brand", "model", "serial_number", "size_label", "wetsuit_thickness",
                   "supplier", "storage_location", "notes"):
        values[column] = row.get(column, "").strip()

    for column in _DECIMAL_COLUMNS:
        raw = row.get(column, "").strip().replace(",", ".")
        if raw:
            try:
                values[column] = Decimal(raw)
            except (InvalidOperation, ValueError):
                errors.append(str(_("“%(column)s” is not a number.") % {"column": column}))
        else:
            values[column] = None

    for column in _MONEY_COLUMNS:
        raw = row.get(column, "").strip().replace(",", ".")
        values[column] = to_decimal(raw) if raw else Decimal("0.00")

    for column in _BOOLEAN_COLUMNS:
        raw = row.get(column, "").strip().lower()
        if raw in _TRUE_VALUES:
            values[column] = True
        elif raw in _FALSE_VALUES:
            values[column] = False
        else:
            errors.append(str(_("“%(column)s” must be yes or no.") % {"column": column}))

    purchase_date = row.get("purchase_date", "").strip()
    if purchase_date:
        parsed = None
        for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(purchase_date, pattern).date()
                break
            except ValueError:
                continue
        if parsed is None:
            errors.append(str(_("“purchase_date” must look like 2025-04-01.")))
        else:
            values["purchase_date"] = parsed
    else:
        values["purchase_date"] = None

    level_values = set(SurfLevel.values)
    minimum = row.get("suitable_min_level", "").strip().lower() or SurfLevel.FIRST_TIME
    maximum = row.get("suitable_max_level", "").strip().lower() or SurfLevel.COMPETITION
    if minimum not in level_values:
        errors.append(str(_("Unknown level “%(value)s”.") % {"value": minimum}))
    if maximum not in level_values:
        errors.append(str(_("Unknown level “%(value)s”.") % {"value": maximum}))
    values["suitable_min_level"] = minimum
    values["suitable_max_level"] = maximum

    status = row.get("status", "").strip().lower() or EquipmentStatus.AVAILABLE
    if status not in set(EquipmentStatus.values):
        errors.append(str(_("Unknown status “%(value)s”.") % {"value": status}))
        status = EquipmentStatus.AVAILABLE
    values["status"] = status

    condition = row.get("condition", "").strip().lower() or EquipmentCondition.GOOD
    if condition not in set(EquipmentCondition.values):
        errors.append(str(_("Unknown condition “%(value)s”.") % {"value": condition}))
        condition = EquipmentCondition.GOOD
    values["condition"] = condition

    return values, errors


def bulk_import_from_rows(rows, user=None, dry_run: bool = True, request=None) -> ImportResult:
    """Validate — and optionally write — a batch of equipment rows.

    Always run once with ``dry_run=True`` to build the preview, then again with
    ``dry_run=False`` after the operator confirms. Rows are independent: one bad
    line never blocks the rest, and every bad line comes back with the reason.
    """
    result = ImportResult(dry_run=dry_run)
    categories = {category.code: category for category in EquipmentCategory.objects.all()}

    for index, row in enumerate(rows, start=2):  # line 1 is the header
        asset_code = (row.get("asset_code") or "").strip().upper()
        category_code = (row.get("category_code") or "").strip().lower()
        name = (row.get("name") or "").strip()

        category = categories.get(category_code)
        if category is None:
            result.rows.append(
                RowOutcome(
                    line_number=index,
                    action="error",
                    asset_code=asset_code,
                    name=name,
                    category=category_code,
                    message=str(
                        _("Unknown category code “%(code)s”.") % {"code": category_code}
                    ),
                    data=row,
                )
            )
            result.errors += 1
            continue

        values, errors = _coerce_row(row)
        if errors:
            result.rows.append(
                RowOutcome(
                    line_number=index,
                    action="error",
                    asset_code=asset_code,
                    name=name,
                    category=category.name,
                    message=" ".join(errors),
                    data=row,
                )
            )
            result.errors += 1
            continue

        existing = (
            Equipment.all_objects.filter(asset_code=asset_code).first() if asset_code else None
        )
        instance = existing or Equipment()
        instance.category = category
        instance.asset_code = asset_code or instance.asset_code
        for attribute, value in values.items():
            setattr(instance, attribute, value)
        if user is not None and getattr(user, "is_authenticated", False):
            if existing is None:
                instance.created_by = user
            instance.updated_by = user

        try:
            instance.full_clean(exclude=["asset_code"])
        except ValidationError as exc:
            result.rows.append(
                RowOutcome(
                    line_number=index,
                    action="error",
                    asset_code=asset_code,
                    name=name,
                    category=category.name,
                    message="; ".join(
                        f"{key}: {' '.join(str(m) for m in messages)}"
                        for key, messages in exc.message_dict.items()
                    ),
                    data=row,
                )
            )
            result.errors += 1
            continue

        action = "update" if existing else "create"
        if not dry_run:
            try:
                with transaction.atomic():
                    instance.save()
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
                result.rows.append(
                    RowOutcome(
                        line_number=index,
                        action="error",
                        asset_code=asset_code,
                        name=name,
                        category=category.name,
                        message=str(exc),
                        data=row,
                    )
                )
                result.errors += 1
                continue
            record_audit(
                request,
                action=AuditAction.CREATE if action == "create" else AuditAction.UPDATE,
                instance=instance,
                user=user,
                description=_("%(code)s imported from CSV")
                % {"code": instance.asset_code},
            )

        result.rows.append(
            RowOutcome(
                line_number=index,
                action=action,
                asset_code=instance.asset_code or asset_code,
                name=instance.name,
                category=category.name,
                data=row,
            )
        )
        if action == "create":
            result.created += 1
        else:
            result.updated += 1

    return result


# ---------------------------------------------------------------------------
# Fleet summary (used by the list header and the API)
# ---------------------------------------------------------------------------
def fleet_summary() -> dict:
    """Headline counts an equipment manager looks at first thing in the morning."""
    queryset = Equipment.objects.all()
    today = timezone.localdate()
    return {
        "total": queryset.count(),
        "available": queryset.filter(status=EquipmentStatus.AVAILABLE).count(),
        "out": queryset.filter(status__in=IN_CIRCULATION_STATUSES).count(),
        "maintenance": queryset.filter(
            status__in=(EquipmentStatus.MAINTENANCE, EquipmentStatus.DAMAGED)
        ).count(),
        "service_due": queryset.filter(next_maintenance_date__lte=today)
        .exclude(status=EquipmentStatus.RETIRED)
        .count(),
        "rentable": queryset.filter(is_rentable=True).count(),
    }
