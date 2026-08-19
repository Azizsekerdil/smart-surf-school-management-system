"""Finance business rules.

Everything that moves money lives here. Views collect input and render results;
this module decides. Three invariants hold across every function:

1. **Atomicity.** Anything that touches more than one row runs inside
   ``transaction.atomic()``. A payment that updates an invoice and a booking
   either does both or neither.
2. **No lost updates.** Balances are incremented with ``F()`` expressions so two
   tills taking money for the same booking at the same second cannot overwrite
   each other's figure.
3. **Auditability.** Every payment writes ``AuditAction.PAYMENT`` and every
   refund writes ``AuditAction.REFUND``. Refunds never mutate the original
   payment — they create the negative counterpart that reverses it.

Revenue recognition for packages
--------------------------------
A package is recognised as revenue when it is **sold** (category ``PACKAGE``).
Redeeming a lesson from a package therefore records no second payment; it
consumes a lesson and settles the booking. Counting it twice would inflate
takings, and the money only ever arrives once.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.permissions import require_capability
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import BookingStatus, PaymentMethod, PaymentStatus
from apps.core.models import SystemSetting
from apps.core.utils import percent_change, previous_period

from . import selectors
from .models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    Invoice,
    InvoiceLine,
    Payment,
    PricePackage,
    to_money,
)

logger = logging.getLogger("apps.finance")

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

#: SystemSetting key holding how many days a customer gets to pay an invoice.
PAYMENT_TERMS_SETTING = "finance.payment_terms_days"
DEFAULT_PAYMENT_TERMS_DAYS = 14
#: SystemSetting key holding the default VAT rate applied to generated invoices.
TAX_RATE_SETTING = "finance.tax_rate"

#: Above this many days a daily chart becomes unreadable, so buckets widen.
DAILY_SERIES_MAX_DAYS = 92
WEEKLY_SERIES_MAX_DAYS = 550


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def payment_terms_days() -> int:
    """Days a customer has to settle an invoice (configurable at runtime)."""
    try:
        value = int(SystemSetting.get(PAYMENT_TERMS_SETTING, DEFAULT_PAYMENT_TERMS_DAYS) or 0)
    except (TypeError, ValueError):
        value = DEFAULT_PAYMENT_TERMS_DAYS
    return max(0, min(value, 365)) or DEFAULT_PAYMENT_TERMS_DAYS


def default_tax_rate() -> Decimal:
    """Default VAT percentage for generated invoices."""
    return to_money(SystemSetting.get(TAX_RATE_SETTING, ZERO) or ZERO)


def _as_date(value):
    """Coerce a datetime to a local date; pass dates and ``None`` through."""
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def _actor(user):
    """Return *user* only when it is a real, authenticated account."""
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
@transaction.atomic
def record_payment(
    customer,
    amount,
    method: str = PaymentMethod.CASH,
    category: str = Payment.Category.OTHER,
    *,
    invoice: Invoice | None = None,
    booking=None,
    rental=None,
    paid_at=None,
    reference: str = "",
    notes: str = "",
    status: str = PaymentStatus.PAID,
    user=None,
    request=None,
) -> Payment:
    """Take money from a customer and settle whatever it was for.

    Updates the linked invoice, booking and rental balances in the same
    transaction, then writes an ``AuditAction.PAYMENT`` entry. Raises
    :class:`ValidationError` for a non-positive amount — a negative payment is a
    refund and must go through :func:`refund_payment`.
    """
    amount = to_money(amount)
    if amount <= ZERO:
        raise ValidationError({"amount": _("A payment must be greater than zero.")})
    if customer is None:
        raise ValidationError({"customer": _("A payment must name the customer who paid.")})

    # Inherit the customer's own links when the caller passed an object only.
    if invoice is not None and booking is None and invoice.booking_id:
        booking = invoice.booking
    if invoice is not None and rental is None and invoice.rental_id:
        rental = invoice.rental

    actor = _actor(user)
    payment = Payment(
        customer=customer,
        invoice=invoice,
        booking=booking,
        rental=rental,
        amount=amount,
        method=method,
        status=status,
        category=category,
        paid_at=paid_at or timezone.now(),
        reference=(reference or "").strip(),
        notes=(notes or "").strip(),
        received_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    payment.full_clean(exclude=["payment_code", "created_by", "updated_by"])
    payment.save()

    _apply_to_balances(amount, invoice=invoice, booking=booking, rental=rental)

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=payment,
        user=actor,
        description=_("Payment %(code)s of %(amount)s received from %(customer)s")
        % {"code": payment.payment_code, "amount": amount, "customer": customer},
        changes={
            "amount": [None, str(amount)],
            "method": [None, method],
            "category": [None, category],
        },
    )
    return payment


@transaction.atomic
def refund_payment(payment: Payment, amount, reason: str, user=None, *, request=None) -> Payment:
    """Reverse part or all of *payment* by writing its negative counterpart.

    The original row is never touched: an auditor reading the ledger sees the
    payment as it was taken and the refund as a separate, explained movement.
    Requires the ``finance.refund`` capability.
    """
    require_capability(user, "finance.refund")

    if payment is None or payment.is_refund:
        raise ValidationError(_("Only an incoming payment can be refunded."))

    amount = to_money(amount)
    reason = (reason or "").strip()
    if amount <= ZERO:
        raise ValidationError({"amount": _("A refund must be greater than zero.")})
    if not reason:
        raise ValidationError({"reason": _("Record why the money is being refunded.")})

    refundable = payment.refundable_amount
    if amount > refundable:
        raise ValidationError(
            {
                "amount": _("At most %(max)s may still be refunded on this payment.")
                % {"max": refundable}
            }
        )

    actor = _actor(user)
    refund = Payment(
        customer=payment.customer,
        invoice=payment.invoice,
        booking=payment.booking,
        rental=payment.rental,
        amount=-amount,
        method=payment.method,
        # The money genuinely left the school, so the row counts as settled and
        # every Sum("amount") in the codebase nets out correctly.
        status=PaymentStatus.PAID,
        category=payment.category,
        paid_at=timezone.now(),
        reference=payment.reference,
        is_refund=True,
        refunded_payment=payment,
        refund_reason=reason,
        received_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    refund.full_clean(exclude=["payment_code", "created_by", "updated_by"])
    refund.save()

    _apply_to_balances(
        -amount, invoice=payment.invoice, booking=payment.booking, rental=payment.rental
    )

    record_audit(
        request,
        action=AuditAction.REFUND,
        instance=refund,
        user=actor,
        description=_("Refund %(code)s of %(amount)s against payment %(original)s: %(reason)s")
        % {
            "code": refund.payment_code,
            "amount": amount,
            "original": payment.payment_code,
            "reason": reason,
        },
        changes={"amount": [None, str(-amount)], "refund_of": [None, payment.payment_code]},
    )
    return refund


def _apply_to_balances(delta: Decimal, *, invoice=None, booking=None, rental=None) -> None:
    """Move *delta* onto every record the payment settles.

    ``F()`` arithmetic keeps the update atomic at database level, so two
    concurrent tills cannot overwrite each other's total.
    """
    delta = to_money(delta)
    if delta == ZERO:
        return

    if invoice is not None:
        Invoice.all_objects.filter(pk=invoice.pk).update(paid_amount=F("paid_amount") + delta)
        invoice.refresh_from_db(fields=["paid_amount", "status"])
        invoice.refresh_status()
        invoice.save(update_fields=["status", "updated_at"])

    # ``Booking`` and ``Rental`` spell the "persist now" flag differently; both
    # are called through their own public API rather than reimplemented here.
    if booking is not None:
        type(booking).all_objects.filter(pk=booking.pk).update(
            paid_amount=F("paid_amount") + delta
        )
        booking.refresh_from_db(fields=["paid_amount", "payment_status"])
        booking.recalculate_totals(commit=True)

    if rental is not None:
        type(rental).all_objects.filter(pk=rental.pk).update(paid_amount=F("paid_amount") + delta)
        rental.refresh_from_db(fields=["paid_amount", "payment_status"])
        rental.recalculate_totals(save=True)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
@transaction.atomic
def create_invoice(
    customer,
    lines: list[dict],
    *,
    booking=None,
    rental=None,
    issue_date=None,
    due_date=None,
    discount_amount=ZERO,
    tax_rate=None,
    notes: str = "",
    terms: str = "",
    user=None,
    request=None,
) -> Invoice:
    """Create a draft invoice with *lines*.

    ``lines`` is a list of ``{"description", "quantity", "unit_price",
    "discount_amount"}`` dictionaries. Totals are computed here, never trusted
    from the caller.
    """
    if not lines:
        raise ValidationError(_("An invoice needs at least one line."))

    issue_date = _as_date(issue_date) or timezone.localdate()
    due_date = _as_date(due_date) or issue_date + timedelta(days=payment_terms_days())
    actor = _actor(user)

    invoice = Invoice(
        customer=customer,
        booking=booking,
        rental=rental,
        issue_date=issue_date,
        due_date=due_date,
        status=Invoice.Status.DRAFT,
        discount_amount=to_money(discount_amount),
        tax_rate=to_money(default_tax_rate() if tax_rate is None else tax_rate),
        notes=notes or "",
        terms=terms or "",
        created_by=actor,
        updated_by=actor,
    )
    invoice.full_clean(exclude=["invoice_number", "created_by", "updated_by"])
    invoice.save()

    for index, raw in enumerate(lines):
        line = InvoiceLine(
            invoice=invoice,
            description=str(raw.get("description") or _("Service"))[:250],
            quantity=Decimal(str(raw.get("quantity") or 1)),
            unit_price=to_money(raw.get("unit_price")),
            discount_amount=to_money(raw.get("discount_amount")),
            sort_order=int(raw.get("sort_order") or index),
        )
        line.compute_total()
        line.full_clean()
        line.save()

    invoice.recalculate()

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=invoice,
        user=actor,
        description=_("Invoice %(number)s created for %(customer)s")
        % {"number": invoice.invoice_number, "customer": customer},
    )
    return invoice


def create_invoice_for_booking(booking, *, user=None, request=None) -> Invoice:
    """Raise the invoice for a booking, or return the one already raised.

    Cancelled bookings are invoiced for their cancellation fee only, which is
    what :meth:`Booking.recalculate_totals` already put in ``total_amount``.
    """
    existing = (
        Invoice.objects.filter(booking=booking)
        .exclude(status=Invoice.Status.CANCELLED)
        .order_by("-id")
        .first()
    )
    if existing is not None:
        return existing

    participants = max(1, int(getattr(booking, "participants", 1) or 1))
    if booking.status == BookingStatus.CANCELLED:
        lines = [
            {
                "description": _("Cancellation fee — booking %(code)s")
                % {"code": booking.booking_code},
                "quantity": Decimal("1"),
                "unit_price": to_money(booking.cancellation_fee),
            }
        ]
    else:
        lines = [
            {
                "description": _("%(activity)s — booking %(code)s")
                % {"activity": booking.activity_label, "code": booking.booking_code},
                "quantity": Decimal(participants),
                "unit_price": to_money(booking.unit_price),
                "discount_amount": to_money(booking.discount_amount),
            }
        ]

    invoice = create_invoice(
        booking.customer,
        lines,
        booking=booking,
        user=user,
        request=request,
        notes=_("Raised automatically from booking %(code)s") % {"code": booking.booking_code},
    )
    _carry_existing_payments(invoice, booking=booking)
    return invoice


def create_invoice_for_rental(rental, *, user=None, request=None) -> Invoice:
    """Raise the invoice for a hire contract: charge, late fee and damages."""
    existing = (
        Invoice.objects.filter(rental=rental)
        .exclude(status=Invoice.Status.CANCELLED)
        .order_by("-id")
        .first()
    )
    if existing is not None:
        return existing

    lines: list[dict] = [
        {
            "description": _("Equipment hire — %(code)s") % {"code": rental.rental_code},
            "quantity": Decimal("1"),
            "unit_price": to_money(rental.subtotal),
            "discount_amount": to_money(rental.discount_amount),
        }
    ]
    if to_money(rental.late_fee) > ZERO:
        lines.append(
            {
                "description": _("Late return fee"),
                "quantity": Decimal("1"),
                "unit_price": to_money(rental.late_fee),
            }
        )
    if to_money(rental.damage_fee) > ZERO:
        lines.append(
            {
                "description": _("Damage charge"),
                "quantity": Decimal("1"),
                "unit_price": to_money(rental.damage_fee),
            }
        )

    invoice = create_invoice(
        rental.customer,
        lines,
        rental=rental,
        user=user,
        request=request,
        notes=_("Raised automatically from rental %(code)s") % {"code": rental.rental_code},
    )
    _carry_existing_payments(invoice, rental=rental)
    return invoice


def _carry_existing_payments(invoice: Invoice, *, booking=None, rental=None) -> None:
    """Attach money already taken on the source record to the new invoice.

    Without this the invoice would claim the customer owes everything, when the
    counter has already taken a deposit against the booking.
    """
    source = booking or rental
    if source is None:
        return
    already_paid = to_money(getattr(source, "paid_amount", ZERO))
    if already_paid <= ZERO:
        return

    filters = {"booking": booking} if booking is not None else {"rental": rental}
    Payment.objects.filter(invoice__isnull=True, **filters).update(invoice=invoice)

    Invoice.all_objects.filter(pk=invoice.pk).update(paid_amount=already_paid)
    invoice.refresh_from_db(fields=["paid_amount"])
    invoice.refresh_status()
    invoice.save(update_fields=["status", "updated_at"])


@transaction.atomic
def issue_invoice(invoice: Invoice, *, user=None, request=None) -> Invoice:
    """Move a draft invoice to issued so it becomes a receivable."""
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(_("Only a draft invoice can be issued."))
    invoice.recalculate(commit=False)
    if to_money(invoice.total_amount) <= ZERO:
        raise ValidationError(_("An invoice with no value cannot be issued."))

    invoice.status = Invoice.Status.ISSUED
    invoice.refresh_status()
    invoice.updated_by = _actor(user)
    invoice.save(
        update_fields=[
            "subtotal",
            "discount_amount",
            "tax_amount",
            "total_amount",
            "status",
            "updated_by",
            "updated_at",
        ]
    )
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=invoice,
        user=_actor(user),
        description=_("Invoice %(number)s issued") % {"number": invoice.invoice_number},
        changes={"status": [Invoice.Status.DRAFT, invoice.status]},
    )
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, reason: str = "", *, user=None, request=None) -> Invoice:
    """Void an invoice. Refuses once money has been taken against it."""
    if invoice.status in Invoice.CLOSED_STATUSES:
        raise ValidationError(_("This invoice is already closed."))
    if to_money(invoice.paid_amount) > ZERO:
        raise ValidationError(
            _("Refund the payments on this invoice before cancelling it.")
        )

    previous = invoice.status
    invoice.status = Invoice.Status.CANCELLED
    if reason:
        invoice.notes = f"{invoice.notes}\n{reason}".strip()
    invoice.updated_by = _actor(user)
    invoice.save(update_fields=["status", "notes", "updated_by", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=invoice,
        user=_actor(user),
        description=_("Invoice %(number)s cancelled") % {"number": invoice.invoice_number},
        changes={"status": [previous, invoice.status]},
    )
    return invoice


def overdue_invoices():
    """Issued invoices past their due date with money still outstanding."""
    return selectors.overdue_invoice_queryset()


def mark_overdue_invoices(*, request=None) -> int:
    """Flip open invoices whose due date has passed to ``OVERDUE``.

    Idempotent, so it is safe to run from a scheduled task every night.
    """
    stale = selectors.open_invoices().exclude(status=Invoice.Status.OVERDUE).filter(
        due_date__lt=timezone.localdate()
    )
    # Materialised on purpose: the queryset carries a balance annotation, and an
    # annotated subquery inside UPDATE is not portable across both backends.
    rows = list(stale.values_list("pk", "invoice_number"))
    if not rows:
        return 0
    codes = [number for _pk, number in rows[:200]]
    count = Invoice.objects.filter(pk__in=[pk for pk, _number in rows]).update(
        status=Invoice.Status.OVERDUE, updated_at=timezone.now()
    )
    if count:
        logger.info("Marked %s invoice(s) overdue", count)
        record_audit(
            request,
            action=AuditAction.SYSTEM,
            description=_("%(count)s invoice(s) marked overdue: %(codes)s")
            % {"count": count, "codes": ", ".join(codes)},
        )
    return count


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
@transaction.atomic
def calculate_commission(instructor, start, end, *, user=None, request=None) -> list[CommissionRecord]:
    """Create the commission rows an instructor earned between two dates.

    The base is the booked value of every lesson the instructor delivered in the
    window — cancellations excluded, because nobody earns commission on a lesson
    that never ran. Lessons that already carry a live commission row are skipped,
    so re-running the calculation never pays twice.
    """
    from apps.bookings.models import Booking
    from apps.core.enums import LessonStatus
    from apps.lessons.models import Lesson

    start_date = _as_date(start)
    end_date = _as_date(end)
    if start_date is None or end_date is None:
        raise ValidationError(_("A commission period needs a start and an end date."))
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    percent = to_money(getattr(instructor, "commission_percent", ZERO))
    if percent <= ZERO:
        return []

    lessons = (
        Lesson.objects.filter(
            instructor=instructor,
            date__gte=start_date,
            date__lte=end_date,
            status=LessonStatus.COMPLETED,
        )
        .exclude(
            commission_records__status__in=[
                CommissionRecord.Status.PENDING,
                CommissionRecord.Status.APPROVED,
                CommissionRecord.Status.PAID,
            ]
        )
        .order_by("date", "start_time")
    )

    actor = _actor(user)
    created: list[CommissionRecord] = []
    for lesson in lessons:
        base = selectors.money_sum(
            Booking.objects.filter(lesson=lesson).exclude(
                status__in=[BookingStatus.CANCELLED, BookingStatus.NO_SHOW]
            ),
            "total_amount",
        )
        if to_money(base) <= ZERO:
            continue
        record = CommissionRecord(
            instructor=instructor,
            lesson=lesson,
            period_start=start_date,
            period_end=end_date,
            base_amount=to_money(base),
            commission_percent=percent,
            status=CommissionRecord.Status.PENDING,
            created_by=actor,
            updated_by=actor,
        )
        record.compute_amount()
        record.full_clean(exclude=["created_by", "updated_by"])
        record.save()
        created.append(record)

    if created:
        total = sum((record.commission_amount for record in created), ZERO)
        record_audit(
            request,
            action=AuditAction.CREATE,
            instance=created[0],
            user=actor,
            description=_(
                "%(count)s commission record(s) totalling %(total)s calculated for %(name)s"
            )
            % {"count": len(created), "total": total, "name": instructor},
        )
    return created


@transaction.atomic
def approve_commission(record: CommissionRecord, *, user=None, request=None) -> CommissionRecord:
    """Sign off a commission row so it can be paid."""
    require_capability(user, "finance.approve")
    if not record.can_approve:
        raise ValidationError(_("Only a pending commission record can be approved."))

    record.status = CommissionRecord.Status.APPROVED
    record.updated_by = _actor(user)
    record.save(update_fields=["status", "updated_by", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=_actor(user),
        description=_("Commission of %(amount)s approved for %(name)s")
        % {"amount": record.commission_amount, "name": record.instructor},
        changes={"status": [CommissionRecord.Status.PENDING, record.status]},
    )
    return record


@transaction.atomic
def pay_commission(record: CommissionRecord, *, user=None, request=None) -> CommissionRecord:
    """Mark an approved commission as paid out."""
    require_capability(user, "finance.change")
    if not record.can_pay:
        raise ValidationError(_("Approve the commission before marking it paid."))

    record.status = CommissionRecord.Status.PAID
    record.paid_at = timezone.now()
    record.updated_by = _actor(user)
    record.save(update_fields=["status", "paid_at", "updated_by", "updated_at"])

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=record,
        user=_actor(user),
        description=_("Commission of %(amount)s paid to %(name)s")
        % {"amount": record.commission_amount, "name": record.instructor},
        changes={"status": [CommissionRecord.Status.APPROVED, record.status]},
    )
    return record


@transaction.atomic
def cancel_commission(
    record: CommissionRecord, reason: str = "", *, user=None, request=None
) -> CommissionRecord:
    """Withdraw a commission row that should never have been raised."""
    require_capability(user, "finance.change")
    if not record.can_cancel:
        raise ValidationError(_("A paid commission cannot be cancelled."))

    previous = record.status
    record.status = CommissionRecord.Status.CANCELLED
    if reason:
        record.notes = f"{record.notes}\n{reason}".strip()
    record.updated_by = _actor(user)
    record.save(update_fields=["status", "notes", "updated_by", "updated_at"])

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=record,
        user=_actor(user),
        description=_("Commission cancelled for %(name)s") % {"name": record.instructor},
        changes={"status": [previous, record.status]},
    )
    return record


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
@transaction.atomic
def sell_package(
    customer,
    package: PricePackage,
    payment_method: str = PaymentMethod.CASH,
    user=None,
    *,
    reference: str = "",
    request=None,
) -> tuple[CustomerPackage, Payment]:
    """Sell *package* to *customer*: issue the card and take the money."""
    if not package.is_active:
        raise ValidationError(_("This package is no longer on sale."))
    if customer is None:
        raise ValidationError({"customer": _("Choose the customer buying the package.")})

    today = timezone.localdate()
    actor = _actor(user)
    customer_package = CustomerPackage(
        customer=customer,
        package=package,
        purchased_on=today,
        expires_on=today + timedelta(days=int(package.validity_days or 1)),
        lessons_total=int(package.lesson_count or 1),
        lessons_used=0,
        amount_paid=to_money(package.price),
        status=CustomerPackage.Status.ACTIVE,
        created_by=actor,
        updated_by=actor,
    )
    customer_package.full_clean(exclude=["created_by", "updated_by"])
    customer_package.save()

    payment = record_payment(
        customer,
        package.price,
        method=payment_method,
        category=Payment.Category.PACKAGE,
        reference=reference,
        notes=_("Package %(code)s — %(count)s lesson(s), valid until %(date)s")
        % {
            "code": package.code,
            "count": customer_package.lessons_total,
            "date": customer_package.expires_on,
        },
        user=user,
        request=request,
    )

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=customer_package,
        user=actor,
        description=_("Package %(name)s sold to %(customer)s")
        % {"name": package.name, "customer": customer},
    )
    return customer_package, payment


@transaction.atomic
def use_package_lesson(customer_package: CustomerPackage, booking, *, user=None, request=None):
    """Take one lesson off a customer's package and settle the booking with it.

    No payment row is written: the money arrived when the package was sold, and
    recording it again would double-count the revenue. The booking is marked as
    settled and the movement is audited.
    """
    if customer_package is None or booking is None:
        raise ValidationError(_("Choose both the package and the booking."))
    if customer_package.customer_id != booking.customer_id:
        raise ValidationError(
            _("This package belongs to a different customer.")
        )
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.NO_SHOW):
        raise ValidationError(_("A cancelled booking cannot use a package lesson."))

    # ``consume`` raises when the package is exhausted, expired or cancelled.
    customer_package.consume(1)

    actor = _actor(user)
    booking.recalculate_totals(commit=False)
    booking.paid_amount = to_money(booking.total_amount)
    booking.recalculate_totals(commit=False)
    booking.save(
        update_fields=["total_amount", "paid_amount", "payment_status", "updated_at"]
    )

    record_audit(
        request,
        action=AuditAction.PAYMENT,
        instance=booking,
        user=actor,
        description=_(
            "Booking %(code)s settled from package %(package)s "
            "(%(left)s lesson(s) remaining)"
        )
        % {
            "code": booking.booking_code,
            "package": customer_package.package.name,
            "left": customer_package.lessons_remaining,
        },
        changes={"paid_amount": [None, str(booking.paid_amount)]},
    )
    return customer_package


def expire_stale_packages() -> int:
    """Age out packages whose validity has run out. Safe to run nightly."""
    stale = CustomerPackage.objects.filter(
        status=CustomerPackage.Status.ACTIVE, expires_on__lt=timezone.localdate()
    )
    count = stale.update(status=CustomerPackage.Status.EXPIRED, updated_at=timezone.now())
    if count:
        logger.info("Expired %s customer package(s)", count)
    return count


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _period_figures(start, end, *, empty: bool = False) -> dict:
    """The raw money numbers for one window.

    ``empty=True`` returns zeroes without querying — used when the selected
    range is "all time" and therefore has no comparable previous period.
    """
    if empty:
        return {
            "gross_revenue": ZERO,
            "refunds": ZERO,
            "revenue": ZERO,
            "expenses": ZERO,
            "gross_profit": ZERO,
            "payment_count": 0,
        }
    revenue = selectors.net_revenue(start, end)
    expenses = selectors.expense_total(_as_date(start), _as_date(end))
    return {
        "gross_revenue": selectors.gross_revenue(start, end),
        "refunds": selectors.refunds_total(start, end),
        "revenue": revenue,
        "expenses": expenses,
        "gross_profit": to_money(revenue - expenses),
        "payment_count": selectors.settled_payments(start, end).filter(is_refund=False).count(),
    }


def financial_summary(start, end) -> dict:
    """Everything the finance dashboard shows, with a like-for-like comparison.

    The previous period is the equally long window immediately before this one
    (:func:`apps.core.utils.previous_period`), so "up 12%" always means against
    a comparable stretch of trading rather than an arbitrary calendar month.
    """
    current = _period_figures(start, end)
    previous_start, previous_end = previous_period(start, end)
    previous = _period_figures(
        previous_start, previous_end, empty=previous_start is None or previous_end is None
    )

    uninvoiced = selectors.uninvoiced_balances()
    invoiced_receivables = selectors.receivables_total()
    receivables = to_money(
        invoiced_receivables + uninvoiced["bookings"] + uninvoiced["rentals"]
    )

    def compare(key: str) -> dict:
        return {
            "current": current[key],
            "previous": previous[key],
            "change": percent_change(current[key], previous[key]),
        }

    margin = ZERO
    if current["revenue"] > ZERO:
        margin = to_money(current["gross_profit"] / current["revenue"] * HUNDRED)

    return {
        "start": start,
        "end": end,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "revenue": compare("revenue"),
        "gross_revenue": compare("gross_revenue"),
        "expenses": compare("expenses"),
        "gross_profit": compare("gross_profit"),
        "refunds": compare("refunds"),
        "payment_count": compare("payment_count"),
        "margin_percent": margin,
        "revenue_by_category": selectors.revenue_by_category(start, end),
        "revenue_by_method": selectors.revenue_by_method(start, end),
        "expenses_by_category": selectors.expenses_by_category(
            _as_date(start), _as_date(end)
        ),
        "outstanding_receivables": receivables,
        "invoiced_receivables": invoiced_receivables,
        "uninvoiced_receivables": to_money(uninvoiced["bookings"] + uninvoiced["rentals"]),
        "overdue_count": selectors.overdue_invoice_queryset().count(),
        "commission_owed": selectors.commission_owed(),
        "commission_paid": selectors.commission_paid(start, end),
        "package_liability": selectors.package_liability(),
    }


def profit_and_loss(start, end) -> dict:
    """A simple P&L for the window: what came in, what went out, what is left.

    Commission already paid is listed as its own cost line because it is a real
    outflow that does not live in the expense ledger.
    """
    revenue_rows = selectors.revenue_by_category(start, end)
    expense_rows = selectors.expenses_by_category(_as_date(start), _as_date(end))

    revenue_total = selectors.net_revenue(start, end)
    expense_total = selectors.expense_total(_as_date(start), _as_date(end))
    commission = selectors.commission_paid(start, end)
    net_profit = to_money(revenue_total - expense_total - commission)

    margin = ZERO
    if revenue_total > ZERO:
        margin = to_money(net_profit / revenue_total * HUNDRED)

    return {
        "start": start,
        "end": end,
        "revenue_rows": revenue_rows,
        "revenue_total": revenue_total,
        "gross_revenue": selectors.gross_revenue(start, end),
        "refunds": selectors.refunds_total(start, end),
        "expense_rows": expense_rows,
        "expense_total": expense_total,
        "commission_paid": commission,
        "cost_total": to_money(expense_total + commission),
        "net_profit": net_profit,
        "margin_percent": margin,
    }


def cash_flow_series(start, end) -> dict:
    """Money in and out, bucketed for a chart.

    Buckets widen from days to weeks to months as the window grows, so a
    one-year view stays readable instead of drawing 365 slivers.
    """
    start_date = _as_date(start)
    end_date = _as_date(end)
    if start_date is None or end_date is None:
        # "All time": anchor the chart on the first payment ever taken.
        first = (
            selectors.settled_payments()
            .order_by("paid_at")
            .values_list("paid_at", flat=True)
            .first()
        )
        start_date = _as_date(first) or timezone.localdate()
        end_date = timezone.localdate()

    span = max(1, (end_date - start_date).days + 1)
    if span <= DAILY_SERIES_MAX_DAYS:
        bucket, granularity = TruncDate, "day"
    elif span <= WEEKLY_SERIES_MAX_DAYS:
        bucket, granularity = TruncWeek, "week"
    else:
        bucket, granularity = TruncMonth, "month"

    revenue_rows = (
        selectors.settled_payments(start_date, end_date)
        .annotate(bucket=bucket("paid_at"))
        .values("bucket")
        .annotate(total=Sum("amount"))
        .order_by("bucket")
    )
    expense_rows = (
        selectors.expenses_in(start_date, end_date)
        .annotate(bucket=bucket("spent_on"))
        .values("bucket")
        .annotate(total=Sum(F("amount") + F("tax_amount")))
        .order_by("bucket")
    )

    revenue_map = {_as_date(row["bucket"]): to_money(row["total"]) for row in revenue_rows}
    expense_map = {_as_date(row["bucket"]): to_money(row["total"]) for row in expense_rows}

    points: list[dict] = []
    for moment in _buckets(start_date, end_date, granularity):
        revenue = revenue_map.get(moment, ZERO)
        expense = expense_map.get(moment, ZERO)
        points.append(
            {
                "date": moment,
                "label": _bucket_label(moment, granularity),
                "revenue": revenue,
                "expenses": expense,
                "net": to_money(revenue - expense),
            }
        )
    return {"granularity": granularity, "points": points}


def _buckets(start_date: date, end_date: date, granularity: str):
    """Yield the start of every bucket between two dates, inclusive."""
    if granularity == "day":
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)
        return
    if granularity == "week":
        current = start_date - timedelta(days=start_date.weekday())
        while current <= end_date:
            yield current
            current += timedelta(days=7)
        return
    current = start_date.replace(day=1)
    while current <= end_date:
        yield current
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def _bucket_label(moment: date, granularity: str) -> str:
    if granularity == "month":
        return f"{moment:%m.%Y}"
    return f"{moment:%d.%m}"


def revenue_chart_payload(start, end) -> dict:
    """Chart.js-ready structures for the finance dashboard."""
    series = cash_flow_series(start, end)
    categories = selectors.revenue_by_category(start, end)
    return {
        "labels": [point["label"] for point in series["points"]],
        "revenue": [float(point["revenue"]) for point in series["points"]],
        "expenses": [float(point["expenses"]) for point in series["points"]],
        "net": [float(point["net"]) for point in series["points"]],
        "granularity": series["granularity"],
        "category_labels": [row["label"] for row in categories],
        "category_values": [float(row["amount"]) for row in categories],
    }


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
@transaction.atomic
def record_expense(
    category,
    description: str,
    amount,
    *,
    spent_on=None,
    tax_amount=ZERO,
    supplier: str = "",
    invoice_reference: str = "",
    equipment=None,
    is_recurring: bool = False,
    recurrence_months: int | None = None,
    receipt=None,
    user=None,
    request=None,
) -> Expense:
    """Record money the school has spent."""
    actor = _actor(user)
    expense = Expense(
        category=category,
        description=description,
        amount=to_money(amount),
        tax_amount=to_money(tax_amount),
        spent_on=_as_date(spent_on) or timezone.localdate(),
        supplier=supplier or "",
        invoice_reference=invoice_reference or "",
        equipment=equipment,
        is_recurring=is_recurring,
        recurrence_months=recurrence_months,
        paid_by=actor,
        created_by=actor,
        updated_by=actor,
    )
    if receipt is not None:
        expense.receipt = receipt
    expense.full_clean(exclude=["expense_code", "created_by", "updated_by", "paid_by"])
    expense.save()

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=expense,
        user=actor,
        description=_("Expense %(code)s of %(amount)s recorded (%(category)s)")
        % {
            "code": expense.expense_code,
            "amount": expense.total_amount,
            "category": category,
        },
    )
    return expense
