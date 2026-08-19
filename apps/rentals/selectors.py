"""Read queries for the rental screens.

Kept apart from :mod:`apps.rentals.services` so the counter's dashboards can be
tuned (indexes, ``select_related``) without touching the money rules.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.enums import PaymentStatus

from .models import Rental, RentalItem

ZERO = Decimal("0.00")

#: Tab key -> the statuses it shows on the rental list.
LIST_TABS: dict[str, tuple[str, ...]] = {
    "active": (Rental.Status.RESERVED, Rental.Status.ACTIVE, Rental.Status.OVERDUE),
    "overdue": (Rental.Status.OVERDUE,),
    "returned": (Rental.Status.RETURNED,),
    "all": tuple(Rental.Status.values),
}


def base_queryset():
    return Rental.objects.select_related("customer", "student", "booking").prefetch_related(
        "items__equipment"
    )


def statuses_for_tab(tab: str) -> tuple[str, ...]:
    return LIST_TABS.get(tab, LIST_TABS["active"])


def open_rentals():
    """Contracts with gear still committed to a customer."""
    return base_queryset().filter(status__in=Rental.OPEN_STATUSES)


def overdue_rentals():
    """Flagged overdue, plus any active hire that has quietly passed its time."""
    now = timezone.now()
    return base_queryset().filter(
        Q(status=Rental.Status.OVERDUE)
        | Q(status=Rental.Status.ACTIVE, expected_return_at__lt=now),
        returned_at__isnull=True,
    )


def due_back_today():
    now = timezone.now()
    end_of_day = timezone.localtime(now).replace(hour=23, minute=59, second=59)
    return base_queryset().filter(
        status__in=Rental.OUT_STATUSES,
        returned_at__isnull=True,
        expected_return_at__lte=end_of_day,
        expected_return_at__gte=now,
    )


def rentals_for_customer(customer):
    return base_queryset().filter(customer=customer)


def items_currently_out():
    """Every physical asset that has not come back yet."""
    return (
        RentalItem.objects.select_related("equipment", "rental", "rental__customer")
        .filter(returned_at__isnull=True, rental__status__in=Rental.OUT_STATUSES)
        .order_by("rental__expected_return_at")
    )


def unpaid_rentals():
    return base_queryset().filter(
        payment_status__in=(PaymentStatus.UNPAID, PaymentStatus.PARTIAL, PaymentStatus.OVERDUE)
    ).exclude(status=Rental.Status.CANCELLED)


def counter_stats() -> dict:
    """Headline numbers for the top of the rental list."""
    now = timezone.now()
    month_start = timezone.localtime(now).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    out = Rental.objects.filter(status__in=Rental.OUT_STATUSES, returned_at__isnull=True)
    money = DecimalField(max_digits=12, decimal_places=2)

    return {
        "out_count": out.count(),
        "overdue_count": out.filter(expected_return_at__lt=now).count(),
        "due_today_count": due_back_today().count(),
        "deposits_held": Rental.objects.filter(
            deposit_status=Rental.DepositStatus.HELD, status__in=Rental.OPEN_STATUSES
        ).aggregate(total=Coalesce(Sum("deposit_amount"), Value(ZERO), output_field=money))[
            "total"
        ],
        "month_revenue": Rental.objects.exclude(status=Rental.Status.CANCELLED)
        .filter(start_at__gte=month_start)
        .aggregate(total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=money))["total"],
        "outstanding": unpaid_rentals().aggregate(
            total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=money)
        )["total"]
        - unpaid_rentals().aggregate(
            total=Coalesce(Sum("paid_amount"), Value(ZERO), output_field=money)
        )["total"],
    }


def top_rented_equipment(days: int = 30, limit: int = 10):
    """Which assets earn their keep — drives purchasing decisions."""
    since = timezone.now() - timedelta(days=days)
    return (
        RentalItem.objects.filter(rental__start_at__gte=since)
        .exclude(rental__status=Rental.Status.CANCELLED)
        .values("equipment_id")
        .annotate(
            hires=Count("id"),
            revenue=Coalesce(
                Sum("line_total"),
                Value(ZERO),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .order_by("-hires")[:limit]
    )
