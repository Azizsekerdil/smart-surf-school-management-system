"""Shared helpers: date ranges, money formatting, codes, QR/barcode rendering."""

from __future__ import annotations

import base64
import io
import secrets
import string
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

# ---------------------------------------------------------------------------
# Date ranges — the vocabulary used by every filter in the product
# ---------------------------------------------------------------------------
RANGE_CHOICES: tuple[tuple[str, object], ...] = (
    ("today", _("Today")),
    ("7", _("Last 7 days")),
    ("30", _("Last 30 days")),
    ("90", _("Last 3 months")),
    ("180", _("Last 6 months")),
    ("365", _("Last year")),
    ("all", _("All time")),
    ("custom", _("Custom range")),
)


def _as_aware(value: date, end_of_day: bool = False) -> datetime:
    moment = datetime.combine(value, time.max if end_of_day else time.min)
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    return moment


def parse_date_range(request, default: str = "30") -> tuple[datetime | None, datetime | None, str]:
    """Interpret ``?range=`` (+ ``?start=``/``?end=`` when custom).

    Returns ``(start, end, human_label)``; ``start``/``end`` may be ``None`` for
    the "all time" range.
    """
    key = (request.GET.get("range") or default).strip()
    today = timezone.localdate()

    if key == "custom":
        start_raw = request.GET.get("start", "")
        end_raw = request.GET.get("end", "")
        try:
            start_date = date.fromisoformat(start_raw) if start_raw else today - timedelta(days=30)
        except ValueError:
            start_date = today - timedelta(days=30)
        try:
            end_date = date.fromisoformat(end_raw) if end_raw else today
        except ValueError:
            end_date = today
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        label = f"{start_date:%d.%m.%Y} – {end_date:%d.%m.%Y}"
        return _as_aware(start_date), _as_aware(end_date, end_of_day=True), label

    if key == "all":
        return None, None, str(_("All time"))

    if key == "today":
        return _as_aware(today), _as_aware(today, end_of_day=True), str(_("Today"))

    try:
        days = int(key)
    except ValueError:
        days = int(default)
    days = max(1, min(days, 3650))
    start_date = today - timedelta(days=days - 1)
    label = dict(RANGE_CHOICES).get(str(days))
    return (
        _as_aware(start_date),
        _as_aware(today, end_of_day=True),
        str(label) if label else _("Last %(n)s days") % {"n": days},
    )


def previous_period(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None]:
    """Return the equally long period immediately before ``[start, end]``.

    Used for every "vs. previous period" comparison on the dashboards.
    """
    if start is None or end is None:
        return None, None
    span = end - start
    return start - span - timedelta(seconds=1), start - timedelta(seconds=1)


def daterange(start: date, end: date):
    """Yield each date from *start* to *end* inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Money & numbers
# ---------------------------------------------------------------------------
def to_decimal(value, default: Decimal = Decimal("0.00")) -> Decimal:
    """Coerce anything to a 2-dp Decimal without raising."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return default


def format_money(amount, currency: str | None = None) -> str:
    """Format an amount with the school's currency symbol."""
    amount = to_decimal(amount)
    symbol = settings.SCHOOL["CURRENCY_SYMBOL"] if currency is None else currency
    return f"{symbol}{amount:,.2f}"


def percent_change(current, previous) -> float | None:
    """Percentage change from *previous* to *current* (``None`` if undefined)."""
    current = float(current or 0)
    previous = float(previous or 0)
    if previous == 0:
        return None if current == 0 else 100.0
    return round(((current - previous) / abs(previous)) * 100, 2)


def safe_divide(numerator, denominator, default=0.0) -> float:
    try:
        denominator = float(denominator)
        if denominator == 0:
            return default
        return float(numerator) / denominator
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Codes & identifiers
# ---------------------------------------------------------------------------
_CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"


def generate_code(prefix: str = "", length: int = 6) -> str:
    """Generate a short, human-transcribable code (no look-alike characters)."""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    return f"{prefix}{body}" if prefix else body


def next_sequential_code(model, field: str, prefix: str, width: int = 5) -> str:
    """Return the next ``PREFIX00001``-style code for *model.field*.

    Falls back to a random code if a race produces a collision.
    """
    latest = (
        model.all_objects.filter(**{f"{field}__startswith": prefix})
        if hasattr(model, "all_objects")
        else model.objects.filter(**{f"{field}__startswith": prefix})
    )
    latest = latest.order_by(f"-{field}").values_list(field, flat=True).first()
    number = 1
    if latest:
        suffix = str(latest)[len(prefix) :]
        if suffix.isdigit():
            number = int(suffix) + 1
    candidate = f"{prefix}{number:0{width}d}"
    manager = model.all_objects if hasattr(model, "all_objects") else model.objects
    if manager.filter(**{field: candidate}).exists():
        return f"{prefix}{generate_code(length=width)}"
    return candidate


# ---------------------------------------------------------------------------
# QR codes & barcodes
# ---------------------------------------------------------------------------
def make_qr_png(data: str, scale: int = 4, border: int = 2) -> bytes:
    """Render *data* as a PNG QR code and return the raw bytes.

    Uses ``segno`` (pure Python, BSD-3): no Pillow requirement and an
    unambiguous licence, which matters because every equipment label carries a
    QR code.
    """
    import segno

    qr = segno.make(data, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border, dark="#0f172a", light="#ffffff")
    return buffer.getvalue()


def make_qr_svg(data: str, scale: int = 4, border: int = 2) -> str:
    """Render *data* as an inline SVG QR code (sharp at any zoom, tiny)."""
    import segno

    qr = segno.make(data, error="m")
    buffer = io.StringIO()
    qr.save(buffer, kind="svg", scale=scale, border=border, dark="#0f172a", xmldecl=False, svgns=True)
    return buffer.getvalue()


def make_qr_data_uri(data: str, scale: int = 4) -> str:
    """Return an inline ``data:`` URI so a QR code can be embedded in HTML/PDF."""
    encoded = base64.b64encode(make_qr_png(data, scale=scale)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def make_barcode_svg(code: str, symbology: str = "code128") -> str:
    """Render *code* as an inline SVG barcode."""
    import barcode
    from barcode.writer import SVGWriter

    barcode_class = barcode.get_barcode_class(symbology)
    buffer = io.BytesIO()
    barcode_class(code, writer=SVGWriter()).write(buffer, options={"module_height": 10.0})
    return buffer.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def truncate(text: str, length: int = 80, suffix: str = "…") -> str:
    text = text or ""
    return text if len(text) <= length else text[: length - len(suffix)] + suffix


def chunked(iterable, size: int):
    """Yield successive lists of at most *size* items."""
    chunk: list = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
