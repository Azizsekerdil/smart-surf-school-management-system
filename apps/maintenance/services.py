"""Maintenance business rules.

Everything that changes state lives here, never in a view: reporting a problem
withdraws the item from service, completing a repair puts it back (but only if
nothing else is still wrong with it), and the preventive schedule rolls forward
by itself.

The predictive part
-------------------
:func:`predict_maintenance_needs` is **deterministic statistics**. It scores
every in-service item from six independent signals, each computed from the
school's own recorded history, and returns a 0–100 risk score together with the
raw numbers behind it. When a signal has no data it is reported as unavailable
and the prediction's *confidence* falls — a number is never invented to fill the
gap. The AI layer may narrate these figures; it must never produce them.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import (
    DamageType,
    EquipmentCondition,
    EquipmentStatus,
    GenericStatus,
    Severity,
)
from apps.core.models import SystemSetting
from apps.core.utils import to_decimal

from . import selectors
from .models import (
    CONDITION_RISK,
    DAMAGING_SEVERITIES,
    MIN_HISTORY_DAYS,
    OPEN_STATUSES,
    SEVERITY_WEIGHT,
    MaintenanceRecord,
    MaintenanceSchedule,
)

logger = logging.getLogger("apps.maintenance")

#: Where the nightly forecast is parked for the board to read instantly.
PREDICTION_CACHE_KEY = "maintenance:predictions:v1"
PREDICTION_CACHE_SECONDS = 60 * 60 * 12

#: SystemSetting key holding the workshop's hourly labour rate.
LABOUR_RATE_SETTING = "maintenance.labour_hourly_rate"


# ===========================================================================
# Workflow
# ===========================================================================
def labour_hourly_rate() -> Decimal:
    """The configured workshop rate, or zero when the school has not set one."""
    return to_decimal(SystemSetting.get(LABOUR_RATE_SETTING, "0"))


def _equipment_status(equipment) -> str | None:
    return getattr(equipment, "status", None)


def _set_equipment_status(equipment, status: str) -> str | None:
    """Write a new status onto the item, returning the previous one."""
    if equipment is None or not selectors.model_has_field(type(equipment), "status"):
        return None
    previous = equipment.status
    if previous == status:
        return previous
    equipment.status = status
    equipment.save(update_fields=["status", "updated_at"])
    return previous


def _touch_last_maintenance(equipment, on: date) -> None:
    if equipment is None or not selectors.model_has_field(
        type(equipment), "last_maintenance_date"
    ):
        return
    equipment.last_maintenance_date = on
    equipment.save(update_fields=["last_maintenance_date", "updated_at"])


def _set_condition(equipment, condition: str | None) -> None:
    if not condition or equipment is None:
        return
    if condition not in EquipmentCondition.values:
        return
    if not selectors.model_has_field(type(equipment), "condition"):
        return
    equipment.condition = condition
    equipment.save(update_fields=["condition", "updated_at"])


def _blocking_open_records(record: MaintenanceRecord):
    """Other open records on the same item that also took it out of service."""
    return selectors.open_records_for_equipment(
        record.equipment_id, exclude_pk=record.pk
    ).filter(made_unusable=True)


@transaction.atomic
def report_issue(
    equipment,
    damage_type: str,
    severity: str,
    description: str,
    user=None,
    photo=None,
    make_unusable: bool = True,
    rental_item=None,
    assigned_to=None,
    request=None,
    force: bool = False,
) -> MaintenanceRecord:
    """Log a problem found on a piece of equipment and withdraw it if needed.

    Refuses to file against retired or lost equipment, and refuses a second
    open record of the same damage type on the same item unless *force* is set
    — two instructors reporting the same ding on the same morning is the normal
    case, and a silent duplicate corrupts every failure statistic downstream.
    """
    if equipment is None:
        raise ValidationError({"equipment": _("Select the piece of equipment.")})
    if not (description or "").strip():
        raise ValidationError({"description": _("Describe what is wrong with the item.")})
    if damage_type not in DamageType.values:
        raise ValidationError({"damage_type": _("Choose a valid damage type.")})
    if severity not in Severity.values:
        raise ValidationError({"severity": _("Choose a valid severity.")})

    status = _equipment_status(equipment)
    if status in (EquipmentStatus.RETIRED, EquipmentStatus.LOST):
        raise ValidationError(
            {
                "equipment": _(
                    "%(item)s is marked as %(status)s and cannot take new maintenance work."
                )
                % {"item": equipment, "status": equipment.get_status_display()}
            }
        )

    if not force:
        duplicate = (
            selectors.open_records_for_equipment(equipment.pk)
            .filter(damage_type=damage_type)
            .first()
        )
        if duplicate is not None:
            raise ValidationError(
                {
                    "damage_type": _(
                        "%(code)s is already open for this item with the same damage type. "
                        "Tick “report as a separate issue” if this really is new damage."
                    )
                    % {"code": duplicate.record_code}
                }
            )

    record = MaintenanceRecord(
        equipment=equipment,
        damage_type=damage_type,
        severity=severity,
        status=GenericStatus.OPEN,
        description=description.strip(),
        reported_by=user if getattr(user, "is_authenticated", False) else None,
        reported_at=timezone.now(),
        assigned_to=assigned_to,
        rental_item=rental_item,
        made_unusable=bool(make_unusable),
        created_by=user if getattr(user, "is_authenticated", False) else None,
        updated_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if photo is not None:
        record.photo_before = photo
    record.full_clean(exclude=["record_code"])
    record.save()

    previous_status = None
    if record.made_unusable:
        new_status = (
            EquipmentStatus.DAMAGED
            if severity in DAMAGING_SEVERITIES
            else EquipmentStatus.MAINTENANCE
        )
        previous_status = _set_equipment_status(equipment, new_status)

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=record,
        user=user,
        description=_(
            "Maintenance %(code)s opened for %(item)s (%(damage)s, %(severity)s severity)."
        )
        % {
            "code": record.record_code,
            "item": equipment,
            "damage": record.get_damage_type_display(),
            "severity": record.get_severity_display(),
        },
        changes=(
            {"equipment_status": [previous_status, _equipment_status(equipment)]}
            if record.made_unusable
            else None
        ),
    )
    return record


@transaction.atomic
def start_work(
    record: MaintenanceRecord,
    user=None,
    assigned_to=None,
    diagnosis: str = "",
    request=None,
) -> MaintenanceRecord:
    """Move a record into active repair."""
    if record.status in (GenericStatus.RESOLVED, GenericStatus.CLOSED, GenericStatus.CANCELLED):
        raise ValidationError(
            _("%(code)s is already finished and cannot be reopened as new work.")
            % {"code": record.record_code}
        )

    previous = record.status
    record.status = GenericStatus.IN_PROGRESS
    record.started_at = record.started_at or timezone.now()
    if assigned_to is not None:
        record.assigned_to = assigned_to
    elif record.assigned_to_id is None and getattr(user, "is_authenticated", False):
        record.assigned_to = user
    if diagnosis:
        record.diagnosis = diagnosis.strip()
    if getattr(user, "is_authenticated", False):
        record.updated_by = user
    record.full_clean()
    record.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=user,
        description=_("Work started on %(code)s.") % {"code": record.record_code},
        changes={"status": [previous, record.status]},
    )
    return record


@transaction.atomic
def put_on_hold(
    record: MaintenanceRecord, reason: str, user=None, request=None
) -> MaintenanceRecord:
    """Park a record — waiting for a part, waiting for the foam to dry out."""
    if not (reason or "").strip():
        raise ValidationError({"reason": _("Say why the work is on hold.")})
    if record.status not in OPEN_STATUSES:
        raise ValidationError(
            _("Only an open record can be put on hold.")
        )

    previous = record.status
    record.status = GenericStatus.ON_HOLD
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    note = _("On hold (%(when)s): %(reason)s") % {"when": stamp, "reason": reason.strip()}
    record.diagnosis = f"{record.diagnosis}\n{note}".strip() if record.diagnosis else note
    if getattr(user, "is_authenticated", False):
        record.updated_by = user
    record.full_clean()
    record.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=user,
        description=_("%(code)s put on hold.") % {"code": record.record_code},
        changes={"status": [previous, record.status]},
    )
    return record


@transaction.atomic
def complete_maintenance(
    record: MaintenanceRecord,
    resolution: str,
    costs: dict | None = None,
    user=None,
    still_unusable: bool = False,
    retire_equipment: bool = False,
    condition_after: str | None = None,
    photo_after=None,
    request=None,
) -> MaintenanceRecord:
    """Close a repair, book its cost, and put the item back into service.

    The item only returns to ``AVAILABLE`` when nothing else is wrong with it:
    a second open record that also took it out of service keeps it withdrawn.
    That check is the difference between a fleet you can trust and a board that
    goes back in the water with an unrepaired leash plug.
    """
    if record.status == GenericStatus.CANCELLED:
        raise ValidationError(
            _("%(code)s was cancelled and cannot be completed.") % {"code": record.record_code}
        )
    if not (resolution or "").strip():
        raise ValidationError({"resolution": _("Describe the repair that was carried out.")})

    costs = costs or {}
    labour_hours = to_decimal(costs.get("labour_hours", record.labour_hours))
    parts_cost = to_decimal(costs.get("parts_cost", record.parts_cost))
    labour_cost = costs.get("labour_cost")
    if labour_cost in (None, ""):
        rate = labour_hourly_rate()
        labour_cost = (labour_hours * rate) if rate > 0 else to_decimal(record.labour_cost)
    labour_cost = to_decimal(labour_cost)

    if labour_hours < 0 or parts_cost < 0 or labour_cost < 0:
        raise ValidationError(_("Costs and hours cannot be negative."))

    previous = record.status
    record.status = GenericStatus.RESOLVED
    record.resolution = resolution.strip()
    record.parts_used = (costs.get("parts_used") or record.parts_used or "").strip()
    record.labour_hours = labour_hours
    record.parts_cost = parts_cost
    record.labour_cost = labour_cost
    record.started_at = record.started_at or record.reported_at
    record.completed_at = timezone.now()
    record.made_unusable = record.made_unusable or bool(still_unusable)
    if photo_after is not None:
        record.photo_after = photo_after
    if getattr(user, "is_authenticated", False):
        record.updated_by = user
    record.recalculate_cost()
    record.full_clean()
    record.save()

    equipment = record.equipment
    completed_on = timezone.localdate()
    _set_condition(equipment, condition_after)
    _touch_last_maintenance(equipment, completed_on)

    blocking = _blocking_open_records(record)
    previous_equipment_status = _equipment_status(equipment)
    released = False

    if retire_equipment:
        _set_equipment_status(equipment, EquipmentStatus.RETIRED)
    elif still_unusable:
        _set_equipment_status(equipment, EquipmentStatus.DAMAGED)
    elif blocking.exists():
        logger.info(
            "Equipment %s stays out of service: %s other open record(s).",
            equipment,
            blocking.count(),
        )
    elif previous_equipment_status in (EquipmentStatus.MAINTENANCE, EquipmentStatus.DAMAGED):
        # Only ever release from a maintenance state: an item marked LOST or
        # RETIRED elsewhere must not silently reappear in the rental pool.
        _set_equipment_status(equipment, EquipmentStatus.AVAILABLE)
        released = True

    schedule = MaintenanceSchedule.objects.filter(equipment_id=record.equipment_id).first()
    if schedule is not None and schedule.is_active:
        schedule.mark_performed(completed_on)

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=user,
        description=_(
            "%(code)s completed for %(item)s. Cost %(cost)s. Item %(outcome)s."
        )
        % {
            "code": record.record_code,
            "item": equipment,
            "cost": record.total_cost,
            "outcome": (
                _("retired")
                if retire_equipment
                else _("back in service")
                if released
                else _("still out of service")
            ),
        },
        changes={
            "status": [previous, record.status],
            "total_cost": [None, str(record.total_cost)],
            "equipment_status": [previous_equipment_status, _equipment_status(equipment)],
        },
    )
    return record


@transaction.atomic
def cancel_maintenance(
    record: MaintenanceRecord, reason: str, user=None, request=None
) -> MaintenanceRecord:
    """Withdraw a record that should never have been filed.

    Used for mis-reports (wrong board scanned, damage that turned out to be
    sand). The item goes back into service unless something else holds it.
    """
    if not (reason or "").strip():
        raise ValidationError({"reason": _("Say why the record is being cancelled.")})
    if record.status in (GenericStatus.RESOLVED, GenericStatus.CLOSED):
        raise ValidationError(
            _("%(code)s is already completed — cancel is only for records that were never worked.")
            % {"code": record.record_code}
        )

    previous = record.status
    record.status = GenericStatus.CANCELLED
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    note = _("Cancelled (%(when)s): %(reason)s") % {"when": stamp, "reason": reason.strip()}
    record.resolution = f"{record.resolution}\n{note}".strip() if record.resolution else note
    record.completed_at = timezone.now()
    if getattr(user, "is_authenticated", False):
        record.updated_by = user
    record.full_clean()
    record.save()

    equipment = record.equipment
    previous_equipment_status = _equipment_status(equipment)
    if (
        record.made_unusable
        and previous_equipment_status
        in (EquipmentStatus.MAINTENANCE, EquipmentStatus.DAMAGED)
        and not _blocking_open_records(record).exists()
    ):
        _set_equipment_status(equipment, EquipmentStatus.AVAILABLE)

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=user,
        description=_("%(code)s cancelled.") % {"code": record.record_code},
        changes={
            "status": [previous, record.status],
            "equipment_status": [previous_equipment_status, _equipment_status(equipment)],
        },
    )
    return record


@transaction.atomic
def mark_schedule_performed(
    schedule: MaintenanceSchedule, performed_on: date | None = None, user=None, request=None
) -> MaintenanceSchedule:
    """Tick off a preventive service and roll the plan forward."""
    performed_on = performed_on or timezone.localdate()
    if performed_on > timezone.localdate():
        raise ValidationError({"performed_on": _("A service cannot be recorded in the future.")})

    previous_due = schedule.next_due_on
    schedule.mark_performed(performed_on)
    _touch_last_maintenance(schedule.equipment, performed_on)

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=schedule,
        user=user,
        description=_("Preventive service recorded for %(item)s.")
        % {"item": schedule.equipment},
        changes={"next_due_on": [str(previous_due or ""), str(schedule.next_due_on or "")]},
    )
    return schedule


def due_for_scheduled_maintenance(on: date | None = None, within_days: int = 0):
    """Active schedules whose next service is due (queryset, ordered by date)."""
    return selectors.due_schedules(on=on, within_days=within_days)


# ===========================================================================
# Cost reporting
# ===========================================================================
def maintenance_cost_report(start=None, end=None) -> dict:
    """Realised maintenance spend between *start* and *end*.

    Only resolved/closed records count: an open repair has not cost the school
    anything yet, and counting estimates as spend makes the number a lie.
    """
    totals = selectors.cost_totals(start, end)
    by_category = selectors.cost_by_category(start, end)
    by_item = selectors.cost_by_item(start, end)
    by_damage_type = selectors.cost_by_damage_type(start, end)

    records = totals.get("records") or 0
    total = totals.get("total") or Decimal("0.00")
    return {
        "start": start,
        "end": end,
        "records": records,
        "parts_cost": totals.get("parts") or Decimal("0.00"),
        "labour_cost": totals.get("labour") or Decimal("0.00"),
        "total_cost": total,
        "labour_hours": totals.get("hours") or Decimal("0.00"),
        "average_cost": (total / records).quantize(Decimal("0.01")) if records else Decimal("0.00"),
        "by_category": by_category,
        "by_item": by_item,
        "by_damage_type": by_damage_type,
    }


# ===========================================================================
# Predictive maintenance — deterministic statistics
# ===========================================================================
#: Each signal's share of the risk score. They sum to 1.0; signals with no data
#: are dropped and the remaining weights are renormalised.
SIGNAL_WEIGHTS: dict[str, float] = {
    "schedule": 0.22,
    "rentals": 0.22,
    "age": 0.16,
    "history": 0.16,
    "hours": 0.14,
    "condition": 0.10,
}

#: How much a signal's source is trusted: the item's own history beats its
#: category's average, which beats the fleet average.
QUALITY_ITEM = 1.00
QUALITY_CATEGORY = 0.65
QUALITY_FLEET = 0.40

#: Minimum sample sizes before an average is worth using at all.
MIN_ITEM_SAMPLES = 2
MIN_CATEGORY_SAMPLES = 3
MIN_FLEET_SAMPLES = 5

#: Weighted failures within a year that saturate the history signal.
HISTORY_SATURATION = 3.0

ACTION_SERVICE_NOW = "service_now"
ACTION_SCHEDULE = "schedule_service"
ACTION_INSPECT = "inspect"
ACTION_MONITOR = "monitor"
ACTION_REVIEW_RETIREMENT = "review_retirement"


def _pressure(ratio: float) -> float:
    """Map "fraction of the limit used" onto 0–1.

    Exactly at the limit scores 0.7; half again past it saturates at 1.0. The
    curve is linear in both segments so a number on the board can always be
    traced back to the ratio that produced it.
    """
    if ratio is None or ratio <= 0:
        return 0.0
    if ratio <= 1.0:
        return round(0.7 * ratio, 4)
    return round(min(1.0, 0.7 + 0.3 * ((ratio - 1.0) / 0.5)), 4)


def _mean(samples) -> float | None:
    values = [float(v) for v in samples if v is not None]
    if not values:
        return None
    return float(statistics.fmean(values))


def _median(samples) -> float | None:
    values = [float(v) for v in samples if v is not None]
    if not values:
        return None
    return float(statistics.median(values))


def _percentile(samples, percent: float) -> float | None:
    values = [float(v) for v in samples if v is not None]
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), percent))


def _rentals_between_failures(
    rental_dates: list[date], failure_dates: list[date], start: date | None
) -> list[int]:
    """How many rentals the item survived between successive failures."""
    if not failure_dates:
        return []
    samples: list[int] = []
    previous = start
    for failure_on in failure_dates:
        if previous is None:
            count = sum(1 for d in rental_dates if d <= failure_on)
        else:
            count = sum(1 for d in rental_dates if previous < d <= failure_on)
        samples.append(count)
        previous = failure_on
    return samples


def predict_maintenance_needs(limit: int | None = None) -> list[dict]:
    """Rank in-service equipment by the risk that it needs work soon.

    Returns a list of dicts sorted by ``risk_score`` descending. Every entry
    carries the numbers the score was built from, so the board can show its
    working and nothing downstream ever has to guess.

    The six signals:

    ``schedule``   days since the last service against the item's interval
    ``rentals``    rentals since the last service against its mean rentals
                   between failures
    ``hours``      cumulative rental hours against the category's 75th percentile
    ``age``        age against the expected (or observed) service life
    ``history``    count and severity of past failures in the last year
    ``condition``  the condition grade an operator last recorded

    A signal with no supporting data is returned with ``available: False`` and
    is excluded from the score; ``confidence`` reflects how much of the model
    could actually be evaluated.
    """
    today = timezone.localdate()
    equipment_rows = selectors.active_equipment_rows()
    if not equipment_rows:
        return []

    equipment_ids = [row["id"] for row in equipment_rows]
    history = selectors.maintenance_history(equipment_ids)
    rentals, rentals_available = selectors.rental_history(equipment_ids)
    hours_map, hours_available = selectors.rental_hours(equipment_ids)
    declared_lifetimes = selectors.category_lifetime_days()
    declared_intervals = selectors.category_interval_days()
    observed_lifetimes = selectors.retired_equipment_ages()

    schedules = {
        schedule.equipment_id: schedule
        for schedule in MaintenanceSchedule.objects.filter(
            equipment_id__in=equipment_ids, is_active=True
        )
    }
    open_counts: dict[int, int] = {}
    for row in (
        MaintenanceRecord.objects.filter(
            equipment_id__in=equipment_ids, status__in=OPEN_STATUSES
        )
        .values("equipment_id")
        .annotate(total=Count("id"))
    ):
        open_counts[row["equipment_id"]] = row["total"]

    # ---- fleet- and category-level baselines, built once ------------------
    interval_samples: dict[int | None, list[int]] = {}
    for equipment_id, schedule in schedules.items():
        category_id = next(
            (r["category_id"] for r in equipment_rows if r["id"] == equipment_id), None
        )
        interval_samples.setdefault(category_id, []).append(int(schedule.interval_days or 0))

    mrbf_samples: dict[int | None, list[int]] = {}
    item_mrbf: dict[int, list[int]] = {}
    if rentals_available:
        for row in equipment_rows:
            failure_dates = [
                entry["reported_on"]
                for entry in history.get(row["id"], [])
                if entry["reported_on"] and entry["made_unusable"]
            ]
            samples = _rentals_between_failures(
                rentals.get(row["id"], []),
                failure_dates,
                row["acquired_on"] or row["created_on"],
            )
            if samples:
                item_mrbf[row["id"]] = samples
                mrbf_samples.setdefault(row["category_id"], []).extend(samples)

    hours_samples: dict[int | None, list[float]] = {}
    for row in equipment_rows:
        value = hours_map.get(row["id"], row["usage_hours"])
        if value is not None:
            hours_samples.setdefault(row["category_id"], []).append(float(value))

    fleet_intervals = [v for values in interval_samples.values() for v in values]
    fleet_mrbf = [v for values in mrbf_samples.values() for v in values]
    fleet_hours = [v for values in hours_samples.values() for v in values]
    fleet_lifetimes = [v for values in observed_lifetimes.values() for v in values]

    predictions: list[dict] = []
    for row in equipment_rows:
        predictions.append(
            _score_equipment(
                row=row,
                today=today,
                schedule=schedules.get(row["id"]),
                records=history.get(row["id"], []),
                rental_dates=rentals.get(row["id"], []),
                rentals_available=rentals_available,
                hours=hours_map.get(row["id"], row["usage_hours"]),
                hours_available=hours_available or row["usage_hours"] is not None,
                open_records=open_counts.get(row["id"], 0),
                declared_intervals=declared_intervals,
                interval_samples=interval_samples,
                fleet_intervals=fleet_intervals,
                item_mrbf=item_mrbf.get(row["id"], []),
                mrbf_samples=mrbf_samples,
                fleet_mrbf=fleet_mrbf,
                hours_samples=hours_samples,
                fleet_hours=fleet_hours,
                declared_lifetimes=declared_lifetimes,
                observed_lifetimes=observed_lifetimes,
                fleet_lifetimes=fleet_lifetimes,
            )
        )

    predictions.sort(key=lambda item: (-item["risk_score"], -item["confidence"], item["equipment"]))
    if limit:
        predictions = predictions[:limit]
    return predictions


def _score_equipment(
    *,
    row: dict,
    today: date,
    schedule,
    records: list[dict],
    rental_dates: list[date],
    rentals_available: bool,
    hours: float | None,
    hours_available: bool,
    open_records: int,
    declared_intervals: dict,
    interval_samples: dict,
    fleet_intervals: list,
    item_mrbf: list[int],
    mrbf_samples: dict,
    fleet_mrbf: list,
    hours_samples: dict,
    fleet_hours: list,
    declared_lifetimes: dict,
    observed_lifetimes: dict,
    fleet_lifetimes: list,
) -> dict:
    """Score one item. Pure function of the facts handed to it."""
    category_id = row["category_id"]
    reasons: list[dict] = []

    completed_dates = [e["completed_on"] for e in records if e["completed_on"]]
    last_service = max(
        [d for d in (schedule.last_performed_on if schedule else None, row["last_maintenance_date"]) if d]
        + completed_dates,
        default=None,
    )
    in_service_since = row["acquired_on"] or row["created_on"]
    baseline = last_service or in_service_since
    serviced_ever = last_service is not None

    # ---- 1. schedule pressure --------------------------------------------
    interval, interval_quality, interval_source = None, 0.0, "none"
    if schedule is not None and schedule.interval_days:
        interval, interval_quality, interval_source = (
            int(schedule.interval_days),
            QUALITY_ITEM,
            "item",
        )
    elif declared_intervals.get(category_id):
        interval, interval_quality, interval_source = (
            declared_intervals[category_id],
            QUALITY_CATEGORY,
            "category",
        )
    elif len(interval_samples.get(category_id, [])) >= MIN_CATEGORY_SAMPLES:
        interval, interval_quality, interval_source = (
            round(_median(interval_samples[category_id]) or 0),
            QUALITY_CATEGORY,
            "category_median",
        )
    elif len(fleet_intervals) >= MIN_FLEET_SAMPLES:
        interval, interval_quality, interval_source = (
            round(_median(fleet_intervals) or 0),
            QUALITY_FLEET,
            "fleet_median",
        )

    if interval and baseline:
        days_since = max(0, (today - baseline).days)
        ratio = days_since / interval
        reasons.append(
            {
                "code": "schedule",
                "available": True,
                "score": _pressure(ratio),
                "weight": SIGNAL_WEIGHTS["schedule"],
                "quality": interval_quality * (1.0 if serviced_ever else 0.8),
                "source": interval_source,
                "values": {
                    "days_since": days_since,
                    "interval_days": interval,
                    "ratio_pct": round(ratio * 100, 1),
                    "serviced_ever": serviced_ever,
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "schedule",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["schedule"],
                "quality": 0.0,
                "source": "none",
                "values": {},
            }
        )

    # ---- 2. rentals since the last service vs mean rentals between failures
    mrbf, mrbf_quality, mrbf_source = None, 0.0, "none"
    if len(item_mrbf) >= MIN_ITEM_SAMPLES:
        mrbf, mrbf_quality, mrbf_source = _mean(item_mrbf), QUALITY_ITEM, "item"
    elif len(mrbf_samples.get(category_id, [])) >= MIN_CATEGORY_SAMPLES:
        mrbf, mrbf_quality, mrbf_source = (
            _mean(mrbf_samples[category_id]),
            QUALITY_CATEGORY,
            "category",
        )
    elif len(fleet_mrbf) >= MIN_FLEET_SAMPLES:
        mrbf, mrbf_quality, mrbf_source = _mean(fleet_mrbf), QUALITY_FLEET, "fleet"

    if rentals_available and mrbf and mrbf > 0:
        since = (
            sum(1 for d in rental_dates if baseline is None or d > baseline)
            if rental_dates
            else 0
        )
        ratio = since / mrbf
        reasons.append(
            {
                "code": "rentals",
                "available": True,
                "score": _pressure(ratio),
                "weight": SIGNAL_WEIGHTS["rentals"],
                "quality": mrbf_quality,
                "source": mrbf_source,
                "values": {
                    "rentals_since": since,
                    "mean_between_failures": round(mrbf, 1),
                    "ratio_pct": round(ratio * 100, 1),
                    "samples": len(item_mrbf) if mrbf_source == "item" else None,
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "rentals",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["rentals"],
                "quality": 0.0,
                "source": "no_rental_history" if rentals_available else "no_rental_data",
                "values": {
                    "rentals_since": (
                        sum(1 for d in rental_dates if baseline is None or d > baseline)
                        if rentals_available
                        else None
                    )
                },
            }
        )

    # ---- 3. cumulative rental hours --------------------------------------
    reference, hours_quality, hours_source = None, 0.0, "none"
    category_hours = hours_samples.get(category_id, [])
    if len(category_hours) >= MIN_CATEGORY_SAMPLES + 1:
        reference, hours_quality, hours_source = (
            _percentile(category_hours, 75),
            QUALITY_CATEGORY,
            "category_p75",
        )
    elif len(fleet_hours) >= MIN_FLEET_SAMPLES:
        reference, hours_quality, hours_source = (
            _percentile(fleet_hours, 75),
            QUALITY_FLEET,
            "fleet_p75",
        )

    if hours_available and hours is not None and reference and reference > 0:
        ratio = float(hours) / reference
        reasons.append(
            {
                "code": "hours",
                "available": True,
                "score": _pressure(ratio),
                "weight": SIGNAL_WEIGHTS["hours"],
                "quality": hours_quality,
                "source": hours_source,
                "values": {
                    "hours": round(float(hours), 1),
                    "reference_hours": round(reference, 1),
                    "ratio_pct": round(ratio * 100, 1),
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "hours",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["hours"],
                "quality": 0.0,
                "source": "no_usage_data",
                "values": {"hours": round(float(hours), 1) if hours is not None else None},
            }
        )

    # ---- 4. age against expected service life ----------------------------
    lifetime, lifetime_quality, lifetime_source = None, 0.0, "none"
    if row["expected_lifetime_days"]:
        lifetime, lifetime_quality, lifetime_source = (
            row["expected_lifetime_days"],
            QUALITY_ITEM,
            "item",
        )
    elif declared_lifetimes.get(category_id):
        lifetime, lifetime_quality, lifetime_source = (
            declared_lifetimes[category_id],
            QUALITY_CATEGORY,
            "category",
        )
    elif len(observed_lifetimes.get(category_id, [])) >= MIN_CATEGORY_SAMPLES:
        lifetime, lifetime_quality, lifetime_source = (
            round(_mean(observed_lifetimes[category_id]) or 0),
            QUALITY_CATEGORY,
            "category_observed",
        )
    elif len(fleet_lifetimes) >= MIN_FLEET_SAMPLES:
        lifetime, lifetime_quality, lifetime_source = (
            round(_mean(fleet_lifetimes) or 0),
            QUALITY_FLEET,
            "fleet_observed",
        )

    age_days = (today - in_service_since).days if in_service_since else None
    age_ratio = None
    if lifetime and lifetime > 0 and age_days is not None:
        age_ratio = age_days / lifetime
        reasons.append(
            {
                "code": "age",
                "available": True,
                "score": _pressure(age_ratio),
                "weight": SIGNAL_WEIGHTS["age"],
                "quality": lifetime_quality,
                "source": lifetime_source,
                "values": {
                    "age_days": age_days,
                    "expected_days": lifetime,
                    "ratio_pct": round(age_ratio * 100, 1),
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "age",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["age"],
                "quality": 0.0,
                "source": "no_lifetime_baseline",
                "values": {"age_days": age_days},
            }
        )

    # ---- 5. failure history ----------------------------------------------
    fleet_age = (today - in_service_since).days if in_service_since else None
    if fleet_age is not None and fleet_age >= MIN_HISTORY_DAYS:
        window_start = today - timedelta(days=365)
        recent = [
            entry
            for entry in records
            if entry["reported_on"] and entry["reported_on"] >= window_start
        ]
        weighted = sum(SEVERITY_WEIGHT.get(entry["severity"], 0.5) for entry in recent)
        reasons.append(
            {
                "code": "history",
                "available": True,
                "score": round(min(1.0, weighted / HISTORY_SATURATION), 4),
                "weight": SIGNAL_WEIGHTS["history"],
                "quality": QUALITY_ITEM,
                "source": "item",
                "values": {
                    "failures_12m": len(recent),
                    "failures_total": len(records),
                    "weighted": round(weighted, 2),
                    "observed_days": fleet_age,
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "history",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["history"],
                "quality": 0.0,
                "source": "too_new",
                "values": {"observed_days": fleet_age, "minimum_days": MIN_HISTORY_DAYS},
            }
        )

    # ---- 6. recorded condition -------------------------------------------
    condition = row["condition"]
    if condition in CONDITION_RISK:
        reasons.append(
            {
                "code": "condition",
                "available": True,
                "score": CONDITION_RISK[condition],
                "weight": SIGNAL_WEIGHTS["condition"],
                "quality": QUALITY_ITEM,
                "source": "item",
                "values": {
                    "condition": condition,
                    "condition_label": str(EquipmentCondition(condition).label),
                },
            }
        )
    else:
        reasons.append(
            {
                "code": "condition",
                "available": False,
                "score": 0.0,
                "weight": SIGNAL_WEIGHTS["condition"],
                "quality": 0.0,
                "source": "not_graded",
                "values": {},
            }
        )

    # ---- combine ----------------------------------------------------------
    available = [r for r in reasons if r["available"]]
    weight_sum = sum(r["weight"] for r in available)
    if weight_sum > 0:
        risk = sum(r["score"] * r["weight"] for r in available) / weight_sum * 100.0
        confidence = sum(r["weight"] * r["quality"] for r in available)
    else:
        risk, confidence = 0.0, 0.0

    risk = round(min(100.0, max(0.0, risk)), 1)
    confidence = round(min(1.0, max(0.0, confidence)), 2)

    if confidence >= 0.70:
        confidence_label = "high"
    elif confidence >= 0.40:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    if age_ratio is not None and age_ratio >= 1.0 and risk >= 60:
        action = ACTION_REVIEW_RETIREMENT
    elif risk >= 80:
        action = ACTION_SERVICE_NOW
    elif risk >= 60:
        action = ACTION_SCHEDULE
    elif risk >= 40:
        action = ACTION_INSPECT
    else:
        action = ACTION_MONITOR

    label = row["label"] or row["name"] or f"#{row['id']}"
    return {
        "equipment_id": row["id"],
        "equipment": label,
        "equipment_name": row["name"],
        "category": row["category"],
        "category_id": category_id,
        "status": row["status"],
        "condition": condition,
        "risk_score": risk,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "signals_used": len(available),
        "signals_total": len(reasons),
        "recommended_action": action,
        "open_records": open_records,
        "last_service_on": last_service.isoformat() if last_service else None,
        "next_due_on": (
            schedule.next_due_on.isoformat() if schedule and schedule.next_due_on else None
        ),
        "days_until_due": schedule.days_until_due if schedule else None,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Rendering the model's output as plain language
# ---------------------------------------------------------------------------
#: Human labels for the recommended actions.
ACTION_LABELS = {
    ACTION_SERVICE_NOW: lambda: _("Service now"),
    ACTION_SCHEDULE: lambda: _("Book a service"),
    ACTION_INSPECT: lambda: _("Inspect at next handover"),
    ACTION_MONITOR: lambda: _("Monitor"),
    ACTION_REVIEW_RETIREMENT: lambda: _("Review for replacement"),
}


def action_label(code: str) -> str:
    builder = ACTION_LABELS.get(code)
    return builder() if builder else code


def describe_reason(reason: dict) -> str:
    """Turn one structured signal into a sentence a staff member can act on.

    The prediction payload stores numbers, not prose, so it can be cached once
    and rendered in whichever language the reader is using.
    """
    code = reason.get("code")
    values = reason.get("values") or {}
    available = reason.get("available")

    if code == "schedule":
        if not available:
            return _("No service schedule for this item and no category baseline — schedule pressure not scored.")
        if not values.get("serviced_ever"):
            return _(
                "Never serviced since it entered the fleet %(days)s days ago; the interval is %(interval)s days (%(pct)s%% of it)."
            ) % {
                "days": values.get("days_since"),
                "interval": values.get("interval_days"),
                "pct": values.get("ratio_pct"),
            }
        return _(
            "Last serviced %(days)s days ago against a %(interval)s-day interval (%(pct)s%% of it)."
        ) % {
            "days": values.get("days_since"),
            "interval": values.get("interval_days"),
            "pct": values.get("ratio_pct"),
        }

    if code == "rentals":
        if not available:
            if reason.get("source") == "no_rental_data":
                return _("Rental usage is not recorded for this item — usage pressure not scored.")
            return _(
                "No failures recorded yet, so there is no mean-rentals-between-failures to compare against."
            )
        if reason.get("source") == "item":
            return _(
                "%(since)s rentals since the last service; this item has historically failed every %(mean)s rentals (%(pct)s%%)."
            ) % {
                "since": values.get("rentals_since"),
                "mean": values.get("mean_between_failures"),
                "pct": values.get("ratio_pct"),
            }
        return _(
            "%(since)s rentals since the last service; comparable items fail every %(mean)s rentals on average (%(pct)s%%)."
        ) % {
            "since": values.get("rentals_since"),
            "mean": values.get("mean_between_failures"),
            "pct": values.get("ratio_pct"),
        }

    if code == "hours":
        if not available:
            return _("Cumulative rental hours are not recorded — usage load not scored.")
        return _(
            "%(hours)s rental hours logged, against %(reference)s for the busiest quarter of comparable items (%(pct)s%%)."
        ) % {
            "hours": values.get("hours"),
            "reference": values.get("reference_hours"),
            "pct": values.get("ratio_pct"),
        }

    if code == "age":
        if not available:
            return _("No expected service life is recorded for this category — age not scored.")
        return _("%(age)s days old against an expected service life of %(expected)s days (%(pct)s%%).") % {
            "age": values.get("age_days"),
            "expected": values.get("expected_days"),
            "pct": values.get("ratio_pct"),
        }

    if code == "history":
        if not available:
            return _(
                "Only %(days)s days in the fleet — too new for its failure history to mean anything."
            ) % {"days": values.get("observed_days") or 0}
        if not values.get("failures_12m"):
            return _("No failures in the last 12 months (%(total)s on record all-time).") % {
                "total": values.get("failures_total")
            }
        return _(
            "%(recent)s failures in the last 12 months, %(total)s all-time (severity-weighted %(weighted)s)."
        ) % {
            "recent": values.get("failures_12m"),
            "total": values.get("failures_total"),
            "weighted": values.get("weighted"),
        }

    if code == "condition":
        if not available:
            return _("Condition has never been graded — condition not scored.")
        return _("Last graded as %(condition)s.") % {"condition": values.get("condition_label")}

    return ""


def annotate_prediction_texts(predictions: list[dict]) -> list[dict]:
    """Attach translated sentences to a cached (language-neutral) forecast."""
    for prediction in predictions:
        prediction["action_label"] = action_label(prediction.get("recommended_action", ""))
        prediction["reason_texts"] = [
            {
                "code": reason.get("code"),
                "available": reason.get("available"),
                "score": reason.get("score"),
                "text": describe_reason(reason),
            }
            for reason in prediction.get("reasons", [])
        ]
    return predictions


# ---------------------------------------------------------------------------
# Cached access
# ---------------------------------------------------------------------------
def cached_maintenance_predictions(refresh: bool = False) -> dict:
    """Return ``{"generated_at": iso, "predictions": [...]}``.

    The board reads the cache so it stays instant on a busy morning; the nightly
    task refreshes it. A cold cache computes inline rather than showing nothing.
    """
    if not refresh:
        payload = cache.get(PREDICTION_CACHE_KEY)
        if payload:
            return payload
    return store_maintenance_predictions()


def store_maintenance_predictions() -> dict:
    payload = {
        "generated_at": timezone.now().isoformat(),
        "predictions": predict_maintenance_needs(),
    }
    cache.set(PREDICTION_CACHE_KEY, payload, PREDICTION_CACHE_SECONDS)
    return payload
