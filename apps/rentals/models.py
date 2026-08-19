"""Rental and RentalItem.

Design notes
------------
* A :class:`Rental` is a *contract*: it freezes the prices agreed at check-out.
  Re-pricing only happens through :mod:`apps.rentals.services` (extension,
  late fee, damage), never implicitly on save.
* Every amount is a :class:`~decimal.Decimal` via ``money_field()``. The counter
  handles cash; a rounding error here is a real till discrepancy.
* ``status`` tracks the *contract*; ``RentalItem.returned_at`` tracks the
  *physical asset*. Both matter, because a customer can bring back the board and
  keep the wetsuit.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import DamageType, EquipmentCondition, PaymentStatus, RentalPeriod
from apps.core.models import BaseModel, money_field
from apps.core.utils import next_sequential_code

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
SECONDS_PER_HOUR = Decimal("3600")


class Rental(BaseModel):
    """One hire contract covering one or more pieces of equipment."""

    class Status(models.TextChoices):
        RESERVED = "reserved", _("Reserved")
        ACTIVE = "active", _("Out with customer")
        RETURNED = "returned", _("Returned")
        OVERDUE = "overdue", _("Overdue")
        CANCELLED = "cancelled", _("Cancelled")
        LOST = "lost", _("Not returned / lost")

    class DepositStatus(models.TextChoices):
        HELD = "held", _("Held")
        RETURNED = "returned", _("Returned to customer")
        FORFEITED = "forfeited", _("Withheld")

    #: Contract statuses in which the gear is still committed to this customer.
    OPEN_STATUSES = (Status.RESERVED, Status.ACTIVE, Status.OVERDUE)
    #: Statuses in which the gear is physically off the premises.
    OUT_STATUSES = (Status.ACTIVE, Status.OVERDUE)

    # --- identity ---------------------------------------------------------
    rental_code = models.CharField(
        _("rental code"),
        max_length=20,
        unique=True,
        db_index=True,
        help_text=_("Assigned automatically, e.g. RNT00001."),
    )

    # --- who --------------------------------------------------------------
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="rentals",
        help_text=_("The person who signs for the equipment and owes the money."),
    )
    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rentals",
        help_text=_("Set when the gear is used by a student other than the payer."),
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rentals",
        help_text=_("Links a hire to the lesson or camp booking it belongs to."),
    )

    # --- lifecycle --------------------------------------------------------
    status = models.CharField(
        _("status"),
        max_length=12,
        choices=Status.choices,
        default=Status.RESERVED,
        db_index=True,
    )
    period_type = models.CharField(
        _("period"),
        max_length=10,
        choices=RentalPeriod.choices,
        default=RentalPeriod.DAILY,
        db_index=True,
    )
    start_at = models.DateTimeField(_("starts at"), db_index=True)
    expected_return_at = models.DateTimeField(_("due back"), db_index=True)
    returned_at = models.DateTimeField(_("returned at"), null=True, blank=True, db_index=True)

    # --- deposit ----------------------------------------------------------
    deposit_amount = money_field(
        _("deposit taken"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Cash or card pre-authorisation held against damage and late return."),
    )
    deposit_returned = money_field(
        _("deposit returned"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Amount actually handed back at check-in."),
    )
    deposit_status = models.CharField(
        _("deposit status"),
        max_length=10,
        choices=DepositStatus.choices,
        default=DepositStatus.HELD,
        db_index=True,
    )

    # --- money ------------------------------------------------------------
    subtotal = money_field(_("hire charge"), validators=[MinValueValidator(ZERO)])
    discount_amount = money_field(_("discount"), validators=[MinValueValidator(ZERO)])
    late_fee = money_field(_("late fee"), validators=[MinValueValidator(ZERO)])
    damage_fee = money_field(_("damage charge"), validators=[MinValueValidator(ZERO)])
    total_amount = money_field(_("total"), validators=[MinValueValidator(ZERO)])
    paid_amount = money_field(_("paid"), validators=[MinValueValidator(ZERO)])
    payment_status = models.CharField(
        _("payment status"),
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
        db_index=True,
    )

    # --- counter paperwork ------------------------------------------------
    id_document_held = models.BooleanField(
        _("identity document held"),
        default=False,
        help_text=_("An ID is retained at the counter until the gear comes back."),
    )
    notes = models.TextField(_("notes"), blank=True)

    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("checked out by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rentals_checked_out",
    )
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("checked in by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rentals_checked_in",
    )

    class Meta:
        verbose_name = _("rental")
        verbose_name_plural = _("rentals")
        ordering = ["-start_at", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["status", "expected_return_at"]),
            models.Index(fields=["customer", "-start_at"]),
            models.Index(fields=["payment_status", "-start_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.rental_code} · {self.customer}"

    # ------------------------------------------------------------------ time
    @property
    def duration_hours(self) -> Decimal:
        """Billed length of the hire, in hours.

        Measured to the actual return when the gear is back, otherwise to the
        agreed due-back time.
        """
        end = self.returned_at or self.expected_return_at
        if not (self.start_at and end):
            return ZERO
        return self._hours_between(self.start_at, end)

    @property
    def is_overdue(self) -> bool:
        """``True`` while gear is out and past its due-back time."""
        if self.status in (self.Status.RETURNED, self.Status.CANCELLED, self.Status.LOST):
            return False
        if self.returned_at is not None or not self.expected_return_at:
            return False
        return timezone.now() > self.expected_return_at

    @property
    def hours_overdue(self) -> Decimal:
        """Hours past the due-back time — also reported after a late return."""
        if not self.expected_return_at:
            return ZERO
        if self.status in (self.Status.CANCELLED,):
            return ZERO
        end = self.returned_at or timezone.now()
        if end <= self.expected_return_at:
            return ZERO
        return self._hours_between(self.expected_return_at, end)

    @staticmethod
    def _hours_between(start, end) -> Decimal:
        """Exact Decimal hour count — never float, this feeds money."""
        delta = end - start
        seconds = Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds)
        if delta.microseconds:
            seconds += Decimal(1)
        return (seconds / SECONDS_PER_HOUR).quantize(CENT)

    # ----------------------------------------------------------------- money
    @property
    def balance_due(self) -> Decimal:
        """Outstanding amount. Negative means the customer is owed a refund."""
        return (self.total_amount or ZERO) - (self.paid_amount or ZERO)

    @property
    def deposit_withheld(self) -> Decimal:
        return (self.deposit_amount or ZERO) - (self.deposit_returned or ZERO)

    @property
    def item_count(self) -> int:
        """Number of physical units on the contract."""
        cache = getattr(self, "_prefetched_objects_cache", {})
        if "items" in cache:
            return sum(item.quantity for item in self.items.all())
        return self.items.aggregate(total=Sum("quantity"))["total"] or 0

    @property
    def can_check_in(self) -> bool:
        """True when there is still something to hand back on this contract."""
        return self.status in self.OPEN_STATUSES and self.open_item_count > 0

    @property
    def open_item_count(self) -> int:
        cache = getattr(self, "_prefetched_objects_cache", {})
        if "items" in cache:
            return sum(i.quantity for i in self.items.all() if i.returned_at is None)
        return self.items.filter(returned_at__isnull=True).aggregate(t=Sum("quantity"))["t"] or 0

    def recalculate_totals(self, *, save: bool = True) -> Decimal:
        """Recompute ``subtotal``/``damage_fee``/``total_amount``/``payment_status``.

        ``subtotal`` and ``damage_fee`` are always derived from the lines, so a
        line change can never silently disagree with the contract total.
        Returns the new total.
        """
        if self.pk:
            aggregates = self.items.aggregate(
                lines=Sum("line_total"), damage=Sum("damage_charge")
            )
            self.subtotal = (aggregates["lines"] or ZERO).quantize(CENT)
            self.damage_fee = (aggregates["damage"] or ZERO).quantize(CENT)

        # A discount can never exceed the hire charge it discounts.
        if self.discount_amount > self.subtotal:
            self.discount_amount = self.subtotal

        total = self.subtotal - self.discount_amount + self.late_fee + self.damage_fee
        self.total_amount = total if total > ZERO else ZERO

        if self.payment_status != PaymentStatus.REFUNDED:
            paid = self.paid_amount or ZERO
            if self.total_amount <= ZERO or paid >= self.total_amount:
                self.payment_status = PaymentStatus.PAID
            elif paid > ZERO:
                self.payment_status = PaymentStatus.PARTIAL
            else:
                self.payment_status = PaymentStatus.UNPAID

        if save and self.pk:
            self.save(
                update_fields=[
                    "subtotal",
                    "discount_amount",
                    "damage_fee",
                    "late_fee",
                    "total_amount",
                    "payment_status",
                    "updated_at",
                ]
            )
        return self.total_amount

    # ------------------------------------------------------------ validation
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.start_at and self.expected_return_at and self.expected_return_at <= self.start_at:
            errors.setdefault("expected_return_at", []).append(
                _("The due-back time must be after the start time.")
            )
        if self.returned_at and self.start_at and self.returned_at < self.start_at:
            errors.setdefault("returned_at", []).append(
                _("Equipment cannot be returned before it was handed out.")
            )
        if self.deposit_returned > self.deposit_amount:
            errors.setdefault("deposit_returned", []).append(
                _("More deposit cannot be returned than was taken.")
            )
        if self.status == self.Status.RETURNED and self.returned_at is None:
            errors.setdefault("returned_at", []).append(
                _("A returned rental must record when it came back.")
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.rental_code:
            self.rental_code = next_sequential_code(Rental, "rental_code", "RNT")
        super().save(*args, **kwargs)


class RentalItem(BaseModel):
    """One piece of equipment on a rental contract.

    ``condition_out`` is recorded at hand-over and ``condition_in`` at check-in.
    The pair is what settles an argument about who broke the board.
    """

    #: Exposed for the inline check-in control on the rental detail screen.
    CONDITION_CHOICES = EquipmentCondition.choices

    rental = models.ForeignKey(
        Rental,
        verbose_name=_("rental"),
        on_delete=models.CASCADE,
        related_name="items",
    )
    equipment = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("equipment"),
        on_delete=models.PROTECT,
        related_name="rental_items",
    )

    unit_price = money_field(
        _("unit price"),
        validators=[MinValueValidator(ZERO)],
        help_text=_("Price of one unit for the whole hire period, as agreed at check-out."),
    )
    quantity = models.PositiveIntegerField(
        _("quantity"), default=1, validators=[MinValueValidator(1)]
    )
    line_total = money_field(_("line total"), validators=[MinValueValidator(ZERO)])

    condition_out = models.CharField(
        _("condition out"),
        max_length=12,
        choices=EquipmentCondition.choices,
        default=EquipmentCondition.GOOD,
    )
    condition_in = models.CharField(
        _("condition in"),
        max_length=12,
        choices=EquipmentCondition.choices,
        null=True,
        blank=True,
    )

    damage_reported = models.BooleanField(_("damage reported"), default=False, db_index=True)
    damage_type = models.CharField(
        _("damage type"), max_length=20, choices=DamageType.choices, blank=True
    )
    damage_notes = models.TextField(_("damage notes"), blank=True)
    damage_charge = money_field(_("damage charge"), validators=[MinValueValidator(ZERO)])

    returned_at = models.DateTimeField(_("returned at"), null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = _("rental item")
        verbose_name_plural = _("rental items")
        ordering = ["rental_id", "id"]
        base_manager_name = "all_objects"
        constraints = [
            models.UniqueConstraint(
                fields=["rental", "equipment"],
                condition=Q(is_deleted=False),
                name="rentals_unique_equipment_per_rental",
            )
        ]
        indexes = [
            models.Index(fields=["equipment", "returned_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.equipment} × {self.quantity}"

    @property
    def is_returned(self) -> bool:
        return self.returned_at is not None

    @property
    def total_with_damage(self) -> Decimal:
        return (self.line_total or ZERO) + (self.damage_charge or ZERO)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}
        if self.quantity is None or self.quantity < 1:
            errors.setdefault("quantity", []).append(_("At least one unit must be hired."))
        if self.damage_reported and not self.damage_type:
            errors.setdefault("damage_type", []).append(
                _("Select the kind of damage so the repair can be planned.")
            )
        if not self.damage_reported and (self.damage_charge or ZERO) > ZERO:
            errors.setdefault("damage_charge", []).append(
                _("A damage charge requires the damage to be reported.")
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.line_total = ((self.unit_price or ZERO) * (self.quantity or 1)).quantize(CENT)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(set(kwargs["update_fields"]) | {"line_total"})
        super().save(*args, **kwargs)


#: Contract statuses in which equipment is committed to a customer.
OPEN_RENTAL_STATUSES = Rental.OPEN_STATUSES
#: Contract statuses in which equipment is physically off the premises.
OUT_RENTAL_STATUSES = Rental.OUT_STATUSES
