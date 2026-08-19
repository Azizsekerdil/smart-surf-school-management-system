"""The report catalogue.

Every entry is a builder ``(user, filters) -> ReportData`` plus the capability a
user must hold to run it. Builders are registered with :func:`report`, which
keeps the catalogue, the run screen, the scheduler and the REST API reading from
one list.

Two rules make this module safe to depend on
--------------------------------------------
**Models are resolved lazily.** ``apps.get_model`` at call time, never an import
at module level. A school that has not switched the finance module on still gets
a working Reports screen; the revenue report simply explains that there is no
data to draw on. A missing module must never be a 500.

**Field names are probed, not assumed.** Cross-module reports name several
candidate field paths per column and keep the first one that exists on the
model. A column that resolves to nothing is dropped rather than exported as a
stripe of empty cells.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Avg, Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import (
    ACTIVE_BOOKING_STATUSES,
    BookingStatus,
    EquipmentStatus,
    GenericStatus,
    LessonStatus,
    Severity,
    SurfLevel,
)
from apps.core.utils import parse_date_range

from .exporters.base import LANDSCAPE, PORTRAIT, ColumnKind, ReportData

ZERO = Decimal("0.00")

#: Hard ceiling on rows in one export. A 200 000-row PDF helps nobody and will
#: exhaust memory on the office machine; the document says so on its face.
MAX_ROWS = 20_000

#: Ceiling on the rental lines scanned when computing utilisation.
MAX_SCAN_ROWS = 50_000


# ---------------------------------------------------------------------------
# Model & field access
# ---------------------------------------------------------------------------
def get_model(label: str):
    """Return a model class, or ``None`` when its app is not installed."""
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def field_path_exists(model, path: str) -> bool:
    """Is ``path`` (``a__b__c``) a traversable ORM path on *model*?"""
    current = model
    parts = path.split("__")
    for index, part in enumerate(parts):
        if current is None:
            return False
        try:
            field = current._meta.get_field(part)
        except (FieldDoesNotExist, AttributeError):
            return False
        related = getattr(field, "related_model", None)
        if related is None and index < len(parts) - 1:
            return False
        current = related
    return True


def terminal_field(model, path: str):
    """The final field object of a validated path."""
    current = model
    field = None
    for part in path.split("__"):
        field = current._meta.get_field(part)
        current = getattr(field, "related_model", None) or current
    return field


def first_path(model, *candidates: str) -> str | None:
    """First candidate path that exists on *model*."""
    for candidate in candidates:
        if field_path_exists(model, candidate):
            return candidate
    return None


def sum_field(queryset: QuerySet, path: str | None) -> Decimal:
    """Portable ``Sum`` that returns ``0.00`` rather than ``None``."""
    if not path or not field_path_exists(queryset.model, path):
        return ZERO
    result = queryset.aggregate(
        total=Coalesce(Sum(path), Value(ZERO), output_field=DecimalField())
    )["total"]
    return result if result is not None else ZERO


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
class _FilterRequest:
    """Adapter so the shared :func:`parse_date_range` works on a plain dict.

    Reusing it keeps the reporting date vocabulary identical to every list
    screen in the product — ``today``, ``7``, ``30``, ``90``, ``180``, ``365``,
    ``all``, ``custom``.
    """

    def __init__(self, filters: Mapping[str, Any]):
        self.GET = {
            key: ("" if value is None else str(value)) for key, value in (filters or {}).items()
        }


@dataclass(frozen=True)
class Period:
    start: datetime | None
    end: datetime | None
    label: str

    @property
    def start_date(self) -> date | None:
        return timezone.localtime(self.start).date() if self.start else None

    @property
    def end_date(self) -> date | None:
        return timezone.localtime(self.end).date() if self.end else None

    @property
    def days(self) -> int:
        if not (self.start and self.end):
            return 0
        return max((self.end - self.start).days + 1, 1)

    @property
    def hours(self) -> Decimal:
        if not (self.start and self.end):
            return ZERO
        return Decimal(str(round((self.end - self.start).total_seconds() / 3600, 2)))


def resolve_period(filters: Mapping[str, Any], default: str = "30") -> Period:
    start, end, label = parse_date_range(_FilterRequest(filters), default=default)
    return Period(start=start, end=end, label=str(label))


def apply_period(queryset: QuerySet, path: str | None, period: Period) -> QuerySet:
    """Filter a queryset on a date or datetime field, whichever *path* is."""
    if not path or not field_path_exists(queryset.model, path):
        return queryset
    if period.start is None and period.end is None:
        return queryset

    field = terminal_field(queryset.model, path)
    date_only = field.get_internal_type() == "DateField"
    start = period.start_date if date_only else period.start
    end = period.end_date if date_only else period.end

    if start is not None:
        queryset = queryset.filter(**{f"{path}__gte": start})
    if end is not None:
        queryset = queryset.filter(**{f"{path}__lte": end})
    return queryset


# ---------------------------------------------------------------------------
# Filter value helpers
# ---------------------------------------------------------------------------
def filter_str(filters: Mapping[str, Any], key: str, default: str = "") -> str:
    value = (filters or {}).get(key, default)
    return "" if value is None else str(value).strip()


def filter_int(filters: Mapping[str, Any], key: str) -> int | None:
    raw = filter_str(filters, key)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def filter_bool(filters: Mapping[str, Any], key: str) -> bool:
    value = (filters or {}).get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def filter_date(filters: Mapping[str, Any], key: str, default: date | None = None) -> date | None:
    raw = filter_str(filters, key)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Column specification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Col:
    """One output column and the field paths that could feed it."""

    label: Any
    paths: tuple[str, ...]
    kind: str = ColumnKind.TEXT
    #: Render through ``get_<field>_display()`` (for ``TextChoices`` fields).
    choice: bool = False


def col(label: Any, *paths: str, kind: str = ColumnKind.TEXT, choice: bool = False) -> Col:
    return Col(label=label, paths=tuple(paths), kind=kind, choice=choice)


@dataclass(frozen=True)
class _ResolvedCol:
    label: Any
    path: str
    kind: str
    choice: bool
    orm: bool


def _resolve_columns(model, columns: Sequence[Col], extra: Iterable[str] = ()) -> list[_ResolvedCol]:
    """Keep the columns this model can actually provide.

    ``extra`` names annotations or computed attributes the caller guarantees.
    """
    known_extra = set(extra)
    resolved: list[_ResolvedCol] = []
    for column in columns:
        for path in column.paths:
            if field_path_exists(model, path):
                resolved.append(
                    _ResolvedCol(column.label, path, column.kind, column.choice, orm=True)
                )
                break
            if path in known_extra or (
                "__" not in path and hasattr(model, path)
            ):
                resolved.append(
                    _ResolvedCol(column.label, path, column.kind, False, orm=False)
                )
                break
    return resolved


def _select_related_paths(model, resolved: Sequence[_ResolvedCol]) -> list[str]:
    """Forward relation prefixes worth joining, so rendering is not N+1."""
    prefixes: set[str] = set()
    for column in resolved:
        if not column.orm:
            continue
        parts = column.path.split("__")
        current = model
        walked: list[str] = []
        for part in parts:
            try:
                field = current._meta.get_field(part)
            except (FieldDoesNotExist, AttributeError):
                break
            if field.is_relation and (field.many_to_one or field.one_to_one):
                walked.append(part)
                prefixes.add("__".join(walked))
                current = field.related_model
            else:
                break
    return sorted(prefixes)


def _attr_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("__"):
        if current is None:
            return None
        current = getattr(current, part, None)
        if callable(current) and not hasattr(current, "_meta"):
            try:
                current = current()
            except TypeError:
                return None
    return current


def _cell(obj: Any, column: _ResolvedCol) -> Any:
    if column.choice:
        parts = column.path.split("__")
        parent = obj
        for part in parts[:-1]:
            parent = getattr(parent, part, None)
            if parent is None:
                return None
        getter = getattr(parent, f"get_{parts[-1]}_display", None)
        if callable(getter):
            return getter()
        return getattr(parent, parts[-1], None)
    return _attr_path(obj, column.path)


# ---------------------------------------------------------------------------
# Table assembly
# ---------------------------------------------------------------------------
def build_table(
    queryset: QuerySet,
    columns: Sequence[Col],
    *,
    title: Any,
    subtitle: Any = "",
    filters: Mapping | None = None,
    summary: Mapping | None = None,
    orientation: str = PORTRAIT,
    message: str = "",
    annotations: Iterable[str] = (),
    row_limit: int = MAX_ROWS,
) -> ReportData:
    """Render a queryset into :class:`ReportData` using the resolvable columns."""
    model = queryset.model
    resolved = _resolve_columns(model, columns, annotations)
    if not resolved:
        return empty_report(
            title,
            str(_("None of this report's columns exist on %(model)s in this installation."))
            % {"model": model._meta.verbose_name},
            filters=filters,
            subtitle=subtitle,
        )

    related = _select_related_paths(model, resolved)
    if related:
        queryset = queryset.select_related(*related)

    records = list(queryset[: row_limit + 1])
    truncated = row_limit if len(records) > row_limit else None
    records = records[:row_limit]

    if not records and not message:
        # An empty document must say why it is empty, or it reads as a failure.
        message = str(_("No records matched the selected filters."))

    return ReportData(
        title=str(title),
        subtitle=str(subtitle or ""),
        columns=[str(column.label) for column in resolved],
        column_kinds=[column.kind for column in resolved],
        rows=[[_cell(record, column) for column in resolved] for record in records],
        summary=dict(summary or {}),
        filters=dict(filters or {}),
        orientation=orientation,
        message=message,
        truncated_at=truncated,
    )


def empty_report(
    title: Any,
    message: Any,
    *,
    filters: Mapping | None = None,
    subtitle: Any = "",
    columns: Sequence[Any] = (),
) -> ReportData:
    """A valid, downloadable report that explains why it has no rows."""
    return ReportData(
        title=str(title),
        subtitle=str(subtitle or ""),
        columns=[str(column) for column in columns],
        rows=[],
        filters=dict(filters or {}),
        message=str(message),
    )


def module_missing(title: Any, module_label: Any, filters: Mapping | None = None) -> ReportData:
    return empty_report(
        title,
        _("The %(module)s module is not installed, so there is nothing to report on yet.")
        % {"module": module_label},
        filters=filters,
    )


def period_filters(period: Period, extra: Mapping[str, Any] | None = None) -> dict:
    """The filter block printed on the document.

    Empty values are dropped: a document that lists "Status: (blank)" reads as
    if a filter had been applied when none was.
    """
    block = {str(_("Period")): period.label}
    for label, value in (extra or {}).items():
        if value not in (None, "", [], False):
            block[str(label)] = value
    return block


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ReportArea:
    OPERATIONS = "operations"
    FINANCE = "finance"
    PEOPLE = "people"
    EQUIPMENT = "equipment"
    CAMPS = "camps"
    SAFETY = "safety"


AREA_LABELS: dict[str, Any] = {
    ReportArea.OPERATIONS: _("Daily operations"),
    ReportArea.FINANCE: _("Finance"),
    ReportArea.PEOPLE: _("People"),
    ReportArea.EQUIPMENT: _("Equipment & rentals"),
    ReportArea.CAMPS: _("Surf camps"),
    ReportArea.SAFETY: _("Safety"),
}

AREA_ICONS: dict[str, str] = {
    ReportArea.OPERATIONS: "calendar-days",
    ReportArea.FINANCE: "wallet",
    ReportArea.PEOPLE: "users",
    ReportArea.EQUIPMENT: "package",
    ReportArea.CAMPS: "tent",
    ReportArea.SAFETY: "shield-alert",
}

#: Order the areas appear on the catalogue screen.
AREA_ORDER: tuple[str, ...] = (
    ReportArea.OPERATIONS,
    ReportArea.FINANCE,
    ReportArea.PEOPLE,
    ReportArea.EQUIPMENT,
    ReportArea.CAMPS,
    ReportArea.SAFETY,
)


@dataclass(frozen=True)
class ReportSpec:
    key: str
    title: Any
    description: Any
    area: str
    capability: str
    builder: Callable[[Any, Mapping], ReportData]
    icon: str = "file-text"
    #: Names from the filter vocabulary understood by ``forms.ReportFilterForm``.
    filter_fields: tuple[str, ...] = ("period",)
    default_filters: Mapping[str, Any] = dataclass_field(default_factory=dict)
    default_format: str = "pdf"

    @property
    def area_label(self) -> Any:
        return AREA_LABELS.get(self.area, self.area)

    def build(self, user, filters: Mapping | None = None) -> ReportData:
        return self.builder(user, dict(filters or {}))


REGISTRY: dict[str, ReportSpec] = {}


def report(
    key: str,
    *,
    title: Any,
    description: Any,
    area: str,
    capability: str,
    icon: str = "file-text",
    filter_fields: tuple[str, ...] = ("period",),
    default_filters: Mapping[str, Any] | None = None,
    default_format: str = "pdf",
):
    """Register a report builder in the catalogue."""

    def decorator(func: Callable[[Any, Mapping], ReportData]):
        REGISTRY[key] = ReportSpec(
            key=key,
            title=title,
            description=description,
            area=area,
            capability=capability,
            builder=func,
            icon=icon,
            filter_fields=filter_fields,
            default_filters=dict(default_filters or {}),
            default_format=default_format,
        )
        return func

    return decorator


def get_report(key: str) -> ReportSpec | None:
    return REGISTRY.get((key or "").strip())


def all_reports() -> list[ReportSpec]:
    order = {area: index for index, area in enumerate(AREA_ORDER)}
    return sorted(REGISTRY.values(), key=lambda spec: (order.get(spec.area, 99), str(spec.title)))


def reports_for_user(user) -> list[ReportSpec]:
    """Only the reports whose underlying data the user may see."""
    if not (user and getattr(user, "is_authenticated", False)):
        return []
    return [spec for spec in all_reports() if user.has_capability(spec.capability)]


def grouped_reports(user) -> list[tuple[str, Any, str, list[ReportSpec]]]:
    """``[(area_key, area_label, icon, specs)]`` for the catalogue screen."""
    available = reports_for_user(user)
    groups: list[tuple[str, Any, str, list[ReportSpec]]] = []
    for area in AREA_ORDER:
        specs = [spec for spec in available if spec.area == area]
        if specs:
            groups.append((area, AREA_LABELS[area], AREA_ICONS.get(area, "file-text"), specs))
    return groups


def report_choices(user=None) -> list[tuple[str, Any]]:
    specs = reports_for_user(user) if user is not None else all_reports()
    return [(spec.key, spec.title) for spec in specs]


# ===========================================================================
# Operations
# ===========================================================================
@report(
    "daily_operations",
    title=_("Daily operations sheet"),
    description=_(
        "Everything happening on one day: lessons, who teaches them, how many "
        "students are expected, plus the gear and camps that need attention."
    ),
    area=ReportArea.OPERATIONS,
    capability="lessons.view",
    icon="calendar-days",
    filter_fields=("date",),
)
def daily_operations(user, filters: Mapping) -> ReportData:
    lesson_model = get_model("lessons.Lesson")
    day = filter_date(filters, "date", timezone.localdate())
    title = _("Daily operations sheet")
    shown = {str(_("Day")): day}

    if lesson_model is None:
        return module_missing(title, _("Lessons"), shown)

    scheduled = lesson_model.objects.filter(date=day).exclude(status=LessonStatus.CANCELLED)
    lessons = scheduled.annotate(
        booked_students=Count("attendances", distinct=True)
    ).order_by("start_time", "lesson_code")

    columns = [
        col(_("Start"), "start_time", kind=ColumnKind.TIME),
        col(_("End"), "end_time", kind=ColumnKind.TIME),
        col(_("Code"), "lesson_code"),
        col(_("Lesson"), "lesson_type__name"),
        col(_("Spot"), "spot__name"),
        col(_("Instructor"), "instructor"),
        col(_("Booked"), "booked_students", kind=ColumnKind.NUMBER),
        col(_("Capacity"), "capacity", kind=ColumnKind.NUMBER),
        col(_("Status"), "status", choice=True),
        col(_("Briefing"), "safety_briefing_done", kind=ColumnKind.BOOLEAN),
    ]

    totals = scheduled.aggregate(
        lessons=Count("id", distinct=True),
        students=Count("attendances"),
        instructors=Count("instructor_id", distinct=True),
        spots=Count("spot_id", distinct=True),
    )
    summary = {
        str(_("Lessons scheduled")): totals["lessons"],
        str(_("Students expected")): totals["students"],
        str(_("Instructors on duty")): totals["instructors"],
        str(_("Spots in use")): totals["spots"],
        str(_("Safety briefings outstanding")): scheduled.filter(
            safety_briefing_done=False
        ).count(),
    }
    summary.update(_day_side_notes(day))

    return build_table(
        lessons,
        columns,
        title=title,
        subtitle=_("Operations for %(day)s") % {"day": day},
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
        annotations=("booked_students",),
        message=str(_("No lessons are scheduled for this day.")) if not totals["lessons"] else "",
    )


def _day_side_notes(day: date) -> dict:
    """Counts from neighbouring modules that the morning briefing needs."""
    notes: dict = {}

    rental_model = get_model("rentals.Rental")
    if rental_model is not None:
        due = rental_model.objects.filter(
            expected_return_at__date=day, returned_at__isnull=True
        ).count()
        notes[str(_("Rentals due back"))] = due
        notes[str(_("Rentals out"))] = rental_model.objects.filter(
            status__in=rental_model.OUT_STATUSES
        ).count()

    camp_model = get_model("surf_camps.SurfCamp")
    if camp_model is not None:
        notes[str(_("Camps running"))] = camp_model.objects.filter(
            start_date__lte=day, end_date__gte=day
        ).count()

    maintenance_model = get_model("maintenance.MaintenanceRecord")
    if maintenance_model is not None:
        notes[str(_("Open maintenance jobs"))] = maintenance_model.objects.filter(
            status__in=(GenericStatus.OPEN, GenericStatus.IN_PROGRESS, GenericStatus.ON_HOLD)
        ).count()

    return notes


@report(
    "bookings_report",
    title=_("Bookings"),
    description=_("Every booking taken in the period, with its value and payment state."),
    area=ReportArea.OPERATIONS,
    capability="bookings.view",
    icon="calendar-days",
    filter_fields=("period", "booking_status", "payment_status"),
)
def bookings_report(user, filters: Mapping) -> ReportData:
    booking_model = get_model("bookings.Booking")
    period = resolve_period(filters)
    title = _("Bookings")
    status = filter_str(filters, "booking_status")
    payment_status = filter_str(filters, "payment_status")
    shown = period_filters(
        period,
        {
            _("Status"): dict(BookingStatus.choices).get(status, ""),
            _("Payment"): payment_status,
        },
    )

    if booking_model is None:
        return module_missing(title, _("Bookings"), shown)

    bookings = apply_period(booking_model.objects.all(), "booked_at", period)
    if status:
        bookings = bookings.filter(status=status)
    if payment_status:
        bookings = bookings.filter(payment_status=payment_status)
    bookings = bookings.order_by("-booked_at")

    columns = [
        col(_("Code"), "booking_code"),
        col(_("Booked"), "booked_at", kind=ColumnKind.DATETIME),
        col(_("Customer"), "customer"),
        col(_("Student"), "student"),
        col(_("Type"), "booking_type", choice=True),
        col(_("Status"), "status", choice=True),
        col(_("People"), "participants", kind=ColumnKind.NUMBER),
        col(_("Total"), "total_amount", kind=ColumnKind.MONEY),
        col(_("Paid"), "paid_amount", kind=ColumnKind.MONEY),
        col(_("Payment"), "payment_status", choice=True),
        col(_("Source"), "source", choice=True),
    ]

    total = sum_field(bookings, "total_amount")
    paid = sum_field(bookings, "paid_amount")
    participants = bookings.aggregate(total=Coalesce(Sum("participants"), 0))["total"] or 0
    summary = {
        str(_("Bookings")): bookings.count(),
        str(_("Participants")): participants,
        str(_("Confirmed")): bookings.filter(status__in=ACTIVE_BOOKING_STATUSES).count(),
        str(_("Cancelled")): bookings.filter(status=BookingStatus.CANCELLED).count(),
        str(_("Total value")): (total, ColumnKind.MONEY),
        str(_("Paid")): (paid, ColumnKind.MONEY),
        str(_("Outstanding")): (total - paid, ColumnKind.MONEY),
    }

    return build_table(
        bookings,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "cancellations_report",
    title=_("Cancellations and no-shows"),
    description=_(
        "What was lost and why. Cancellation fees charged are shown against the "
        "value of the booking that was given up."
    ),
    area=ReportArea.OPERATIONS,
    capability="bookings.view",
    icon="circle-x",
    filter_fields=("period",),
)
def cancellations_report(user, filters: Mapping) -> ReportData:
    booking_model = get_model("bookings.Booking")
    period = resolve_period(filters)
    title = _("Cancellations and no-shows")
    shown = period_filters(period)

    if booking_model is None:
        return module_missing(title, _("Bookings"), shown)

    lost_statuses = (BookingStatus.CANCELLED, BookingStatus.NO_SHOW)
    cancelled = booking_model.objects.filter(status__in=lost_statuses)
    if period.start and period.end:
        # A no-show is never "cancelled at" anything, so those rows are dated by
        # the booking itself; otherwise every no-show would drop out of the report.
        cancelled = cancelled.filter(
            Q(cancelled_at__gte=period.start, cancelled_at__lte=period.end)
            | Q(
                cancelled_at__isnull=True,
                booked_at__gte=period.start,
                booked_at__lte=period.end,
            )
        )
    cancelled = cancelled.order_by("-cancelled_at", "-booked_at")

    columns = [
        col(_("Code"), "booking_code"),
        col(_("Cancelled"), "cancelled_at", kind=ColumnKind.DATETIME),
        col(_("Booked"), "booked_at", kind=ColumnKind.DATETIME),
        col(_("Customer"), "customer"),
        col(_("Status"), "status", choice=True),
        col(_("People"), "participants", kind=ColumnKind.NUMBER),
        col(_("Booking value"), "total_amount", kind=ColumnKind.MONEY),
        col(_("Fee charged"), "cancellation_fee", kind=ColumnKind.MONEY),
        col(_("Reason"), "cancellation_reason"),
    ]

    all_in_period = apply_period(booking_model.objects.all(), "booked_at", period)
    total_bookings = all_in_period.count()
    lost_value = sum_field(cancelled, "total_amount")
    fees = sum_field(cancelled, "cancellation_fee")
    cancelled_count = cancelled.count()
    rate = (
        Decimal(cancelled_count) / Decimal(total_bookings) * 100 if total_bookings else ZERO
    )

    summary = {
        str(_("Cancelled")): cancelled.filter(status=BookingStatus.CANCELLED).count(),
        str(_("No-shows")): cancelled.filter(status=BookingStatus.NO_SHOW).count(),
        str(_("Bookings taken in period")): total_bookings,
        str(_("Cancellation rate")): (rate, ColumnKind.PERCENT),
        str(_("Value given up")): (lost_value, ColumnKind.MONEY),
        str(_("Fees charged")): (fees, ColumnKind.MONEY),
        str(_("Net loss")): (lost_value - fees, ColumnKind.MONEY),
    }

    return build_table(
        cancelled,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


# ===========================================================================
# Finance
# ===========================================================================
@report(
    "revenue_report",
    title=_("Revenue"),
    description=_("Invoices raised in the period, what they were worth and what has been settled."),
    area=ReportArea.FINANCE,
    # ``finance.view`` is granted to reception and rental staff so they can look
    # up one customer's balance. A whole revenue ledger is a different thing, so
    # the statement-level reports require the export capability instead.
    capability="finance.export",
    icon="banknote",
    filter_fields=("period", "payment_status"),
)
def revenue_report(user, filters: Mapping) -> ReportData:
    invoice_model = get_model("finance.Invoice")
    period = resolve_period(filters)
    title = _("Revenue")
    shown = period_filters(period)

    if invoice_model is None:
        return module_missing(title, _("Finance"), shown)

    date_path = first_path(invoice_model, "issue_date", "issued_on", "date", "created_at")
    total_path = first_path(invoice_model, "total_amount", "total", "grand_total")
    paid_path = first_path(invoice_model, "paid_amount", "amount_paid", "paid")
    status_path = first_path(invoice_model, "payment_status", "status")

    invoices = apply_period(invoice_model.objects.all(), date_path, period)
    payment_status = filter_str(filters, "payment_status")
    if payment_status and status_path:
        invoices = invoices.filter(**{status_path: payment_status})
    if date_path:
        invoices = invoices.order_by(f"-{date_path}")

    columns = [
        col(_("Invoice"), "invoice_number", "invoice_code", "number", "code"),
        col(_("Date"), "issue_date", "issued_on", "date", "created_at", kind=ColumnKind.DATE),
        col(_("Due"), "due_date", "due_on", kind=ColumnKind.DATE),
        col(_("Customer"), "customer"),
        col(_("Net"), "subtotal", "net_amount", kind=ColumnKind.MONEY),
        col(_("Tax"), "tax_amount", "vat_amount", kind=ColumnKind.MONEY),
        col(_("Total"), "total_amount", "total", "grand_total", kind=ColumnKind.MONEY),
        col(_("Paid"), "paid_amount", "amount_paid", "paid", kind=ColumnKind.MONEY),
        col(_("Status"), "payment_status", "status", choice=True),
    ]

    total = sum_field(invoices, total_path)
    paid = sum_field(invoices, paid_path)
    count = invoices.count()
    summary = {
        str(_("Invoices")): count,
        str(_("Invoiced")): (total, ColumnKind.MONEY),
        str(_("Collected")): (paid, ColumnKind.MONEY),
        str(_("Outstanding")): (total - paid, ColumnKind.MONEY),
        str(_("Average invoice")): (
            (total / count).quantize(Decimal("0.01")) if count else ZERO,
            ColumnKind.MONEY,
        ),
    }

    return build_table(
        invoices,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "payments_report",
    title=_("Payments received"),
    description=_("Money actually taken in, by date and payment method — the till reconciliation."),
    area=ReportArea.FINANCE,
    capability="finance.export",
    icon="credit-card",
    filter_fields=("period", "payment_method"),
)
def payments_report(user, filters: Mapping) -> ReportData:
    payment_model = get_model("finance.Payment")
    period = resolve_period(filters)
    title = _("Payments received")
    method = filter_str(filters, "payment_method")
    shown = period_filters(period, {_("Method"): method})

    if payment_model is None:
        return module_missing(title, _("Finance"), shown)

    date_path = first_path(
        payment_model, "paid_at", "received_at", "payment_date", "date", "created_at"
    )
    amount_path = first_path(payment_model, "amount", "amount_paid", "total_amount")
    method_path = first_path(payment_model, "method", "payment_method")

    payments = apply_period(payment_model.objects.all(), date_path, period)
    if method and method_path:
        payments = payments.filter(**{method_path: method})
    if date_path:
        payments = payments.order_by(f"-{date_path}")

    columns = [
        col(_("Date"), "paid_at", "received_at", "payment_date", "date", "created_at",
            kind=ColumnKind.DATETIME),
        col(_("Reference"), "payment_code", "reference", "code", "transaction_id"),
        col(_("Customer"), "customer", "invoice__customer"),
        col(_("Invoice"), "invoice"),
        col(_("Method"), "method", "payment_method", choice=True),
        col(_("Amount"), "amount", "amount_paid", "total_amount", kind=ColumnKind.MONEY),
        col(_("Status"), "status", choice=True),
        col(_("Taken by"), "received_by", "created_by"),
    ]

    total = sum_field(payments, amount_path)
    summary = {
        str(_("Payments")): payments.count(),
        str(_("Total received")): (total, ColumnKind.MONEY),
    }
    if method_path and amount_path:
        breakdown = (
            payments.values(method_path)
            .annotate(total=Coalesce(Sum(amount_path), Value(ZERO), output_field=DecimalField()))
            .order_by("-total")[:8]
        )
        labels = dict(terminal_field(payment_model, method_path).choices or ())
        for entry in breakdown:
            key = entry[method_path]
            summary[str(labels.get(key, key))] = (entry["total"], ColumnKind.MONEY)

    return build_table(
        payments,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "expenses_report",
    title=_("Expenses"),
    description=_("What the school spent in the period, grouped by expense category."),
    area=ReportArea.FINANCE,
    capability="finance.export",
    icon="receipt",
    filter_fields=("period",),
)
def expenses_report(user, filters: Mapping) -> ReportData:
    expense_model = get_model("finance.Expense")
    period = resolve_period(filters)
    title = _("Expenses")
    shown = period_filters(period)

    if expense_model is None:
        return module_missing(title, _("Finance"), shown)

    date_path = first_path(expense_model, "expense_date", "incurred_on", "date", "created_at")
    amount_path = first_path(expense_model, "amount", "total_amount", "gross_amount")

    expenses = apply_period(expense_model.objects.all(), date_path, period)
    if date_path:
        expenses = expenses.order_by(f"-{date_path}")

    columns = [
        col(_("Date"), "expense_date", "incurred_on", "date", "created_at", kind=ColumnKind.DATE),
        col(_("Category"), "category"),
        col(_("Supplier"), "vendor", "supplier", "payee"),
        col(_("Description"), "description", "note", "notes"),
        col(_("Method"), "payment_method", "method", choice=True),
        col(_("Amount"), "amount", "total_amount", "gross_amount", kind=ColumnKind.MONEY),
    ]

    total = sum_field(expenses, amount_path)
    summary = {
        str(_("Entries")): expenses.count(),
        str(_("Total spent")): (total, ColumnKind.MONEY),
    }
    if amount_path and field_path_exists(expense_model, "category"):
        breakdown = (
            expenses.values("category__name")
            .annotate(total=Coalesce(Sum(amount_path), Value(ZERO), output_field=DecimalField()))
            .order_by("-total")[:10]
        )
        for entry in breakdown:
            name = entry["category__name"] or str(_("Uncategorised"))
            summary[str(name)] = (entry["total"], ColumnKind.MONEY)

    return build_table(
        expenses,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
    )


@report(
    "profit_loss",
    title=_("Profit and loss"),
    description=_(
        "Income against expenses for the period, with the net result and margin. "
        "Income counts money received, not money invoiced."
    ),
    area=ReportArea.FINANCE,
    capability="finance.export",
    icon="chart-line",
    filter_fields=("period",),
)
def profit_loss(user, filters: Mapping) -> ReportData:
    period = resolve_period(filters)
    title = _("Profit and loss")
    shown = period_filters(period)

    payment_model = get_model("finance.Payment")
    expense_model = get_model("finance.Expense")
    if payment_model is None and expense_model is None:
        return module_missing(title, _("Finance"), shown)

    rows: list[list[Any]] = []
    income_total = ZERO
    expense_total = ZERO

    if payment_model is not None:
        date_path = first_path(
            payment_model, "paid_at", "received_at", "payment_date", "date", "created_at"
        )
        amount_path = first_path(payment_model, "amount", "amount_paid", "total_amount")
        method_path = first_path(payment_model, "method", "payment_method")
        payments = apply_period(payment_model.objects.all(), date_path, period)
        income_total = sum_field(payments, amount_path)

        if method_path and amount_path:
            labels = dict(terminal_field(payment_model, method_path).choices or ())
            for entry in (
                payments.values(method_path)
                .annotate(
                    total=Coalesce(Sum(amount_path), Value(ZERO), output_field=DecimalField())
                )
                .order_by("-total")
            ):
                key = entry[method_path]
                rows.append([str(_("Income")), str(labels.get(key, key)), entry["total"]])
        elif income_total:
            rows.append([str(_("Income")), str(_("Payments received")), income_total])

    if expense_model is not None:
        date_path = first_path(expense_model, "expense_date", "incurred_on", "date", "created_at")
        amount_path = first_path(expense_model, "amount", "total_amount", "gross_amount")
        expenses = apply_period(expense_model.objects.all(), date_path, period)
        expense_total = sum_field(expenses, amount_path)

        if amount_path and field_path_exists(expense_model, "category"):
            for entry in (
                expenses.values("category__name")
                .annotate(
                    total=Coalesce(Sum(amount_path), Value(ZERO), output_field=DecimalField())
                )
                .order_by("-total")
            ):
                name = entry["category__name"] or str(_("Uncategorised"))
                rows.append([str(_("Expense")), str(name), entry["total"]])
        elif expense_total:
            rows.append([str(_("Expense")), str(_("Total expenses")), expense_total])

    net = income_total - expense_total
    rows.append([str(_("Result")), str(_("Net result")), net])

    margin = (net / income_total * 100).quantize(Decimal("0.1")) if income_total else ZERO
    summary = {
        str(_("Total income")): (income_total, ColumnKind.MONEY),
        str(_("Total expenses")): (expense_total, ColumnKind.MONEY),
        str(_("Net result")): (net, ColumnKind.MONEY),
        str(_("Margin")): (margin, ColumnKind.PERCENT),
    }

    # Repair spend is recorded in the maintenance module. It is shown as a memo
    # rather than an expense line: double-counting it once finance also books
    # the invoice would silently overstate costs.
    maintenance_model = get_model("maintenance.MaintenanceRecord")
    if maintenance_model is not None:
        jobs = apply_period(maintenance_model.objects.all(), "completed_at", period)
        summary[str(_("Memo: maintenance work completed"))] = (
            sum_field(jobs, "total_cost"),
            ColumnKind.MONEY,
        )

    message = ""
    if not rows or (income_total == ZERO and expense_total == ZERO):
        message = str(_("No payments or expenses were recorded in this period."))

    return ReportData(
        title=str(title),
        subtitle=period.label,
        columns=[str(_("Section")), str(_("Item")), str(_("Amount"))],
        column_kinds=[ColumnKind.TEXT, ColumnKind.TEXT, ColumnKind.MONEY],
        rows=rows,
        summary=summary,
        filters=shown,
        message=message,
    )


@report(
    "instructor_commission",
    title=_("Instructor commission"),
    description=_("Commission earned per instructor in the period, and what is still owed."),
    area=ReportArea.FINANCE,
    capability="instructors.view_commission",
    icon="percent",
    filter_fields=("period", "instructor"),
)
def instructor_commission(user, filters: Mapping) -> ReportData:
    commission_model = get_model("finance.CommissionRecord")
    period = resolve_period(filters)
    title = _("Instructor commission")
    instructor_id = filter_int(filters, "instructor")
    shown = period_filters(period, {_("Instructor"): _instructor_label(instructor_id)})

    if commission_model is None:
        return module_missing(title, _("Finance"), shown)

    date_path = first_path(
        commission_model, "earned_on", "period_end", "date", "calculated_at", "created_at"
    )
    amount_path = first_path(commission_model, "amount", "commission_amount", "total_amount")

    records = apply_period(commission_model.objects.all(), date_path, period)
    if instructor_id and field_path_exists(commission_model, "instructor"):
        records = records.filter(instructor_id=instructor_id)
    if date_path:
        records = records.order_by("instructor_id", f"-{date_path}")

    columns = [
        col(_("Date"), "earned_on", "period_end", "date", "calculated_at", "created_at",
            kind=ColumnKind.DATE),
        col(_("Instructor"), "instructor"),
        col(_("Source"), "lesson", "booking", "description", "note"),
        col(_("Base amount"), "base_amount", "gross_amount", "revenue_amount",
            kind=ColumnKind.MONEY),
        col(_("Rate"), "commission_percent", "percent", "rate", kind=ColumnKind.PERCENT),
        col(_("Commission"), "amount", "commission_amount", "total_amount", kind=ColumnKind.MONEY),
        col(_("Status"), "status", choice=True),
        col(_("Paid"), "is_paid", "paid", kind=ColumnKind.BOOLEAN),
        col(_("Paid on"), "paid_on", "paid_at", kind=ColumnKind.DATE),
    ]

    total = sum_field(records, amount_path)
    paid_flag = first_path(commission_model, "is_paid", "paid")
    paid_total = sum_field(records.filter(**{paid_flag: True}), amount_path) if paid_flag else ZERO
    summary = {
        str(_("Commission records")): records.count(),
        str(_("Instructors")): records.values("instructor_id").distinct().count()
        if field_path_exists(commission_model, "instructor")
        else 0,
        str(_("Total commission")): (total, ColumnKind.MONEY),
        str(_("Paid out")): (paid_total, ColumnKind.MONEY),
        str(_("Still owed")): (total - paid_total, ColumnKind.MONEY),
    }

    return build_table(
        records,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


# ===========================================================================
# People
# ===========================================================================
@report(
    "student_list",
    title=_("Student register"),
    description=_("Every student on the books, their level, swimming ability and lesson history."),
    area=ReportArea.PEOPLE,
    capability="students.view",
    icon="graduation-cap",
    filter_fields=("period", "level", "include_inactive"),
    default_filters={"range": "all"},
)
def student_list(user, filters: Mapping) -> ReportData:
    student_model = get_model("students.Student")
    period = resolve_period(filters, default="all")
    title = _("Student register")
    level = filter_str(filters, "level")
    include_inactive = filter_bool(filters, "include_inactive")
    shown = period_filters(
        period,
        {
            _("Level"): dict(SurfLevel.choices).get(level, ""),
            _("Includes inactive"): include_inactive,
        },
    )

    if student_model is None:
        return module_missing(title, _("Students"), shown)

    students = apply_period(student_model.objects.all(), "joined_at", period)
    if not include_inactive:
        students = students.filter(is_active=True)
    if level:
        students = students.filter(surf_level=level)
    students = students.order_by("customer__last_name", "customer__first_name")

    columns = [
        col(_("Code"), "student_code"),
        col(_("Student"), "customer"),
        col(_("Level"), "surf_level", choice=True),
        col(_("Can swim"), "can_swim", kind=ColumnKind.BOOLEAN),
        col(_("Swim distance (m)"), "swim_distance_m", kind=ColumnKind.NUMBER),
        col(_("Weight (kg)"), "weight_kg", kind=ColumnKind.NUMBER),
        col(_("Wetsuit"), "wetsuit_size"),
        col(_("Lessons"), "total_lessons", kind=ColumnKind.NUMBER),
        col(_("Hours"), "total_hours", kind=ColumnKind.NUMBER),
        col(_("Last lesson"), "last_lesson_date", kind=ColumnKind.DATE),
        col(_("Joined"), "joined_at", kind=ColumnKind.DATE),
        col(_("Active"), "is_active", kind=ColumnKind.BOOLEAN),
    ]

    level_labels = dict(SurfLevel.choices)
    summary = {
        str(_("Students")): students.count(),
        str(_("Non-swimmers")): students.filter(can_swim=False).count(),
    }
    for entry in students.values("surf_level").annotate(total=Count("id")).order_by("-total"):
        summary[str(level_labels.get(entry["surf_level"], entry["surf_level"]))] = entry["total"]

    return build_table(
        students,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "student_progress",
    title=_("Student progress"),
    description=_("Skill assessments in the period: scores, level changes and the next focus."),
    area=ReportArea.PEOPLE,
    capability="students.view",
    icon="trending-up",
    filter_fields=("period", "instructor"),
)
def student_progress(user, filters: Mapping) -> ReportData:
    assessment_model = get_model("students.SkillAssessment")
    period = resolve_period(filters)
    title = _("Student progress")
    instructor_id = filter_int(filters, "instructor")
    shown = period_filters(period, {_("Instructor"): _instructor_label(instructor_id)})

    if assessment_model is None:
        return module_missing(title, _("Students"), shown)

    assessments = apply_period(assessment_model.objects.all(), "assessed_on", period)
    if instructor_id:
        assessments = assessments.filter(instructor_id=instructor_id)
    assessments = assessments.order_by("-assessed_on", "student_id")

    columns = [
        col(_("Date"), "assessed_on", kind=ColumnKind.DATE),
        col(_("Student"), "student"),
        col(_("Instructor"), "instructor"),
        col(_("Level before"), "level_before", choice=True),
        col(_("Level after"), "level_after", choice=True),
        col(_("Paddling"), "paddling", kind=ColumnKind.NUMBER),
        col(_("Pop-up"), "popup", kind=ColumnKind.NUMBER),
        col(_("Positioning"), "positioning", kind=ColumnKind.NUMBER),
        col(_("Wave reading"), "wave_reading", kind=ColumnKind.NUMBER),
        col(_("Safety"), "safety", kind=ColumnKind.NUMBER),
        col(_("Next focus"), "next_focus"),
    ]

    level_ups = sum(
        1
        for before, after in assessments.values_list("level_before", "level_after")
        if before and after and before != after
    )
    averages = assessments.aggregate(
        paddling=Avg("paddling"),
        popup=Avg("popup"),
        positioning=Avg("positioning"),
        wave_reading=Avg("wave_reading"),
        safety=Avg("safety"),
    )
    scores = [value for value in averages.values() if value is not None]
    summary = {
        str(_("Assessments")): assessments.count(),
        str(_("Students assessed")): assessments.values("student_id").distinct().count(),
        str(_("Level changes")): level_ups,
        str(_("Average score")): (
            Decimal(str(round(sum(scores) / len(scores), 2))) if scores else ZERO,
            ColumnKind.NUMBER,
        ),
        str(_("Average safety score")): (
            Decimal(str(round(averages["safety"], 2))) if averages["safety"] else ZERO,
            ColumnKind.NUMBER,
        ),
    }

    return build_table(
        assessments,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "instructor_performance",
    title=_("Instructor performance"),
    description=_(
        "Lessons taught, students coached, hours in the water and student ratings "
        "for each instructor over the period."
    ),
    area=ReportArea.PEOPLE,
    capability="instructors.view",
    icon="user-check",
    filter_fields=("period", "include_inactive"),
)
def instructor_performance(user, filters: Mapping) -> ReportData:
    instructor_model = get_model("instructors.Instructor")
    lesson_model = get_model("lessons.Lesson")
    period = resolve_period(filters)
    title = _("Instructor performance")
    include_inactive = filter_bool(filters, "include_inactive")
    shown = period_filters(period, {_("Includes inactive"): include_inactive})

    if instructor_model is None:
        return module_missing(title, _("Instructors"), shown)

    instructors = instructor_model.objects.all()
    if not include_inactive:
        instructors = instructors.filter(is_active=True)
    instructors = instructors.select_related("user").order_by("user__last_name", "instructor_code")

    lesson_counts: dict[int, int] = {}
    minutes: dict[int, int] = {}
    students: dict[int, int] = {}
    ratings: dict[int, float] = {}

    if lesson_model is not None:
        lessons = apply_period(
            lesson_model.objects.filter(status=LessonStatus.COMPLETED), "date", period
        )
        for instructor_id, start_time, end_time in lessons.values_list(
            "instructor_id", "start_time", "end_time"
        )[:MAX_SCAN_ROWS]:
            lesson_counts[instructor_id] = lesson_counts.get(instructor_id, 0) + 1
            minutes[instructor_id] = minutes.get(instructor_id, 0) + _minutes_between(
                start_time, end_time
            )

        attendance_model = get_model("lessons.LessonAttendance")
        if attendance_model is not None:
            attendance = attendance_model.objects.filter(lesson__in=lessons)
            for entry in attendance.values("lesson__instructor_id").annotate(
                total=Count("id"), rating=Avg("rating")
            ):
                key = entry["lesson__instructor_id"]
                students[key] = entry["total"]
                if entry["rating"] is not None:
                    ratings[key] = round(float(entry["rating"]), 2)

    rows: list[list[Any]] = []
    for instructor in instructors[:MAX_ROWS]:
        taught = lesson_counts.get(instructor.pk, 0)
        rows.append(
            [
                instructor.instructor_code,
                str(instructor),
                instructor.get_max_level_taught_display()
                if hasattr(instructor, "get_max_level_taught_display")
                else "",
                taught,
                students.get(instructor.pk, 0),
                Decimal(str(round(minutes.get(instructor.pk, 0) / 60, 2))),
                ratings.get(instructor.pk),
                instructor.rating_average,
                instructor.commission_percent,
                instructor.is_active,
            ]
        )
    rows.sort(key=lambda row: (-row[3], row[1]))

    total_lessons = sum(lesson_counts.values())
    total_students = sum(students.values())
    total_hours = Decimal(str(round(sum(minutes.values()) / 60, 2)))
    active_count = sum(1 for row in rows if row[3])
    summary = {
        str(_("Instructors listed")): len(rows),
        str(_("Instructors who taught")): active_count,
        str(_("Lessons taught")): total_lessons,
        str(_("Student places coached")): total_students,
        str(_("Hours in the water")): (total_hours, ColumnKind.NUMBER),
        str(_("Lessons per active instructor")): (
            Decimal(str(round(total_lessons / active_count, 2))) if active_count else ZERO,
            ColumnKind.NUMBER,
        ),
    }

    return ReportData(
        title=str(title),
        subtitle=period.label,
        columns=[
            str(_("Code")),
            str(_("Instructor")),
            str(_("Teaches up to")),
            str(_("Lessons")),
            str(_("Students")),
            str(_("Hours")),
            str(_("Rating (period)")),
            str(_("Rating (lifetime)")),
            str(_("Commission")),
            str(_("Active")),
        ],
        column_kinds=[
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.PERCENT,
            ColumnKind.BOOLEAN,
        ],
        rows=rows,
        summary=summary,
        filters=shown,
        orientation=LANDSCAPE,
        message=str(_("No instructors match this filter.")) if not rows else "",
    )


@report(
    "customer_list",
    title=_("Customer list"),
    description=_(
        "Customer contact details and value. Marketing consent is shown for every "
        "row — rows without consent may not be used for campaigns."
    ),
    area=ReportArea.PEOPLE,
    capability="customers.export",
    icon="users",
    filter_fields=("period", "marketing_only", "include_inactive"),
    default_filters={"range": "all"},
)
def customer_list(user, filters: Mapping) -> ReportData:
    customer_model = get_model("customers.Customer")
    period = resolve_period(filters, default="all")
    title = _("Customer list")
    marketing_only = filter_bool(filters, "marketing_only")
    include_inactive = filter_bool(filters, "include_inactive")
    shown = period_filters(
        period,
        {
            _("Consented to marketing only"): marketing_only,
            _("Includes inactive"): include_inactive,
        },
    )

    if customer_model is None:
        return module_missing(title, _("Customers"), shown)

    customers = apply_period(customer_model.objects.all(), "created_at", period)
    if not include_inactive:
        customers = customers.filter(is_active=True)

    total_in_scope = customers.count()
    consented_in_scope = customers.filter(marketing_consent=True).count()

    if marketing_only:
        customers = customers.filter(marketing_consent=True)
    customers = customers.order_by("last_name", "first_name")

    columns = [
        col(_("Code"), "customer_code"),
        col(_("First name"), "first_name"),
        col(_("Last name"), "last_name"),
        col(_("E-mail"), "email"),
        col(_("Phone"), "phone"),
        col(_("City"), "city"),
        col(_("Country"), "country"),
        col(_("Language"), "preferred_language", choice=True),
        col(_("Source"), "source", choice=True),
        # Never omitted: an export without this column invites unlawful use.
        col(_("Marketing consent"), "marketing_consent", kind=ColumnKind.BOOLEAN),
        col(_("Consent given"), "marketing_consent_at", kind=ColumnKind.DATETIME),
        col(_("Bookings"), "total_bookings", kind=ColumnKind.NUMBER),
        col(_("Lifetime value"), "lifetime_value", kind=ColumnKind.MONEY),
        col(_("Last visit"), "last_visit_date", kind=ColumnKind.DATE),
        col(_("Active"), "is_active", kind=ColumnKind.BOOLEAN),
    ]

    without_consent = total_in_scope - consented_in_scope
    summary = {
        str(_("Customers exported")): customers.count(),
        str(_("With marketing consent")): consented_in_scope,
        str(_("Without marketing consent")): without_consent,
        str(_("Lifetime value")): (sum_field(customers, "lifetime_value"), ColumnKind.MONEY),
    }

    if marketing_only:
        message = str(
            _("Filtered to customers who gave marketing consent. %(count)s customers were "
              "excluded because they have not consented.")
            % {"count": without_consent}
        )
    else:
        message = str(
            _("This export contains %(count)s customers who have NOT consented to marketing. "
              "Their contact details may be used for their own bookings only, never for "
              "campaigns.")
            % {"count": without_consent}
        )

    return build_table(
        customers,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
        message=message,
    )


# ===========================================================================
# Equipment & rentals
# ===========================================================================
@report(
    "equipment_inventory",
    title=_("Equipment inventory"),
    description=_("The full asset register with condition, value and where each item lives."),
    area=ReportArea.EQUIPMENT,
    capability="equipment.view",
    icon="package",
    filter_fields=("equipment_status", "equipment_category"),
    default_filters={"range": "all"},
)
def equipment_inventory(user, filters: Mapping) -> ReportData:
    equipment_model = get_model("equipment.Equipment")
    title = _("Equipment inventory")
    status = filter_str(filters, "equipment_status")
    category_id = filter_int(filters, "equipment_category")
    shown = {
        str(_("Status")): dict(EquipmentStatus.choices).get(status, str(_("All"))),
        str(_("Category")): _category_label(category_id),
    }

    if equipment_model is None:
        return module_missing(title, _("Equipment"), shown)

    items = equipment_model.objects.all()
    if status:
        items = items.filter(status=status)
    if category_id:
        items = items.filter(category_id=category_id)
    items = items.order_by("category__sort_order", "category__name", "asset_code")

    columns = [
        col(_("Asset code"), "asset_code"),
        col(_("Name"), "name"),
        col(_("Category"), "category__name"),
        col(_("Brand"), "brand"),
        col(_("Size"), "size_label"),
        col(_("Status"), "status", choice=True),
        col(_("Condition"), "condition", choice=True),
        col(_("Purchased"), "purchase_date", kind=ColumnKind.DATE),
        col(_("Purchase price"), "purchase_price", kind=ColumnKind.MONEY),
        col(_("Current value"), "current_value", kind=ColumnKind.MONEY),
        col(_("Storage"), "storage_location"),
        col(_("Rentable"), "is_rentable", kind=ColumnKind.BOOLEAN),
        col(_("Next service"), "next_maintenance_date", kind=ColumnKind.DATE),
    ]

    status_labels = dict(EquipmentStatus.choices)
    summary = {
        str(_("Items")): items.count(),
        str(_("Purchase value")): (sum_field(items, "purchase_price"), ColumnKind.MONEY),
        str(_("Current value")): (sum_field(items, "current_value"), ColumnKind.MONEY),
    }
    for entry in items.values("status").annotate(total=Count("id")).order_by("-total"):
        summary[str(status_labels.get(entry["status"], entry["status"]))] = entry["total"]

    return build_table(
        items,
        columns,
        title=title,
        subtitle=_("As at %(when)s") % {"when": timezone.localdate()},
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "equipment_utilisation",
    title=_("Equipment utilisation"),
    description=_(
        "How hard each item worked in the period: times hired, hours out, revenue "
        "earned and lesson assignments. Idle stock and bottlenecks both show here."
    ),
    area=ReportArea.EQUIPMENT,
    capability="equipment.view",
    icon="activity",
    filter_fields=("period", "equipment_category"),
)
def equipment_utilisation(user, filters: Mapping) -> ReportData:
    equipment_model = get_model("equipment.Equipment")
    period = resolve_period(filters)
    title = _("Equipment utilisation")
    category_id = filter_int(filters, "equipment_category")
    shown = period_filters(period, {_("Category"): _category_label(category_id)})

    if equipment_model is None:
        return module_missing(title, _("Equipment"), shown)

    items = equipment_model.objects.select_related("category")
    if category_id:
        items = items.filter(category_id=category_id)
    items = items.order_by("category__name", "asset_code")

    hires: dict[int, int] = {}
    hours_out: dict[int, Decimal] = {}
    revenue: dict[int, Decimal] = {}

    rental_item_model = get_model("rentals.RentalItem")
    if rental_item_model is not None and period.start and period.end:
        lines = (
            rental_item_model.objects.filter(
                rental__start_at__lte=period.end
            )
            .filter(
                Q(rental__returned_at__gte=period.start)
                | Q(rental__returned_at__isnull=True, rental__expected_return_at__gte=period.start)
            )
            .select_related("rental")
        )
        for line in lines[:MAX_SCAN_ROWS]:
            rental = line.rental
            equipment_id = line.equipment_id
            hires[equipment_id] = hires.get(equipment_id, 0) + 1
            revenue[equipment_id] = revenue.get(equipment_id, ZERO) + (line.line_total or ZERO)
            hours_out[equipment_id] = hours_out.get(equipment_id, ZERO) + _overlap_hours(
                rental.start_at,
                rental.returned_at or rental.expected_return_at,
                period.start,
                period.end,
            )

    lesson_uses: dict[int, int] = {}
    attendance_model = get_model("lessons.LessonAttendance")
    if attendance_model is not None:
        attendance = apply_period(attendance_model.objects.all(), "lesson__date", period)
        for name in ("assigned_board", "assigned_wetsuit"):
            if not field_path_exists(attendance_model, name):
                continue
            column = f"{name}_id"
            for entry in (
                attendance.filter(**{f"{name}__isnull": False})
                .values(column)
                .annotate(total=Count("id"))
            ):
                key = entry[column]
                lesson_uses[key] = lesson_uses.get(key, 0) + entry["total"]

    period_hours = period.hours or Decimal("1")
    rows: list[list[Any]] = []
    for item in items[:MAX_ROWS]:
        out = hours_out.get(item.pk, ZERO)
        utilisation = (out / period_hours * 100).quantize(Decimal("0.1")) if period_hours else ZERO
        rows.append(
            [
                item.asset_code,
                item.name,
                item.category.name if item.category_id else "",
                item.get_status_display(),
                hires.get(item.pk, 0),
                out.quantize(Decimal("0.1")),
                utilisation,
                lesson_uses.get(item.pk, 0),
                revenue.get(item.pk, ZERO),
                item.total_rentals,
            ]
        )
    rows.sort(key=lambda row: (-row[6], -row[4], row[0]))

    used = sum(1 for row in rows if row[4] or row[7])
    summary = {
        str(_("Items in scope")): len(rows),
        str(_("Items used")): used,
        str(_("Items never used in period")): len(rows) - used,
        str(_("Hire lines")): sum(hires.values()),
        str(_("Hire revenue")): (sum(revenue.values(), ZERO), ColumnKind.MONEY),
        str(_("Lesson assignments")): sum(lesson_uses.values()),
        str(_("Average utilisation")): (
            Decimal(str(round(sum(float(row[6]) for row in rows) / len(rows), 1)))
            if rows
            else ZERO,
            ColumnKind.PERCENT,
        ),
    }

    return ReportData(
        title=str(title),
        subtitle=period.label,
        columns=[
            str(_("Asset code")),
            str(_("Name")),
            str(_("Category")),
            str(_("Status")),
            str(_("Times hired")),
            str(_("Hours out")),
            str(_("Utilisation")),
            str(_("Lesson uses")),
            str(_("Hire revenue")),
            str(_("Hires (lifetime)")),
        ],
        column_kinds=[
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.PERCENT,
            ColumnKind.NUMBER,
            ColumnKind.MONEY,
            ColumnKind.NUMBER,
        ],
        rows=rows,
        summary=summary,
        filters=shown,
        orientation=LANDSCAPE,
        message=str(_("No equipment matches this filter.")) if not rows else "",
    )


@report(
    "maintenance_report",
    title=_("Maintenance"),
    description=_("Repairs reported in the period, what they cost and how long they took."),
    area=ReportArea.EQUIPMENT,
    capability="maintenance.view",
    icon="wrench",
    filter_fields=("period", "maintenance_status", "severity"),
)
def maintenance_report(user, filters: Mapping) -> ReportData:
    record_model = get_model("maintenance.MaintenanceRecord")
    period = resolve_period(filters)
    title = _("Maintenance")
    status = filter_str(filters, "maintenance_status")
    severity = filter_str(filters, "severity")
    shown = period_filters(
        period,
        {
            _("Status"): dict(GenericStatus.choices).get(status, ""),
            _("Severity"): dict(Severity.choices).get(severity, ""),
        },
    )

    if record_model is None:
        return module_missing(title, _("Maintenance"), shown)

    records = apply_period(record_model.objects.all(), "reported_at", period)
    if status:
        records = records.filter(status=status)
    if severity:
        records = records.filter(severity=severity)
    records = records.order_by("-reported_at")

    columns = [
        col(_("Record"), "record_code"),
        col(_("Reported"), "reported_at", kind=ColumnKind.DATETIME),
        col(_("Equipment"), "equipment__asset_code"),
        col(_("Item"), "equipment__name"),
        col(_("Damage"), "damage_type", choice=True),
        col(_("Severity"), "severity", choice=True),
        col(_("Status"), "status", choice=True),
        col(_("Assigned to"), "assigned_to"),
        col(_("Completed"), "completed_at", kind=ColumnKind.DATETIME),
        col(_("Labour (h)"), "labour_hours", kind=ColumnKind.NUMBER),
        col(_("Parts"), "parts_cost", kind=ColumnKind.MONEY),
        col(_("Labour"), "labour_cost", kind=ColumnKind.MONEY),
        col(_("Total cost"), "total_cost", kind=ColumnKind.MONEY),
    ]

    open_statuses = (GenericStatus.OPEN, GenericStatus.IN_PROGRESS, GenericStatus.ON_HOLD)
    completed = records.filter(completed_at__isnull=False)
    turnarounds = [
        (finished - reported).total_seconds() / 86400
        for reported, finished in completed.values_list("reported_at", "completed_at")[:MAX_SCAN_ROWS]
        if reported and finished
    ]
    summary = {
        str(_("Records")): records.count(),
        str(_("Still open")): records.filter(status__in=open_statuses).count(),
        str(_("Completed")): completed.count(),
        str(_("Made item unusable")): records.filter(made_unusable=True).count(),
        str(_("Parts cost")): (sum_field(records, "parts_cost"), ColumnKind.MONEY),
        str(_("Labour cost")): (sum_field(records, "labour_cost"), ColumnKind.MONEY),
        str(_("Total cost")): (sum_field(records, "total_cost"), ColumnKind.MONEY),
        str(_("Average turnaround (days)")): (
            Decimal(str(round(sum(turnarounds) / len(turnarounds), 1))) if turnarounds else ZERO,
            ColumnKind.NUMBER,
        ),
    }

    return build_table(
        records,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "rental_report",
    title=_("Rentals"),
    description=_("Hire contracts in the period, including late and damage charges."),
    area=ReportArea.EQUIPMENT,
    capability="rentals.view",
    icon="arrow-left-right",
    filter_fields=("period", "rental_status"),
)
def rental_report(user, filters: Mapping) -> ReportData:
    rental_model = get_model("rentals.Rental")
    period = resolve_period(filters)
    title = _("Rentals")
    status = filter_str(filters, "rental_status")
    shown = period_filters(period, {_("Status"): status})

    if rental_model is None:
        return module_missing(title, _("Rentals"), shown)

    rentals = apply_period(rental_model.objects.all(), "start_at", period)
    if status:
        rentals = rentals.filter(status=status)
    rentals = rentals.annotate(item_count=Count("items", distinct=True)).order_by("-start_at")

    columns = [
        col(_("Code"), "rental_code"),
        col(_("Customer"), "customer"),
        col(_("Out"), "start_at", kind=ColumnKind.DATETIME),
        col(_("Due back"), "expected_return_at", kind=ColumnKind.DATETIME),
        col(_("Returned"), "returned_at", kind=ColumnKind.DATETIME),
        col(_("Items"), "item_count", kind=ColumnKind.NUMBER),
        col(_("Status"), "status", choice=True),
        col(_("Hire"), "subtotal", kind=ColumnKind.MONEY),
        col(_("Late fee"), "late_fee", kind=ColumnKind.MONEY),
        col(_("Damage"), "damage_fee", kind=ColumnKind.MONEY),
        col(_("Total"), "total_amount", kind=ColumnKind.MONEY),
        col(_("Paid"), "paid_amount", kind=ColumnKind.MONEY),
        col(_("Payment"), "payment_status", choice=True),
    ]

    total = sum_field(rentals, "total_amount")
    paid = sum_field(rentals, "paid_amount")
    summary = {
        str(_("Rentals")): rentals.count(),
        str(_("Hire revenue")): (sum_field(rentals, "subtotal"), ColumnKind.MONEY),
        str(_("Late fees")): (sum_field(rentals, "late_fee"), ColumnKind.MONEY),
        str(_("Damage charges")): (sum_field(rentals, "damage_fee"), ColumnKind.MONEY),
        str(_("Total billed")): (total, ColumnKind.MONEY),
        str(_("Collected")): (paid, ColumnKind.MONEY),
        str(_("Outstanding")): (total - paid, ColumnKind.MONEY),
        str(_("Still out")): rentals.filter(status__in=rental_model.OUT_STATUSES).count(),
    }

    return build_table(
        rentals,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
        annotations=("item_count",),
    )


@report(
    "overdue_rentals",
    title=_("Overdue rentals"),
    description=_(
        "Gear that is past its return time right now, oldest first, with the "
        "customer's phone number and the deposit still held."
    ),
    area=ReportArea.EQUIPMENT,
    capability="rentals.view",
    icon="triangle-alert",
    filter_fields=(),
)
def overdue_rentals(user, filters: Mapping) -> ReportData:
    rental_model = get_model("rentals.Rental")
    title = _("Overdue rentals")
    now = timezone.now()
    shown = {str(_("As at")): now}

    if rental_model is None:
        return module_missing(title, _("Rentals"), shown)

    overdue = (
        rental_model.objects.filter(
            returned_at__isnull=True,
            expected_return_at__lt=now,
            status__in=rental_model.OPEN_STATUSES,
        )
        .select_related("customer")
        .annotate(item_count=Count("items", distinct=True))
        .order_by("expected_return_at")
    )

    rows: list[list[Any]] = []
    for rental in overdue[:MAX_ROWS]:
        overdue_hours = (now - rental.expected_return_at).total_seconds() / 3600
        rows.append(
            [
                rental.rental_code,
                str(rental.customer),
                rental.customer.phone,
                rental.expected_return_at,
                Decimal(str(round(overdue_hours / 24, 1))),
                rental.item_count,
                rental.get_status_display(),
                rental.deposit_amount,
                rental.total_amount,
                rental.paid_amount,
            ]
        )

    at_risk = sum((row[8] for row in rows), ZERO)
    deposits = sum((row[7] for row in rows), ZERO)
    summary = {
        str(_("Overdue contracts")): len(rows),
        str(_("Items not returned")): sum(row[5] for row in rows),
        str(_("Value at risk")): (at_risk, ColumnKind.MONEY),
        str(_("Deposits held")): (deposits, ColumnKind.MONEY),
        str(_("Longest overdue (days)")): (
            max((row[4] for row in rows), default=ZERO),
            ColumnKind.NUMBER,
        ),
    }

    return ReportData(
        title=str(title),
        subtitle=_("As at %(when)s") % {"when": timezone.localtime(now).strftime("%d.%m.%Y %H:%M")},
        columns=[
            str(_("Code")),
            str(_("Customer")),
            str(_("Phone")),
            str(_("Was due")),
            str(_("Days overdue")),
            str(_("Items")),
            str(_("Status")),
            str(_("Deposit held")),
            str(_("Total")),
            str(_("Paid")),
        ],
        column_kinds=[
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.DATETIME,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.TEXT,
            ColumnKind.MONEY,
            ColumnKind.MONEY,
            ColumnKind.MONEY,
        ],
        rows=rows,
        summary=summary,
        filters=shown,
        orientation=LANDSCAPE,
        message=str(_("Nothing is overdue. Every hire is either returned or still within time."))
        if not rows
        else "",
    )


# ===========================================================================
# Surf camps
# ===========================================================================
@report(
    "surf_camp_roster",
    title=_("Camp roster"),
    description=_(
        "The participant list a camp runs on: rooms, arrivals, transfers, dietary "
        "needs and t-shirt sizes."
    ),
    area=ReportArea.CAMPS,
    capability="surf_camps.view",
    icon="tent",
    filter_fields=("camp", "period"),
    default_filters={"range": "90"},
)
def surf_camp_roster(user, filters: Mapping) -> ReportData:
    participant_model = get_model("surf_camps.CampParticipant")
    period = resolve_period(filters, default="90")
    title = _("Camp roster")
    camp_id = filter_int(filters, "camp")
    shown = period_filters(period, {_("Camp"): _camp_label(camp_id)})

    if participant_model is None:
        return module_missing(title, _("Surf camps"), shown)

    participants = participant_model.objects.select_related(
        "camp", "student", "student__customer"
    )
    if camp_id:
        participants = participants.filter(camp_id=camp_id)
    elif period.start_date and period.end_date:
        # Camps that overlap the window, not camps that merely started in it.
        participants = participants.filter(
            camp__start_date__lte=period.end_date, camp__end_date__gte=period.start_date
        )
    participants = participants.order_by("camp__start_date", "student__customer__last_name")

    columns = [
        col(_("Camp"), "camp__name"),
        col(_("Participant"), "student"),
        col(_("Level"), "student__surf_level", choice=True),
        col(_("Status"), "status", choice=True),
        col(_("Room"), "room_number"),
        col(_("Room type"), "room_type", choice=True),
        col(_("Arrival"), "arrival_datetime", kind=ColumnKind.DATETIME),
        col(_("Departure"), "departure_datetime", kind=ColumnKind.DATETIME),
        col(_("Flight in"), "arrival_flight"),
        col(_("Transfer"), "needs_transfer", kind=ColumnKind.BOOLEAN),
        col(_("Dietary"), "dietary_requirements"),
        col(_("T-shirt"), "t_shirt_size", choice=True),
        col(_("Paid"), "amount_paid", kind=ColumnKind.MONEY),
    ]

    summary = {
        str(_("Participants")): participants.count(),
        str(_("Camps covered")): participants.values("camp_id").distinct().count(),
        str(_("Transfers needed")): participants.filter(needs_transfer=True).count(),
        str(_("With dietary requirements")): participants.exclude(
            dietary_requirements=""
        ).count(),
        str(_("Deposit paid")): participants.filter(deposit_paid=True).count(),
        str(_("Collected")): (sum_field(participants, "amount_paid"), ColumnKind.MONEY),
    }

    return build_table(
        participants,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


@report(
    "camp_financials",
    title=_("Camp financials"),
    description=_("Fill rate and money collected for each camp, against its list price."),
    area=ReportArea.CAMPS,
    capability="finance.view",
    icon="wallet",
    filter_fields=("period",),
    default_filters={"range": "365"},
)
def camp_financials(user, filters: Mapping) -> ReportData:
    camp_model = get_model("surf_camps.SurfCamp")
    participant_model = get_model("surf_camps.CampParticipant")
    period = resolve_period(filters, default="365")
    title = _("Camp financials")
    shown = period_filters(period)

    if camp_model is None:
        return module_missing(title, _("Surf camps"), shown)

    camps = camp_model.objects.all()
    if period.start_date and period.end_date:
        camps = camps.filter(
            start_date__lte=period.end_date, end_date__gte=period.start_date
        )
    camps = camps.order_by("-start_date")

    booked: dict[int, int] = {}
    confirmed: dict[int, int] = {}
    collected: dict[int, Decimal] = {}
    if participant_model is not None:
        cancelled_values = {"cancelled", "no_show", "withdrawn"}
        for entry in (
            participant_model.objects.filter(camp__in=camps)
            .values("camp_id", "status")
            .annotate(
                total=Count("id"),
                paid=Coalesce(Sum("amount_paid"), Value(ZERO), output_field=DecimalField()),
            )
        ):
            camp_id = entry["camp_id"]
            booked[camp_id] = booked.get(camp_id, 0) + entry["total"]
            collected[camp_id] = collected.get(camp_id, ZERO) + entry["paid"]
            if str(entry["status"]) not in cancelled_values:
                confirmed[camp_id] = confirmed.get(camp_id, 0) + entry["total"]

    rows: list[list[Any]] = []
    for camp in camps[:MAX_ROWS]:
        seats = camp.capacity or 0
        active = confirmed.get(camp.pk, 0)
        expected = (camp.price or ZERO) * active
        received = collected.get(camp.pk, ZERO)
        fill = (
            (Decimal(active) / Decimal(seats) * 100).quantize(Decimal("0.1")) if seats else ZERO
        )
        rows.append(
            [
                camp.code,
                camp.name,
                camp.start_date,
                camp.end_date,
                camp.get_status_display(),
                seats,
                active,
                fill,
                camp.price,
                expected,
                received,
                expected - received,
            ]
        )

    summary = {
        str(_("Camps")): len(rows),
        str(_("Seats offered")): sum(row[5] for row in rows),
        str(_("Seats taken")): sum(row[6] for row in rows),
        str(_("Expected revenue")): (sum((row[9] for row in rows), ZERO), ColumnKind.MONEY),
        str(_("Collected")): (sum((row[10] for row in rows), ZERO), ColumnKind.MONEY),
        str(_("Outstanding")): (sum((row[11] for row in rows), ZERO), ColumnKind.MONEY),
        str(_("Average fill rate")): (
            Decimal(str(round(sum(float(row[7]) for row in rows) / len(rows), 1)))
            if rows
            else ZERO,
            ColumnKind.PERCENT,
        ),
    }

    return ReportData(
        title=str(title),
        subtitle=period.label,
        columns=[
            str(_("Code")),
            str(_("Camp")),
            str(_("From")),
            str(_("To")),
            str(_("Status")),
            str(_("Capacity")),
            str(_("Booked")),
            str(_("Fill rate")),
            str(_("Price")),
            str(_("Expected")),
            str(_("Collected")),
            str(_("Outstanding")),
        ],
        column_kinds=[
            ColumnKind.TEXT,
            ColumnKind.TEXT,
            ColumnKind.DATE,
            ColumnKind.DATE,
            ColumnKind.TEXT,
            ColumnKind.NUMBER,
            ColumnKind.NUMBER,
            ColumnKind.PERCENT,
            ColumnKind.MONEY,
            ColumnKind.MONEY,
            ColumnKind.MONEY,
            ColumnKind.MONEY,
        ],
        rows=rows,
        summary=summary,
        filters=shown,
        orientation=LANDSCAPE,
        message=str(_("No camps fall inside this period.")) if not rows else "",
    )


# ===========================================================================
# Safety
# ===========================================================================
@report(
    "safety_incidents",
    title=_("Safety incidents"),
    description=_(
        "Every incident recorded in the period. This is the document an insurer "
        "or an inspector asks for, so it exports in full."
    ),
    area=ReportArea.SAFETY,
    capability="safety.view",
    icon="shield-alert",
    filter_fields=("period", "severity"),
)
def safety_incidents(user, filters: Mapping) -> ReportData:
    incident_model = get_model("safety.SafetyIncident")
    period = resolve_period(filters)
    title = _("Safety incidents")
    severity = filter_str(filters, "severity")
    shown = period_filters(period, {_("Severity"): dict(Severity.choices).get(severity, "")})

    if incident_model is None:
        return module_missing(title, _("Safety"), shown)

    date_path = first_path(
        incident_model, "occurred_at", "incident_datetime", "incident_date", "date", "created_at"
    )
    incidents = apply_period(incident_model.objects.all(), date_path, period)
    if severity and field_path_exists(incident_model, "severity"):
        incidents = incidents.filter(severity=severity)
    if date_path:
        incidents = incidents.order_by(f"-{date_path}")

    columns = [
        col(_("Reference"), "incident_code", "code", "reference"),
        col(_("When"), "occurred_at", "incident_datetime", "incident_date", "date", "created_at",
            kind=ColumnKind.DATETIME),
        col(_("Type"), "incident_type", "category", "kind", choice=True),
        col(_("Severity"), "severity", choice=True),
        col(_("Spot"), "spot__name", "location", "spot"),
        col(_("Person involved"), "student", "person", "customer", "involved_person"),
        col(_("Lesson"), "lesson"),
        col(_("Description"), "description", "summary", "what_happened"),
        col(_("Action taken"), "actions_taken", "action_taken", "resolution", "treatment"),
        col(_("Status"), "status", choice=True),
        col(_("Reported by"), "reported_by", "created_by"),
    ]

    severity_labels = dict(Severity.choices)
    summary = {str(_("Incidents")): incidents.count()}
    if field_path_exists(incident_model, "severity"):
        for entry in incidents.values("severity").annotate(total=Count("id")).order_by("-total"):
            key = entry["severity"]
            summary[str(severity_labels.get(key, key))] = entry["total"]
        summary[str(_("High or critical"))] = incidents.filter(
            severity__in=(Severity.HIGH, Severity.CRITICAL)
        ).count()

    return build_table(
        incidents,
        columns,
        title=title,
        subtitle=period.label,
        filters=shown,
        summary=summary,
        orientation=LANDSCAPE,
    )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _minutes_between(start, end) -> int:
    """Minutes between two ``time`` values, tolerating a session past midnight."""
    if not (start and end):
        return 0
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes < start_minutes:
        end_minutes += 24 * 60
    return end_minutes - start_minutes


def _overlap_hours(
    start: datetime | None, end: datetime | None, window_start: datetime, window_end: datetime
) -> Decimal:
    """Hours of ``[start, end]`` that fall inside the reporting window."""
    if start is None:
        return ZERO
    finish = end or window_end
    begin = max(start, window_start)
    stop = min(finish, window_end)
    if stop <= begin:
        return ZERO
    return Decimal(str(round((stop - begin).total_seconds() / 3600, 2)))


def _instructor_label(instructor_id: int | None) -> str:
    if not instructor_id:
        return ""
    model = get_model("instructors.Instructor")
    if model is None:
        return ""
    instructor = model.objects.filter(pk=instructor_id).first()
    return str(instructor) if instructor else ""


def _category_label(category_id: int | None) -> str:
    if not category_id:
        return str(_("All"))
    model = get_model("equipment.EquipmentCategory")
    if model is None:
        return ""
    category = model.objects.filter(pk=category_id).first()
    return str(category) if category else ""


def _camp_label(camp_id: int | None) -> str:
    if not camp_id:
        return ""
    model = get_model("surf_camps.SurfCamp")
    if model is None:
        return ""
    camp = model.objects.filter(pk=camp_id).first()
    return str(camp) if camp else ""


__all__ = [
    "MAX_ROWS",
    "Col",
    "Period",
    "ReportArea",
    "ReportSpec",
    "REGISTRY",
    "AREA_LABELS",
    "AREA_ICONS",
    "AREA_ORDER",
    "all_reports",
    "apply_period",
    "build_table",
    "col",
    "empty_report",
    "field_path_exists",
    "first_path",
    "get_model",
    "get_report",
    "grouped_reports",
    "report",
    "report_choices",
    "reports_for_user",
    "resolve_period",
    "sum_field",
]
