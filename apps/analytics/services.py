"""Metric computation over the rest of the system.

Two rules shape this module.

**Analytics never hard-depends on another app.** Every model is resolved at call
time through :func:`django.apps.apps.get_model`. A module that is absent, empty,
or has renamed a column produces a metric with ``no_data=True`` and zeros — not
a traceback on the dashboard. The field names other modules own are probed
rather than assumed (see :func:`_pick`), so a rename elsewhere degrades one tile
instead of breaking the screen.

**Nothing is invented.** When a figure cannot be derived, the metric says so.
There is no "estimated", no "typical" and no filler: an empty school reports
zero, and a missing module reports that it is missing.

Every metric function takes ``(start, end)`` — aware datetimes — and returns the
same shape::

    {
        "key": "revenue",           # stable identifier
        "label": <translated>,      # what to print above the number
        "current": Decimal | float, # the figure for [start, end]
        "previous": ...,            # the same figure for the preceding window
        "change_pct": float | None, # percentage change, None when undefined
        "direction": "up"|"down"|"flat",
        "higher_is_better": bool,   # a rising cancellation rate is not good news
        "series": [ {"date", "label", "value"}, ... ],
        "unit": "money"|"count"|"percent"|"hours"|"days",
        "no_data": bool,
        ...                         # metric-specific extras
    }
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import FieldError
from django.db import DatabaseError
from django.db.models import (
    Count,
    DateField,
    DateTimeField,
    DecimalField,
    F,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import (
    Coalesce,
    ExtractHour,
    ExtractIsoWeekDay,
    TruncDay,
    TruncMonth,
    TruncWeek,
)
from django.utils import dateformat, timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    BookingSource,
    BookingStatus,
    EquipmentStatus,
    LessonStatus,
    SurfLevel,
)
from apps.core.models import SystemSetting
from apps.core.utils import percent_change, previous_period

from . import statistics as stats

logger = logging.getLogger("apps.analytics")

ZERO = Decimal("0.00")

#: Booking statuses that occupy a seat for occupancy purposes. A completed
#: lesson still consumed its capacity; a cancellation released it.
OCCUPANCY_STATUSES = (*ACTIVE_BOOKING_STATUSES, BookingStatus.COMPLETED)

#: Lesson statuses that were actually delivered or are still going to be.
DELIVERED_LESSON_STATUSES = (
    LessonStatus.SCHEDULED,
    LessonStatus.CONFIRMED,
    LessonStatus.IN_PROGRESS,
    LessonStatus.COMPLETED,
)

#: How many hours a day the fleet can realistically be hired out. Equipment
#: utilisation measured against 24 hours would make a fully booked shop look
#: 40 % idle. Configurable, because a summer school and a year-round shop run
#: very different days.
DEFAULT_OPERATING_HOURS_PER_DAY = 10
OPERATING_HOURS_SETTING = "analytics.operating_hours_per_day"

#: Upper bound on rows pulled into Python for the lead-time average, so one
#: pathological query cannot hold a worker for a minute.
LEAD_TIME_SAMPLE = 5000

#: Above this many days a daily chart becomes an unreadable comb.
DAILY_BUCKET_MAX_DAYS = 70
WEEKLY_BUCKET_MAX_DAYS = 400


# ---------------------------------------------------------------------------
# Model resolution — every cross-app reference goes through here
# ---------------------------------------------------------------------------
def _model(label: str):
    """Resolve ``"app_label.ModelName"`` lazily, or ``None`` if unavailable."""
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _fields(model) -> set[str]:
    if model is None:
        return set()
    return {field.name for field in model._meta.get_fields()}


def _pick(model, *candidates: str) -> str | None:
    """First of *candidates* that actually exists on *model*.

    Lets analytics read a column another team owns without a hard contract on
    its exact name: ``_pick(Payment, "paid_at", "received_at", "created_at")``.
    """
    names = _fields(model)
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def _guard(build, fallback):
    """Run *build*; on any data-layer failure log it and return *fallback()*.

    The dashboard is a read-only screen that several roles keep open all day.
    A module mid-migration must not take it down.
    """
    try:
        return build()
    except (DatabaseError, FieldError, LookupError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("analytics metric unavailable: %s", exc, exc_info=False)
        return fallback()


def _decimal_sum(queryset, field: str) -> Decimal:
    """Portable ``SUM`` that returns ``0.00`` rather than ``None``."""
    return queryset.aggregate(
        total=Coalesce(Sum(field), Value(ZERO), output_field=DecimalField())
    )["total"]


def _grouped(queryset):
    """Strip the model's default ordering before a ``values().annotate()`` group.

    Django adds every ``Meta.ordering`` field to the ``GROUP BY``. Booking orders
    by ``-booked_at, -id``, so ``values("status").annotate(Count("id"))`` would
    silently return one row per booking instead of one row per status — and the
    same clause breaks ``values(...).distinct()`` outright on PostgreSQL. Calling
    ``order_by()`` with no arguments clears it. Every aggregation and every
    distinct in this module goes through here.
    """
    return queryset.order_by()


# ---------------------------------------------------------------------------
# Date ranges and buckets
# ---------------------------------------------------------------------------
def normalise_range(start, end) -> tuple[datetime, datetime]:
    """Guarantee a concrete, ordered ``(start, end)`` pair of aware datetimes."""
    now = timezone.now()
    end = end or now
    if start is None:
        start = end - timedelta(days=364)
    if start > end:
        start, end = end, start
    return start, end


def _as_date(value) -> date_cls:
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def choose_bucket(start, end) -> str:
    """Pick ``day`` / ``week`` / ``month`` so the chart stays legible."""
    span = (_as_date(end) - _as_date(start)).days + 1
    if span <= DAILY_BUCKET_MAX_DAYS:
        return "day"
    if span <= WEEKLY_BUCKET_MAX_DAYS:
        return "week"
    return "month"


_TRUNCATORS = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}


def _trunc(field: str, bucket: str):
    """Truncate a date **or** datetime column to a bucket start date.

    ``output_field=DateField()`` is what makes this work on both kinds of
    column and on both database backends: the database does the truncation,
    Django hands back a plain ``date``, and no raw SQL is needed.
    """
    return _TRUNCATORS[bucket](field, output_field=DateField())


def bucket_starts(start, end, bucket: str) -> list[date_cls]:
    """Every bucket start date in ``[start, end]``, so charts have no holes.

    A day with no bookings must still appear on the axis as a zero — dropping it
    would silently compress a quiet week into a busy-looking line.
    """
    first = _as_date(start)
    last = _as_date(end)
    if bucket == "day":
        step = timedelta(days=1)
        current = first
        result = []
        while current <= last:
            result.append(current)
            current += step
        return result

    if bucket == "week":
        current = first - timedelta(days=first.weekday())  # Django truncates to Monday
        result = []
        while current <= last:
            result.append(current)
            current += timedelta(days=7)
        return result

    current = first.replace(day=1)
    result = []
    while current <= last:
        result.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return result


def _bucket_label(value: date_cls, bucket: str) -> str:
    """Short, translated axis label for a bucket start."""
    if bucket == "month":
        return dateformat.format(value, "M Y")
    return dateformat.format(value, "d M")


def _series_from_rows(rows, start, end, bucket: str) -> list[dict]:
    """Turn ``[{"bucket": date, "value": x}, …]`` into a gap-free series."""
    by_date: dict[date_cls, float] = {}
    for row in rows:
        key = row.get("bucket")
        if key is None:
            continue
        key = _as_date(key)
        value = row.get("value") or 0
        by_date[key] = float(value)

    return [
        {
            "date": moment.isoformat(),
            "label": _bucket_label(moment, bucket),
            "value": by_date.get(moment, 0.0),
        }
        for moment in bucket_starts(start, end, bucket)
    ]


def _empty_series(start, end, bucket: str) -> list[dict]:
    return _series_from_rows([], start, end, bucket)


def series_values(series: list[dict]) -> list[float]:
    """Just the numbers, for handing to :mod:`apps.analytics.statistics`."""
    return [float(point.get("value") or 0.0) for point in series or []]


def _bucketed(queryset, date_field: str, aggregate, start, end, bucket: str) -> list[dict]:
    rows = (
        _grouped(queryset)
        .annotate(bucket=_trunc(date_field, bucket))
        .values("bucket")
        .annotate(value=aggregate)
        .order_by("bucket")
    )
    return _series_from_rows(rows, start, end, bucket)


def _date_filter(field: str, start, end, is_date_field: bool) -> dict:
    """Range filter kwargs, matching the column's own type."""
    if is_date_field:
        return {f"{field}__gte": _as_date(start), f"{field}__lte": _as_date(end)}
    return {f"{field}__gte": start, f"{field}__lte": end}


def _is_date_only(model, field: str) -> bool:
    try:
        return not isinstance(model._meta.get_field(field), DateTimeField)
    except Exception:  # noqa: BLE001 - unknown field: treat as datetime and let the guard catch it
        return False


# ---------------------------------------------------------------------------
# The standard metric envelope
# ---------------------------------------------------------------------------
def _direction(change_pct: float | None) -> str:
    if change_pct is None:
        return "flat"
    if abs(change_pct) < 0.5:
        return "flat"
    return "up" if change_pct > 0 else "down"


def _metric(
    key: str,
    label,
    current,
    previous,
    series: list[dict],
    *,
    unit: str = "count",
    higher_is_better: bool = True,
    no_data: bool = False,
    **extra,
) -> dict:
    # ``previous=None`` means "this metric has no meaningful predecessor" (a
    # distribution, an hour-of-day profile). It must not be read as zero, which
    # would render every non-empty distribution as "up 100 %".
    change = None if previous is None else percent_change(current, previous)
    direction = _direction(change)
    if change is None or direction == "flat":
        is_good = None
    else:
        is_good = (direction == "up") == higher_is_better
    return {
        "key": key,
        "label": label,
        "current": current,
        "previous": previous,
        "change_pct": change,
        "direction": direction,
        "higher_is_better": higher_is_better,
        "trend_is_good": is_good,
        "series": series,
        "unit": unit,
        "no_data": no_data,
        **extra,
    }


def _blank_metric(key: str, label, start, end, *, unit: str = "count", **extra) -> dict:
    bucket = choose_bucket(start, end)
    zero: object = ZERO if unit == "money" else 0
    return _metric(
        key,
        label,
        zero,
        zero,
        _empty_series(start, end, bucket),
        unit=unit,
        no_data=True,
        **extra,
    )


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------
def _revenue_sources() -> list[dict]:
    """Where money is read from, most authoritative first.

    ``finance.Payment`` is the system of record. When it exists it is used
    **alone**, because it already contains the money that lessons, rentals and
    the till took — adding those again would double-count. Only when the finance
    module is unavailable do we fall back to the amounts the operational modules
    record for themselves, and the caller is told which happened via the
    metric's ``source`` key.
    """
    payment = _model("finance.Payment")
    if payment is not None:
        amount = _pick(payment, "amount", "total_amount", "total")
        moment = _pick(payment, "paid_at", "received_at", "payment_date", "created_at")
        if amount and moment:
            return [
                {
                    "model": payment,
                    "amount": amount,
                    "date": moment,
                    "label": _("Payments received"),
                    "source": "finance",
                    # Nothing is excluded on purpose. The ledger records a refund
                    # as its own row with a negative amount, so summing every row
                    # already gives the net. Filtering out the refunded original
                    # would subtract the refund twice.
                    "exclude": {},
                }
            ]

    sources: list[dict] = []
    booking = _model("bookings.Booking")
    if booking is not None:
        sources.append(
            {
                "model": booking,
                "amount": "paid_amount",
                "date": "booked_at",
                "label": _("Booking payments"),
                "source": "bookings",
                "exclude": {"status": BookingStatus.CANCELLED},
            }
        )
    rental = _model("rentals.Rental")
    if rental is not None:
        sources.append(
            {
                "model": rental,
                "amount": "paid_amount",
                "date": "start_at",
                "label": _("Rental payments"),
                "source": "rentals",
                "exclude": {},
            }
        )
    sale = _model("pos.Sale")
    if sale is not None:
        amount = _pick(sale, "total_amount", "total", "grand_total")
        moment = _pick(sale, "sold_at", "completed_at", "created_at")
        if amount and moment:
            sources.append(
                {
                    "model": sale,
                    "amount": amount,
                    "date": moment,
                    # A draft was never rung up, and a voided or refunded sale
                    # gave the money back.
                    "label": _("Shop sales"),
                    "source": "pos",
                    "exclude": (
                        {"status__in": ["draft", "voided", "void", "refunded", "cancelled"]}
                        if "status" in _fields(sale)
                        else {}
                    ),
                }
            )
    return sources


def revenue_metrics(start, end, *, bucket: str | None = None) -> dict:
    """Money taken in the window, with a per-bucket series.

    Falls back from the finance ledger to what bookings, rentals and the till
    each recorded, and reports which via ``source``. ``bucket`` overrides the
    automatic day/week/month choice — :func:`revenue_forecast` forces ``"day"``
    because it needs a daily series regardless of how long the history is.
    """
    start, end = normalise_range(start, end)
    label = _("Revenue")

    def build() -> dict:
        sources = _revenue_sources()
        if not sources:
            return _blank_metric(
                "revenue", label, start, end, unit="money", source="none", breakdown=[]
            )

        chart_bucket = bucket if bucket in _TRUNCATORS else choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        current_total = ZERO
        previous_total = ZERO
        totals_by_bucket: dict[str, float] = {}
        breakdown: list[dict] = []
        rows_counted = 0

        for source in sources:
            model = source["model"]
            date_field = source["date"]
            date_only = _is_date_only(model, date_field)

            queryset = model.objects.all()
            if source["exclude"]:
                queryset = queryset.exclude(**source["exclude"])

            window = queryset.filter(**_date_filter(date_field, start, end, date_only))
            amount = _decimal_sum(window, source["amount"])
            current_total += amount
            rows_counted += window.count()

            if previous_start is not None:
                previous_window = queryset.filter(
                    **_date_filter(date_field, previous_start, previous_end, date_only)
                )
                previous_total += _decimal_sum(previous_window, source["amount"])

            points = _bucketed(
                window,
                date_field,
                Coalesce(Sum(source["amount"]), Value(ZERO), output_field=DecimalField()),
                start,
                end,
                chart_bucket,
            )
            for point in points:
                totals_by_bucket[point["date"]] = (
                    totals_by_bucket.get(point["date"], 0.0) + point["value"]
                )

            breakdown.append(
                {"label": source["label"], "source": source["source"], "amount": amount}
            )

        series = [
            {
                "date": moment.isoformat(),
                "label": _bucket_label(moment, chart_bucket),
                "value": totals_by_bucket.get(moment.isoformat(), 0.0),
            }
            for moment in bucket_starts(start, end, chart_bucket)
        ]

        return _metric(
            "revenue",
            label,
            current_total,
            previous_total,
            series,
            unit="money",
            no_data=rows_counted == 0,
            source=sources[0]["source"] if len(sources) == 1 else "operational",
            breakdown=breakdown,
            bucket=chart_bucket,
        )

    return _guard(
        build,
        lambda: _blank_metric(
            "revenue", label, start, end, unit="money", source="none", breakdown=[]
        ),
    )


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------
def booking_metrics(start, end) -> dict:
    """Volume, cancellation rate, no-show rate and average lead time.

    Lead time is the gap between the moment a booking was taken and the moment
    the activity starts. A school whose lead time collapses to a day is living
    on walk-ins, which is a very different business from one booked out weeks
    ahead — hence it sits on the front page.
    """
    start, end = normalise_range(start, end)
    label = _("Bookings")

    def build() -> dict:
        booking = _model("bookings.Booking")
        if booking is None:
            return _blank_metric("bookings", label, start, end)

        bucket = choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        window = booking.objects.filter(booked_at__gte=start, booked_at__lte=end)
        total = window.count()
        previous_total = 0
        if previous_start is not None:
            previous_total = booking.objects.filter(
                booked_at__gte=previous_start, booked_at__lte=previous_end
            ).count()

        by_status = {
            row["status"]: row["n"]
            for row in _grouped(window).values("status").annotate(n=Count("id"))
        }
        cancelled = by_status.get(BookingStatus.CANCELLED, 0)
        no_show = by_status.get(BookingStatus.NO_SHOW, 0)

        cancellation_rate = round(cancelled / total * 100, 2) if total else 0.0
        no_show_rate = round(no_show / total * 100, 2) if total else 0.0

        # Lead time: bounded sample, most recent first, computed in Python
        # because the activity date lives on a related model as a plain date.
        lead_days: list[float] = []
        scheduled = (
            window.filter(Q(lesson__isnull=False) | Q(surf_camp__isnull=False))
            .order_by("-booked_at")
            .values_list("booked_at", "lesson__date", "surf_camp__start_date")[
                :LEAD_TIME_SAMPLE
            ]
        )
        for booked_at, lesson_date, camp_date in scheduled:
            activity = lesson_date or camp_date
            if not (booked_at and activity):
                continue
            delta = (activity - _as_date(booked_at)).days
            if delta >= 0:
                lead_days.append(float(delta))

        series = _bucketed(window, "booked_at", Count("id"), start, end, bucket)
        status_breakdown = [
            {
                "value": value,
                "label": display,
                "count": by_status.get(value, 0),
            }
            for value, display in BookingStatus.choices
        ]

        return _metric(
            "bookings",
            label,
            total,
            previous_total,
            series,
            unit="count",
            no_data=total == 0,
            cancelled=cancelled,
            no_show=no_show,
            cancellation_rate=cancellation_rate,
            no_show_rate=no_show_rate,
            average_lead_days=stats.mean(lead_days),
            median_lead_days=stats.median(lead_days),
            lead_time_sample=len(lead_days),
            status_breakdown=status_breakdown,
            bucket=bucket,
        )

    return _guard(build, lambda: _blank_metric("bookings", label, start, end))


# ---------------------------------------------------------------------------
# Lesson occupancy
# ---------------------------------------------------------------------------
def lesson_occupancy(start, end) -> dict:
    """Seats sold as a percentage of seats offered.

    The denominator is the capacity of every lesson that ran (or is going to
    run) in the window; cancelled lessons never offered their seats and are
    excluded. The numerator counts booking *participants*, because a family of
    four on one booking occupies four places in the water.
    """
    start, end = normalise_range(start, end)
    label = _("Lesson occupancy")

    def build() -> dict:
        lesson = _model("lessons.Lesson")
        booking = _model("bookings.Booking")
        if lesson is None or booking is None:
            return _blank_metric("occupancy", label, start, end, unit="percent", by_type=[])

        bucket = choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        def window_rate(from_moment, to_moment) -> tuple[float, int, int]:
            lessons = lesson.objects.filter(
                date__gte=_as_date(from_moment),
                date__lte=_as_date(to_moment),
                status__in=DELIVERED_LESSON_STATUSES,
            )
            capacity = lessons.aggregate(total=Coalesce(Sum("capacity"), Value(0)))["total"] or 0
            seats = (
                booking.objects.filter(
                    lesson__date__gte=_as_date(from_moment),
                    lesson__date__lte=_as_date(to_moment),
                    lesson__status__in=DELIVERED_LESSON_STATUSES,
                    status__in=OCCUPANCY_STATUSES,
                ).aggregate(total=Coalesce(Sum("participants"), Value(0)))["total"]
                or 0
            )
            rate = round(seats / capacity * 100, 2) if capacity else 0.0
            return rate, seats, capacity

        current_rate, seats, capacity = window_rate(start, end)
        previous_rate = 0.0
        if previous_start is not None:
            previous_rate = window_rate(previous_start, previous_end)[0]

        lessons_in_window = lesson.objects.filter(
            date__gte=_as_date(start),
            date__lte=_as_date(end),
            status__in=DELIVERED_LESSON_STATUSES,
        )
        capacity_points = _bucketed(
            lessons_in_window,
            "date",
            Coalesce(Sum("capacity"), Value(0)),
            start,
            end,
            bucket,
        )
        seat_points = _bucketed(
            booking.objects.filter(
                lesson__date__gte=_as_date(start),
                lesson__date__lte=_as_date(end),
                lesson__status__in=DELIVERED_LESSON_STATUSES,
                status__in=OCCUPANCY_STATUSES,
            ),
            "lesson__date",
            Coalesce(Sum("participants"), Value(0)),
            start,
            end,
            bucket,
        )
        seats_by_date = {point["date"]: point["value"] for point in seat_points}
        series = [
            {
                "date": point["date"],
                "label": point["label"],
                "value": (
                    round(seats_by_date.get(point["date"], 0.0) / point["value"] * 100, 2)
                    if point["value"]
                    else 0.0
                ),
            }
            for point in capacity_points
        ]

        # Per lesson type, for the horizontal bar chart.
        type_capacity = {
            row["lesson_type__name"] or str(_("Unassigned")): row["total"]
            for row in _grouped(lessons_in_window)
            .values("lesson_type__name")
            .annotate(total=Coalesce(Sum("capacity"), Value(0)))
        }
        type_seats = {
            row["lesson__lesson_type__name"] or str(_("Unassigned")): row["total"]
            for row in _grouped(
                booking.objects.filter(
                    lesson__date__gte=_as_date(start),
                    lesson__date__lte=_as_date(end),
                    lesson__status__in=DELIVERED_LESSON_STATUSES,
                    status__in=OCCUPANCY_STATUSES,
                )
            )
            .values("lesson__lesson_type__name")
            .annotate(total=Coalesce(Sum("participants"), Value(0)))
        }
        by_type = sorted(
            (
                {
                    "label": name,
                    "capacity": total,
                    "seats": type_seats.get(name, 0),
                    "rate": round(type_seats.get(name, 0) / total * 100, 2) if total else 0.0,
                }
                for name, total in type_capacity.items()
            ),
            key=lambda row: row["rate"],
            reverse=True,
        )

        return _metric(
            "occupancy",
            label,
            current_rate,
            previous_rate,
            series,
            unit="percent",
            no_data=capacity == 0,
            seats=seats,
            capacity=capacity,
            empty_seats=max(0, capacity - seats),
            lesson_count=lessons_in_window.count(),
            by_type=by_type,
            bucket=bucket,
        )

    return _guard(
        build,
        lambda: _blank_metric("occupancy", label, start, end, unit="percent", by_type=[]),
    )


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def customer_metrics(start, end) -> dict:
    """Who came, how many were new, and how many came back.

    ``returning`` counts customers active in this window whose first booking
    predates it — the number that tells you whether the school is keeping
    people, as opposed to endlessly replacing them.
    """
    start, end = normalise_range(start, end)
    label = _("Active customers")

    def build() -> dict:
        customer = _model("customers.Customer")
        booking = _model("bookings.Booking")
        if customer is None:
            return _blank_metric("customers", label, start, end, new_vs_returning=[])

        bucket = choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        new_count = customer.objects.filter(created_at__gte=start, created_at__lte=end).count()
        previous_new = 0
        if previous_start is not None:
            previous_new = customer.objects.filter(
                created_at__gte=previous_start, created_at__lte=previous_end
            ).count()

        active_ids: set[int] = set()
        returning = 0
        repeat_customers = 0
        total_active = 0
        previous_active = 0

        if booking is not None:
            active_ids = set(
                _grouped(booking.objects.filter(booked_at__gte=start, booked_at__lte=end))
                .values_list("customer_id", flat=True)
                .distinct()
            )
            total_active = len(active_ids)
            if previous_start is not None:
                previous_active = (
                    _grouped(
                        booking.objects.filter(
                            booked_at__gte=previous_start, booked_at__lte=previous_end
                        )
                    )
                    .values("customer_id")
                    .distinct()
                    .count()
                )
            if active_ids:
                returning = (
                    _grouped(
                        booking.objects.filter(
                            customer_id__in=active_ids, booked_at__lt=start
                        )
                    )
                    .values("customer_id")
                    .distinct()
                    .count()
                )
                repeat_customers = (
                    _grouped(booking.objects.filter(customer_id__in=active_ids))
                    .values("customer_id")
                    .annotate(n=Count("id"))
                    .filter(n__gte=2)
                    .count()
                )

        first_timers = max(0, total_active - returning)
        repeat_rate = round(repeat_customers / total_active * 100, 2) if total_active else 0.0

        series = _bucketed(
            customer.objects.filter(created_at__gte=start, created_at__lte=end),
            "created_at",
            Count("id"),
            start,
            end,
            bucket,
        )

        return _metric(
            "customers",
            label,
            total_active,
            previous_active,
            series,
            unit="count",
            no_data=total_active == 0 and new_count == 0,
            new=new_count,
            previous_new=previous_new,
            new_change_pct=percent_change(new_count, previous_new),
            returning=returning,
            first_time=first_timers,
            repeat_customers=repeat_customers,
            repeat_rate=repeat_rate,
            total_on_file=customer.objects.count(),
            new_vs_returning=[
                {"label": _("First booking"), "count": first_timers},
                {"label": _("Returning"), "count": returning},
            ],
            bucket=bucket,
        )

    return _guard(
        build, lambda: _blank_metric("customers", label, start, end, new_vs_returning=[])
    )


# ---------------------------------------------------------------------------
# Instructors
# ---------------------------------------------------------------------------
def instructor_metrics(start, end) -> dict:
    """Lessons delivered, seats taught and student ratings, per instructor."""
    start, end = normalise_range(start, end)
    label = _("Lessons delivered")

    def build() -> dict:
        lesson = _model("lessons.Lesson")
        if lesson is None:
            return _blank_metric("instructors", label, start, end, by_instructor=[])

        bucket = choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        window = lesson.objects.filter(
            date__gte=_as_date(start),
            date__lte=_as_date(end),
            status__in=DELIVERED_LESSON_STATUSES,
        )
        delivered = window.count()
        previous_delivered = 0
        if previous_start is not None:
            previous_delivered = lesson.objects.filter(
                date__gte=_as_date(previous_start),
                date__lte=_as_date(previous_end),
                status__in=DELIVERED_LESSON_STATUSES,
            ).count()

        rows = (
            _grouped(window.exclude(instructor__isnull=True))
            .values(
                "instructor_id",
                "instructor__user__first_name",
                "instructor__user__last_name",
                "instructor__instructor_code",
            )
            .annotate(lessons=Count("id"), seats=Coalesce(Sum("capacity"), Value(0)))
            .order_by("-lessons")
        )
        by_instructor = []
        for row in rows:
            name = " ".join(
                part
                for part in (
                    row.get("instructor__user__first_name"),
                    row.get("instructor__user__last_name"),
                )
                if part
            ).strip()
            by_instructor.append(
                {
                    "id": row["instructor_id"],
                    "label": name or row.get("instructor__instructor_code") or str(_("Unnamed")),
                    "lessons": row["lessons"],
                    "capacity": row["seats"],
                }
            )

        unassigned = window.filter(instructor__isnull=True).count()
        active_instructors = len(by_instructor)
        series = _bucketed(window, "date", Count("id"), start, end, bucket)

        # Average student rating over the same window, when attendance records
        # carry one. Never invented: absent ratings give None, not 0.
        attendance = _model("lessons.LessonAttendance")
        average_rating = None
        rating_count = 0
        if attendance is not None and "rating" in _fields(attendance):
            rated = attendance.objects.filter(
                lesson__date__gte=_as_date(start),
                lesson__date__lte=_as_date(end),
                rating__isnull=False,
            ).values_list("rating", flat=True)
            ratings = list(rated)
            rating_count = len(ratings)
            average_rating = stats.mean(ratings)

        return _metric(
            "instructors",
            label,
            delivered,
            previous_delivered,
            series,
            unit="count",
            no_data=delivered == 0,
            by_instructor=by_instructor[:12],
            active_instructors=active_instructors,
            unassigned_lessons=unassigned,
            lessons_per_instructor=(
                round(delivered / active_instructors, 2) if active_instructors else 0.0
            ),
            average_rating=average_rating,
            rating_count=rating_count,
            bucket=bucket,
        )

    return _guard(
        build, lambda: _blank_metric("instructors", label, start, end, by_instructor=[])
    )


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------
def operating_hours_per_day() -> float:
    """Hours a day the fleet is available to hire, from settings."""
    value = SystemSetting.get(OPERATING_HOURS_SETTING, DEFAULT_OPERATING_HOURS_PER_DAY)
    try:
        hours = float(value)
    except (TypeError, ValueError):
        hours = float(DEFAULT_OPERATING_HOURS_PER_DAY)
    return min(24.0, max(1.0, hours))


def _hire_intervals(rental_item_model, start, end) -> list[tuple[int, datetime, datetime, int]]:
    """Every ``(equipment_id, from, to, quantity)`` hire that overlaps the window.

    One query. The interval is clipped later, not here, so the same rows can be
    reused for the whole-window figure and for each bucket of the chart.

    An open rental (nothing returned yet) is bounded by its due-back time or by
    *now*, whichever is earlier — equipment still in a customer's van has not
    yet earned tomorrow's hours, and an overdue board should not accumulate
    utilisation forever.
    """
    rows = (
        rental_item_model.objects.filter(rental__start_at__lte=end)
        .filter(
            Q(rental__returned_at__gte=start)
            | Q(rental__returned_at__isnull=True, rental__expected_return_at__gte=start)
        )
        .values(
            "equipment_id",
            "quantity",
            "rental__start_at",
            "rental__returned_at",
            "rental__expected_return_at",
        )
    )

    now = timezone.now()
    intervals: list[tuple[int, datetime, datetime, int]] = []
    for row in rows:
        equipment_id = row.get("equipment_id")
        began = row.get("rental__start_at")
        if equipment_id is None or began is None:
            continue
        finished = row.get("rental__returned_at")
        if finished is None:
            due = row.get("rental__expected_return_at")
            finished = min(due, now) if due else now
        if finished <= began:
            continue
        intervals.append((equipment_id, began, finished, max(1, int(row.get("quantity") or 1))))
    return intervals


def _hours_by_item(intervals, start, end) -> dict[int, float]:
    """Clip each hire to ``[start, end]`` and total the hours per item.

    A two-week hire seen through a seven-day window contributes seven days, not
    fourteen — otherwise a long rental would push utilisation above 100 %.
    """
    hours: dict[int, float] = {}
    for equipment_id, began, finished, quantity in intervals:
        window_start = max(began, start)
        window_end = min(finished, end)
        if window_end <= window_start:
            continue
        seconds = (window_end - window_start).total_seconds()
        hours[equipment_id] = hours.get(equipment_id, 0.0) + (seconds / 3600.0) * quantity
    return hours


def equipment_utilisation(start, end) -> dict:
    """Share of the rentable fleet's available hours that were actually hired.

    Available hours = fleet size × operating hours per day × days in the window.
    The operating-day length is a configurable assumption, surfaced in the UI so
    nobody mistakes it for a measurement.
    """
    start, end = normalise_range(start, end)
    label = _("Equipment utilisation")

    def build() -> dict:
        equipment = _model("equipment.Equipment")
        rental_item = _model("rentals.RentalItem")
        if equipment is None or rental_item is None:
            return _blank_metric(
                "equipment", label, start, end, unit="percent", by_item=[], fleet_size=0
            )

        fleet = equipment.objects.filter(is_rentable=True).exclude(
            status__in=[EquipmentStatus.RETIRED, EquipmentStatus.LOST]
        )
        fleet_size = fleet.count()
        days = max(1, (_as_date(end) - _as_date(start)).days + 1)
        hours_per_day = operating_hours_per_day()
        available_hours = fleet_size * hours_per_day * days

        # Two queries in total: one set of hire intervals for this window, one
        # for the comparison window. Every bucket of the chart is then clipped
        # out of the same rows in Python.
        intervals = _hire_intervals(rental_item, start, end)
        current_hours_by_item = _hours_by_item(intervals, start, end)
        used_hours = sum(current_hours_by_item.values())
        rate = round(used_hours / available_hours * 100, 2) if available_hours else 0.0

        previous_start, previous_end = previous_period(start, end)
        previous_rate = 0.0
        if previous_start is not None and available_hours:
            previous_days = max(1, (_as_date(previous_end) - _as_date(previous_start)).days + 1)
            previous_available = fleet_size * hours_per_day * previous_days
            previous_used = sum(
                _hours_by_item(
                    _hire_intervals(rental_item, previous_start, previous_end),
                    previous_start,
                    previous_end,
                ).values()
            )
            previous_rate = (
                round(previous_used / previous_available * 100, 2) if previous_available else 0.0
            )

        identity = {
            row["pk"]: (row["name"], row["asset_code"])
            for row in equipment.objects.filter(
                pk__in=list(current_hours_by_item.keys())
            ).values("pk", "name", "asset_code")
        }
        by_item = sorted(
            (
                {
                    "id": key,
                    "label": identity.get(key, ("", ""))[0]
                    or identity.get(key, ("", ""))[1]
                    or str(_("Unknown item")),
                    "code": identity.get(key, ("", ""))[1],
                    "hours": round(value, 1),
                    "rate": (
                        round(value / (hours_per_day * days) * 100, 2) if days else 0.0
                    ),
                }
                for key, value in current_hours_by_item.items()
            ),
            key=lambda row: row["hours"],
            reverse=True,
        )

        # Per-bucket utilisation, so the chart shows when the shelves emptied.
        bucket = choose_bucket(start, end)
        series = []
        for moment in bucket_starts(start, end, bucket):
            bucket_start = max(start, _aware_start(moment))
            bucket_end = min(end, _bucket_end(moment, bucket))
            if bucket_end <= bucket_start:
                series.append(
                    {
                        "date": moment.isoformat(),
                        "label": _bucket_label(moment, bucket),
                        "value": 0.0,
                    }
                )
                continue
            bucket_days = max(1, (_as_date(bucket_end) - _as_date(bucket_start)).days + 1)
            bucket_available = fleet_size * hours_per_day * bucket_days
            bucket_used = sum(_hours_by_item(intervals, bucket_start, bucket_end).values())
            series.append(
                {
                    "date": moment.isoformat(),
                    "label": _bucket_label(moment, bucket),
                    "value": (
                        round(bucket_used / bucket_available * 100, 2)
                        if bucket_available
                        else 0.0
                    ),
                }
            )

        return _metric(
            "equipment",
            label,
            rate,
            previous_rate,
            series,
            unit="percent",
            no_data=fleet_size == 0,
            fleet_size=fleet_size,
            used_hours=round(used_hours, 1),
            available_hours=round(available_hours, 1),
            operating_hours_per_day=hours_per_day,
            items_used=len(current_hours_by_item),
            items_idle=max(0, fleet_size - len(current_hours_by_item)),
            by_item=by_item[:12],
            bucket=bucket,
        )

    return _guard(
        build,
        lambda: _blank_metric(
            "equipment", label, start, end, unit="percent", by_item=[], fleet_size=0
        ),
    )


def _aware_start(value: date_cls) -> datetime:
    return timezone.make_aware(
        datetime.combine(value, time.min), timezone.get_current_timezone()
    )


def _bucket_end(value: date_cls, bucket: str) -> datetime:
    if bucket == "day":
        last = value
    elif bucket == "week":
        last = value + timedelta(days=6)
    elif value.month == 12:
        last = value.replace(year=value.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last = value.replace(month=value.month + 1, day=1) - timedelta(days=1)
    return timezone.make_aware(
        datetime.combine(last, time.max), timezone.get_current_timezone()
    )


# ---------------------------------------------------------------------------
# Rentals
# ---------------------------------------------------------------------------
def rental_metrics(start, end) -> dict:
    """Hire volume, income, average duration, late returns and damage."""
    start, end = normalise_range(start, end)
    label = _("Rentals")

    def build() -> dict:
        rental = _model("rentals.Rental")
        if rental is None:
            return _blank_metric("rentals", label, start, end)

        bucket = choose_bucket(start, end)
        previous_start, previous_end = previous_period(start, end)

        window = rental.objects.filter(start_at__gte=start, start_at__lte=end)
        total = window.count()
        previous_total = 0
        if previous_start is not None:
            previous_total = rental.objects.filter(
                start_at__gte=previous_start, start_at__lte=previous_end
            ).count()

        income = _decimal_sum(window, "total_amount")
        late = window.filter(returned_at__gt=F("expected_return_at")).count()
        outstanding = window.filter(returned_at__isnull=True).count()
        overdue = window.filter(
            returned_at__isnull=True, expected_return_at__lt=timezone.now()
        ).count()

        durations = [
            (returned - began).total_seconds() / 3600.0
            for began, returned in window.filter(returned_at__isnull=False).values_list(
                "start_at", "returned_at"
            )
            if returned and began and returned > began
        ]

        rental_item = _model("rentals.RentalItem")
        damaged = 0
        items_out = 0
        if rental_item is not None:
            items = rental_item.objects.filter(
                rental__start_at__gte=start, rental__start_at__lte=end
            )
            items_out = items.count()
            if "damage_reported" in _fields(rental_item):
                damaged = items.filter(damage_reported=True).count()

        series = _bucketed(window, "start_at", Count("id"), start, end, bucket)

        return _metric(
            "rentals",
            label,
            total,
            previous_total,
            series,
            unit="count",
            no_data=total == 0,
            income=income,
            average_hours=stats.mean(durations),
            median_hours=stats.median(durations),
            late_returns=late,
            late_rate=round(late / total * 100, 2) if total else 0.0,
            outstanding=outstanding,
            overdue=overdue,
            items_out=items_out,
            damaged_items=damaged,
            damage_rate=round(damaged / items_out * 100, 2) if items_out else 0.0,
            bucket=bucket,
        )

    return _guard(build, lambda: _blank_metric("rentals", label, start, end))


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------
def student_level_distribution(start, end) -> dict:
    """How many students of each surf level the school served in the window.

    Counts students with a booking in the period. When there were none it falls
    back to the whole active roster and says so via ``scope``, so a quiet week
    shows the school's shape rather than an empty chart pretending to be data.
    """
    start, end = normalise_range(start, end)
    label = _("Student levels")

    def build() -> dict:
        student = _model("students.Student")
        booking = _model("bookings.Booking")
        if student is None:
            return _blank_metric("levels", label, start, end, distribution=[], scope="none")

        scope = "period"
        queryset = student.objects.none()
        if booking is not None:
            ids = (
                _grouped(
                    booking.objects.filter(
                        booked_at__gte=start, booked_at__lte=end, student__isnull=False
                    )
                )
                .values_list("student_id", flat=True)
                .distinct()
            )
            queryset = student.objects.filter(pk__in=list(ids))

        if not queryset.exists():
            scope = "all_active"
            queryset = student.objects.filter(is_active=True)

        counts = {
            row["surf_level"]: row["n"]
            for row in _grouped(queryset).values("surf_level").annotate(n=Count("id"))
        }
        distribution = [
            {"value": value, "label": display, "count": counts.get(value, 0)}
            for value, display in SurfLevel.choices
        ]
        total = sum(row["count"] for row in distribution)
        for row in distribution:
            row["share"] = round(row["count"] / total * 100, 1) if total else 0.0

        previous_total = 0
        previous_start, previous_end = previous_period(start, end)
        if booking is not None and previous_start is not None:
            previous_total = (
                _grouped(
                    booking.objects.filter(
                        booked_at__gte=previous_start,
                        booked_at__lte=previous_end,
                        student__isnull=False,
                    )
                )
                .values("student_id")
                .distinct()
                .count()
            )

        return _metric(
            "levels",
            label,
            total,
            previous_total,
            [],
            unit="count",
            no_data=total == 0,
            distribution=distribution,
            scope=scope,
        )

    return _guard(
        build,
        lambda: _blank_metric("levels", label, start, end, distribution=[], scope="none"),
    )


def channel_mix(start, end) -> dict:
    """Where bookings came from — walk-in, phone, website, partner, social."""
    start, end = normalise_range(start, end)
    label = _("Booking channels")

    def build() -> dict:
        booking = _model("bookings.Booking")
        if booking is None:
            return _blank_metric("channels", label, start, end, channels=[])

        window = booking.objects.filter(booked_at__gte=start, booked_at__lte=end)
        counts = {
            row["source"]: row["n"]
            for row in _grouped(window).values("source").annotate(n=Count("id"))
        }
        revenue = {
            row["source"]: row["total"]
            for row in _grouped(window)
            .values("source")
            .annotate(
                total=Coalesce(Sum("paid_amount"), Value(ZERO), output_field=DecimalField())
            )
        }
        total = sum(counts.values())

        previous_start, previous_end = previous_period(start, end)
        previous_total = 0
        if previous_start is not None:
            previous_total = booking.objects.filter(
                booked_at__gte=previous_start, booked_at__lte=previous_end
            ).count()

        channels = [
            {
                "value": value,
                "label": display,
                "count": counts.get(value, 0),
                "share": round(counts.get(value, 0) / total * 100, 1) if total else 0.0,
                "revenue": revenue.get(value, ZERO),
            }
            for value, display in BookingSource.choices
        ]
        channels.sort(key=lambda row: row["count"], reverse=True)

        return _metric(
            "channels",
            label,
            total,
            previous_total,
            [],
            unit="count",
            no_data=total == 0,
            channels=channels,
            leading_channel=channels[0]["label"] if channels and channels[0]["count"] else None,
        )

    return _guard(build, lambda: _blank_metric("channels", label, start, end, channels=[]))


def busiest_hours(start, end) -> dict:
    """Seats sold by lesson start hour — the staffing chart.

    Reads the hour straight from the lesson's start time, so a school that only
    ever runs 09:00 and 14:00 sessions gets two spikes rather than a smear.
    """
    start, end = normalise_range(start, end)
    label = _("Busiest hours")

    def build() -> dict:
        lesson = _model("lessons.Lesson")
        booking = _model("bookings.Booking")
        if lesson is None:
            return _blank_metric("hours", label, start, end, hours=[])

        lessons = lesson.objects.filter(
            date__gte=_as_date(start),
            date__lte=_as_date(end),
            status__in=DELIVERED_LESSON_STATUSES,
        )
        lesson_counts = {
            row["hour"]: row["n"]
            for row in _grouped(lessons)
            .annotate(hour=ExtractHour("start_time"))
            .values("hour")
            .annotate(n=Count("id"))
            if row["hour"] is not None
        }

        seat_counts: dict[int, int] = {}
        if booking is not None:
            seat_counts = {
                row["hour"]: row["seats"]
                for row in _grouped(
                    booking.objects.filter(
                        lesson__date__gte=_as_date(start),
                        lesson__date__lte=_as_date(end),
                        lesson__status__in=DELIVERED_LESSON_STATUSES,
                        status__in=OCCUPANCY_STATUSES,
                    )
                )
                .annotate(hour=ExtractHour("lesson__start_time"))
                .values("hour")
                .annotate(seats=Coalesce(Sum("participants"), Value(0)))
                if row["hour"] is not None
            }

        hours = [
            {
                "hour": hour,
                "label": f"{hour:02d}:00",
                "lessons": lesson_counts.get(hour, 0),
                "seats": seat_counts.get(hour, 0),
            }
            for hour in range(24)
        ]
        total_seats = sum(row["seats"] for row in hours)
        busiest = max(hours, key=lambda row: (row["seats"], row["lessons"]))

        return _metric(
            "hours",
            label,
            total_seats,
            None,  # an hour-of-day profile has no "previous period" twin
            [],
            unit="count",
            no_data=not lesson_counts,
            hours=hours,
            peak_hour=busiest["label"] if busiest["lessons"] or busiest["seats"] else None,
            peak_seats=busiest["seats"],
        )

    return _guard(build, lambda: _blank_metric("hours", label, start, end, hours=[]))


def busiest_weekdays(start, end) -> dict:
    """Seats and lessons by day of the week, Monday first.

    Uses ISO weekday numbering (1 = Monday … 7 = Sunday), which both SQLite and
    PostgreSQL produce identically through Django's ``ExtractIsoWeekDay``.
    """
    start, end = normalise_range(start, end)
    label = _("Busiest weekdays")
    day_names = [
        _("Monday"),
        _("Tuesday"),
        _("Wednesday"),
        _("Thursday"),
        _("Friday"),
        _("Saturday"),
        _("Sunday"),
    ]

    def build() -> dict:
        lesson = _model("lessons.Lesson")
        booking = _model("bookings.Booking")
        if lesson is None:
            return _blank_metric("weekdays", label, start, end, weekdays=[])

        lessons = lesson.objects.filter(
            date__gte=_as_date(start),
            date__lte=_as_date(end),
            status__in=DELIVERED_LESSON_STATUSES,
        )
        lesson_counts = {
            row["weekday"]: row["n"]
            for row in _grouped(lessons)
            .annotate(weekday=ExtractIsoWeekDay("date"))
            .values("weekday")
            .annotate(n=Count("id"))
            if row["weekday"] is not None
        }

        seat_counts: dict[int, int] = {}
        if booking is not None:
            seat_counts = {
                row["weekday"]: row["seats"]
                for row in _grouped(
                    booking.objects.filter(
                        lesson__date__gte=_as_date(start),
                        lesson__date__lte=_as_date(end),
                        lesson__status__in=DELIVERED_LESSON_STATUSES,
                        status__in=OCCUPANCY_STATUSES,
                    )
                )
                .annotate(weekday=ExtractIsoWeekDay("lesson__date"))
                .values("weekday")
                .annotate(seats=Coalesce(Sum("participants"), Value(0)))
                if row["weekday"] is not None
            }

        weekdays = [
            {
                "weekday": index,
                "label": day_names[index - 1],
                "lessons": lesson_counts.get(index, 0),
                "seats": seat_counts.get(index, 0),
            }
            for index in range(1, 8)
        ]
        busiest = max(weekdays, key=lambda row: (row["seats"], row["lessons"]))

        return _metric(
            "weekdays",
            label,
            sum(row["seats"] for row in weekdays),
            None,  # a weekday profile has no "previous period" twin
            [],
            unit="count",
            no_data=not lesson_counts,
            weekdays=weekdays,
            peak_weekday=busiest["label"] if busiest["lessons"] or busiest["seats"] else None,
            seasonal_index=stats.seasonality(
                [row["seats"] for row in weekdays], 7
            ),
        )

    return _guard(build, lambda: _blank_metric("weekdays", label, start, end, weekdays=[]))


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------
def revenue_forecast(days: int = 30) -> dict:
    """Project daily revenue forward, with an explicit reliability verdict.

    The history window is four times the horizon (at least 90 days) so the
    forecast has something to learn from. :func:`apps.analytics.statistics.forecast`
    decides the confidence, and it will refuse to call a projection reliable
    when the history is too short or the trend too weak. The dashboard shows
    that warning prominently — an unreliable forecast is worse than none.
    """
    try:
        horizon = int(days)
    except (TypeError, ValueError):
        horizon = 30
    horizon = max(1, min(stats.MAX_FORECAST_PERIODS, horizon))

    def build() -> dict:
        today = timezone.localdate()
        lookback = max(90, horizon * 4)
        history_start = timezone.make_aware(
            datetime.combine(today - timedelta(days=lookback - 1), time.min),
            timezone.get_current_timezone(),
        )
        history_end = timezone.make_aware(
            datetime.combine(today, time.max), timezone.get_current_timezone()
        )

        # Force daily buckets: the horizon is measured in days, so the history
        # must be too, however long the look-back window is.
        history = revenue_metrics(history_start, history_end, bucket="day")
        daily = series_values(history["series"])

        # A gap-free series pads quiet days with zeros, so an empty school hands
        # the engine ninety perfectly flat points — which fit a straight line
        # beautifully. Projecting "zero, with medium confidence" from no trading
        # at all would be false authority, so this case is caught here rather
        # than laundered through the regression.
        if history.get("no_data") or not any(daily):
            message = _(
                "No revenue was recorded in the last %(days)s days, so there is "
                "nothing to project from."
            ) % {"days": len(daily)}
            return {
                "key": "revenue_forecast",
                "label": _("Revenue forecast"),
                "unit": "money",
                "values": [],
                "series": [],
                "history": history["series"],
                "history_days": len(daily),
                "horizon_days": horizon,
                "method": "none",
                "confidence": "none",
                "low_confidence": True,
                "warning": message,
                "warning_code": "no_history",
                "warnings": [{"code": "no_history", "message": message}],
                "n": 0,
                "periods": horizon,
                "required_points": horizon * stats.MIN_HISTORY_MULTIPLE,
                "slope": None,
                "intercept": None,
                "r_squared": None,
                "projected_total": ZERO,
                "daily_average": ZERO,
                "no_data": True,
            }

        result = stats.forecast(daily, periods=horizon, method="linear")

        # Attach real calendar dates so the chart can continue the same axis.
        points = []
        for offset, value in enumerate(result["values"], start=1):
            moment = today + timedelta(days=offset)
            points.append(
                {
                    "date": moment.isoformat(),
                    "label": _bucket_label(moment, "day"),
                    "value": round(float(value), 2),
                }
            )

        total = round(sum(float(value) for value in result["values"]), 2)
        return {
            **result,
            "key": "revenue_forecast",
            "label": _("Revenue forecast"),
            "unit": "money",
            "series": points,
            "history": history["series"],
            "history_days": len(daily),
            "horizon_days": horizon,
            "projected_total": Decimal(str(total)),
            "daily_average": (
                Decimal(str(round(total / horizon, 2))) if horizon else ZERO
            ),
            "no_data": not result["values"],
        }

    def fallback() -> dict:
        message = _("The revenue history could not be read, so no projection is available.")
        return {
            "key": "revenue_forecast",
            "label": _("Revenue forecast"),
            "unit": "money",
            "values": [],
            "series": [],
            "history": [],
            "history_days": 0,
            "horizon_days": horizon,
            "method": "none",
            "confidence": "none",
            "low_confidence": True,
            "warning": message,
            "warning_code": "unavailable",
            "warnings": [{"code": "unavailable", "message": message}],
            "n": 0,
            "required_points": horizon * stats.MIN_HISTORY_MULTIPLE,
            "slope": None,
            "intercept": None,
            "r_squared": None,
            "projected_total": ZERO,
            "daily_average": ZERO,
            "no_data": True,
        }

    return _guard(build, fallback)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
#: Series a user may pick for the "Statistical summary" panel.
ANALYSABLE_METRICS: tuple[tuple[str, object], ...] = (
    ("revenue", _("Revenue")),
    ("bookings", _("Bookings")),
    ("occupancy", _("Lesson occupancy")),
    ("customers", _("New customers")),
    ("instructors", _("Lessons delivered")),
    ("rentals", _("Rentals")),
    ("equipment", _("Equipment utilisation")),
)


def dashboard_metrics(start, end) -> dict:
    """Every metric the dashboard needs, computed once.

    Returned as a dict keyed by metric key so the template and the CSV export
    read the same numbers — the export can never disagree with the screen.
    """
    start, end = normalise_range(start, end)
    return {
        "revenue": revenue_metrics(start, end),
        "bookings": booking_metrics(start, end),
        "occupancy": lesson_occupancy(start, end),
        "customers": customer_metrics(start, end),
        "instructors": instructor_metrics(start, end),
        "equipment": equipment_utilisation(start, end),
        "rentals": rental_metrics(start, end),
        "levels": student_level_distribution(start, end),
        "channels": channel_mix(start, end),
        "hours": busiest_hours(start, end),
        "weekdays": busiest_weekdays(start, end),
    }


def statistical_summary(metrics: dict, metric_key: str) -> dict:
    """Full descriptive statistics for one already-computed metric's series.

    Also correlates the chosen series against revenue, which is the question
    behind most of them: "does this move with the money?" Correlating revenue
    with itself is pointless, so that pairing is skipped.
    """
    metric = metrics.get(metric_key) or metrics.get("revenue") or {}
    values = series_values(metric.get("series") or [])
    summary = stats.summarise(values)
    summary["metric"] = metric.get("key")
    summary["metric_label"] = metric.get("label")
    summary["unit"] = metric.get("unit", "count")
    summary["moving_average"] = stats.moving_average(values, min(7, max(2, len(values) // 3 or 2)))

    revenue_values = series_values((metrics.get("revenue") or {}).get("series") or [])
    if metric.get("key") != "revenue" and revenue_values and len(revenue_values) == len(values):
        summary["revenue_correlation"] = stats.correlation(values, revenue_values)
    else:
        summary["revenue_correlation"] = None
    return summary


def ai_narrative_payload(metrics: dict, forecast_result: dict, range_label: str) -> dict:
    """The already-computed figures handed to the AI narrator.

    Deliberately small and entirely numeric. The model is asked to describe
    these numbers, never to produce one, so there is nothing here for it to
    embellish.
    """

    def head(metric: dict) -> dict:
        return {
            "current": float(metric.get("current") or 0),
            "previous": float(metric.get("previous") or 0),
            "change_pct": metric.get("change_pct"),
            "unit": metric.get("unit"),
        }

    payload = {
        "period": range_label,
        "revenue": head(metrics.get("revenue", {})),
        "bookings": {
            **head(metrics.get("bookings", {})),
            "cancellation_rate": metrics.get("bookings", {}).get("cancellation_rate"),
            "no_show_rate": metrics.get("bookings", {}).get("no_show_rate"),
            "average_lead_days": metrics.get("bookings", {}).get("average_lead_days"),
        },
        "occupancy": head(metrics.get("occupancy", {})),
        "customers": {
            **head(metrics.get("customers", {})),
            "new": metrics.get("customers", {}).get("new"),
            "returning": metrics.get("customers", {}).get("returning"),
            "repeat_rate": metrics.get("customers", {}).get("repeat_rate"),
        },
        "equipment_utilisation": head(metrics.get("equipment", {})),
        "rentals": head(metrics.get("rentals", {})),
        "peak_hour": metrics.get("hours", {}).get("peak_hour"),
        "peak_weekday": str(metrics.get("weekdays", {}).get("peak_weekday") or ""),
        "leading_channel": str(metrics.get("channels", {}).get("leading_channel") or ""),
    }
    payload["forecast"] = {
        "horizon_days": forecast_result.get("horizon_days"),
        "projected_total": float(forecast_result.get("projected_total") or 0),
        "confidence": forecast_result.get("confidence"),
        "reliable": not forecast_result.get("low_confidence", True),
    }
    return payload


def export_rows(metrics: dict, forecast_result: dict, range_label: str) -> list[list]:
    """Flatten the dashboard into CSV rows: ``section, metric, value, unit``.

    Exactly the numbers on the screen, so a spreadsheet and the dashboard can
    never tell two different stories.
    """
    rows: list[list] = [
        [str(_("Section")), str(_("Metric")), str(_("Value")), str(_("Unit"))],
        [str(_("Period")), str(_("Date range")), range_label, ""],
    ]

    def add(section, name, value, unit=""):
        rows.append([str(section), str(name), "" if value is None else str(value), str(unit)])

    headline = str(_("Headline"))
    for key in ("revenue", "bookings", "occupancy", "customers", "equipment", "rentals"):
        metric = metrics.get(key)
        if not metric:
            continue
        add(headline, metric["label"], metric["current"], metric["unit"])
        add(headline, _("%(label)s — previous period") % {"label": metric["label"]},
            metric["previous"], metric["unit"])
        add(headline, _("%(label)s — change") % {"label": metric["label"]},
            metric["change_pct"], "%")

    bookings = metrics.get("bookings", {})
    section = str(_("Bookings"))
    add(section, _("Cancelled"), bookings.get("cancelled"), _("count"))
    add(section, _("Cancellation rate"), bookings.get("cancellation_rate"), "%")
    add(section, _("No-shows"), bookings.get("no_show"), _("count"))
    add(section, _("No-show rate"), bookings.get("no_show_rate"), "%")
    add(section, _("Average lead time"), bookings.get("average_lead_days"), _("days"))

    section = str(_("Customers"))
    customers = metrics.get("customers", {})
    add(section, _("New customers"), customers.get("new"), _("count"))
    add(section, _("Returning customers"), customers.get("returning"), _("count"))
    add(section, _("Repeat rate"), customers.get("repeat_rate"), "%")

    section = str(_("Occupancy by lesson type"))
    for row in metrics.get("occupancy", {}).get("by_type", []):
        add(section, row["label"], row["rate"], "%")

    section = str(_("Booking channels"))
    for row in metrics.get("channels", {}).get("channels", []):
        add(section, row["label"], row["count"], _("count"))

    section = str(_("Student levels"))
    for row in metrics.get("levels", {}).get("distribution", []):
        add(section, row["label"], row["count"], _("count"))

    section = str(_("Busiest hours"))
    for row in metrics.get("hours", {}).get("hours", []):
        if row["lessons"] or row["seats"]:
            add(section, row["label"], row["seats"], _("seats"))

    section = str(_("Busiest weekdays"))
    for row in metrics.get("weekdays", {}).get("weekdays", []):
        add(section, row["label"], row["seats"], _("seats"))

    section = str(_("Equipment"))
    equipment = metrics.get("equipment", {})
    add(section, _("Fleet size"), equipment.get("fleet_size"), _("items"))
    add(section, _("Hours hired"), equipment.get("used_hours"), _("hours"))
    add(section, _("Idle items"), equipment.get("items_idle"), _("items"))

    section = str(_("Revenue forecast"))
    add(section, _("Horizon"), forecast_result.get("horizon_days"), _("days"))
    add(section, _("Projected total"), forecast_result.get("projected_total"), _("money"))
    add(section, _("Confidence"), forecast_result.get("confidence"), "")
    add(section, _("Warning"), forecast_result.get("warning"), "")

    section = str(_("Revenue over time"))
    for point in metrics.get("revenue", {}).get("series", []):
        add(section, point["date"], point["value"], _("money"))

    return rows
