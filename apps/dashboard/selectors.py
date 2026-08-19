"""Cross-module read layer for the dashboard.

Why this module exists
----------------------
The dashboard reads from a dozen apps that are written, deployed and migrated
independently. Importing them at module level would make the *home page* — the
first screen every user sees — fail whenever any one of them is absent or
mid-migration. So every lookup here is lazy and every failure degrades to
``None`` or an empty queryset, which the templates render as a neutral
"no data yet" state.

Three rules hold for everything below:

1. **Never invent a number.** A missing source returns ``None``; the tile then
   shows a dash and says why. A present source with no rows returns a real zero.
2. **Aggregate in the database.** Money is summed with
   ``Coalesce(Sum(...), Value(Decimal("0.00")))`` so an empty table yields an
   exact ``0.00`` rather than ``None``.
3. **Keep the query count flat.** One query per tile, ``select_related`` for
   every column the template touches.
"""

from __future__ import annotations

import importlib
import logging
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import DatabaseError
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    IntegerField,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    BookingStatus,
    EquipmentCondition,
    EquipmentStatus,
    GenericStatus,
    LessonStatus,
    PaymentStatus,
    Severity,
)

logger = logging.getLogger("apps.dashboard")

#: The project-wide money type. Every aggregate below is cast to it so SQLite
#: and PostgreSQL return the same Decimal precision.
MONEY = DecimalField(max_digits=12, decimal_places=2)
ZERO = Decimal("0.00")

#: How many rows each dashboard panel shows before "see all".
PANEL_ROWS = 6
#: How many results one search group may return.
SEARCH_GROUP_LIMIT = 10
#: Shortest search term we will run a query for.
MIN_SEARCH_LENGTH = 2


# ---------------------------------------------------------------------------
# Lazy access helpers
# ---------------------------------------------------------------------------
def get_model(app_label: str, model_name: str):
    """Return a model class, or ``None`` when its app is not installed."""
    try:
        return django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return None


def import_module(dotted_path: str):
    """Import another app's module, or return ``None``.

    Any import error at all is swallowed — a module that fails to configure
    (missing provider key, half-applied migration) must cost the dashboard one
    panel, never the whole home page.
    """
    try:
        return importlib.import_module(dotted_path)
    except Exception:  # noqa: BLE001 - the home page must survive any import
        logger.debug("Could not import %s", dotted_path, exc_info=True)
        return None


def module_constant(dotted_path: str, name: str, default):
    """Read a module-level constant from another app without importing it eagerly.

    Used for vocabularies that a module owns (which lesson statuses still hold a
    slot, which maintenance statuses are still open) so the dashboard follows
    the owning module rather than keeping a second copy that can drift.
    """
    module = import_module(dotted_path)
    return default if module is None else getattr(module, name, default)


def safe_reverse(url_name: str, *args, **kwargs) -> str | None:
    """``reverse`` that returns ``None`` instead of raising.

    Deep links point into other modules' URL configurations. A renamed or not
    yet published route must cost us a link, never the whole page.
    """
    try:
        return reverse(url_name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return None


def _resolve_field(model, candidates: tuple[str, ...], kinds: set[str] | None = None):
    """Return the first field of *model* named in *candidates* (optionally typed).

    Modules that land after this one may name their columns slightly
    differently. Resolving the name once, here, keeps the adaptation in a single
    documented place instead of scattering ``getattr`` calls through the views.
    """
    for name in candidates:
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if kinds and field.get_internal_type() not in kinds:
            continue
        return field
    return None


def _sum_money(queryset, field: str) -> Decimal:
    """Database-side sum that returns an exact ``0.00`` for an empty queryset."""
    return queryset.aggregate(
        total=Coalesce(Sum(field), Value(ZERO), output_field=MONEY)
    )["total"]


def _live_lesson_statuses() -> tuple[str, ...]:
    return tuple(
        module_constant(
            "apps.lessons.models",
            "LIVE_LESSON_STATUSES",
            (LessonStatus.SCHEDULED, LessonStatus.CONFIRMED, LessonStatus.IN_PROGRESS),
        )
    )


def _open_maintenance_statuses() -> tuple[str, ...]:
    return tuple(
        module_constant(
            "apps.maintenance.models",
            "OPEN_STATUSES",
            (GenericStatus.OPEN, GenericStatus.IN_PROGRESS, GenericStatus.ON_HOLD),
        )
    )


def _seat_taking_statuses(attendance_model) -> tuple[str, ...]:
    return tuple(
        getattr(
            attendance_model,
            "SEAT_TAKING_STATUSES",
            ("registered", "checked_in", "attended"),
        )
    )


# ---------------------------------------------------------------------------
# Lessons — today's schedule
# ---------------------------------------------------------------------------
def todays_lessons(day: date_cls, *, instructor=None):
    """Today's lessons with seat counts, newest first in the day.

    ``booked`` is annotated under exactly the name ``Lesson.booked_count``
    looks for, so the template can use the model property without a query per
    row. ``awaiting_check_in`` counts registered students who have not yet been
    checked in — the number the desk actually works from.
    """
    Lesson = get_model("lessons", "Lesson")
    Attendance = get_model("lessons", "LessonAttendance")
    if Lesson is None or Attendance is None:
        return None

    seat_statuses = _seat_taking_statuses(Attendance)
    registered = getattr(getattr(Attendance, "Status", None), "REGISTERED", "registered")
    checked_in = getattr(getattr(Attendance, "Status", None), "CHECKED_IN", "checked_in")

    queryset = Lesson.objects.filter(date=day)

    if instructor is not None:
        # Resolved through a separate id query: filtering across the assistant
        # many-to-many in the same statement would multiply rows and inflate
        # every Count below.
        lesson_ids = list(
            Lesson.objects.filter(date=day)
            .filter(Q(instructor=instructor) | Q(assistant_instructors=instructor))
            .values_list("id", flat=True)
        )
        queryset = queryset.filter(id__in=lesson_ids)

    return (
        queryset.select_related("lesson_type", "spot", "instructor", "instructor__user")
        .annotate(
            booked=Count(
                "attendances",
                filter=Q(
                    attendances__is_deleted=False,
                    attendances__status__in=seat_statuses,
                ),
                distinct=True,
            ),
            awaiting_check_in=Count(
                "attendances",
                filter=Q(attendances__is_deleted=False, attendances__status=registered),
                distinct=True,
            ),
            checked_in_count=Count(
                "attendances",
                filter=Q(attendances__is_deleted=False, attendances__status=checked_in),
                distinct=True,
            ),
        )
        .order_by("start_time", "id")
    )


def todays_student_count(day: date_cls) -> int | None:
    """Distinct students holding a seat in a lesson that runs today."""
    Attendance = get_model("lessons", "LessonAttendance")
    if Attendance is None:
        return None
    seat_statuses = _seat_taking_statuses(Attendance)
    return (
        Attendance.objects.filter(
            lesson__date=day,
            lesson__status__in=_live_lesson_statuses() + (LessonStatus.COMPLETED,),
            status__in=seat_statuses,
        )
        .values("student_id")
        .distinct()
        .count()
    )


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------
def booking_queryset():
    Booking = get_model("bookings", "Booking")
    if Booking is None:
        return None
    return Booking.objects.select_related("customer", "student", "lesson", "surf_camp")


def bookings_awaiting_payment(*, limit: int = PANEL_ROWS, customer=None):
    """Active bookings that still owe money, largest balance first."""
    queryset = booking_queryset()
    if queryset is None:
        return None
    queryset = queryset.filter(
        status__in=ACTIVE_BOOKING_STATUSES,
        payment_status__in=(
            PaymentStatus.UNPAID,
            PaymentStatus.PARTIAL,
            PaymentStatus.OVERDUE,
        ),
    ).annotate(balance=F("total_amount") - F("paid_amount"))
    if customer is not None:
        queryset = queryset.filter(customer=customer)
    return queryset.filter(balance__gt=ZERO).order_by("-balance", "-booked_at")[:limit]


def outstanding_booking_balance(*, customer=None) -> Decimal | None:
    """Total still owed on active bookings — one aggregate, both sums."""
    queryset = booking_queryset()
    if queryset is None:
        return None
    queryset = queryset.filter(
        status__in=ACTIVE_BOOKING_STATUSES,
        payment_status__in=(
            PaymentStatus.UNPAID,
            PaymentStatus.PARTIAL,
            PaymentStatus.OVERDUE,
        ),
    )
    if customer is not None:
        queryset = queryset.filter(customer=customer)
    totals = queryset.aggregate(
        billed=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO), output_field=MONEY),
    )
    return totals["billed"] - totals["paid"]


def pending_confirmation_count() -> int | None:
    """Bookings a human still has to accept or decline."""
    queryset = booking_queryset()
    if queryset is None:
        return None
    return queryset.filter(status=BookingStatus.PENDING).count()


def bookings_for_customer(
    customer, *, upcoming_from=None, before=None, limit: int | None = None
):
    """A customer's own bookings — theirs as payer or as the named student.

    ``upcoming_from`` keeps what is still to come, ``before`` keeps what has
    already happened; the two are mutually exclusive so a booking never shows
    up in both panels. Returns an unsliced queryset unless ``limit`` is given,
    so the caller can count and page it.
    """
    queryset = booking_queryset()
    if queryset is None or customer is None:
        return None
    queryset = queryset.filter(Q(customer=customer) | Q(student__customer=customer))

    # A booking's date lives on the lesson or the camp; a rental or package
    # booking has neither, so the day it was made stands in for it.
    if upcoming_from is not None:
        queryset = queryset.filter(
            Q(lesson__date__gte=upcoming_from)
            | Q(surf_camp__end_date__gte=upcoming_from)
            | Q(lesson__isnull=True, surf_camp__isnull=True, booked_at__date__gte=upcoming_from)
        )
    if before is not None:
        queryset = queryset.filter(
            Q(lesson__date__lt=before)
            | Q(surf_camp__end_date__lt=before)
            | Q(lesson__isnull=True, surf_camp__isnull=True, booked_at__date__lt=before)
        )

    queryset = queryset.exclude(status=BookingStatus.CANCELLED)
    if before is not None:
        queryset = queryset.order_by("-lesson__date", "-booked_at")
    else:
        queryset = queryset.order_by("lesson__date", "lesson__start_time", "-booked_at")
    return queryset[:limit] if limit else queryset


# ---------------------------------------------------------------------------
# Rentals
# ---------------------------------------------------------------------------
def rental_queryset():
    Rental = get_model("rentals", "Rental")
    if Rental is None:
        return None
    return Rental.objects.select_related("customer", "student")


def _rental_statuses(field: str, default: tuple[str, ...]) -> tuple[str, ...]:
    Rental = get_model("rentals", "Rental")
    if Rental is None:
        return default
    return tuple(getattr(Rental, field, default))


def active_rental_count() -> int | None:
    """Hire contracts with gear still committed to a customer."""
    queryset = rental_queryset()
    if queryset is None:
        return None
    return queryset.filter(
        status__in=_rental_statuses("OPEN_STATUSES", ("reserved", "active", "overdue"))
    ).count()


def overdue_rentals(*, limit: int = PANEL_ROWS):
    """Gear that is late back: flagged overdue, or quietly past its return time."""
    queryset = rental_queryset()
    if queryset is None:
        return None
    now = timezone.now()
    out_statuses = _rental_statuses("OUT_STATUSES", ("active", "overdue"))
    return (
        queryset.filter(
            Q(status="overdue") | Q(status__in=out_statuses, expected_return_at__lt=now),
            returned_at__isnull=True,
        )
        # ``Rental.item_count`` honours a prefetch, so the panel costs two
        # queries rather than one per row.
        .prefetch_related("items")
        .order_by("expected_return_at")[:limit]
    )


def rentals_due_back_today(day: date_cls) -> int | None:
    queryset = rental_queryset()
    if queryset is None:
        return None
    out_statuses = _rental_statuses("OUT_STATUSES", ("active", "overdue"))
    return queryset.filter(
        status__in=out_statuses,
        returned_at__isnull=True,
        expected_return_at__date=day,
    ).count()


def outstanding_rental_balance() -> Decimal | None:
    queryset = rental_queryset()
    if queryset is None:
        return None
    queryset = queryset.filter(
        payment_status__in=(
            PaymentStatus.UNPAID,
            PaymentStatus.PARTIAL,
            PaymentStatus.OVERDUE,
        )
    ).exclude(status="cancelled")
    totals = queryset.aggregate(
        billed=Coalesce(Sum("total_amount"), Value(ZERO), output_field=MONEY),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO), output_field=MONEY),
    )
    return totals["billed"] - totals["paid"]


def rentals_for_customer(customer, *, limit: int | None = None):
    """A customer's own hire contracts, most recent first."""
    queryset = rental_queryset()
    if queryset is None or customer is None:
        return None
    queryset = queryset.filter(customer=customer).order_by("-start_at")
    return queryset[:limit] if limit else queryset


# ---------------------------------------------------------------------------
# Equipment & maintenance
# ---------------------------------------------------------------------------
def equipment_warning_counts(day: date_cls) -> dict | None:
    """Everything about the fleet that needs a human today.

    One query per class of problem, all counts — the panel below shows the rows.
    """
    Equipment = get_model("equipment", "Equipment")
    if Equipment is None:
        return None

    in_service = Equipment.objects.exclude(status=EquipmentStatus.RETIRED)
    counts = {
        "service_due": in_service.filter(next_maintenance_date__lte=day).count(),
        "in_maintenance": Equipment.objects.filter(
            status=EquipmentStatus.MAINTENANCE
        ).count(),
        "damaged": Equipment.objects.filter(
            status__in=(EquipmentStatus.DAMAGED, EquipmentStatus.LOST)
        ).count(),
        "poor_condition": in_service.filter(
            condition__in=(EquipmentCondition.POOR, EquipmentCondition.UNUSABLE)
        ).count(),
        "open_repairs": 0,
        "critical_repairs": 0,
    }

    Record = get_model("maintenance", "MaintenanceRecord")
    if Record is not None:
        open_records = Record.objects.filter(status__in=_open_maintenance_statuses())
        counts["open_repairs"] = open_records.count()
        counts["critical_repairs"] = open_records.filter(
            severity__in=(Severity.HIGH, Severity.CRITICAL)
        ).count()

    counts["total"] = (
        counts["service_due"]
        + counts["damaged"]
        + counts["poor_condition"]
        + counts["open_repairs"]
    )
    return counts


def open_maintenance_records(*, limit: int = PANEL_ROWS):
    """Open repairs, worst first — severity outranks age."""
    Record = get_model("maintenance", "MaintenanceRecord")
    if Record is None:
        return None
    severity_rank = Case(
        When(severity=Severity.CRITICAL, then=Value(0)),
        When(severity=Severity.HIGH, then=Value(1)),
        When(severity=Severity.MEDIUM, then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )
    return (
        Record.objects.filter(status__in=_open_maintenance_statuses())
        .select_related("equipment", "assigned_to")
        .annotate(severity_order=severity_rank)
        .order_by("severity_order", "reported_at")[:limit]
    )


def equipment_service_due(day: date_cls, *, limit: int = PANEL_ROWS):
    """Items whose preventive service date has passed."""
    Equipment = get_model("equipment", "Equipment")
    if Equipment is None:
        return None
    return (
        Equipment.objects.exclude(status=EquipmentStatus.RETIRED)
        .filter(next_maintenance_date__lte=day)
        .select_related("category")
        .order_by("next_maintenance_date")[:limit]
    )


# ---------------------------------------------------------------------------
# Instructors
# ---------------------------------------------------------------------------
def instructor_availability(day: date_cls) -> dict | None:
    """Who can be put on the water today.

    "Available" means an active instructor who accepts bookings and is not on
    approved leave. Teaching today is reported separately: a fully booked
    instructor is available to the school but not to a new lesson.
    """
    Instructor = get_model("instructors", "Instructor")
    if Instructor is None:
        return None

    active = Instructor.objects.filter(is_active=True)
    off_ids: set[int] = set()
    TimeOff = get_model("instructors", "TimeOff")
    if TimeOff is not None:
        off_ids = set(
            TimeOff.objects.filter(
                is_approved=True, start_date__lte=day, end_date__gte=day
            ).values_list("instructor_id", flat=True)
        )

    teaching_ids: set[int] = set()
    Lesson = get_model("lessons", "Lesson")
    if Lesson is not None:
        teaching_ids = set(
            Lesson.objects.filter(
                date=day, status__in=_live_lesson_statuses()
            ).values_list("instructor_id", flat=True)
        )

    bookable = active.filter(is_available_for_booking=True)
    return {
        "active": active.count(),
        "available": bookable.exclude(pk__in=off_ids).count(),
        "on_leave": len(off_ids),
        "teaching": len(teaching_ids),
    }


def instructor_for_user(user):
    """The instructor profile linked to *user*, or ``None``."""
    Instructor = get_model("instructors", "Instructor")
    if Instructor is None or user is None or not user.is_authenticated:
        return None
    return Instructor.objects.select_related("user").filter(user=user).first()


def expiring_certifications(*, within_days: int = 30, limit: int = PANEL_ROWS):
    """Certifications that expire soon or already have — a legal blocker."""
    Certification = get_model("instructors", "Certification")
    if Certification is None:
        return None
    horizon = timezone.localdate() + timedelta(days=within_days)
    return (
        Certification.objects.filter(expires_on__isnull=False, expires_on__lte=horizon)
        .select_related("instructor", "instructor__user")
        .order_by("expires_on")[:limit]
    )


# ---------------------------------------------------------------------------
# Customers & students
# ---------------------------------------------------------------------------
def customer_for_user(user):
    """The customer record a self-service user signs in as."""
    Customer = get_model("customers", "Customer")
    if Customer is None or user is None or not user.is_authenticated:
        return None
    return Customer.objects.filter(user=user).first()


def student_for_customer(customer):
    Student = get_model("students", "Student")
    if Student is None or customer is None:
        return None
    return Student.objects.select_related("customer").filter(customer=customer).first()


# ---------------------------------------------------------------------------
# Finance — money actually received
# ---------------------------------------------------------------------------
#: Column names the finance module may use for "when the money arrived".
_PAYMENT_DATE_FIELDS = (
    "paid_at",
    "received_at",
    "payment_date",
    "paid_on",
    "date",
    "created_at",
)
#: Column names the finance module may use for the amount.
_PAYMENT_AMOUNT_FIELDS = ("amount", "total_amount", "total")


def _finance_selectors():
    """The finance module's own read layer, when it is installed."""
    return import_module("apps.finance.selectors")


def _payment_source():
    """Return ``(model, date_field, amount_field)`` for finance payments.

    Only used as a fallback when the finance module does not publish the read
    helpers below. ``None`` means the school has no payment ledger yet — a
    different statement from "no money came in today", which the dashboard
    renders differently and never conflates.
    """
    Payment = get_model("finance", "Payment")
    if Payment is None:
        return None
    date_field = _resolve_field(
        Payment, _PAYMENT_DATE_FIELDS, {"DateTimeField", "DateField"}
    )
    amount_field = _resolve_field(Payment, _PAYMENT_AMOUNT_FIELDS, {"DecimalField"})
    if date_field is None or amount_field is None:
        logger.debug("finance.Payment present but its date/amount columns were not recognised")
        return None
    return Payment, date_field, amount_field


def _settled_payments(start: date_cls, end: date_cls):
    """Payments whose money actually moved in ``[start, end]``.

    Prefers ``apps.finance.selectors.settled_payments`` so the dashboard's
    revenue always equals the finance screen's revenue — that module knows
    which payment statuses count and that refunds are negative amounts.
    """
    finance = _finance_selectors()
    if finance is not None and hasattr(finance, "settled_payments"):
        try:
            return finance.settled_payments(start, end), "amount"
        except (FieldError, DatabaseError, TypeError):
            logger.debug("finance.settled_payments failed", exc_info=True)

    source = _payment_source()
    if source is None:
        return None, None
    Payment, date_field, amount_field = source
    is_datetime = date_field.get_internal_type() == "DateTimeField"
    lookup = f"{date_field.name}__date__range" if is_datetime else f"{date_field.name}__range"
    try:
        return Payment._default_manager.filter(**{lookup: (start, end)}), amount_field.name
    except (FieldError, DatabaseError):
        return None, None


def revenue_between(start: date_cls, end: date_cls) -> Decimal | None:
    """Net money received between two dates, inclusive. ``None`` = no ledger."""
    finance = _finance_selectors()
    if finance is not None and hasattr(finance, "net_revenue"):
        try:
            return finance.net_revenue(start, end)
        except (FieldError, DatabaseError, TypeError, InvalidOperation):
            logger.debug("finance.net_revenue failed", exc_info=True)

    queryset, amount_field = _settled_payments(start, end)
    if queryset is None:
        return None
    try:
        return _sum_money(queryset, amount_field)
    except (FieldError, DatabaseError, InvalidOperation):
        logger.debug("Revenue aggregate failed", exc_info=True)
        return None


def revenue_by_day(start: date_cls, end: date_cls) -> list[tuple[date_cls, Decimal]] | None:
    """Daily takings across ``[start, end]``, with empty days filled in as 0.00.

    Grouping uses ``TruncDate``, which both SQLite and PostgreSQL support.
    """
    queryset, amount_field = _settled_payments(start, end)
    if queryset is None:
        return None

    Payment = get_model("finance", "Payment")
    date_field = (
        _resolve_field(Payment, _PAYMENT_DATE_FIELDS, {"DateTimeField", "DateField"})
        if Payment is not None
        else None
    )
    if date_field is None:
        return None
    is_datetime = date_field.get_internal_type() == "DateTimeField"

    try:
        if is_datetime:
            queryset = queryset.annotate(bucket=TruncDate(date_field.name))
        else:
            queryset = queryset.annotate(bucket=F(date_field.name))
        rows = (
            queryset.values("bucket")
            .annotate(total=Coalesce(Sum(amount_field), Value(ZERO), output_field=MONEY))
            .order_by("bucket")
        )
        totals = {row["bucket"]: row["total"] for row in rows}
    except (FieldError, DatabaseError, InvalidOperation):
        logger.debug("Revenue-by-day aggregate failed", exc_info=True)
        return None

    series: list[tuple[date_cls, Decimal]] = []
    cursor = start
    while cursor <= end:
        series.append((cursor, totals.get(cursor, ZERO)))
        cursor += timedelta(days=1)
    return series


def overdue_invoice_count() -> int | None:
    """Invoices past their due date, when the finance module keeps invoices."""
    finance = _finance_selectors()
    if finance is not None and hasattr(finance, "overdue_invoice_queryset"):
        try:
            return finance.overdue_invoice_queryset().count()
        except (FieldError, DatabaseError, TypeError):
            logger.debug("finance.overdue_invoice_queryset failed", exc_info=True)

    Invoice = get_model("finance", "Invoice")
    if Invoice is None:
        return None
    try:
        return Invoice._default_manager.filter(status=PaymentStatus.OVERDUE).count()
    except (FieldError, DatabaseError):
        return None


# ---------------------------------------------------------------------------
# Surf conditions
# ---------------------------------------------------------------------------
_CONDITION_SPOT_FIELDS = ("spot", "surf_spot", "location")
_CONDITION_TIME_FIELDS = ("recorded_at", "observed_at", "measured_at", "timestamp", "created_at")
_WAVE_FIELDS = ("wave_height_m", "wave_height", "swell_height_m", "significant_wave_height_m")
_PERIOD_FIELDS = ("wave_period_s", "swell_period_s", "wave_period", "period_seconds")
_WIND_FIELDS = ("wind_speed_kmh", "wind_speed", "wind_kmh")
_WIND_DIR_FIELDS = ("wind_direction_deg", "wind_direction", "wind_dir_deg")
_WATER_TEMP_FIELDS = (
    "water_temperature_c",
    "water_temp_c",
    "water_temperature",
    "sea_temperature_c",
)
_SCORE_VALUE_FIELDS = ("score", "value", "rating", "score_percent", "points")


def primary_spot():
    """The school's default surf spot, or the first active one."""
    SurfSpot = get_model("locations", "SurfSpot")
    if SurfSpot is None:
        return None
    return (
        SurfSpot.objects.filter(is_active=True)
        .order_by("-is_primary", "name")
        .first()
    )


def _numeric(instance, field) -> float | None:
    if field is None:
        return None
    value = getattr(instance, field.name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _surf_services():
    """The surf-conditions module's own service layer, when it is installed."""
    return import_module("apps.surf_conditions.services")


def _find_condition(spot):
    """The condition row that best describes *now* at *spot*.

    Prefers ``apps.surf_conditions.services.current_or_nearest``, which knows
    the difference between a fresh observation and a stale one and falls back
    to the nearest forecast hour. The direct query below only runs if that
    module changes shape.
    """
    module = _surf_services()
    if module is not None:
        for name in ("current_or_nearest", "latest_condition"):
            resolver = getattr(module, name, None)
            if resolver is None:
                continue
            try:
                return resolver(spot)
            except (FieldError, DatabaseError, TypeError, AttributeError):
                logger.debug("surf_conditions.%s failed", name, exc_info=True)

    Condition = get_model("surf_conditions", "SurfCondition")
    if Condition is None:
        return None
    time_field = _resolve_field(Condition, _CONDITION_TIME_FIELDS, {"DateTimeField", "DateField"})
    if time_field is None:
        return None
    spot_field = _resolve_field(Condition, _CONDITION_SPOT_FIELDS, {"ForeignKey"})
    try:
        queryset = Condition._default_manager.all()
        if spot is not None and spot_field is not None:
            queryset = queryset.filter(**{spot_field.name: spot})
        if spot_field is not None:
            queryset = queryset.select_related(spot_field.name)
        return queryset.order_by(f"-{time_field.name}").first()
    except (FieldError, DatabaseError):
        logger.debug("Surf condition lookup failed", exc_info=True)
        return None


def latest_surf_reading(spot=None) -> dict | None:
    """The current condition reading, normalised to plain numbers.

    Returns ``None`` when the surf-conditions module is absent or has never
    recorded anything for the spot — the panel then invites the operator to
    record a reading instead of showing an invented forecast.
    """
    reading = _find_condition(spot)
    if reading is None:
        return None

    Condition = type(reading)
    time_field = _resolve_field(Condition, _CONDITION_TIME_FIELDS, {"DateTimeField", "DateField"})
    spot_field = _resolve_field(Condition, _CONDITION_SPOT_FIELDS, {"ForeignKey"})

    return {
        "reading": reading,
        "spot": getattr(reading, spot_field.name, None) if spot_field else spot,
        "observed_at": getattr(reading, time_field.name, None) if time_field else None,
        "is_forecast": bool(getattr(reading, "is_forecast", False)),
        "is_stale": bool(getattr(reading, "is_stale", False)),
        "wave_height_m": _numeric(
            reading, _resolve_field(Condition, _WAVE_FIELDS, {"FloatField", "DecimalField"})
        ),
        "wave_period_s": _numeric(
            reading, _resolve_field(Condition, _PERIOD_FIELDS, {"FloatField", "DecimalField"})
        ),
        "wind_speed_kmh": _numeric(
            reading, _resolve_field(Condition, _WIND_FIELDS, {"FloatField", "DecimalField"})
        ),
        "wind_direction_deg": _numeric(
            reading, _resolve_field(Condition, _WIND_DIR_FIELDS, {"FloatField", "DecimalField"})
        ),
        "wind_type": getattr(reading, "wind_type", None) or None,
        "water_temp_c": _numeric(
            reading, _resolve_field(Condition, _WATER_TEMP_FIELDS, {"FloatField", "DecimalField"})
        ),
        "tide_state": getattr(reading, "tide_state", None) or getattr(reading, "tide", None),
        "module_scores": _module_surf_scores(reading),
    }


def _module_surf_scores(reading) -> dict[str, dict]:
    """Per-level scores published by the surf-conditions module itself.

    When that module has already scored the reading its numbers win: the
    dashboard must never disagree with the screen an instructor was looking at
    ten seconds earlier. Its safety verdict wins too, because that module owns
    the gate.
    """
    Score = get_model("surf_conditions", "SurfScore")
    if Score is None or reading is None:
        return {}

    level_field = _resolve_field(Score, ("level", "surf_level"), {"CharField"})
    value_field = _resolve_field(
        Score,
        _SCORE_VALUE_FIELDS,
        {"IntegerField", "PositiveSmallIntegerField", "FloatField", "DecimalField"},
    )
    condition_field = _resolve_field(
        Score, ("condition", "surf_condition", "reading"), {"ForeignKey"}
    )
    if level_field is None or value_field is None or condition_field is None:
        return {}

    try:
        rows = Score._default_manager.filter(**{condition_field.name: reading})
        scores: dict[str, dict] = {}
        for row in rows:
            value = getattr(row, value_field.name, None)
            if value is None:
                continue
            safe = getattr(row, "is_safe_for_level", None)
            scores[getattr(row, level_field.name)] = {
                "score": int(round(float(value))),
                "safe": True if safe is None else bool(safe),
                "recommendation": getattr(row, "recommendation", "") or "",
            }
        return scores
    except (FieldError, DatabaseError, TypeError, ValueError):
        logger.debug("Surf score lookup failed", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Audit & AI
# ---------------------------------------------------------------------------
def recent_activity(*, limit: int = 8):
    """The last things that happened, from the audit log."""
    AuditLog = get_model("audit", "AuditLog")
    if AuditLog is None:
        return None
    return AuditLog.objects.select_related("user").order_by("-created_at", "-id")[:limit]


def ai_alerts(user, *, limit: int = PANEL_ROWS):
    """Unread AI-generated notifications addressed to *user*.

    These are recommendations. The safety rule is enforced at render time: the
    panel is an ``.ai-surface`` carrying the "AI Recommendation" chip, and every
    row links back to the record so a named human decides.
    """
    Notification = get_model("notifications", "Notification")
    if Notification is None or user is None or not user.is_authenticated:
        return None
    return Notification.objects.filter(
        recipient=user, is_read=False, category="ai"
    ).order_by("-created_at")[:limit]


def ai_alert_count(user) -> int | None:
    Notification = get_model("notifications", "Notification")
    if Notification is None or user is None or not user.is_authenticated:
        return None
    return Notification.objects.filter(recipient=user, is_read=False, category="ai").count()


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------
#: ``(app_label, model, code field, capability, detail route)`` — an exact match
#: on one of these codes jumps straight to the record.
DIRECT_HIT_CODES: tuple[tuple[str, str, str, str, str], ...] = (
    ("equipment", "Equipment", "asset_code", "equipment.view", "equipment:detail"),
    ("bookings", "Booking", "booking_code", "bookings.view", "bookings:detail"),
    ("customers", "Customer", "customer_code", "customers.view", "customers:detail"),
    ("students", "Student", "student_code", "students.view", "students:detail"),
    ("rentals", "Rental", "rental_code", "rentals.view", "rentals:detail"),
    ("lessons", "Lesson", "lesson_code", "lessons.view", "lessons:detail"),
)


def direct_hit_url(user, term: str) -> str | None:
    """URL of the single record whose code is exactly *term*, if any."""
    term = (term or "").strip()
    if len(term) < MIN_SEARCH_LENGTH or getattr(user, "is_external", False):
        return None

    for app_label, model_name, field, capability, route in DIRECT_HIT_CODES:
        if not user.has_capability(capability):
            continue
        model = get_model(app_label, model_name)
        if model is None:
            continue
        try:
            match = model.objects.filter(**{f"{field}__iexact": term}).only("pk").first()
        except (FieldError, DatabaseError):
            continue
        if match is not None:
            return safe_reverse(route, match.pk)
    return None


def search_customers(term: str, *, scope_to=None):
    Customer = get_model("customers", "Customer")
    if Customer is None:
        return None
    queryset = Customer.objects.search(term)
    if scope_to is not None:
        queryset = queryset.filter(pk=scope_to.pk)
    return queryset.order_by("last_name", "first_name")[:SEARCH_GROUP_LIMIT]


def search_students(term: str, *, scope_to=None):
    Student = get_model("students", "Student")
    if Student is None:
        return None
    queryset = Student.objects.search(term).select_related("customer")
    if scope_to is not None:
        queryset = queryset.filter(customer=scope_to)
    return queryset.order_by("customer__last_name", "customer__first_name")[:SEARCH_GROUP_LIMIT]


def search_instructors(term: str):
    Instructor = get_model("instructors", "Instructor")
    if Instructor is None:
        return None
    return (
        Instructor.objects.select_related("user")
        .filter(
            Q(instructor_code__icontains=term)
            | Q(user__first_name__icontains=term)
            | Q(user__last_name__icontains=term)
            | Q(user__email__icontains=term)
        )
        .order_by("user__first_name", "user__last_name")[:SEARCH_GROUP_LIMIT]
    )


def search_bookings(term: str, *, scope_to=None):
    queryset = booking_queryset()
    if queryset is None:
        return None
    queryset = queryset.filter(
        Q(booking_code__icontains=term)
        | Q(customer__first_name__icontains=term)
        | Q(customer__last_name__icontains=term)
        | Q(customer__customer_code__icontains=term)
    )
    if scope_to is not None:
        queryset = queryset.filter(Q(customer=scope_to) | Q(student__customer=scope_to))
    return queryset.order_by("-booked_at")[:SEARCH_GROUP_LIMIT]


def search_lessons(term: str):
    Lesson = get_model("lessons", "Lesson")
    if Lesson is None:
        return None
    return (
        Lesson.objects.select_related("lesson_type", "spot", "instructor", "instructor__user")
        .filter(
            Q(lesson_code__icontains=term)
            | Q(lesson_type__name__icontains=term)
            | Q(spot__name__icontains=term)
        )
        .order_by("-date", "start_time")[:SEARCH_GROUP_LIMIT]
    )


def search_equipment(term: str):
    Equipment = get_model("equipment", "Equipment")
    if Equipment is None:
        return None
    return (
        Equipment.objects.select_related("category")
        .filter(
            Q(asset_code__icontains=term)
            | Q(name__icontains=term)
            | Q(brand__icontains=term)
            | Q(model__icontains=term)
            | Q(serial_number__icontains=term)
        )
        .order_by("asset_code")[:SEARCH_GROUP_LIMIT]
    )


def search_rentals(term: str, *, scope_to=None):
    queryset = rental_queryset()
    if queryset is None:
        return None
    queryset = queryset.filter(
        Q(rental_code__icontains=term)
        | Q(customer__first_name__icontains=term)
        | Q(customer__last_name__icontains=term)
        | Q(customer__customer_code__icontains=term)
    )
    if scope_to is not None:
        queryset = queryset.filter(customer=scope_to)
    return queryset.order_by("-start_at")[:SEARCH_GROUP_LIMIT]
