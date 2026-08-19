"""Template tags and filters shared across every screen."""

from __future__ import annotations

import functools
import json
from decimal import Decimal
from pathlib import Path

from django import template
from django.conf import settings
from django.urls import NoReverseMatch, resolve, reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from apps.core.utils import format_money as _format_money
from apps.core.utils import percent_change as _percent_change

register = template.Library()

# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------
_ICON_DIR = Path(settings.BASE_DIR) / "static" / "vendor" / "icons"

# Fallback rendered when an icon name is unknown — a neutral circle, so a typo
# degrades visually instead of breaking the page.
_FALLBACK_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><circle cx="12" cy="12" r="10"/></svg>'
)


@functools.lru_cache(maxsize=256)
def _load_icon(name: str) -> str:
    """Read a vendored Lucide SVG from disk (cached for the process lifetime)."""
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
    path = _ICON_DIR / f"{safe}.svg"
    try:
        # resolve() + relative check: the name can never escape the icon folder.
        resolved = path.resolve()
        if not resolved.is_relative_to(_ICON_DIR.resolve()):
            return _FALLBACK_ICON
        return resolved.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _FALLBACK_ICON


@register.simple_tag
def icon(name: str, css_class: str = "h-5 w-5", stroke_width: str = "2") -> str:
    """Inline a Lucide icon: ``{% icon "waves" "h-4 w-4 text-sky-500" %}``."""
    svg = _load_icon(name)
    svg = svg.replace("<svg", f'<svg class="{escape(css_class)}" aria-hidden="true"', 1)
    if stroke_width != "2":
        svg = svg.replace('stroke-width="2"', f'stroke-width="{escape(stroke_width)}"')
    return mark_safe(svg)  # noqa: S308 - content is a vendored static file  # nosec


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def nav_active(context, url_name: str, css: str = "nav-link-active") -> str:
    """Return *css* when the current request matches *url_name*'s namespace."""
    request = context.get("request")
    if request is None:
        return ""
    try:
        target = reverse(url_name)
    except NoReverseMatch:
        return ""

    # Compare the app namespace so any page inside a module keeps it highlighted.
    try:
        current = resolve(request.path_info)
        wanted = resolve(target)
    except Exception:  # noqa: BLE001 - unresolvable paths simply are not active
        return css if request.path == target else ""

    if current.app_name and current.app_name == wanted.app_name:
        # Dashboard lives at "/" and would otherwise match everything.
        if current.app_name == "dashboard":
            return css if request.path_info.rstrip("/") == target.rstrip("/") else ""
        return css
    return ""


@register.simple_tag(takes_context=True)
def query_string(context, **kwargs) -> str:
    """Rebuild the query string with *kwargs* replaced.

    Used by pagination and filters so the current filters survive a page change.
    """
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode() if hasattr(params, "urlencode") else ""
    return f"?{encoded}" if encoded else ""


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
@register.filter
def has_capability(user, capability: str) -> bool:
    """``{% if request.user|has_capability:"bookings.add" %}``"""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.has_capability(capability)


@register.simple_tag(takes_context=True)
def can(context, capability: str) -> bool:
    user = getattr(context.get("request"), "user", None)
    return bool(user and user.is_authenticated and user.has_capability(capability))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
@register.filter
def money(value, currency: str | None = None) -> str:
    return _format_money(value, currency)


@register.filter
def delta(current, previous):
    """Percentage change, or ``None`` when undefined."""
    return _percent_change(current, previous)


@register.filter
def pct(value, decimals: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


@register.filter
def duration_hm(minutes) -> str:
    """Render a minute count as ``2h 30m``."""
    try:
        total = int(minutes or 0)
    except (TypeError, ValueError):
        return "—"
    hours, mins = divmod(abs(total), 60)
    sign = "-" if total < 0 else ""
    if hours and mins:
        return f"{sign}{hours}h {mins}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{mins}m"


@register.filter
def get_item(mapping, key):
    """Dictionary lookup by variable key: ``{{ mydict|get_item:mykey }}``."""
    if mapping is None:
        return None
    try:
        return mapping.get(key)
    except AttributeError:
        try:
            return mapping[key]
        except (KeyError, IndexError, TypeError):
            return None


@register.filter
def to_json(value) -> str:
    """Serialise a value for embedding in a ``<script>`` block or Alpine state."""

    def default(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return str(obj)

    return mark_safe(  # noqa: S308 - escaped below  # nosec
        json.dumps(value, default=default, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------
#: Maps a status value to a badge palette. Modules extend this via
#: ``{% status_badge value colour_map %}`` when they need custom colours.
DEFAULT_STATUS_COLORS = {
    "draft": "slate", "pending": "amber", "tentative": "amber",
    "confirmed": "sky", "scheduled": "sky", "active": "emerald",
    "in_progress": "sky", "completed": "emerald", "paid": "emerald",
    "available": "emerald", "returned": "emerald", "resolved": "emerald",
    "cancelled": "rose", "canceled": "rose", "no_show": "rose",
    "overdue": "rose", "damaged": "rose", "lost": "rose", "failed": "rose",
    "refunded": "violet", "partially_paid": "amber", "maintenance": "amber",
    "retired": "slate", "rented": "sky", "reserved": "violet",
}


@register.simple_tag
def status_badge(value, label=None, colors: dict | None = None) -> str:
    """Render a coloured status pill."""
    palette = {**DEFAULT_STATUS_COLORS, **(colors or {})}
    color = palette.get(str(value), "slate")
    text = escape(str(label if label is not None else value).replace("_", " ").title())
    return mark_safe(f'<span class="badge-{color}">{text}</span>')  # noqa: S308  # nosec


@register.simple_tag
def ai_chip(label: str | None = None) -> str:
    """The mandatory 'AI Recommendation' marker.

    Every AI-generated suggestion must carry this chip so staff can never
    mistake a model's opinion for a system-of-record fact.
    """
    text = escape(label or _("AI Recommendation"))
    return mark_safe(  # noqa: S308  # nosec
        f'<span class="ai-chip">{text}</span>'
    )
