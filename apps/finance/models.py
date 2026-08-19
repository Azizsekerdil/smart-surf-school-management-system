"""Finance models.

Money conventions
-----------------
* Every amount is ``money_field()`` — ``Decimal(12, 2)``. Percentages use
  ``percent_field()``.
* Derived amounts are quantised through :func:`to_money`, which rounds half up.
  Banker's rounding is wrong on a till receipt.
* A **refund** is a separate :class:`Payment` row with a *negative* ``amount``,
  ``is_refund=True`` and ``refunded_payment`` pointing at the original. Its
  ``status`` stays :attr:`PaymentStatus.PAID` because the money genuinely moved
  (outwards). That makes every naive ``Sum("amount")`` anywhere in the codebase
  — including ``apps.customers.selectors.paid_total`` and
  ``apps.ai.tools.get_revenue_summary`` — produce the correct *net* figure
  without those modules knowing refunds exist.

Sequences
---------
``invoice_number`` is year-scoped (``INV-2026-00001``); ``payment_code`` and
``expense_code`` are global (``PAY00001`` / ``EXP00001``). All three are
allocated inside a savepoint and retried on collision, so two receptionists
saving at the same moment cannot produce a duplicate reference.
"""

from __future__ import annotations

import calendar
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import PaymentMethod, PaymentStatus
from apps.core.models import BaseModel, TimeStampedModel, money_field, percent_field
from apps.core.utils import next_sequential_code
from apps.core.validators import validate_document_upload

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")

#: Number of attempts to allocate a unique document reference before giving up.
CODE_ALLOCATION_ATTEMPTS = 6

#: Default number of days a customer has to pay. Overridable per invoice.
DEFAULT_PAYMENT_TERMS_DAYS = 14

INVOICE_PREFIX = "INV"
PAYMENT_PREFIX = "PAY"
EXPENSE_PREFIX = "EXP"


def to_money(value) -> Decimal:
    """Coerce anything to a 2-dp Decimal, rounding half up."""
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def default_currency() -> str:
    """The school's configured currency code (callable so tests can override)."""
    return settings.SCHOOL.get("CURRENCY", "TRY")


def _money_sum(queryset, field: str) -> Decimal:
    """``Sum`` that returns ``0.00`` instead of ``None`` on both backends."""
    return queryset.aggregate(
        total=Coalesce(
            Sum(field),
            Value(ZERO),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"] or ZERO


def next_invoice_number(year: int | None = None) -> str:
    """Return the next ``INV-<year>-00001`` reference for *year*."""
    year = int(year or timezone.localdate().year)
    prefix = f"{INVOICE_PREFIX}-{year}-"
    latest = (
        Invoice.all_objects.filter(invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .values_list("invoice_number", flat=True)
        .first()
    )
    number = 1
    if latest:
        suffix = str(latest)[len(prefix) :]
        if suffix.isdigit():
            number = int(suffix) + 1
    return f"{prefix}{number:05d}"


def _save_with_allocated_code(instance, field: str, allocate, save_super) -> None:
    """Save *instance*, re-allocating ``field`` if a concurrent write took it.

    Each attempt runs in its own savepoint so a losing race leaves the
    surrounding transaction usable.
    """
    for attempt in range(CODE_ALLOCATION_ATTEMPTS):
        setattr(instance, field, allocate())
        try:
            with transaction.atomic():
                save_super()
            return
        except IntegrityError:
            if attempt == CODE_ALLOCATION_ATTEMPTS - 1:
                raise
            setattr(instance, field, "")


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class Invoice(BaseModel):
    """A statement of what a customer owes, and for what."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ISSUED = "issued", _("Issued")
        PARTIAL = "partial", _("Partially paid")
        PAID = "paid", _("Paid")
        OVERDUE = "overdue", _("Overdue")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    #: Statuses in which the invoice still represents money the school expects.
    OPEN_STATUSES = (Status.ISSUED, Status.PARTIAL, Status.OVERDUE)
    #: Statuses that no longer move: editing the lines would rewrite history.
    CLOSED_STATUSES = (Status.PAID, Status.CANCELLED, Status.REFUNDED)

    invoice_number = models.CharField(
        _("invoice number"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Allocated automatically per year, e.g. INV-2026-00001."),
    )
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    rental = models.ForeignKey(
        "rentals.Rental",
        verbose_name=_("rental"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )

    issue_date = models.DateField(_("issue date"), default=timezone.localdate, db_index=True)
    due_date = models.DateField(_("due date"), db_index=True)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    subtotal = money_field(_("subtotal"), validators=[MinValueValidator(ZERO)])
    discount_amount = money_field(_("discount"), validators=[MinValueValidator(ZERO)])
    tax_rate = percent_field(
        _("tax rate %"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
        help_text=_("VAT percentage applied after the discount."),
    )
    tax_amount = money_field(_("tax"), validators=[MinValueValidator(ZERO)])
    total_amount = money_field(_("total"), validators=[MinValueValidator(ZERO)])
    paid_amount = money_field(_("paid"))

    currency = models.CharField(_("currency"), max_length=3, default=default_currency)
    notes = models.TextField(_("notes"), blank=True, help_text=_("Printed on the invoice."))
    terms = models.TextField(_("payment terms"), blank=True)

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ["-issue_date", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["issue_date", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} · {self.customer}"

    # -- persistence --------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.due_date:
            self.due_date = (self.issue_date or timezone.localdate()) + timedelta(
                days=DEFAULT_PAYMENT_TERMS_DAYS
            )
        if self.invoice_number:
            return super().save(*args, **kwargs)
        year = (self.issue_date or timezone.localdate()).year
        _save_with_allocated_code(
            self,
            "invoice_number",
            lambda: next_invoice_number(year),
            lambda: super(Invoice, self).save(*args, **kwargs),
        )
        return None

    # -- money --------------------------------------------------------------
    @property
    def balance_due(self) -> Decimal:
        """What is still owed. Negative means the customer is in credit."""
        return to_money(self.total_amount) - to_money(self.paid_amount)

    @property
    def is_paid(self) -> bool:
        total = to_money(self.total_amount)
        return total > ZERO and to_money(self.paid_amount) >= total

    @property
    def is_overdue(self) -> bool:
        if self.status in self.CLOSED_STATUSES or self.status == self.Status.DRAFT:
            return False
        if not self.due_date:
            return False
        return self.due_date < timezone.localdate() and self.balance_due > ZERO

    @property
    def days_overdue(self) -> int:
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days

    @property
    def taxable_amount(self) -> Decimal:
        return max(ZERO, to_money(self.subtotal) - to_money(self.discount_amount))

    @property
    def can_edit(self) -> bool:
        """Only a draft may have its lines changed."""
        return self.status == self.Status.DRAFT

    @property
    def can_issue(self) -> bool:
        return self.status == self.Status.DRAFT and self.total_amount > ZERO

    @property
    def can_cancel(self) -> bool:
        return self.status not in self.CLOSED_STATUSES and to_money(self.paid_amount) <= ZERO

    # -- calculation --------------------------------------------------------
    def recalculate(self, commit: bool = True) -> Decimal:
        """Recompute totals from the lines and refresh the status.

        Order matters and is the legal one: line totals, then the invoice-level
        discount, then tax on the discounted amount.
        """
        if self.pk:
            self.subtotal = to_money(_money_sum(self.lines.all(), "line_total"))
        else:
            self.subtotal = to_money(self.subtotal)

        discount = min(to_money(self.discount_amount), to_money(self.subtotal))
        self.discount_amount = discount
        taxable = to_money(self.subtotal) - discount
        self.tax_amount = to_money(taxable * to_money(self.tax_rate) / HUNDRED)
        self.total_amount = to_money(taxable + self.tax_amount)

        self.refresh_status()

        if commit and self.pk:
            self.save(
                update_fields=[
                    "subtotal",
                    "discount_amount",
                    "tax_amount",
                    "total_amount",
                    "status",
                    "updated_at",
                ]
            )
        return self.total_amount

    def refresh_status(self) -> str:
        """Move the status to match the money, without persisting."""
        if self.status == self.Status.CANCELLED:
            return self.status

        total = to_money(self.total_amount)
        paid = to_money(self.paid_amount)

        if self.status == self.Status.DRAFT and paid <= ZERO:
            return self.status

        if total > ZERO and paid <= ZERO and self.pk and self.has_refunds:
            self.status = self.Status.REFUNDED
        elif total > ZERO and paid >= total:
            self.status = self.Status.PAID
        elif self.due_date and self.due_date < timezone.localdate():
            self.status = self.Status.OVERDUE
        elif paid > ZERO:
            self.status = self.Status.PARTIAL
        else:
            self.status = self.Status.ISSUED
        return self.status

    @property
    def has_refunds(self) -> bool:
        if not self.pk:
            return False
        return self.payments.filter(is_refund=True).exists()

    # -- validation ---------------------------------------------------------
    def clean(self):
        errors: dict[str, list] = {}

        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            errors.setdefault("due_date", []).append(
                _("The due date cannot fall before the issue date.")
            )
        if to_money(self.discount_amount) < ZERO:
            errors.setdefault("discount_amount", []).append(
                _("A discount cannot be negative.")
            )
        if not (ZERO <= to_money(self.tax_rate) <= HUNDRED):
            errors.setdefault("tax_rate", []).append(
                _("The tax rate must be between 0 and 100 percent.")
            )
        if self.booking_id and self.rental_id:
            errors.setdefault("rental", []).append(
                _("An invoice covers a booking or a rental, not both.")
            )
        if errors:
            raise ValidationError(errors)


class InvoiceLine(TimeStampedModel):
    """One chargeable item on an invoice."""

    invoice = models.ForeignKey(
        Invoice,
        verbose_name=_("invoice"),
        on_delete=models.CASCADE,
        related_name="lines",
    )
    description = models.CharField(_("description"), max_length=250)
    quantity = models.DecimalField(
        _("quantity"),
        max_digits=8,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    unit_price = money_field(_("unit price"), validators=[MinValueValidator(ZERO)])
    discount_amount = money_field(_("line discount"), validators=[MinValueValidator(ZERO)])
    line_total = money_field(_("line total"), validators=[MinValueValidator(ZERO)])
    sort_order = models.PositiveSmallIntegerField(_("order"), default=0)

    class Meta:
        verbose_name = _("invoice line")
        verbose_name_plural = _("invoice lines")
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["invoice", "sort_order"])]

    def __str__(self) -> str:
        return f"{self.description} × {self.quantity}"

    @property
    def gross_amount(self) -> Decimal:
        """Price before the line discount."""
        return to_money(Decimal(str(self.quantity or 0)) * to_money(self.unit_price))

    def compute_total(self) -> Decimal:
        """Set and return ``line_total`` — never below zero."""
        self.line_total = max(ZERO, self.gross_amount - to_money(self.discount_amount))
        return self.line_total

    def save(self, *args, **kwargs):
        self.compute_total()
        super().save(*args, **kwargs)

    def clean(self):
        errors: dict[str, list] = {}
        if Decimal(str(self.quantity or 0)) <= ZERO:
            errors.setdefault("quantity", []).append(_("The quantity must be above zero."))
        if to_money(self.unit_price) < ZERO:
            errors.setdefault("unit_price", []).append(_("The unit price cannot be negative."))
        if to_money(self.discount_amount) > self.gross_amount:
            errors.setdefault("discount_amount", []).append(
                _("The line discount cannot exceed the line value.")
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class Payment(BaseModel):
    """One movement of money between the customer and the school.

    A positive ``amount`` is money in; a negative ``amount`` is a refund going
    back out. The two are distinguished by ``is_refund`` so screens can label
    them, but every aggregation simply sums ``amount`` and gets the net.
    """

    class Category(models.TextChoices):
        LESSON = "lesson", _("Lesson")
        CAMP = "camp", _("Surf camp")
        RENTAL = "rental", _("Equipment rental")
        SHOP = "shop", _("Shop")
        PACKAGE = "package", _("Lesson package")
        DEPOSIT = "deposit", _("Deposit")
        OTHER = "other", _("Other")

    payment_code = models.CharField(
        _("payment code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Allocated automatically, e.g. PAY00001."),
    )
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="payments",
    )
    invoice = models.ForeignKey(
        Invoice,
        verbose_name=_("invoice"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name=_("booking"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    rental = models.ForeignKey(
        "rentals.Rental",
        verbose_name=_("rental"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )

    amount = money_field(_("amount"))
    method = models.CharField(
        _("method"),
        max_length=12,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
        db_index=True,
    )
    category = models.CharField(
        _("category"),
        max_length=10,
        choices=Category.choices,
        default=Category.OTHER,
        db_index=True,
        help_text=_("What the money was for. Drives the revenue breakdown."),
    )

    paid_at = models.DateTimeField(_("paid at"), default=timezone.now, db_index=True)
    reference = models.CharField(
        _("reference"),
        max_length=100,
        blank=True,
        help_text=_("Card authorisation, transfer reference or receipt number."),
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("received by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_payments_received",
    )

    is_refund = models.BooleanField(_("refund"), default=False, db_index=True)
    refunded_payment = models.ForeignKey(
        "self",
        verbose_name=_("refund of"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds",
    )
    refund_reason = models.TextField(_("refund reason"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ["-paid_at", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["category", "paid_at"]),
            models.Index(fields=["customer", "-paid_at"]),
            models.Index(fields=["method", "paid_at"]),
            models.Index(fields=["is_refund", "paid_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.payment_code} · {self.amount}"

    def save(self, *args, **kwargs):
        if self.payment_code:
            return super().save(*args, **kwargs)
        _save_with_allocated_code(
            self,
            "payment_code",
            lambda: next_sequential_code(Payment, "payment_code", PAYMENT_PREFIX, width=5),
            lambda: super(Payment, self).save(*args, **kwargs),
        )
        return None

    # -- derived ------------------------------------------------------------
    @property
    def absolute_amount(self) -> Decimal:
        """Magnitude of the movement, for display."""
        return abs(to_money(self.amount))

    @property
    def refunded_amount(self) -> Decimal:
        """How much of this payment has already been refunded (positive)."""
        if not self.pk or self.is_refund:
            return ZERO
        return abs(to_money(_money_sum(self.refunds.all(), "amount")))

    @property
    def refundable_amount(self) -> Decimal:
        """What may still be refunded against this payment."""
        if self.is_refund:
            return ZERO
        return max(ZERO, to_money(self.amount) - self.refunded_amount)

    @property
    def is_fully_refunded(self) -> bool:
        if self.is_refund or to_money(self.amount) <= ZERO:
            return False
        return self.refundable_amount <= ZERO

    @property
    def can_refund(self) -> bool:
        return not self.is_refund and self.refundable_amount > ZERO

    @property
    def linked_label(self) -> str:
        """Human description of what this payment settles."""
        if self.invoice_id:
            return str(self.invoice.invoice_number)
        if self.booking_id:
            return str(self.booking.booking_code)
        if self.rental_id:
            return str(self.rental.rental_code)
        return ""

    # -- validation ---------------------------------------------------------
    def clean(self):
        errors: dict[str, list] = {}
        amount = to_money(self.amount)

        if amount == ZERO:
            errors.setdefault("amount", []).append(_("A payment of zero cannot be recorded."))
        elif self.is_refund and amount > ZERO:
            errors.setdefault("amount", []).append(
                _("A refund must be recorded as a negative amount.")
            )
        elif not self.is_refund and amount < ZERO:
            errors.setdefault("amount", []).append(
                _("Use the refund action to record money going back to a customer.")
            )

        if self.is_refund and not (self.refund_reason or "").strip():
            errors.setdefault("refund_reason", []).append(
                _("Record why the money was refunded.")
            )
        if self.refunded_payment_id and self.refunded_payment_id == self.pk:
            errors.setdefault("refunded_payment", []).append(
                _("A payment cannot refund itself.")
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
class ExpenseCategory(TimeStampedModel):
    """A bucket for outgoings — wetsuit repairs, fuel, rent, wages…"""

    code = models.CharField(_("code"), max_length=20, unique=True, db_index=True)
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(_("order"), default=0)

    class Meta:
        verbose_name = _("expense category")
        verbose_name_plural = _("expense categories")
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class Expense(BaseModel):
    """One outgoing payment made by the school."""

    expense_code = models.CharField(
        _("expense code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Allocated automatically, e.g. EXP00001."),
    )
    category = models.ForeignKey(
        ExpenseCategory,
        verbose_name=_("category"),
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    description = models.CharField(_("description"), max_length=250)
    amount = money_field(_("amount"), validators=[MinValueValidator(ZERO)])
    tax_amount = money_field(_("tax"), validators=[MinValueValidator(ZERO)])
    spent_on = models.DateField(_("spent on"), default=timezone.localdate, db_index=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("paid by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_expenses_paid",
    )
    supplier = models.CharField(_("supplier"), max_length=150, blank=True)
    invoice_reference = models.CharField(_("supplier invoice no."), max_length=100, blank=True)
    receipt = models.FileField(
        _("receipt"),
        upload_to="finance/receipts/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_document_upload],
    )
    is_recurring = models.BooleanField(_("recurring"), default=False, db_index=True)
    recurrence_months = models.PositiveSmallIntegerField(
        _("repeats every (months)"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    equipment = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("equipment"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text=_("Set when the cost belongs to one item of kit."),
    )

    class Meta:
        verbose_name = _("expense")
        verbose_name_plural = _("expenses")
        ordering = ["-spent_on", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["category", "spent_on"]),
            models.Index(fields=["spent_on", "is_recurring"]),
        ]

    def __str__(self) -> str:
        return f"{self.expense_code} · {self.description}"

    def save(self, *args, **kwargs):
        if self.expense_code:
            return super().save(*args, **kwargs)
        _save_with_allocated_code(
            self,
            "expense_code",
            lambda: next_sequential_code(Expense, "expense_code", EXPENSE_PREFIX, width=5),
            lambda: super(Expense, self).save(*args, **kwargs),
        )
        return None

    @property
    def total_amount(self) -> Decimal:
        """What actually left the bank account, tax included."""
        return to_money(to_money(self.amount) + to_money(self.tax_amount))

    @property
    def next_due_on(self):
        """Date the next instance of a recurring cost falls due."""
        if not (self.is_recurring and self.recurrence_months and self.spent_on):
            return None
        month = self.spent_on.month - 1 + int(self.recurrence_months)
        year = self.spent_on.year + month // 12
        month = month % 12 + 1
        day = min(self.spent_on.day, calendar.monthrange(year, month)[1])
        return self.spent_on.replace(year=year, month=month, day=day)

    def clean(self):
        errors: dict[str, list] = {}
        if to_money(self.amount) <= ZERO:
            errors.setdefault("amount", []).append(_("An expense must be above zero."))
        if self.is_recurring and not self.recurrence_months:
            errors.setdefault("recurrence_months", []).append(
                _("State how often the cost repeats.")
            )
        if self.spent_on and self.spent_on > timezone.localdate() + timedelta(days=1):
            errors.setdefault("spent_on", []).append(
                _("An expense cannot be dated in the future.")
            )
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
class CommissionRecord(BaseModel):
    """What the school owes an instructor for work already delivered."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending approval")
        APPROVED = "approved", _("Approved")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")

    #: Statuses that still represent a liability.
    OWED_STATUSES = (Status.PENDING, Status.APPROVED)

    instructor = models.ForeignKey(
        "instructors.Instructor",
        verbose_name=_("instructor"),
        on_delete=models.PROTECT,
        related_name="commissions",
    )
    lesson = models.ForeignKey(
        "lessons.Lesson",
        verbose_name=_("lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commission_records",
    )
    period_start = models.DateField(_("period start"), db_index=True)
    period_end = models.DateField(_("period end"), db_index=True)

    base_amount = money_field(_("base amount"), validators=[MinValueValidator(ZERO)])
    commission_percent = percent_field(
        _("commission %"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
    )
    commission_amount = money_field(_("commission"), validators=[MinValueValidator(ZERO)])

    status = models.CharField(
        _("status"),
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True, db_index=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("commission record")
        verbose_name_plural = _("commission records")
        ordering = ["-period_end", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["instructor", "status"]),
            models.Index(fields=["status", "period_end"]),
        ]

    def __str__(self) -> str:
        return f"{self.instructor} · {self.commission_amount}"

    @property
    def can_approve(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def can_pay(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def can_cancel(self) -> bool:
        return self.status in (self.Status.PENDING, self.Status.APPROVED)

    def compute_amount(self) -> Decimal:
        """Set and return ``commission_amount`` from base × percent."""
        self.commission_amount = to_money(
            to_money(self.base_amount) * to_money(self.commission_percent) / HUNDRED
        )
        return self.commission_amount

    def clean(self):
        errors: dict[str, list] = {}
        if self.period_start and self.period_end and self.period_end < self.period_start:
            errors.setdefault("period_end", []).append(
                _("The period end cannot fall before its start.")
            )
        if not (ZERO <= to_money(self.commission_percent) <= HUNDRED):
            errors.setdefault("commission_percent", []).append(
                _("The commission must be between 0 and 100 percent.")
            )
        if self.status == self.Status.PAID and self.paid_at is None:
            errors.setdefault("paid_at", []).append(_("Record when the commission was paid."))
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
class PricePackage(BaseModel):
    """A bundle of lessons sold up-front at a discount."""

    name = models.CharField(_("name"), max_length=120)
    code = models.CharField(_("code"), max_length=20, unique=True, db_index=True)
    description = models.TextField(_("description"), blank=True)
    lesson_type = models.ForeignKey(
        "lessons.LessonType",
        verbose_name=_("lesson type"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="price_packages",
        help_text=_("Used to work out the saving against buying lessons singly."),
    )
    lesson_count = models.PositiveSmallIntegerField(
        _("lessons included"), default=5, validators=[MinValueValidator(1)]
    )
    price = money_field(_("price"), validators=[MinValueValidator(ZERO)])
    validity_days = models.PositiveSmallIntegerField(
        _("valid for (days)"),
        default=180,
        validators=[MinValueValidator(1)],
        help_text=_("Counted from the day the package is sold."),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(_("order"), default=0)

    class Meta:
        verbose_name = _("price package")
        verbose_name_plural = _("price packages")
        ordering = ["sort_order", "name"]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def price_per_lesson(self) -> Decimal:
        count = int(self.lesson_count or 0)
        if count <= 0:
            return ZERO
        return to_money(to_money(self.price) / Decimal(count))

    @property
    def single_lesson_price(self) -> Decimal:
        """Rack rate of one lesson of this type, or zero when unknown."""
        if not self.lesson_type_id:
            return ZERO
        return to_money(getattr(self.lesson_type, "base_price", ZERO))

    @property
    def saving_vs_single(self) -> Decimal:
        """Money saved against buying every lesson at the single rate."""
        single = self.single_lesson_price
        if single <= ZERO:
            return ZERO
        full = to_money(single * Decimal(int(self.lesson_count or 0)))
        return max(ZERO, to_money(full - to_money(self.price)))

    @property
    def saving_percent(self) -> Decimal:
        single = self.single_lesson_price
        if single <= ZERO:
            return ZERO
        full = to_money(single * Decimal(int(self.lesson_count or 0)))
        if full <= ZERO:
            return ZERO
        return to_money(self.saving_vs_single / full * HUNDRED)

    def clean(self):
        errors: dict[str, list] = {}
        if int(self.lesson_count or 0) < 1:
            errors.setdefault("lesson_count", []).append(
                _("A package must contain at least one lesson.")
            )
        if to_money(self.price) <= ZERO:
            errors.setdefault("price", []).append(_("A package must have a price."))
        if int(self.validity_days or 0) < 1:
            errors.setdefault("validity_days", []).append(
                _("A package must be valid for at least one day.")
            )
        if errors:
            raise ValidationError(errors)


class CustomerPackage(BaseModel):
    """A package a specific customer has bought, and how much of it is left."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        EXHAUSTED = "exhausted", _("Fully used")
        EXPIRED = "expired", _("Expired")
        CANCELLED = "cancelled", _("Cancelled")

    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        related_name="packages",
    )
    package = models.ForeignKey(
        PricePackage,
        verbose_name=_("package"),
        on_delete=models.PROTECT,
        related_name="customer_packages",
    )
    purchased_on = models.DateField(_("purchased on"), default=timezone.localdate, db_index=True)
    expires_on = models.DateField(_("expires on"), db_index=True)
    lessons_total = models.PositiveSmallIntegerField(
        _("lessons included"), default=1, validators=[MinValueValidator(1)]
    )
    lessons_used = models.PositiveSmallIntegerField(_("lessons used"), default=0)
    amount_paid = money_field(_("amount paid"), validators=[MinValueValidator(ZERO)])
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    class Meta:
        verbose_name = _("customer package")
        verbose_name_plural = _("customer packages")
        ordering = ["-purchased_on", "-id"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["status", "expires_on"]),
        ]

    def __str__(self) -> str:
        return f"{self.customer} · {self.package.name}"

    # -- derived ------------------------------------------------------------
    @property
    def lessons_remaining(self) -> int:
        return max(0, int(self.lessons_total or 0) - int(self.lessons_used or 0))

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < timezone.localdate())

    @property
    def is_usable(self) -> bool:
        """True when a lesson may still be taken off this package today."""
        return (
            self.status == self.Status.ACTIVE
            and self.lessons_remaining > 0
            and not self.is_expired
        )

    @property
    def usage_percent(self) -> int:
        total = int(self.lessons_total or 0)
        if total <= 0:
            return 0
        return int(round(int(self.lessons_used or 0) * 100 / total))

    @property
    def value_per_lesson(self) -> Decimal:
        total = int(self.lessons_total or 0)
        if total <= 0:
            return ZERO
        return to_money(to_money(self.amount_paid) / Decimal(total))

    @property
    def days_left(self) -> int | None:
        if not self.expires_on:
            return None
        return (self.expires_on - timezone.localdate()).days

    # -- behaviour ----------------------------------------------------------
    def consume(self, count: int = 1, commit: bool = True) -> int:
        """Take *count* lessons off the package.

        Raises :class:`~django.core.exceptions.ValidationError` when the package
        is cancelled, expired or does not hold enough lessons — a customer must
        never be able to take a seventh lesson out of a six-lesson card.
        """
        count = int(count or 0)
        if count < 1:
            raise ValidationError(_("At least one lesson must be taken off the package."))
        if self.status == self.Status.CANCELLED:
            raise ValidationError(_("This package has been cancelled."))
        if self.is_expired:
            if self.status != self.Status.EXPIRED and commit:
                self.status = self.Status.EXPIRED
                self.save(update_fields=["status", "updated_at"])
            raise ValidationError(
                _("This package expired on %(date)s.") % {"date": self.expires_on}
            )
        if self.lessons_remaining < count:
            raise ValidationError(
                _("Only %(left)s lesson(s) remain on this package.")
                % {"left": self.lessons_remaining}
            )

        self.lessons_used = int(self.lessons_used or 0) + count
        if self.lessons_remaining == 0:
            self.status = self.Status.EXHAUSTED
        if commit:
            self.save(update_fields=["lessons_used", "status", "updated_at"])
        return self.lessons_remaining

    def refresh_status(self, commit: bool = True) -> str:
        """Age the package: expire it, or mark it exhausted."""
        if self.status == self.Status.CANCELLED:
            return self.status
        previous = self.status
        if self.lessons_remaining <= 0:
            self.status = self.Status.EXHAUSTED
        elif self.is_expired:
            self.status = self.Status.EXPIRED
        else:
            self.status = self.Status.ACTIVE
        if commit and previous != self.status and self.pk:
            self.save(update_fields=["status", "updated_at"])
        return self.status

    # -- validation ---------------------------------------------------------
    def clean(self):
        errors: dict[str, list] = {}
        if self.purchased_on and self.expires_on and self.expires_on < self.purchased_on:
            errors.setdefault("expires_on", []).append(
                _("A package cannot expire before it is bought.")
            )
        if int(self.lessons_used or 0) > int(self.lessons_total or 0):
            errors.setdefault("lessons_used", []).append(
                _("More lessons cannot be used than the package contains.")
            )
        if errors:
            raise ValidationError(errors)
