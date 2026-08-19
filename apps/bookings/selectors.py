"""Read-side queries for the booking screens.

Kept apart from :mod:`apps.bookings.services` so the write path stays free of
presentation-driven query tweaks, and so the API and the HTML views filter
bookings in exactly the same way.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.core.exceptions import FieldError
from django.db.models import Q
from django.utils import timezone

from apps.core.enums import ACTIVE_BOOKING_STATUSES, BookingStatus, PaymentStatus

from .models import Booking, WaitlistEntry, as_aware, as_end_of_day


def base_queryset():
    """Every booking screen starts here: no N+1, newest first."""
    return Booking.objects.select_related(
        "customer", "student", "lesson", "surf_camp", "created_by"
    ).order_by("-booked_at", "-id")


def _safe(queryset, *args, **kwargs):
    """Filter across an app boundary, ignoring lookups that app does not expose."""
    try:
        filtered = queryset.filter(*args, **kwargs)
        str(filtered.query)
        return filtered
    except (FieldError, ValueError, TypeError):
        return queryset


def parse_date(raw: str) -> date | None:
    """Read an ISO date from a query parameter, tolerating rubbish."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


#: Internal alias kept for readability inside this module.
_parse_date = parse_date


def filter_bookings(queryset, params) -> tuple:
    """Apply the list-screen filters. Returns ``(queryset, applied)``.

    ``applied`` is echoed back into the template so the filter bar keeps its
    state across HTMX swaps and pagination.
    """
    applied = {
        "status": (params.get("status") or "").strip(),
        "payment_status": (params.get("payment_status") or "").strip(),
        "booking_type": (params.get("booking_type") or "").strip(),
        "source": (params.get("source") or "").strip(),
        "start": (params.get("start") or "").strip(),
        "end": (params.get("end") or "").strip(),
        "lesson": (params.get("lesson") or "").strip(),
        "surf_camp": (params.get("surf_camp") or "").strip(),
        "customer": (params.get("customer") or "").strip(),
        "scope": (params.get("scope") or "").strip(),
    }

    if applied["status"] in dict(BookingStatus.choices):
        queryset = queryset.filter(status=applied["status"])
    elif applied["status"] == "active":
        queryset = queryset.filter(status__in=ACTIVE_BOOKING_STATUSES)

    if applied["payment_status"] in dict(PaymentStatus.choices):
        queryset = queryset.filter(payment_status=applied["payment_status"])
    elif applied["payment_status"] == "outstanding":
        queryset = queryset.filter(
            payment_status__in=[
                PaymentStatus.UNPAID,
                PaymentStatus.PARTIAL,
                PaymentStatus.OVERDUE,
            ]
        )

    if applied["booking_type"] in dict(Booking.BookingType.choices):
        queryset = queryset.filter(booking_type=applied["booking_type"])

    if applied["source"]:
        queryset = queryset.filter(source=applied["source"])

    start = _parse_date(applied["start"])
    end = _parse_date(applied["end"])
    if start and end and end < start:
        start, end = end, start
        applied["start"], applied["end"] = end.isoformat(), start.isoformat()
    if start:
        queryset = queryset.filter(booked_at__gte=as_aware(start))
    if end:
        queryset = queryset.filter(booked_at__lte=as_end_of_day(end))

    for field in ("lesson", "surf_camp", "customer"):
        raw = applied[field]
        if raw.isdigit():
            queryset = queryset.filter(**{f"{field}_id": int(raw)})

    if applied["scope"] == "today":
        today = timezone.localdate()
        queryset = _safe(
            queryset, Q(lesson__start_time__date=today) | Q(surf_camp__start_date=today)
        )
    elif applied["scope"] == "upcoming":
        queryset = _safe(
            queryset,
            Q(lesson__start_time__gte=timezone.now())
            | Q(surf_camp__start_date__gte=timezone.localdate()),
        )

    return queryset, applied


def upcoming_for_customer(customer, limit: int = 10):
    """The customer's next sessions — used on the detail and portal screens."""
    queryset = base_queryset().filter(customer=customer, status__in=ACTIVE_BOOKING_STATUSES)
    queryset = _safe(
        queryset,
        Q(lesson__start_time__gte=timezone.now())
        | Q(surf_camp__end_date__gte=timezone.localdate()),
    )
    return list(queryset[:limit])


def bookings_needing_reminder(hours_ahead: int = 24):
    """Active bookings starting inside the reminder window with no reminder yet."""
    now = timezone.now()
    horizon = now + timedelta(hours=hours_ahead)
    queryset = base_queryset().filter(
        status__in=[BookingStatus.PENDING, BookingStatus.CONFIRMED],
        reminder_sent=False,
    )
    return _safe(queryset, lesson__start_time__gte=now, lesson__start_time__lte=horizon)


def waitlist_queryset():
    return WaitlistEntry.objects.select_related(
        "customer", "student", "lesson", "surf_camp", "converted_booking"
    ).order_by("is_converted", "position", "requested_at")


def filter_waitlist(queryset, params) -> tuple:
    applied = {
        "state": (params.get("state") or "waiting").strip(),
        "lesson": (params.get("lesson") or "").strip(),
        "surf_camp": (params.get("surf_camp") or "").strip(),
    }
    if applied["state"] == "waiting":
        queryset = queryset.filter(is_converted=False)
    elif applied["state"] == "converted":
        queryset = queryset.filter(is_converted=True)
    if applied["lesson"].isdigit():
        queryset = queryset.filter(lesson_id=int(applied["lesson"]))
    if applied["surf_camp"].isdigit():
        queryset = queryset.filter(surf_camp_id=int(applied["surf_camp"]))
    return queryset, applied


def parse_anchor(raw: str) -> date:
    """Read the calendar's ``?date=`` parameter, defaulting to today."""
    parsed = _parse_date(raw)
    if parsed is None:
        return timezone.localdate()
    return parsed


def anchor_from_datetime(value: datetime) -> date:
    return timezone.localtime(as_aware(value)).date()
