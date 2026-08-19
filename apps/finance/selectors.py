"""Read queries for the finance module.

Everything here is a queryset builder: no writes, no side effects. Views and
services compose them so the same prefetch rules apply on every screen and an
N+1 query never reaches production.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    QuerySet,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.enums import PaymentStatus

from .models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    Invoice,
    Payment,
    PricePackage,
)

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)

#: Payment statuses that represent money that has genuinely moved. An "unpaid"
#: or "overdue" row is a promise, not a receipt, and must never inflate revenue.
SETTLED_PAYMENT_STATUSES = (
    PaymentStatus.PAID,
    PaymentStatus.PARTIAL,
    PaymentStatus.REFUNDED,
)


def money_sum(queryset, field: str = "amount") -> Decimal:
    """Sum *field* over *queryset*, returning ``0.00`` rather than ``None``."""
    return queryset.aggregate(
        total=Coalesce(Sum(field), Value(ZERO), output_field=MONEY)
    )["total"] or ZERO


def _as_datetime(value, end_of_day: bool = False):
    """Normalise a ``date`` into an aware datetime bound."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        from datetime import time

        moment = datetime.combine(value, time.max if end_of_day else time.min)
        return timezone.make_aware(moment, timezone.get_current_timezone())
    return value


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def payment_queryset() -> QuerySet[Payment]:
    return Payment.objects.select_related(
        "customer", "invoice", "booking", "rental", "received_by"
    )


def settled_payments(start=None, end=None) -> QuerySet[Payment]:
    """Payments whose money actually moved, optionally inside a window."""
    queryset = Payment.objects.filter(status__in=SETTLED_PAYMENT_STATUSES)
    start = _as_datetime(start)
    end = _as_datetime(end, end_of_day=True)
    if start is not None:
        queryset = queryset.filter(paid_at__gte=start)
    if end is not None:
        queryset = queryset.filter(paid_at__lte=end)
    return queryset


def revenue_by_category(start=None, end=None) -> list[dict]:
    """Net revenue per :class:`Payment.Category` — refunds already deducted."""
    labels = dict(Payment.Category.choices)
    rows = (
        settled_payments(start, end)
        .values("category")
        .annotate(total=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
        .order_by("-total")
    )
    return [
        {
            "category": row["category"],
            "label": str(labels.get(row["category"], row["category"])),
            "amount": row["total"] or ZERO,
        }
        for row in rows
        if (row["total"] or ZERO) != ZERO
    ]


def revenue_by_method(start=None, end=None) -> list[dict]:
    """Net takings per payment method — what should be in the till and the bank."""
    from apps.core.enums import PaymentMethod

    labels = dict(PaymentMethod.choices)
    rows = (
        settled_payments(start, end)
        .values("method")
        .annotate(total=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
        .order_by("-total")
    )
    return [
        {
            "method": row["method"],
            "label": str(labels.get(row["method"], row["method"])),
            "amount": row["total"] or ZERO,
        }
        for row in rows
        if (row["total"] or ZERO) != ZERO
    ]


def gross_revenue(start=None, end=None) -> Decimal:
    """Money in, before refunds."""
    return money_sum(settled_payments(start, end).filter(is_refund=False))


def refunds_total(start=None, end=None) -> Decimal:
    """Money refunded in the window, as a positive number."""
    return abs(money_sum(settled_payments(start, end).filter(is_refund=True)))


def net_revenue(start=None, end=None) -> Decimal:
    return money_sum(settled_payments(start, end))


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
def invoice_queryset() -> QuerySet[Invoice]:
    return Invoice.objects.select_related("customer", "booking", "rental")


def with_balance(queryset: QuerySet[Invoice]) -> QuerySet[Invoice]:
    """Annotate ``balance`` = total − paid so the DB can filter and sum on it."""
    return queryset.annotate(
        balance=ExpressionWrapper(F("total_amount") - F("paid_amount"), output_field=MONEY)
    )


def open_invoices() -> QuerySet[Invoice]:
    """Invoices that still represent money the school expects to receive."""
    return with_balance(invoice_queryset().filter(status__in=Invoice.OPEN_STATUSES)).filter(
        balance__gt=ZERO
    )


def overdue_invoice_queryset() -> QuerySet[Invoice]:
    return open_invoices().filter(due_date__lt=timezone.localdate()).order_by("due_date")


def receivables_total() -> Decimal:
    """Everything currently owed on issued invoices."""
    return money_sum(open_invoices(), "balance")


def uninvoiced_balances() -> dict:
    """Money owed on bookings and rentals that carry no invoice yet.

    A school that never issues invoices still needs to see what it is owed, so
    the receivables figure adds these operational balances to the invoiced ones.
    """
    from apps.bookings.models import Booking
    from apps.core.enums import BookingStatus
    from apps.rentals.models import Rental

    bookings = (
        Booking.objects.filter(invoices__isnull=True)
        .exclude(status__in=[BookingStatus.CANCELLED, BookingStatus.NO_SHOW])
        .annotate(
            balance=ExpressionWrapper(F("total_amount") - F("paid_amount"), output_field=MONEY)
        )
        .filter(balance__gt=ZERO)
    )
    rentals = (
        Rental.objects.filter(invoices__isnull=True)
        .exclude(status=Rental.Status.CANCELLED)
        .annotate(
            balance=ExpressionWrapper(F("total_amount") - F("paid_amount"), output_field=MONEY)
        )
        .filter(balance__gt=ZERO)
    )
    return {
        "bookings": money_sum(bookings, "balance"),
        "rentals": money_sum(rentals, "balance"),
        "booking_count": bookings.count(),
        "rental_count": rentals.count(),
    }


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
def expense_queryset() -> QuerySet[Expense]:
    return Expense.objects.select_related("category", "paid_by", "equipment")


def expenses_in(start=None, end=None) -> QuerySet[Expense]:
    queryset = expense_queryset()
    if start is not None:
        queryset = queryset.filter(spent_on__gte=_as_date(start))
    if end is not None:
        queryset = queryset.filter(spent_on__lte=_as_date(end))
    return queryset


def _as_date(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def expense_total(start=None, end=None) -> Decimal:
    """Total spend including tax."""
    return expenses_in(start, end).aggregate(
        total=Coalesce(Sum(F("amount") + F("tax_amount")), Value(ZERO), output_field=MONEY)
    )["total"] or ZERO


def expenses_by_category(start=None, end=None) -> list[dict]:
    rows = (
        expenses_in(start, end)
        .values("category__id", "category__name")
        .annotate(
            total=Coalesce(Sum(F("amount") + F("tax_amount")), Value(ZERO), output_field=MONEY)
        )
        .order_by("-total")
    )
    return [
        {
            "category_id": row["category__id"],
            "label": row["category__name"] or "",
            "amount": row["total"] or ZERO,
        }
        for row in rows
        if (row["total"] or ZERO) != ZERO
    ]


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
def commission_queryset() -> QuerySet[CommissionRecord]:
    return CommissionRecord.objects.select_related(
        "instructor", "instructor__user", "lesson", "lesson__lesson_type"
    )


def commission_owed(start=None, end=None) -> Decimal:
    """Commission approved or awaiting approval — a liability, not a cost yet."""
    queryset = CommissionRecord.objects.filter(status__in=CommissionRecord.OWED_STATUSES)
    if start is not None:
        queryset = queryset.filter(period_end__gte=_as_date(start))
    if end is not None:
        queryset = queryset.filter(period_start__lte=_as_date(end))
    return money_sum(queryset, "commission_amount")


def commission_paid(start=None, end=None) -> Decimal:
    queryset = CommissionRecord.objects.filter(status=CommissionRecord.Status.PAID)
    start_dt = _as_datetime(start)
    end_dt = _as_datetime(end, end_of_day=True)
    if start_dt is not None:
        queryset = queryset.filter(paid_at__gte=start_dt)
    if end_dt is not None:
        queryset = queryset.filter(paid_at__lte=end_dt)
    return money_sum(queryset, "commission_amount")


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
def package_queryset() -> QuerySet[PricePackage]:
    return PricePackage.objects.select_related("lesson_type")


def customer_package_queryset() -> QuerySet[CustomerPackage]:
    return CustomerPackage.objects.select_related("customer", "package", "package__lesson_type")


def usable_packages_for(customer) -> QuerySet[CustomerPackage]:
    """Packages this customer may still take a lesson from, soonest expiry first."""
    return (
        customer_package_queryset()
        .filter(
            customer=customer,
            status=CustomerPackage.Status.ACTIVE,
            expires_on__gte=timezone.localdate(),
        )
        .annotate(
            remaining=ExpressionWrapper(
                F("lessons_total") - F("lessons_used"), output_field=IntegerField()
            )
        )
        .filter(remaining__gt=0)
        .order_by("expires_on", "id")
    )


def package_liability() -> Decimal:
    """Value of lessons customers have paid for but not yet taken.

    Unearned income: the money is in the bank but the school still owes the
    surfing. It is shown on the dashboard so nobody mistakes it for profit.

    Computed in Python rather than SQL on purpose — dividing a Decimal column by
    an integer column behaves differently on SQLite and PostgreSQL, and this
    number must be exact. The set is bounded by "packages still in play", which
    is small for any real school.
    """
    from .models import to_money

    total = ZERO
    rows = CustomerPackage.objects.filter(status=CustomerPackage.Status.ACTIVE).only(
        "amount_paid", "lessons_total", "lessons_used"
    )
    for row in rows.iterator(chunk_size=500):
        total += row.value_per_lesson * row.lessons_remaining
    return to_money(total)


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------
def customer_search(term: str, limit: int = 20):
    """Look a customer up by name, code, e-mail or phone."""
    from apps.customers.models import Customer

    term = (term or "").strip()
    if not term:
        return Customer.objects.none()
    condition = (
        Q(first_name__icontains=term)
        | Q(last_name__icontains=term)
        | Q(customer_code__icontains=term)
        | Q(email__icontains=term)
        | Q(phone__icontains=term)
    )
    return Customer.objects.filter(condition).order_by("last_name", "first_name")[:limit]
