"""Read queries for the customer screens.

Several of the customer tabs show data owned by modules that are deployed
independently (bookings, rentals, finance). Rather than importing them — which
would create a hard dependency and an import cycle — every cross-module read
goes through :func:`optional_model` and degrades to an empty result when the
model or the expected field is not present.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import DecimalField, Max, Min, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.core.enums import ACTIVE_BOOKING_STATUSES, BookingStatus, PaymentStatus
from apps.core.models import Document, Note

from .models import Customer

#: How many rows a detail tab shows before the user has to open the module.
TAB_ROW_LIMIT = 25

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Cross-module helpers
# ---------------------------------------------------------------------------
def optional_model(app_label: str, model_name: str):
    """Return a model class, or ``None`` when that module is not installed."""
    try:
        return django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return None


def model_has_field(model, field_name: str) -> bool:
    """True when *model* really declares *field_name* (concrete or relation)."""
    if model is None:
        return False
    try:
        model._meta.get_field(field_name)
    except Exception:  # noqa: BLE001 - FieldDoesNotExist and friends
        return False
    return True


def first_existing_field(model, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if model_has_field(model, name):
            return name
    return None


def _manager(model):
    """Prefer the soft-delete-aware default manager."""
    return getattr(model, "objects", None) or model._default_manager


def _limited(queryset, order_by: str | None, limit: int = TAB_ROW_LIMIT):
    if order_by:
        queryset = queryset.order_by(order_by)
    return list(queryset[:limit])


# ---------------------------------------------------------------------------
# Customer list
# ---------------------------------------------------------------------------
def customer_list(
    *,
    search: str = "",
    is_active: str = "",
    source: str = "",
    has_bookings: str = "",
    tag: str = "",
    minors_only: bool = False,
):
    """The filtered customer queryset behind the list screen and the API."""
    queryset = Customer.objects.all().prefetch_related("tags")

    if search:
        queryset = queryset.search(search)
    if is_active == "active":
        queryset = queryset.active()
    elif is_active == "inactive":
        queryset = queryset.inactive()
    if source:
        queryset = queryset.filter(source=source)
    if has_bookings == "yes":
        queryset = queryset.with_bookings()
    elif has_bookings == "no":
        queryset = queryset.without_bookings()
    if tag:
        queryset = queryset.filter(tags__slug=tag)
    if minors_only:
        queryset = queryset.minors()
    return queryset.distinct()


# ---------------------------------------------------------------------------
# Detail tabs
# ---------------------------------------------------------------------------
def customer_bookings(customer, limit: int = TAB_ROW_LIMIT) -> list:
    """Recent bookings, newest first. Empty when the bookings module is absent."""
    Booking = optional_model("bookings", "Booking")
    if not model_has_field(Booking, "customer"):
        return []
    order_field = first_existing_field(
        Booking, ("start_at", "scheduled_for", "booking_date", "date", "created_at")
    )
    queryset = _manager(Booking).filter(customer=customer)
    return _limited(queryset, f"-{order_field}" if order_field else None, limit)


def customer_rentals(customer, limit: int = TAB_ROW_LIMIT) -> list:
    Rental = optional_model("rentals", "Rental")
    if not model_has_field(Rental, "customer"):
        return []
    order_field = first_existing_field(
        Rental, ("checked_out_at", "start_at", "rental_date", "date", "created_at")
    )
    queryset = _manager(Rental).filter(customer=customer)
    return _limited(queryset, f"-{order_field}" if order_field else None, limit)


def customer_payments(customer, limit: int = TAB_ROW_LIMIT) -> list:
    Payment = optional_model("finance", "Payment")
    if not model_has_field(Payment, "customer"):
        return []
    order_field = first_existing_field(
        Payment, ("paid_at", "received_at", "payment_date", "date", "created_at")
    )
    queryset = _manager(Payment).filter(customer=customer)
    return _limited(queryset, f"-{order_field}" if order_field else None, limit)


def customer_invoices(customer, limit: int = TAB_ROW_LIMIT) -> list:
    Invoice = optional_model("finance", "Invoice")
    if not model_has_field(Invoice, "customer"):
        return []
    order_field = first_existing_field(
        Invoice, ("issued_on", "issue_date", "date", "created_at")
    )
    queryset = _manager(Invoice).filter(customer=customer)
    return _limited(queryset, f"-{order_field}" if order_field else None, limit)


def customer_documents(customer):
    content_type = ContentType.objects.get_for_model(Customer)
    return list(
        Document.objects.filter(content_type=content_type, object_id=customer.pk).order_by(
            "-created_at"
        )
    )


def customer_notes(customer, *, include_internal: bool = True):
    content_type = ContentType.objects.get_for_model(Customer)
    queryset = Note.objects.filter(content_type=content_type, object_id=customer.pk)
    if not include_internal:
        queryset = queryset.filter(is_internal=False)
    return list(queryset.select_related("created_by"))


# ---------------------------------------------------------------------------
# Money & counters
# ---------------------------------------------------------------------------
def paid_total(customer) -> Decimal:
    """Sum of money actually received from this customer.

    Assumes ``finance.Payment`` carries ``customer``, ``amount`` and (optionally)
    a ``status`` drawn from :class:`apps.core.enums.PaymentStatus`. Refunded rows
    are excluded rather than negated, because a refund reverses the payment.
    """
    Payment = optional_model("finance", "Payment")
    if not (model_has_field(Payment, "customer") and model_has_field(Payment, "amount")):
        return ZERO
    queryset = _manager(Payment).filter(customer=customer)
    if model_has_field(Payment, "status"):
        queryset = queryset.exclude(status=PaymentStatus.REFUNDED)
    total = queryset.aggregate(
        total=Coalesce(
            Sum("amount"),
            Value(ZERO),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]
    return total or ZERO


def booking_stats(customer) -> dict:
    """Counted bookings plus first/last visit dates, from the bookings module."""
    empty = {"total": 0, "active": 0, "first_date": None, "last_date": None}
    Booking = optional_model("bookings", "Booking")
    if not model_has_field(Booking, "customer"):
        return empty

    queryset = _manager(Booking).filter(customer=customer)
    if model_has_field(Booking, "status"):
        counted = queryset.exclude(
            status__in=[BookingStatus.DRAFT, BookingStatus.CANCELLED]
        )
        active = queryset.filter(status__in=list(ACTIVE_BOOKING_STATUSES)).count()
    else:
        counted = queryset
        active = 0

    date_field = first_existing_field(
        Booking, ("start_at", "scheduled_for", "booking_date", "date", "created_at")
    )
    first_date = last_date = None
    if date_field:
        bounds = counted.aggregate(first=Min(date_field), last=Max(date_field))
        first_date = _as_date(bounds["first"])
        last_date = _as_date(bounds["last"])
    return {
        "total": counted.count(),
        "active": active,
        "first_date": first_date,
        "last_date": last_date,
    }


def _as_date(value):
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def open_balance(customer) -> Decimal:
    """Invoiced but unpaid amount, when the finance module exposes it."""
    Invoice = optional_model("finance", "Invoice")
    amount_field = first_existing_field(Invoice, ("balance_due", "total", "total_amount"))
    if not (model_has_field(Invoice, "customer") and amount_field):
        return ZERO
    queryset = _manager(Invoice).filter(customer=customer)
    if model_has_field(Invoice, "payment_status"):
        queryset = queryset.filter(
            payment_status__in=[
                PaymentStatus.UNPAID,
                PaymentStatus.PARTIAL,
                PaymentStatus.OVERDUE,
            ]
        )
    elif model_has_field(Invoice, "status"):
        queryset = queryset.filter(
            status__in=[PaymentStatus.UNPAID, PaymentStatus.PARTIAL, PaymentStatus.OVERDUE]
        )
    total = queryset.aggregate(
        total=Coalesce(
            Sum(amount_field),
            Value(ZERO),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"]
    if amount_field != "balance_due":
        total = (total or ZERO) - paid_total(customer)
    return max(total or ZERO, ZERO)


# ---------------------------------------------------------------------------
# Duplicate detection support
# ---------------------------------------------------------------------------
def customers_matching_contact(email: str = "", phone: str = "", exclude_pk=None):
    """Customers reachable on the same e-mail or phone — the counter-desk check."""
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    condition = Q(pk__in=[])
    if email:
        condition |= Q(email__iexact=email)
    if phone:
        from .models import normalise_phone

        normalised = normalise_phone(phone)
        if normalised:
            condition |= Q(phone=normalised)
    queryset = Customer.objects.filter(condition)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset
