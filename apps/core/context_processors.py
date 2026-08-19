"""Template context processors: site metadata and the capability-filtered menu."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.accounts.scoping import is_external_user


@dataclass(frozen=True)
class NavItem:
    label: object
    url_name: str
    icon: str
    capability: str | None = None
    badge_key: str | None = None
    #: Hide the entry from customers and students. Use it when the default
    #: screen is a back-office console with no self-service equivalent, so the
    #: menu never offers a link that the view will refuse.
    staff_only: bool = False
    #: Where to send a user who does *not* hold ``alt_capability``. Keeps one
    #: menu entry pointing at the screen each role can actually open.
    alt_url_name: str | None = None
    alt_capability: str | None = None


@dataclass(frozen=True)
class NavSection:
    label: object | None
    items: list[NavItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The menu, exactly as specified in the product brief.
# Items whose capability the user lacks are removed; empty sections disappear.
# ---------------------------------------------------------------------------
NAVIGATION: tuple[NavSection, ...] = (
    NavSection(
        label=None,
        items=[NavItem(_("Dashboard"), "dashboard:home", "layout-dashboard", "dashboard.view")],
    ),
    NavSection(
        label=_("Operations"),
        items=[
            NavItem(_("Students"), "students:list", "graduation-cap", "students.view"),
            NavItem(_("Customers"), "customers:list", "users", "customers.view"),
            NavItem(_("Instructors"), "instructors:list", "user-check", "instructors.view"),
            NavItem(_("Lessons"), "lessons:list", "book-open", "lessons.view"),
            NavItem(
                _("Bookings"),
                "bookings:calendar",
                "calendar-days",
                "bookings.view",
                alt_url_name="bookings:list",
                alt_capability="bookings.change",
            ),
            NavItem(_("Surf Camps"), "surf_camps:list", "tent", "surf_camps.view", staff_only=True),
        ],
    ),
    NavSection(
        label=_("Equipment"),
        items=[
            NavItem(_("Inventory"), "equipment:list", "package", "equipment.view"),
            NavItem(_("Rentals"), "rentals:list", "arrow-left-right", "rentals.view", "active_rentals"),
            NavItem(_("Maintenance"), "maintenance:list", "wrench", "maintenance.view", "open_maintenance"),
        ],
    ),
    NavSection(
        label=_("Surf"),
        items=[
            NavItem(_("Surf Conditions"), "surf_conditions:dashboard", "waves", "surf_conditions.view"),
            NavItem(_("Locations"), "locations:list", "map-pin", "locations.view"),
            NavItem(_("Safety"), "safety:dashboard", "shield-alert", "safety.view"),
        ],
    ),
    NavSection(
        label=_("Business"),
        items=[
            NavItem(_("CRM"), "crm:dashboard", "heart-handshake", "crm.view"),
            NavItem(
                _("Finance"),
                "finance:dashboard",
                "wallet",
                "finance.view",
                alt_url_name="finance:invoice_list",
                alt_capability="finance.revenue",
            ),
            NavItem(_("Point of Sale"), "pos:terminal", "shopping-cart", "pos.view"),
            NavItem(_("Reports"), "reporting:list", "file-text", "reporting.view"),
            NavItem(_("Analytics"), "analytics:dashboard", "chart-line", "analytics.view"),
        ],
    ),
    NavSection(
        label=_("Artificial Intelligence"),
        items=[
            NavItem(_("AI Assistant"), "ai:chat", "sparkles", "ai.view"),
            NavItem(_("AI Control Center"), "ai:control_center", "cpu", "ai.manage"),
            NavItem(_("AI Development Terminal"), "ai_terminal:console", "terminal", "ai_terminal.view"),
            NavItem(_("AI Usage & Costs"), "ai:usage", "gauge", "ai.view"),
        ],
    ),
    NavSection(
        label=_("System"),
        items=[
            NavItem(_("Backup & Restore"), "backups:list", "database-backup", "backups.view"),
            NavItem(_("Audit Log"), "audit:list", "scroll-text", "audit.view"),
            NavItem(_("Training Center"), "training:home", "school", "training.view"),
            NavItem(_("Help"), "help_center:home", "circle-help", "help_center.view"),
            NavItem(_("Users & Roles"), "accounts:user_list", "shield-check", "accounts.view"),
            NavItem(_("Settings"), "core:settings", "settings", "settings.view"),
        ],
    ),
)


def navigation(request) -> dict:
    """Build the sidebar for the current user."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_sections": []}

    capabilities = user.get_capabilities()
    external = is_external_user(user)
    sections = []
    for section in NAVIGATION:
        allowed = []
        for item in section.items:
            if item.capability is not None and item.capability not in capabilities:
                continue
            if external and item.staff_only:
                continue
            if (
                item.alt_url_name
                and item.alt_capability
                and item.alt_capability not in capabilities
            ):
                item = replace(item, url_name=item.alt_url_name)
            allowed.append(item)
        if allowed:
            sections.append({"label": section.label, "items": allowed})

    return {"nav_sections": sections, "user_capabilities": capabilities}


def site_context(request) -> dict:
    """School identity, version and feature flags available to every template."""
    return {
        "SCHOOL_NAME": settings.SCHOOL["NAME"],
        "SCHOOL_CURRENCY": settings.SCHOOL["CURRENCY"],
        "CURRENCY_SYMBOL": settings.SCHOOL["CURRENCY_SYMBOL"],
        "APP_VERSION": settings.APP_VERSION,
        "AI_TERMINAL_ENABLED": settings.AI_TERMINAL["ENABLED"],
        "AI_ROUTING_MODE": settings.AI["ROUTING_MODE"],
        "DEBUG": settings.DEBUG,
    }
