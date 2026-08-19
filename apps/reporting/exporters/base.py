"""The data structure every report produces and every exporter consumes.

Why a single flat table
-----------------------
A surf school hands reports to accountants, insurers and instructors. Those
people want one predictable shape: a titled table with the filters that produced
it and a summary underneath. Keeping :class:`ReportData` flat means the PDF, the
spreadsheet and the CSV always agree, and a new report never has to teach three
exporters about a new layout.

Values stay *typed* in :attr:`ReportData.rows` (``Decimal``, ``date``, ``bool``)
and are converted to text only at render time. Excel needs the real numbers to
apply cell formats; PDF and CSV need locale-aware text. Formatting once, in the
builder, would break one of the two.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _


class ColumnKind:
    """How a column should be aligned, formatted and totalled."""

    TEXT = "text"
    NUMBER = "number"
    MONEY = "money"
    PERCENT = "percent"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    DURATION = "duration"
    BOOLEAN = "boolean"


#: Kinds that are right-aligned and may carry a numeric cell format.
NUMERIC_KINDS = frozenset(
    {ColumnKind.NUMBER, ColumnKind.MONEY, ColumnKind.PERCENT, ColumnKind.DURATION}
)

#: Page orientations understood by the PDF engine.
PORTRAIT = "portrait"
LANDSCAPE = "landscape"


def school_name() -> str:
    """The name printed on every document header."""
    return str(settings.SCHOOL.get("NAME") or _("Surf School"))


def currency_symbol() -> str:
    return str(settings.SCHOOL.get("CURRENCY_SYMBOL") or "")


@dataclass
class ReportData:
    """One rendered report: a table, its provenance and its totals.

    Attributes
    ----------
    columns:
        Header labels, already translated.
    rows:
        One list per row, positionally aligned with ``columns``.
    column_kinds:
        Optional per-column :class:`ColumnKind`. Shorter than ``columns`` is
        allowed — missing entries default to ``TEXT``.
    summary:
        ``{label: value}`` printed beneath the table. Values may be
        ``(value, kind)`` tuples when a total needs money or percent formatting.
    filters:
        ``{label: value}`` describing what was asked for. Printed on the
        document so a saved PDF can never be misread as "everything".
    message:
        Shown instead of the table when there is nothing to report — an empty
        report must explain itself rather than look like a failure.
    """

    title: str
    subtitle: str = ""
    generated_at: datetime = field(default_factory=timezone.now)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    orientation: str = PORTRAIT
    column_kinds: list[str] = field(default_factory=list)
    message: str = ""
    currency: str = field(default_factory=currency_symbol)
    #: Set when a row cap was hit, so nobody treats a truncated export as complete.
    truncated_at: int | None = None

    # --- derived ----------------------------------------------------------
    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def kind(self, index: int) -> str:
        if 0 <= index < len(self.column_kinds):
            return self.column_kinds[index] or ColumnKind.TEXT
        return ColumnKind.TEXT

    @property
    def effective_orientation(self) -> str:
        """Wide tables are laid out landscape whatever the builder asked for."""
        if self.orientation == LANDSCAPE:
            return LANDSCAPE
        return LANDSCAPE if len(self.columns) > 6 else PORTRAIT

    def base_filename(self) -> str:
        """``daily-operations-2026-08-18`` — no extension, always ASCII."""
        stamp = timezone.localtime(self.generated_at).date()
        slug = slugify(self.title) or "report"
        return f"{slug}-{stamp:%Y-%m-%d}"

    def display_row(self, row: list[Any]) -> list[str]:
        return [display_value(value, self.kind(i), self.currency) for i, value in enumerate(row)]

    def display_rows(self) -> list[list[str]]:
        """Every row as formatted text — what the HTML preview iterates."""
        return [self.display_row(row) for row in self.rows]

    def summary_items(self) -> list[tuple[str, str]]:
        """Summary as ``[(label, formatted_value)]``."""
        items: list[tuple[str, str]] = []
        for label, raw in self.summary.items():
            value, kind = _unpack(raw)
            items.append((str(label), display_value(value, kind, self.currency)))
        return items

    def filter_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for label, raw in self.filters.items():
            value, kind = _unpack(raw)
            items.append((str(label), display_value(value, kind, self.currency)))
        return items


def _unpack(raw: Any) -> tuple[Any, str]:
    """Accept either ``value`` or ``(value, kind)`` in summary/filter dicts."""
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], str):
        return raw[0], raw[1]
    if isinstance(raw, Decimal):
        return raw, ColumnKind.MONEY
    return raw, ColumnKind.TEXT


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------
def display_value(value: Any, kind: str = ColumnKind.TEXT, currency: str = "") -> str:
    """Render one cell as locale-aware text.

    Turkish and English differ on the decimal separator and the date order, so
    this always goes through Django's format machinery rather than ``str()``.
    """
    if value is None or value == "":
        return ""

    if kind == ColumnKind.BOOLEAN or isinstance(value, bool):
        return gettext("Yes") if value else gettext("No")

    if kind == ColumnKind.MONEY:
        amount = _as_decimal(value)
        if amount is None:
            return str(value)
        text = number_format(amount, decimal_pos=2, force_grouping=True)
        return f"{currency}{text}" if currency else text

    if kind == ColumnKind.PERCENT:
        amount = _as_decimal(value)
        if amount is None:
            return str(value)
        return f"{number_format(amount, decimal_pos=1)}%"

    if kind == ColumnKind.DURATION:
        return _format_duration(value)

    if kind == ColumnKind.NUMBER or isinstance(value, (int, float, Decimal)):
        if isinstance(value, int):
            return number_format(value, force_grouping=True)
        amount = _as_decimal(value)
        if amount is None:
            return str(value)
        decimals = 0 if amount == amount.to_integral_value() else 2
        return number_format(amount, decimal_pos=decimals, force_grouping=True)

    if isinstance(value, datetime):
        moment = timezone.localtime(value) if timezone.is_aware(value) else value
        return date_format(moment, "SHORT_DATETIME_FORMAT", use_l10n=True)

    if isinstance(value, date):
        return date_format(value, "SHORT_DATE_FORMAT", use_l10n=True)

    if isinstance(value, time):
        return value.strftime("%H:%M")

    if isinstance(value, timedelta):
        return _format_duration(value.total_seconds() / 60)

    return str(value)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _format_duration(minutes: Any) -> str:
    """Minutes as ``2h 30m`` — the way a lesson board is read."""
    try:
        total = int(round(float(minutes)))
    except (TypeError, ValueError):
        return str(minutes)
    sign = "-" if total < 0 else ""
    hours, mins = divmod(abs(total), 60)
    if hours and mins:
        return f"{sign}{hours}h {mins}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{mins}m"


# ---------------------------------------------------------------------------
# Exporter contract
# ---------------------------------------------------------------------------
class BaseExporter(ABC):
    """Turn a :class:`ReportData` into bytes for download."""

    #: MIME type sent in the HTTP response.
    content_type: str = "application/octet-stream"
    #: Extension without the dot.
    file_extension: str = "bin"
    #: Short label for the format picker.
    label: str = ""

    @abstractmethod
    def render(self, data: ReportData) -> bytes:
        """Return the complete document as bytes. Must never return ``None``."""

    def filename(self, data: ReportData) -> str:
        return f"{data.base_filename()}.{self.file_extension}"
