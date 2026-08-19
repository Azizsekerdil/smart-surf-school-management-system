"""Rental business rules.

Everything that decides money, availability or equipment state lives here — the
views only collect input and display the result.

Integration seams
-----------------
``equipment``, ``customers``, ``maintenance`` and ``notifications`` are separate
modules. This module reaches them through :func:`django.apps.apps.get_model` and
only touches fields it has verified exist, so a rental can always be taken even
if a neighbouring module has not been migrated yet. The rate fields expected on
``equipment.Equipment`` are ``hourly_rate``, ``daily_rate`` and ``weekly_rate``;
a missing rate is derived from the ones that are configured.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import ceil

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit, record_system_event
from apps.core.enums import (
    UNAVAILABLE_EQUIPMENT_STATUSES,
    DamageType,
    EquipmentCondition,
    EquipmentStatus,
    RentalPeriod,
)
from apps.core.models import SystemSetting
from apps.core.utils import to_decimal

from .models import Rental, RentalItem

logger = logging.getLogger("apps.rentals")

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

#: Operating hours in a hire "day" — used only to derive an hourly rate for a
#: piece of equipment that is priced by the day alone.
HOURS_PER_RENTAL_DAY = Decimal("8")
DAYS_PER_WEEK = 7
#: A late fee may never exceed three times the agreed hire charge. Beyond that
#: the item is treated as lost and settled through :func:`mark_rental_lost`.
LATE_FEE_CAP_MULTIPLIER = Decimal("3")
#: Grace before a late fee starts. Somebody walking up two minutes after the
#: hour is not charged another day, which is both fair and what a counter does.
LATE_FEE_GRACE_MINUTES = 15
#: Age below which a hire needs an adult's identity document at the counter.
ADULT_AGE = 18
#: SystemSetting key holding the percentage of the hire charge kept when a
#: reservation is cancelled inside the free-cancellation window. Default 0 —
#: a school must opt in to charging for a cancellation.
LATE_CANCELLATION_PERCENT_KEY = "rentals.late_cancellation_percent"
FREE_CANCELLATION_HOURS_KEY = "rentals.free_cancellation_hours"
DEFAULT_FREE_CANCELLATION_HOURS = 24

#: Identifier fields we will accept when an operator scans or types an asset code.
EQUIPMENT_CODE_FIELDS = ("asset_code", "serial_number", "barcode", "code")
#: Extra fields included in the free-text equipment picker.
EQUIPMENT_TEXT_FIELDS = ("name", "brand", "model", "size_label")
#: Fields used by the customer picker on the check-out screen.
CUSTOMER_TEXT_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "mobile",
    "customer_code",
    "company_name",
)
STUDENT_TEXT_FIELDS = ("first_name", "last_name", "email", "student_code", "nickname")
BOOKING_TEXT_FIELDS = ("booking_code", "reference")

#: Pickers offered by the check-out screen: key -> (app label, model, search fields).
SEARCHABLE_RELATIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "customer": ("customers", "Customer", CUSTOMER_TEXT_FIELDS),
    "student": ("students", "Student", STUDENT_TEXT_FIELDS),
    "booking": ("bookings", "Booking", BOOKING_TEXT_FIELDS),
    "equipment": ("equipment", "Equipment", EQUIPMENT_CODE_FIELDS + EQUIPMENT_TEXT_FIELDS),
}


# ---------------------------------------------------------------------------
# Small cross-app helpers
# ---------------------------------------------------------------------------
def _concrete_field_names(model) -> set[str]:
    return {field.name for field in model._meta.fields}


def _existing(model, candidates) -> list[str]:
    names = _concrete_field_names(model)
    return [candidate for candidate in candidates if candidate in names]


def equipment_model():
    return apps.get_model("equipment", "Equipment")


def customer_model():
    return apps.get_model("customers", "Customer")


def equipment_label(equipment) -> str:
    """Best available human label for a piece of equipment."""
    for field in EQUIPMENT_CODE_FIELDS:
        code = getattr(equipment, field, None)
        if code:
            return f"{code} · {equipment}"
    return str(equipment)


def equipment_code(equipment) -> str:
    for field in EQUIPMENT_CODE_FIELDS:
        code = getattr(equipment, field, None)
        if code:
            return str(code)
    return str(equipment.pk)


def find_equipment_by_code(code: str):
    """Exact (case-insensitive) lookup on whichever identifier field exists."""
    code = (code or "").strip()
    if not code:
        return None
    model = equipment_model()
    fields = _existing(model, EQUIPMENT_CODE_FIELDS)
    if not fields:
        return None
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__iexact": code})
    return model.objects.filter(condition).first()


def search_equipment(term: str, limit: int = 12):
    """Free-text search used by the check-out screen's asset picker."""
    return search_related("equipment", term, limit)


def equipment_code_q(term: str, prefix: str = "") -> Q:
    """``Q`` matching *term* against whichever asset-identifier fields exist.

    ``prefix`` lets a caller reach the asset through a relation, e.g.
    ``"items__equipment__"`` from a :class:`~apps.rentals.models.Rental` query.
    """
    condition = Q()
    term = (term or "").strip()
    if not term:
        return condition
    for field in _existing(equipment_model(), EQUIPMENT_CODE_FIELDS):
        condition |= Q(**{f"{prefix}{field}__icontains": term})
    return condition


def search_related(kind: str, term: str, limit: int = 12):
    """Free-text search over one of :data:`SEARCHABLE_RELATIONS`.

    The neighbouring modules own their own field names, so only fields that
    actually exist on the target model are queried.
    """
    spec = SEARCHABLE_RELATIONS.get(kind)
    if spec is None:
        raise ValidationError(_("Unknown search target."))
    app_label, model_name, candidates = spec
    model = apps.get_model(app_label, model_name)
    term = (term or "").strip()
    fields = _existing(model, candidates)
    if not term or not fields:
        return model.objects.none()
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": term})
    return model.objects.filter(condition)[:limit]


def search_customers(term: str, limit: int = 12):
    return search_related("customer", term, limit)


def _age_of(person) -> int | None:
    """Age in whole years, when the related module exposes one."""
    if person is None:
        return None
    age = getattr(person, "age", None)
    if isinstance(age, int):
        return age
    birth = getattr(person, "date_of_birth", None) or getattr(person, "birth_date", None)
    if not birth:
        return None
    today = timezone.localdate()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def is_minor(person) -> bool:
    age = _age_of(person)
    return age is not None and age < ADULT_AGE


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def equipment_rate(equipment, period_type: str) -> Decimal:
    """Unit rate of *equipment* for one *period_type* unit.

    A school rarely configures all three rates. Missing rates are derived so a
    hire can still be priced: an hourly rate from the daily one (and back) using
    :data:`HOURS_PER_RENTAL_DAY`, and a weekly rate from seven days at the daily
    rate — i.e. no implicit weekly discount is invented.
    """
    # equipment.Equipment names these rental_price_*; the shorter names are
    # accepted too so a differently-shaped catalogue still prices correctly.
    def _rate(*names) -> Decimal:
        for name in names:
            value = getattr(equipment, name, None)
            if value:
                return to_decimal(value)
        return ZERO

    hourly = _rate("rental_price_hourly", "hourly_rate")
    daily = _rate("rental_price_daily", "daily_rate")
    weekly = _rate("rental_price_weekly", "weekly_rate")

    if period_type == RentalPeriod.HOURLY:
        if hourly > ZERO:
            return hourly
        if daily > ZERO:
            return (daily / HOURS_PER_RENTAL_DAY).quantize(CENT, rounding=ROUND_HALF_UP)
        return ZERO

    if period_type == RentalPeriod.DAILY:
        if daily > ZERO:
            return daily
        if hourly > ZERO:
            return (hourly * HOURS_PER_RENTAL_DAY).quantize(CENT, rounding=ROUND_HALF_UP)
        return ZERO

    if weekly > ZERO:
        return weekly
    daily_equivalent = equipment_rate(equipment, RentalPeriod.DAILY)
    return (daily_equivalent * DAYS_PER_WEEK).quantize(CENT, rounding=ROUND_HALF_UP)


def hours_between(start, end) -> Decimal:
    """Exact Decimal hours between two datetimes (float never touches money)."""
    delta = end - start
    seconds = Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds)
    if delta.microseconds:
        seconds += Decimal(1)
    return seconds / Decimal(3600)


def period_units(period_type: str, start, end) -> int:
    """Whole billable units between *start* and *end*, always rounded **up**.

    Fifteen minutes over the hour is a whole extra hour; one hour over the day
    is a whole extra day. That is how every hire counter in the world charges.
    """
    hours = hours_between(start, end)
    if hours <= 0:
        return 0
    if period_type == RentalPeriod.HOURLY:
        return int(ceil(hours))
    days = int(ceil(hours / Decimal(24)))
    if period_type == RentalPeriod.DAILY:
        return days
    return int(ceil(Decimal(days) / Decimal(DAYS_PER_WEEK)))


def calculate_rental_price(
    equipment,
    period_type: str,
    start,
    end,
    quantity: int = 1,
) -> Decimal:
    """Price *quantity* units of *equipment* for the window ``[start, end)``.

    Units are rounded up to the next whole hour/day/week. The customer is then
    charged the *cheapest* legitimate way of expressing that window: once a hire
    reaches a full day it never costs more than the daily rate, and a hire of
    seven days or more is billed weekly whenever the weekly rate is cheaper.
    """
    if end is None or start is None or end <= start:
        raise ValidationError(_("The due-back time must be after the start time."))
    quantity = max(int(quantity or 1), 1)

    hours = hours_between(start, end)
    days = int(ceil(hours / Decimal(24)))

    candidates: list[Decimal] = [
        equipment_rate(equipment, period_type) * period_units(period_type, start, end)
    ]
    # Never charge more hourly than a full day, nor more daily than a full week.
    if period_type == RentalPeriod.HOURLY and days >= 1:
        candidates.append(
            equipment_rate(equipment, RentalPeriod.DAILY)
            * period_units(RentalPeriod.DAILY, start, end)
        )
    if period_type in (RentalPeriod.HOURLY, RentalPeriod.DAILY) and days >= DAYS_PER_WEEK:
        candidates.append(
            equipment_rate(equipment, RentalPeriod.WEEKLY)
            * period_units(RentalPeriod.WEEKLY, start, end)
        )

    priced = [value for value in candidates if value > ZERO]
    unit_price = min(priced) if priced else ZERO
    return (unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def price_lines(items, period_type: str, start, end) -> list[dict]:
    """Price a draft basket: ``[(equipment, quantity), …]`` -> line dictionaries."""
    lines = []
    for equipment, quantity in items:
        quantity = max(int(quantity or 1), 1)
        unit_price = calculate_rental_price(equipment, period_type, start, end, quantity=1)
        lines.append(
            {
                "equipment": equipment,
                "equipment_id": equipment.pk,
                "code": equipment_code(equipment),
                "label": str(equipment),
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": (unit_price * quantity).quantize(CENT),
            }
        )
    return lines


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def equipment_conflicts(equipment, start, end, *, exclude_rental=None) -> list[str]:
    """Reasons *equipment* cannot be hired for ``[start, end)`` (empty = free).

    Two independent things can block a hire: the asset's own state (in the
    workshop, written off, already in a lesson) and an overlapping commitment to
    another customer.
    """
    reasons: list[str] = []
    status = getattr(equipment, "status", None)
    label = equipment_label(equipment)

    if status in UNAVAILABLE_EQUIPMENT_STATUSES:
        reasons.append(
            _("%(item)s is not rentable (%(status)s).")
            % {"item": label, "status": equipment.get_status_display()}
        )
    elif status == EquipmentStatus.IN_LESSON:
        reasons.append(_("%(item)s is currently assigned to a lesson.") % {"item": label})

    now = timezone.now()
    overlapping = RentalItem.objects.filter(
        equipment=equipment,
        returned_at__isnull=True,
        rental__status__in=Rental.OPEN_STATUSES,
        rental__start_at__lt=end,
    )
    if exclude_rental is not None:
        overlapping = overlapping.exclude(rental_id=getattr(exclude_rental, "pk", exclude_rental))

    window = Q(rental__expected_return_at__gt=start)
    if start <= now:
        # Gear that is still physically out cannot be handed to anybody today,
        # no matter what its paperwork says it was due back.
        window |= Q(rental__status__in=Rental.OUT_STATUSES)

    clash = overlapping.filter(window).select_related("rental").first()
    if clash is not None:
        reasons.append(
            _("%(item)s is committed to rental %(code)s until %(due)s.")
            % {
                "item": label,
                "code": clash.rental.rental_code,
                "due": timezone.localtime(clash.rental.expected_return_at).strftime("%d.%m.%Y %H:%M"),
            }
        )
    return reasons


def is_equipment_available(equipment, start, end, *, exclude_rental=None) -> bool:
    return not equipment_conflicts(equipment, start, end, exclude_rental=exclude_rental)


def _set_equipment_state(equipment, status: str, *, condition: str | None = None) -> None:
    """Write the asset's operational state back to the equipment module."""
    fields = _concrete_field_names(type(equipment))
    updates: list[str] = []
    if "status" in fields and getattr(equipment, "status", None) != status:
        equipment.status = status
        updates.append("status")
    if condition and "condition" in fields and getattr(equipment, "condition", None) != condition:
        equipment.condition = condition
        updates.append("condition")
    if updates:
        if "updated_at" in fields:
            updates.append("updated_at")
        equipment.save(update_fields=updates)


def _increment_equipment_counters(equipment, hours: Decimal) -> None:
    """Bump usage counters the equipment module keeps, when it keeps them."""
    field_map = {field.name: field for field in type(equipment)._meta.fields}
    updates: dict[str, object] = {}
    if "times_rented" in field_map:
        updates["times_rented"] = F("times_rented") + 1
    for name in ("total_rental_hours", "usage_hours"):
        field = field_map.get(name)
        if field is None:
            continue
        if isinstance(field, models.IntegerField):
            increment: object = int(hours)
        elif isinstance(field, models.FloatField):
            increment = float(hours)
        else:
            increment = hours
        updates[name] = F(name) + increment
    if updates:
        type(equipment)._default_manager.filter(pk=equipment.pk).update(**updates)


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------
@transaction.atomic
def create_rental(
    *,
    customer,
    items,
    period_type: str,
    start_at,
    expected_return_at,
    student=None,
    booking=None,
    deposit_amount: Decimal = ZERO,
    discount_amount: Decimal = ZERO,
    paid_amount: Decimal = ZERO,
    id_document_held: bool = False,
    notes: str = "",
    user=None,
    request=None,
) -> Rental:
    """Hand equipment over to a customer.

    *items* is ``[(equipment, quantity), …]``. Every asset is re-checked against
    the requested window inside the transaction, so two operators serving two
    customers at the same counter cannot double-book the same board.
    """
    if not items:
        raise ValidationError(_("Add at least one piece of equipment to the hire."))
    if expected_return_at is None or start_at is None or expected_return_at <= start_at:
        raise ValidationError(_("The due-back time must be after the start time."))

    deposit_amount = to_decimal(deposit_amount)
    discount_amount = to_decimal(discount_amount)
    paid_amount = to_decimal(paid_amount)

    # Liability: a minor may not sign for equipment without an adult's document.
    if (is_minor(student) or is_minor(customer)) and not id_document_held:
        raise ValidationError(
            _(
                "This hire is for a minor. An adult guardian's identity document "
                "must be held at the counter before the equipment leaves."
            )
        )

    # Re-read the assets with a row lock so the availability check cannot be
    # overtaken by a parallel check-out.
    model = equipment_model()
    ids = [equipment.pk for equipment, _quantity in items]
    if len(set(ids)) != len(ids):
        raise ValidationError(_("The same asset was added twice. Adjust the quantity instead."))
    locked = {
        obj.pk: obj for obj in model._default_manager.select_for_update().filter(pk__in=ids)
    }

    problems: list[str] = []
    basket: list[tuple[object, int]] = []
    for equipment, quantity in items:
        asset = locked.get(equipment.pk, equipment)
        quantity = max(int(quantity or 1), 1)
        problems.extend(equipment_conflicts(asset, start_at, expected_return_at))
        basket.append((asset, quantity))
    if problems:
        raise ValidationError(problems)

    now = timezone.now()
    status = Rental.Status.ACTIVE if start_at <= now else Rental.Status.RESERVED

    rental = Rental(
        customer=customer,
        student=student,
        booking=booking,
        status=status,
        period_type=period_type,
        start_at=start_at,
        expected_return_at=expected_return_at,
        deposit_amount=deposit_amount,
        deposit_status=Rental.DepositStatus.HELD,
        discount_amount=discount_amount,
        paid_amount=paid_amount,
        id_document_held=id_document_held,
        notes=notes or "",
        checked_out_by=user if getattr(user, "is_authenticated", False) else None,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        updated_by=user if getattr(user, "is_authenticated", False) else None,
    )
    rental.full_clean(exclude=["rental_code", "created_by", "updated_by"])
    rental.save()

    asset_status = (
        EquipmentStatus.RENTED if status == Rental.Status.ACTIVE else EquipmentStatus.RESERVED
    )
    for asset, quantity in basket:
        unit_price = calculate_rental_price(
            asset, period_type, start_at, expected_return_at, quantity=1
        )
        item = RentalItem(
            rental=rental,
            equipment=asset,
            unit_price=unit_price,
            quantity=quantity,
            condition_out=getattr(asset, "condition", None) or EquipmentCondition.GOOD,
            created_by=rental.created_by,
            updated_by=rental.updated_by,
        )
        item.full_clean(exclude=["created_by", "updated_by", "line_total"])
        item.save()
        _set_equipment_state(asset, asset_status)

    rental.recalculate_totals()

    record_audit(
        request,
        action=AuditAction.RENTAL_OUT,
        instance=rental,
        user=user,
        description=_("Rental %(code)s: %(count)s item(s) checked out to %(customer)s")
        % {
            "code": rental.rental_code,
            "count": rental.item_count,
            "customer": str(customer),
        },
        changes={
            "total_amount": [None, rental.total_amount],
            "deposit_amount": [None, rental.deposit_amount],
            "expected_return_at": [None, rental.expected_return_at],
        },
    )
    return rental


@transaction.atomic
def add_rental_item(rental: Rental, equipment, quantity: int = 1, *, user=None, request=None):
    """Add one more asset to an open contract, priced for the remaining window."""
    if rental.status not in Rental.OPEN_STATUSES:
        raise ValidationError(_("Items can only be added to an open rental."))
    if rental.items.filter(equipment=equipment).exists():
        raise ValidationError(_("That asset is already on this rental."))

    problems = equipment_conflicts(
        equipment, timezone.now(), rental.expected_return_at, exclude_rental=rental
    )
    if problems:
        raise ValidationError(problems)

    unit_price = calculate_rental_price(
        equipment, rental.period_type, timezone.now(), rental.expected_return_at, quantity=1
    )
    item = RentalItem(
        rental=rental,
        equipment=equipment,
        unit_price=unit_price,
        quantity=max(int(quantity or 1), 1),
        condition_out=getattr(equipment, "condition", None) or EquipmentCondition.GOOD,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    item.full_clean(exclude=["created_by", "updated_by", "line_total"])
    item.save()
    _set_equipment_state(
        equipment,
        EquipmentStatus.RENTED
        if rental.status in Rental.OUT_STATUSES
        else EquipmentStatus.RESERVED,
    )
    rental.recalculate_totals()
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=rental,
        user=user,
        description=_("%(item)s added to rental %(code)s")
        % {"item": equipment_label(equipment), "code": rental.rental_code},
    )
    return item


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------
def calculate_late_fee(rental: Rental, returned_at=None) -> Decimal:
    """Late fee for *rental*: overdue units × the contract's unit rate.

    A :data:`LATE_FEE_GRACE_MINUTES` grace applies before the clock starts, and
    the fee is capped at :data:`LATE_FEE_CAP_MULTIPLIER` times the agreed hire
    charge — past that point the school pursues the replacement value instead,
    which is what :func:`mark_rental_lost` does.
    """
    returned_at = returned_at or timezone.now()
    if not rental.expected_return_at:
        return ZERO

    charge_from = rental.expected_return_at + timedelta(minutes=LATE_FEE_GRACE_MINUTES)
    if returned_at <= charge_from:
        return ZERO

    units = period_units(rental.period_type, charge_from, returned_at)
    if units <= 0:
        return ZERO

    unit_rate = ZERO
    for item in rental.items.select_related("equipment"):
        unit_rate += equipment_rate(item.equipment, rental.period_type) * item.quantity
    fee = (unit_rate * units).quantize(CENT, rounding=ROUND_HALF_UP)

    cap = (rental.subtotal or ZERO) * LATE_FEE_CAP_MULTIPLIER
    if cap > ZERO and fee > cap:
        return cap.quantize(CENT)
    return fee


def _normalise_condition(raw) -> tuple[str, str, str, Decimal]:
    """Accept either a 4-tuple or a mapping for one item's check-in data."""
    if isinstance(raw, dict):
        return (
            raw.get("condition") or raw.get("condition_in") or "",
            raw.get("damage_type") or "",
            raw.get("notes") or raw.get("damage_notes") or "",
            to_decimal(raw.get("charge") or raw.get("damage_charge") or ZERO),
        )
    if isinstance(raw, (list, tuple)):
        padded = list(raw) + [None] * (4 - len(raw))
        return (
            padded[0] or "",
            padded[1] or "",
            padded[2] or "",
            to_decimal(padded[3] or ZERO),
        )
    return (str(raw or ""), "", "", ZERO)


def _create_maintenance_record(item: RentalItem, *, user=None) -> None:
    """Open a workshop job for a damaged asset (best effort, never fatal)."""
    try:
        model = apps.get_model("maintenance", "MaintenanceRecord")
    except LookupError:  # pragma: no cover - maintenance app always installed
        return

    title = _("Damage on return — rental %(code)s") % {"code": item.rental.rental_code}
    description = item.damage_notes or _("Damage reported at check-in.")
    fields = _concrete_field_names(model)
    payload: dict[str, object] = {}
    if "equipment" in fields:
        payload["equipment"] = item.equipment
    for name, value in (
        ("title", title),
        ("summary", title),
        ("description", description),
        ("notes", description),
        ("damage_type", item.damage_type),
        ("issue_type", item.damage_type),
        ("reported_by", user if getattr(user, "is_authenticated", False) else None),
        ("reported_at", item.returned_at or timezone.now()),
        ("estimated_cost", item.damage_charge),
        ("created_by", user if getattr(user, "is_authenticated", False) else None),
    ):
        if name in fields and value not in (None, ""):
            payload[name] = value
    try:
        model.objects.create(**payload)
    except Exception:  # noqa: BLE001 - a workshop row must never block check-in
        logger.exception(
            "Could not open a maintenance record for equipment %s", item.equipment_id
        )


def _notify(*, title: str, body: str, url: str = "", user=None, category: str = "rental") -> None:
    """Best-effort notification. Never breaks the operation that triggered it.

    Two things this has to get right, both learned the hard way:

    1. **A notification needs a recipient.** ``Notification.recipient`` is NOT
       NULL, so firing one with no target raises ``IntegrityError``. When no
       user is supplied the message goes to the people whose job it is — rental
       staff and their managers — rather than nowhere.
    2. **The failure must be contained in a savepoint.** Catching an exception
       from a failed INSERT does *not* undo it: inside ``transaction.atomic``
       the transaction is already marked for rollback, so the caller's work
       silently disappears at commit. A rental check-in once looked successful
       while nothing at all was written. The inner ``atomic()`` block creates a
       savepoint that can be rolled back on its own, leaving the outer
       transaction intact.
    """
    try:
        from apps.notifications import services as notification_services
    except ImportError:  # pragma: no cover - notifications module absent
        return

    recipients = []
    if user is not None and getattr(user, "is_authenticated", False):
        recipients = [user]

    try:
        with transaction.atomic():  # savepoint — see the docstring
            if recipients:
                for recipient in recipients:
                    notification_services.notify(
                        recipient, category, title, body, link_url=url
                    )
            else:
                from apps.accounts.constants import Role

                for role in (Role.RENTAL_STAFF, Role.EQUIPMENT_MANAGER, Role.MANAGER):
                    notification_services.notify_role(
                        role, category, title, body, link_url=url
                    )
    except Exception:  # noqa: BLE001 - a missed notification must not break ops
        logger.warning("Could not create rental notification: %s", title, exc_info=True)


def _settle_deposit(rental: Rental) -> None:
    """Deduct late and damage charges from the deposit, refund the rest.

    The deposit exists to cover exactly those two things. Whatever is withheld
    counts as money received against the contract.
    """
    deductible = (rental.late_fee or ZERO) + (rental.damage_fee or ZERO)
    # Idempotent: while the deposit is still HELD nothing has been credited yet.
    already_credited = (
        ZERO if rental.deposit_status == Rental.DepositStatus.HELD else rental.deposit_withheld
    )
    withheld = min(rental.deposit_amount or ZERO, deductible)
    rental.deposit_returned = (rental.deposit_amount or ZERO) - withheld
    rental.paid_amount = (rental.paid_amount or ZERO) + (withheld - already_credited)
    if rental.paid_amount < ZERO:
        rental.paid_amount = ZERO
    rental.deposit_status = (
        Rental.DepositStatus.RETURNED if withheld <= ZERO else Rental.DepositStatus.FORFEITED
    )


@transaction.atomic
def return_rental(
    rental: Rental,
    item_conditions: dict | None = None,
    user=None,
    *,
    returned_at=None,
    request=None,
) -> Rental:
    """Check equipment back in.

    ``item_conditions`` maps ``RentalItem`` id to
    ``(condition, damage_type, notes, charge)`` — a mapping with the same keys is
    also accepted. An empty mapping means "everything back, unchanged".

    Partial returns are supported: the contract stays open until the last item
    is in, and only then is the late fee charged and the deposit settled.
    """
    if rental.status not in Rental.OPEN_STATUSES:
        raise ValidationError(
            _("Rental %(code)s is %(status)s and cannot be checked in.")
            % {"code": rental.rental_code, "status": rental.get_status_display()}
        )

    moment = returned_at or timezone.now()
    if moment < rental.start_at:
        raise ValidationError(_("Equipment cannot be returned before it was handed out."))

    open_items = list(rental.items.select_related("equipment").filter(returned_at__isnull=True))
    if not open_items:
        raise ValidationError(_("Every item on this rental is already back."))

    conditions = {int(key): value for key, value in (item_conditions or {}).items()}
    targets = [item for item in open_items if item.pk in conditions] if conditions else open_items
    if not targets:
        raise ValidationError(_("Select at least one item to check in."))

    for item in targets:
        condition, damage_type, notes, charge = _normalise_condition(conditions.get(item.pk))
        condition = condition or item.condition_out
        if condition not in EquipmentCondition.values:
            raise ValidationError(
                _("“%(value)s” is not a valid condition.") % {"value": condition}
            )
        if damage_type and damage_type not in DamageType.values:
            raise ValidationError(
                _("“%(value)s” is not a valid damage type.") % {"value": damage_type}
            )

        damaged = bool(damage_type) or charge > ZERO or condition == EquipmentCondition.UNUSABLE
        item.condition_in = condition
        item.damage_reported = damaged
        item.damage_type = damage_type or (DamageType.GENERAL if damaged else "")
        item.damage_notes = notes or item.damage_notes
        item.damage_charge = charge if damaged else ZERO
        item.returned_at = moment
        item.updated_by = user if getattr(user, "is_authenticated", False) else None
        item.full_clean(exclude=["created_by", "updated_by", "line_total"])
        item.save()

        # Put the asset back into circulation, or into the workshop.
        if condition == EquipmentCondition.UNUSABLE:
            asset_status = EquipmentStatus.DAMAGED
        elif damaged:
            asset_status = EquipmentStatus.MAINTENANCE
        else:
            asset_status = EquipmentStatus.AVAILABLE
        _set_equipment_state(item.equipment, asset_status, condition=condition)
        _increment_equipment_counters(
            item.equipment, Rental._hours_between(rental.start_at, moment)
        )
        if damaged:
            _create_maintenance_record(item, user=user)

    still_out = rental.items.filter(returned_at__isnull=True).exists()
    rental.updated_by = user if getattr(user, "is_authenticated", False) else None

    if still_out:
        rental.recalculate_totals()
        rental.save(update_fields=["updated_by", "updated_at"])
        record_audit(
            request,
            action=AuditAction.RENTAL_RETURN,
            instance=rental,
            user=user,
            description=_("Partial return on rental %(code)s — %(n)s item(s) still out")
            % {"code": rental.rental_code, "n": rental.open_item_count},
        )
        return rental

    previous_total = rental.total_amount
    rental.returned_at = moment
    rental.status = Rental.Status.RETURNED
    rental.checked_in_by = user if getattr(user, "is_authenticated", False) else None
    rental.recalculate_totals(save=False)
    rental.late_fee = calculate_late_fee(rental, moment)
    _settle_deposit(rental)
    rental.recalculate_totals(save=False)
    rental.full_clean(exclude=["rental_code", "created_by", "updated_by"])
    rental.save()

    record_audit(
        request,
        action=AuditAction.RENTAL_RETURN,
        instance=rental,
        user=user,
        description=_("Rental %(code)s checked in (%(late)s late fee, %(damage)s damage)")
        % {
            "code": rental.rental_code,
            "late": rental.late_fee,
            "damage": rental.damage_fee,
        },
        changes={
            "status": [Rental.Status.ACTIVE, rental.status],
            "total_amount": [previous_total, rental.total_amount],
            "late_fee": [ZERO, rental.late_fee],
            "damage_fee": [ZERO, rental.damage_fee],
            "deposit_returned": [ZERO, rental.deposit_returned],
        },
    )
    if rental.balance_due > ZERO:
        _notify(
            title=_("Rental %(code)s returned with a balance") % {"code": rental.rental_code},
            body=_("%(customer)s owes %(amount)s after check-in.")
            % {"customer": str(rental.customer), "amount": rental.balance_due},
            category="rental",
        )
    return rental


def quick_return_by_asset_code(code: str, *, user=None, request=None):
    """Check one scanned asset back in, wherever it happens to be out.

    This is the counter's fast path: a customer drops a board on the desk, the
    operator scans it, and the right contract is found and updated.
    """
    equipment = find_equipment_by_code(code)
    if equipment is None:
        raise ValidationError(_("No asset matches the code “%(code)s”.") % {"code": code})

    item = (
        RentalItem.objects.select_related("rental", "equipment")
        .filter(
            equipment=equipment,
            returned_at__isnull=True,
            rental__status__in=Rental.OPEN_STATUSES,
        )
        .order_by("rental__expected_return_at")
        .first()
    )
    if item is None:
        raise ValidationError(
            _("%(item)s is not currently out on a rental.")
            % {"item": equipment_label(equipment)}
        )

    rental = return_rental(
        item.rental,
        {item.pk: (item.condition_out, "", "", ZERO)},
        user,
        request=request,
    )
    item.refresh_from_db()
    return rental, item


# ---------------------------------------------------------------------------
# Contract changes
# ---------------------------------------------------------------------------
@transaction.atomic
def extend_rental(rental: Rental, new_return_at, *, user=None, request=None) -> Rental:
    """Push the due-back time out and re-price the whole hire."""
    if rental.status not in Rental.OPEN_STATUSES:
        raise ValidationError(_("Only an open rental can be extended."))
    if new_return_at is None or new_return_at <= rental.expected_return_at:
        raise ValidationError(_("The new due-back time must be later than the current one."))

    open_items = list(rental.items.select_related("equipment").filter(returned_at__isnull=True))
    if not open_items:
        raise ValidationError(_("Every item is already back; there is nothing to extend."))

    problems: list[str] = []
    for item in open_items:
        problems.extend(
            equipment_conflicts(
                item.equipment,
                rental.expected_return_at,
                new_return_at,
                exclude_rental=rental,
            )
        )
    if problems:
        raise ValidationError(problems)

    previous_due = rental.expected_return_at
    for item in open_items:
        item.unit_price = calculate_rental_price(
            item.equipment, rental.period_type, rental.start_at, new_return_at, quantity=1
        )
        item.updated_by = user if getattr(user, "is_authenticated", False) else None
        item.save(update_fields=["unit_price", "line_total", "updated_by", "updated_at"])

    rental.expected_return_at = new_return_at
    # An extension granted before the gear is chased is no longer overdue.
    if rental.status == Rental.Status.OVERDUE and new_return_at > timezone.now():
        rental.status = Rental.Status.ACTIVE
    rental.updated_by = user if getattr(user, "is_authenticated", False) else None
    rental.recalculate_totals(save=False)
    rental.full_clean(exclude=["rental_code", "created_by", "updated_by"])
    rental.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=rental,
        user=user,
        description=_("Rental %(code)s extended") % {"code": rental.rental_code},
        changes={
            "expected_return_at": [previous_due, new_return_at],
            "total_amount": [None, rental.total_amount],
        },
    )
    return rental


def late_cancellation_charge(rental: Rental, *, at=None) -> Decimal:
    """Charge kept when a reservation is cancelled inside the notice window.

    Both the window and the percentage are school policy, stored as system
    settings; the default is a free cancellation.
    """
    at = at or timezone.now()
    hours_notice = hours_between(at, rental.start_at)
    free_hours = SystemSetting.get(FREE_CANCELLATION_HOURS_KEY, DEFAULT_FREE_CANCELLATION_HOURS)
    try:
        free_hours = Decimal(str(free_hours))
    except (TypeError, ArithmeticError, ValueError):
        free_hours = Decimal(DEFAULT_FREE_CANCELLATION_HOURS)
    if hours_notice >= free_hours:
        return ZERO
    percent = to_decimal(SystemSetting.get(LATE_CANCELLATION_PERCENT_KEY, ZERO))
    if percent <= ZERO:
        return ZERO
    return ((rental.subtotal or ZERO) * percent / Decimal(100)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


@transaction.atomic
def cancel_rental(rental: Rental, *, user=None, reason: str = "", request=None) -> Rental:
    """Cancel a reservation that was never picked up.

    A hire whose gear has already left the shop cannot be cancelled — it has to
    be checked in (or written off as lost), because the equipment state and the
    money both need resolving.
    """
    if rental.status != Rental.Status.RESERVED:
        raise ValidationError(
            _("Only a reservation can be cancelled. Check the equipment in instead.")
        )

    charge = late_cancellation_charge(rental)
    for item in rental.items.select_related("equipment"):
        _set_equipment_state(item.equipment, EquipmentStatus.AVAILABLE)

    rental.status = Rental.Status.CANCELLED
    rental.discount_amount = max((rental.subtotal or ZERO) - charge, ZERO)
    rental.late_fee = ZERO
    rental.deposit_returned = rental.deposit_amount or ZERO
    rental.deposit_status = Rental.DepositStatus.RETURNED
    if reason:
        stamp = timezone.localtime(timezone.now()).strftime("%d.%m.%Y %H:%M")
        rental.notes = f"{rental.notes}\n[{stamp}] {_('Cancelled')}: {reason}".strip()
    rental.updated_by = user if getattr(user, "is_authenticated", False) else None
    rental.recalculate_totals(save=False)
    rental.full_clean(exclude=["rental_code", "created_by", "updated_by"])
    rental.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=rental,
        user=user,
        description=_("Rental %(code)s cancelled") % {"code": rental.rental_code},
        changes={"status": [Rental.Status.RESERVED, Rental.Status.CANCELLED]},
    )
    return rental


@transaction.atomic
def mark_rental_lost(
    rental: Rental,
    *,
    replacement_charge: Decimal = ZERO,
    user=None,
    reason: str = "",
    request=None,
) -> Rental:
    """Write off equipment that is never coming back.

    The replacement value is spread across the items still out (in proportion to
    their hire price) so the charge shows up per asset, and the deposit is
    applied against it.
    """
    if rental.status not in Rental.OPEN_STATUSES:
        raise ValidationError(_("Only an open rental can be written off."))

    open_items = list(rental.items.select_related("equipment").filter(returned_at__isnull=True))
    if not open_items:
        raise ValidationError(_("Every item is already back."))

    replacement_charge = to_decimal(replacement_charge)
    weights = [item.line_total or ZERO for item in open_items]
    weight_total = sum(weights, ZERO)

    allocated = ZERO
    for index, item in enumerate(open_items):
        if index == len(open_items) - 1:
            share = replacement_charge - allocated
        elif weight_total > ZERO:
            share = (replacement_charge * weights[index] / weight_total).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
        else:
            share = (replacement_charge / Decimal(len(open_items))).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
        allocated += share

        item.damage_reported = True
        item.damage_type = DamageType.OTHER
        item.damage_charge = max(share, ZERO)
        item.condition_in = EquipmentCondition.UNUSABLE
        item.damage_notes = (
            reason or _("Not returned — charged at replacement value.")
        )
        item.updated_by = user if getattr(user, "is_authenticated", False) else None
        item.save()
        _set_equipment_state(item.equipment, EquipmentStatus.LOST)

    rental.status = Rental.Status.LOST
    rental.updated_by = user if getattr(user, "is_authenticated", False) else None
    rental.recalculate_totals(save=False)
    rental.late_fee = calculate_late_fee(rental, timezone.now())
    _settle_deposit(rental)
    rental.recalculate_totals(save=False)
    rental.full_clean(exclude=["rental_code", "created_by", "updated_by"])
    rental.save()

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=rental,
        user=user,
        description=_("Rental %(code)s written off as lost") % {"code": rental.rental_code},
        changes={
            "status": [None, Rental.Status.LOST],
            "damage_fee": [None, rental.damage_fee],
            "total_amount": [None, rental.total_amount],
        },
    )
    return rental


@transaction.atomic
def register_payment(
    rental: Rental, amount, *, user=None, method: str = "", request=None
) -> Rental:
    """Record money taken at the counter against a hire."""
    amount = to_decimal(amount)
    if amount <= ZERO:
        raise ValidationError(_("The payment amount must be greater than zero."))

    previous = rental.paid_amount or ZERO
    rental.paid_amount = previous + amount
    rental.updated_by = user if getattr(user, "is_authenticated", False) else None
    rental.recalculate_totals(save=False)
    rental.save()

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=rental,
        user=user,
        description=_("%(amount)s received against rental %(code)s")
        % {"amount": amount, "code": rental.rental_code},
        changes={
            "paid_amount": [previous, rental.paid_amount],
            "payment_method": [None, method or ""],
        },
    )
    return rental


# ---------------------------------------------------------------------------
# Scheduled work & reporting
# ---------------------------------------------------------------------------
def flag_overdue_rentals(*, now=None) -> int:
    """Mark every active hire past its due-back time as overdue.

    Returns the number of contracts flagged. Runs hourly from Celery beat; it is
    idempotent, so running it twice changes nothing.
    """
    now = now or timezone.now()
    candidates = list(
        Rental.objects.filter(
            status=Rental.Status.ACTIVE,
            returned_at__isnull=True,
            expected_return_at__lt=now,
        ).select_related("customer")[:500]
    )
    if not candidates:
        return 0

    ids = [rental.pk for rental in candidates]
    Rental.objects.filter(pk__in=ids).update(status=Rental.Status.OVERDUE, updated_at=now)

    for rental in candidates:
        hours = Rental._hours_between(rental.expected_return_at, now)
        _notify(
            title=_("Overdue rental %(code)s") % {"code": rental.rental_code},
            body=_("%(customer)s is %(hours)s hours late returning %(count)s item(s).")
            % {
                "customer": str(rental.customer),
                "hours": hours,
                "count": rental.open_item_count,
            },
            category="rental_overdue",
        )
    record_system_event(
        AuditAction.SYSTEM,
        _("%(count)s rental(s) flagged overdue") % {"count": len(ids)},
    )
    return len(ids)


def rental_revenue(start=None, end=None) -> Decimal:
    """Total invoiced hire revenue for a period, cancellations excluded."""
    queryset = Rental.objects.exclude(status=Rental.Status.CANCELLED)
    if start is not None:
        queryset = queryset.filter(start_at__gte=start)
    if end is not None:
        queryset = queryset.filter(start_at__lte=end)
    return queryset.aggregate(
        total=Coalesce(
            Sum("total_amount"),
            Value(ZERO),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]
