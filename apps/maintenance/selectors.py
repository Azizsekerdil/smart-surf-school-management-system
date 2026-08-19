"""Read queries that feed the maintenance screens and the risk model.

Why this module exists
----------------------
The risk model needs facts that live in the ``equipment`` and ``rentals``
modules. Those modules own their field layout, so every access here goes
through a small capability probe: if a field is absent the signal that depends
on it is reported as *unavailable* and the prediction's confidence drops. The
alternative — assuming a field exists — would either crash the maintenance
board or, far worse, invent a number. Neither is acceptable for a screen that
tells a school which board to pull out of the water.

All of these functions are bulk queries: the risk model must never issue one
query per item.
"""

from __future__ import annotations

import functools
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.enums import EquipmentStatus, GenericStatus, Severity

from .models import (
    COSTED_STATUSES,
    OPEN_STATUSES,
    MaintenanceRecord,
    MaintenanceSchedule,
)

# ---------------------------------------------------------------------------
# Candidate field names in the sibling modules, most specific first.
# ---------------------------------------------------------------------------
EQUIPMENT_PURCHASE_FIELDS = ("purchase_date", "purchased_on", "acquired_on", "in_service_since")
EQUIPMENT_LIFETIME_FIELDS = ("expected_lifetime_months", "expected_life_months", "lifetime_months")
EQUIPMENT_HOURS_FIELDS = ("total_rental_hours", "total_usage_hours", "usage_hours", "hours_used")
EQUIPMENT_LABEL_FIELDS = ("equipment_code", "asset_code", "code", "serial_number")
EQUIPMENT_NAME_FIELDS = ("name", "model", "title", "label")
CATEGORY_LIFETIME_FIELDS = (
    "expected_lifetime_months",
    "default_lifetime_months",
    "lifetime_months",
)
CATEGORY_INTERVAL_FIELDS = (
    "maintenance_interval_days",
    "default_maintenance_interval_days",
    "service_interval_days",
)
CATEGORY_NAME_FIELDS = ("name", "title", "label")
RENTAL_ITEM_DATE_FIELDS = ("checked_out_at", "issued_at", "start_at", "started_at", "created_at")
RENTAL_ITEM_HOURS_FIELDS = ("hours", "duration_hours", "rental_hours", "billable_hours")

#: Equipment that can never need maintenance again.
DEAD_EQUIPMENT_STATUSES = (EquipmentStatus.RETIRED, EquipmentStatus.LOST)


# ---------------------------------------------------------------------------
# Model access & capability probing
# ---------------------------------------------------------------------------
def get_equipment_model():
    """Return the ``equipment.Equipment`` model, or ``None`` if unavailable."""
    try:
        return django_apps.get_model("equipment", "Equipment")
    except LookupError:  # pragma: no cover - only when the module is absent
        return None


def get_rental_item_model():
    try:
        return django_apps.get_model("rentals", "RentalItem")
    except LookupError:  # pragma: no cover
        return None


@functools.lru_cache(maxsize=32)
def _concrete_field_names(label: str) -> frozenset[str]:
    """Names of the concrete fields on a model, cached per process."""
    try:
        model = django_apps.get_model(label)
    except LookupError:  # pragma: no cover
        return frozenset()
    return frozenset(f.name for f in model._meta.get_fields() if getattr(f, "concrete", False))


def model_has_field(model, name: str) -> bool:
    if model is None:
        return False
    return name in _concrete_field_names(model._meta.label)


def first_available_field(model, candidates) -> str | None:
    """Return the first of *candidates* that actually exists on *model*."""
    if model is None:
        return None
    names = _concrete_field_names(model._meta.label)
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def equipment_has_category() -> bool:
    return model_has_field(get_equipment_model(), "category")


def record_search_fields() -> tuple[str, ...]:
    """Search paths for the record list, built from fields that really exist."""
    equipment_model = get_equipment_model()
    fields = ["record_code", "description", "resolution"]
    for candidate in (*EQUIPMENT_LABEL_FIELDS, *EQUIPMENT_NAME_FIELDS):
        if model_has_field(equipment_model, candidate):
            fields.append(f"equipment__{candidate}")
    return tuple(fields)


# ---------------------------------------------------------------------------
# Record queries
# ---------------------------------------------------------------------------
def records_queryset():
    """Base queryset with the joins every maintenance screen needs."""
    queryset = MaintenanceRecord.objects.select_related(
        "reported_by", "assigned_to", "equipment"
    )
    if equipment_has_category():
        queryset = queryset.select_related("equipment__category")
    return queryset


def open_records_for_equipment(equipment_id: int, exclude_pk: int | None = None):
    queryset = MaintenanceRecord.objects.filter(
        equipment_id=equipment_id, status__in=OPEN_STATUSES
    )
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset


def record_status_counts() -> dict[str, int]:
    """``{status: count}`` for the tab badges — one query."""
    rows = MaintenanceRecord.objects.values("status").annotate(total=Count("id"))
    return {row["status"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# Schedule queries
# ---------------------------------------------------------------------------
def schedules_queryset():
    queryset = MaintenanceSchedule.objects.select_related("equipment")
    if equipment_has_category():
        queryset = queryset.select_related("equipment__category")
    return queryset


def due_schedules(on: date | None = None, within_days: int = 0):
    """Active schedules due on or before *on* (plus an optional look-ahead)."""
    reference = (on or timezone.localdate()) + timedelta(days=max(0, within_days))
    queryset = schedules_queryset().filter(
        is_active=True, next_due_on__isnull=False, next_due_on__lte=reference
    )
    equipment_model = get_equipment_model()
    if model_has_field(equipment_model, "status"):
        queryset = queryset.exclude(equipment__status__in=DEAD_EQUIPMENT_STATUSES)
    return queryset.order_by("next_due_on", "id")


# ---------------------------------------------------------------------------
# Bulk fact gathering for the risk model
# ---------------------------------------------------------------------------
def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = timezone.localtime(value) if timezone.is_aware(value) else value
        return aware.date()
    if isinstance(value, date):
        return value
    return None


def active_equipment_rows() -> list[dict]:
    """One dict per in-service item, holding only fields that really exist.

    Keys always present: ``id``, ``label``, ``name``, ``category_id``,
    ``category``, ``status``, ``condition``, ``acquired_on``,
    ``last_maintenance_date``, ``expected_lifetime_days``, ``usage_hours``,
    ``created_on``. Any value the equipment module does not provide is ``None``.
    """
    equipment_model = get_equipment_model()
    if equipment_model is None:
        return []

    purchase_field = first_available_field(equipment_model, EQUIPMENT_PURCHASE_FIELDS)
    label_field = first_available_field(equipment_model, EQUIPMENT_LABEL_FIELDS)
    name_field = first_available_field(equipment_model, EQUIPMENT_NAME_FIELDS)
    lifetime_field = first_available_field(equipment_model, EQUIPMENT_LIFETIME_FIELDS)
    hours_field = first_available_field(equipment_model, EQUIPMENT_HOURS_FIELDS)
    has_category = model_has_field(equipment_model, "category")
    has_condition = model_has_field(equipment_model, "condition")
    has_status = model_has_field(equipment_model, "status")
    has_last_maintenance = model_has_field(equipment_model, "last_maintenance_date")

    values = ["id", "created_at"]
    for field in (purchase_field, label_field, name_field, lifetime_field, hours_field):
        if field and field not in values:
            values.append(field)
    if has_category:
        values.append("category_id")
    if has_condition:
        values.append("condition")
    if has_status:
        values.append("status")
    if has_last_maintenance:
        values.append("last_maintenance_date")

    queryset = equipment_model.objects.all()
    if has_status:
        queryset = queryset.exclude(status__in=DEAD_EQUIPMENT_STATUSES)

    category_names = category_name_map() if has_category else {}

    rows: list[dict] = []
    for raw in queryset.values(*values):
        lifetime_months = raw.get(lifetime_field) if lifetime_field else None
        try:
            lifetime_days = round(float(lifetime_months) * 30.44) if lifetime_months else None
        except (TypeError, ValueError):
            lifetime_days = None
        category_id = raw.get("category_id") if has_category else None
        rows.append(
            {
                "id": raw["id"],
                "label": str(raw.get(label_field) or "") if label_field else "",
                "name": str(raw.get(name_field) or "") if name_field else "",
                "category_id": category_id,
                "category": category_names.get(category_id, ""),
                "status": raw.get("status") if has_status else None,
                "condition": raw.get("condition") if has_condition else None,
                "acquired_on": _as_date(raw.get(purchase_field)) if purchase_field else None,
                "created_on": _as_date(raw.get("created_at")),
                "last_maintenance_date": (
                    _as_date(raw.get("last_maintenance_date")) if has_last_maintenance else None
                ),
                "expected_lifetime_days": lifetime_days,
                "usage_hours": _to_float(raw.get(hours_field)) if hours_field else None,
            }
        )
    return rows


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


def category_name_map() -> dict[int, str]:
    equipment_model = get_equipment_model()
    if not model_has_field(equipment_model, "category"):
        return {}
    category_model = equipment_model._meta.get_field("category").related_model
    name_field = first_available_field(category_model, CATEGORY_NAME_FIELDS)
    if not name_field:
        return {}
    return {
        row["id"]: str(row[name_field] or "")
        for row in category_model.objects.values("id", name_field)
    }


def category_lifetime_days() -> dict[int, int]:
    """Declared expected lifetime per category, in days (empty when unmodelled)."""
    equipment_model = get_equipment_model()
    if not model_has_field(equipment_model, "category"):
        return {}
    category_model = equipment_model._meta.get_field("category").related_model
    field = first_available_field(category_model, CATEGORY_LIFETIME_FIELDS)
    if not field:
        return {}
    result: dict[int, int] = {}
    for row in category_model.objects.values("id", field):
        months = _to_float(row.get(field))
        if months and months > 0:
            result[row["id"]] = round(months * 30.44)
    return result


def category_interval_days() -> dict[int, int]:
    """Declared preventive interval per category, in days."""
    equipment_model = get_equipment_model()
    if not model_has_field(equipment_model, "category"):
        return {}
    category_model = equipment_model._meta.get_field("category").related_model
    field = first_available_field(category_model, CATEGORY_INTERVAL_FIELDS)
    if not field:
        return {}
    result: dict[int, int] = {}
    for row in category_model.objects.values("id", field):
        days = _to_float(row.get(field))
        if days and days >= 1:
            result[row["id"]] = round(days)
    return result


def retired_equipment_ages() -> dict[int, list[int]]:
    """Observed service life (days) of already-retired items, by category.

    This is the only honest source of "how long does a category last here":
    it is measured from the school's own fleet, never assumed.
    """
    equipment_model = get_equipment_model()
    if equipment_model is None or not model_has_field(equipment_model, "status"):
        return {}
    if not model_has_field(equipment_model, "category"):
        return {}
    purchase_field = first_available_field(equipment_model, EQUIPMENT_PURCHASE_FIELDS)

    values = ["id", "category_id", "created_at", "updated_at"]
    if purchase_field:
        values.append(purchase_field)

    ages: dict[int, list[int]] = {}
    manager = getattr(equipment_model, "all_objects", equipment_model.objects)
    rows = manager.filter(status=EquipmentStatus.RETIRED).values(*values)
    for raw in rows:
        start = _as_date(raw.get(purchase_field)) if purchase_field else None
        start = start or _as_date(raw.get("created_at"))
        end = _as_date(raw.get("updated_at"))
        if not (start and end) or end <= start:
            continue
        ages.setdefault(raw["category_id"], []).append((end - start).days)
    return ages


def maintenance_history(equipment_ids) -> dict[int, list[dict]]:
    """``{equipment_id: [{reported_on, severity, made_unusable, cost}, ...]}``."""
    history: dict[int, list[dict]] = {}
    if not equipment_ids:
        return history
    rows = (
        MaintenanceRecord.objects.filter(equipment_id__in=equipment_ids)
        .exclude(status=GenericStatus.CANCELLED)
        .values("equipment_id", "reported_at", "completed_at", "severity", "made_unusable", "total_cost")
        .order_by("reported_at")
    )
    for row in rows:
        history.setdefault(row["equipment_id"], []).append(
            {
                "reported_on": _as_date(row["reported_at"]),
                "completed_on": _as_date(row["completed_at"]),
                "severity": row["severity"],
                "made_unusable": row["made_unusable"],
                "cost": row["total_cost"] or Decimal("0.00"),
            }
        )
    return history


def rental_history(equipment_ids) -> tuple[dict[int, list[date]], bool]:
    """``({equipment_id: [rental dates]}, data_available)``.

    ``data_available`` is False when the rentals module is not installed or
    exposes no usable date — the caller must then mark the usage signals as
    unmeasured rather than assume zero rentals.
    """
    rental_item_model = get_rental_item_model()
    if rental_item_model is None or not model_has_field(rental_item_model, "equipment"):
        return {}, False
    date_field = first_available_field(rental_item_model, RENTAL_ITEM_DATE_FIELDS)
    if not date_field or not equipment_ids:
        return {}, bool(date_field)

    history: dict[int, list[date]] = {}
    rows = (
        rental_item_model.objects.filter(equipment_id__in=equipment_ids)
        .values("equipment_id", date_field)
        .order_by(date_field)
    )
    for row in rows:
        moment = _as_date(row.get(date_field))
        if moment:
            history.setdefault(row["equipment_id"], []).append(moment)
    return history, True


def rental_hours(equipment_ids) -> tuple[dict[int, float], bool]:
    """Cumulative rental hours per item, from whichever module records them."""
    equipment_model = get_equipment_model()
    equipment_hours_field = first_available_field(equipment_model, EQUIPMENT_HOURS_FIELDS)
    if equipment_hours_field and equipment_ids:
        rows = equipment_model.objects.filter(id__in=equipment_ids).values(
            "id", equipment_hours_field
        )
        hours = {}
        for row in rows:
            value = _to_float(row.get(equipment_hours_field))
            if value is not None:
                hours[row["id"]] = value
        if hours:
            return hours, True

    rental_item_model = get_rental_item_model()
    hours_field = first_available_field(rental_item_model, RENTAL_ITEM_HOURS_FIELDS)
    if not hours_field or not model_has_field(rental_item_model, "equipment") or not equipment_ids:
        return {}, False

    rows = (
        rental_item_model.objects.filter(equipment_id__in=equipment_ids)
        .values("equipment_id")
        .annotate(
            total=Coalesce(
                Sum(hours_field), Value(Decimal("0.00")), output_field=DecimalField()
            )
        )
    )
    return {row["equipment_id"]: float(row["total"] or 0) for row in rows}, True


# ---------------------------------------------------------------------------
# Cost aggregation
# ---------------------------------------------------------------------------
def _money_sum(field: str):
    return Coalesce(Sum(field), Value(Decimal("0.00")), output_field=DecimalField())


def costed_records(start=None, end=None):
    """Records whose spend is realised, optionally bounded by completion date."""
    queryset = MaintenanceRecord.objects.filter(status__in=COSTED_STATUSES)
    if start is not None:
        queryset = queryset.filter(completed_at__gte=start)
    if end is not None:
        queryset = queryset.filter(completed_at__lte=end)
    return queryset.filter(completed_at__isnull=False)


def cost_by_category(start=None, end=None) -> list[dict]:
    if not equipment_has_category():
        return []
    equipment_model = get_equipment_model()
    category_model = equipment_model._meta.get_field("category").related_model
    name_field = first_available_field(category_model, CATEGORY_NAME_FIELDS)
    group = "equipment__category_id"
    values = [group]
    if name_field:
        values.append(f"equipment__category__{name_field}")

    rows = (
        costed_records(start, end)
        .values(*values)
        .annotate(
            records=Count("id"),
            parts=_money_sum("parts_cost"),
            labour=_money_sum("labour_cost"),
            total=_money_sum("total_cost"),
            hours=Coalesce(
                Sum("labour_hours"), Value(Decimal("0.00")), output_field=DecimalField()
            ),
        )
        .order_by("-total")
    )
    result = []
    for row in rows:
        result.append(
            {
                "category_id": row.get(group),
                "category": (
                    str(row.get(f"equipment__category__{name_field}") or "")
                    if name_field
                    else ""
                ),
                "records": row["records"],
                "parts": row["parts"],
                "labour": row["labour"],
                "total": row["total"],
                "hours": row["hours"],
            }
        )
    return result


def cost_by_item(start=None, end=None, limit: int = 50) -> list[dict]:
    equipment_model = get_equipment_model()
    label_field = first_available_field(equipment_model, EQUIPMENT_LABEL_FIELDS)
    name_field = first_available_field(equipment_model, EQUIPMENT_NAME_FIELDS)

    values = ["equipment_id"]
    for field in (label_field, name_field):
        if field:
            values.append(f"equipment__{field}")

    rows = (
        costed_records(start, end)
        .values(*values)
        .annotate(
            records=Count("id"),
            parts=_money_sum("parts_cost"),
            labour=_money_sum("labour_cost"),
            total=_money_sum("total_cost"),
            downtime=Coalesce(
                Sum("labour_hours"), Value(Decimal("0.00")), output_field=DecimalField()
            ),
        )
        .order_by("-total")[:limit]
    )
    result = []
    for row in rows:
        label = str(row.get(f"equipment__{label_field}") or "") if label_field else ""
        name = str(row.get(f"equipment__{name_field}") or "") if name_field else ""
        result.append(
            {
                "equipment_id": row["equipment_id"],
                "code": label,
                "name": name,
                "records": row["records"],
                "parts": row["parts"],
                "labour": row["labour"],
                "total": row["total"],
                "hours": row["downtime"],
            }
        )
    return result


def cost_by_damage_type(start=None, end=None) -> list[dict]:
    rows = (
        costed_records(start, end)
        .values("damage_type")
        .annotate(records=Count("id"), total=_money_sum("total_cost"))
        .order_by("-total")
    )
    return [
        {"damage_type": row["damage_type"], "records": row["records"], "total": row["total"]}
        for row in rows
    ]


def cost_totals(start=None, end=None) -> dict:
    row = costed_records(start, end).aggregate(
        records=Count("id"),
        parts=_money_sum("parts_cost"),
        labour=_money_sum("labour_cost"),
        total=_money_sum("total_cost"),
        hours=Coalesce(Sum("labour_hours"), Value(Decimal("0.00")), output_field=DecimalField()),
    )
    return row


def open_workload() -> dict:
    """Headline numbers for the list screen: what is open and how bad it is."""
    aggregate = MaintenanceRecord.objects.filter(status__in=OPEN_STATUSES).aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(severity=Severity.CRITICAL)),
        high=Count("id", filter=Q(severity=Severity.HIGH)),
        out_of_service=Count("id", filter=Q(made_unusable=True)),
        unassigned=Count("id", filter=Q(assigned_to__isnull=True)),
    )
    aggregate["due_now"] = due_schedules().count()
    return aggregate
