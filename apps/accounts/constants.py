"""Roles and the capability matrix that drives every access decision.

Why capabilities rather than raw Django permissions
---------------------------------------------------
Django's model permissions answer "may this user change a Booking row?".
Surf-school operations also need answers to questions that are not per-model —
"may this user approve an AI terminal command?", "may this user restore a
backup?", "may this user see other instructors' commission?".

A capability is a dotted string ``"<module>.<action>"``. The matrix below is the
single source of truth: it drives the navigation menu, the HTML view mixins, the
DRF permission class and the Django group synchronisation. Changing access for a
role is a one-line edit here, and :mod:`apps.accounts.tests` asserts the matrix
stays consistent.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """The 14 operational roles of a surf school."""

    SUPER_ADMIN = "super_admin", _("Super Admin")
    MANAGER = "manager", _("Manager")
    OPERATIONS_MANAGER = "operations_manager", _("Operations Manager")
    HEAD_INSTRUCTOR = "head_instructor", _("Head Instructor")
    SURF_INSTRUCTOR = "surf_instructor", _("Surf Instructor")
    LIFEGUARD = "lifeguard", _("Lifeguard")
    RECEPTION = "reception", _("Reception")
    RENTAL_STAFF = "rental_staff", _("Rental Staff")
    EQUIPMENT_MANAGER = "equipment_manager", _("Equipment Manager")
    MAINTENANCE_STAFF = "maintenance_staff", _("Maintenance Staff")
    FINANCE = "finance", _("Finance")
    MARKETING = "marketing", _("Marketing")
    PHOTOGRAPHER = "photographer", _("Photographer")
    CUSTOMER = "customer", _("Customer")
    STUDENT = "student", _("Student")


#: Roles that belong to school personnel (as opposed to external people).
STAFF_ROLES: frozenset[str] = frozenset(
    {
        Role.SUPER_ADMIN,
        Role.MANAGER,
        Role.OPERATIONS_MANAGER,
        Role.HEAD_INSTRUCTOR,
        Role.SURF_INSTRUCTOR,
        Role.LIFEGUARD,
        Role.RECEPTION,
        Role.RENTAL_STAFF,
        Role.EQUIPMENT_MANAGER,
        Role.MAINTENANCE_STAFF,
        Role.FINANCE,
        Role.MARKETING,
        Role.PHOTOGRAPHER,
    }
)

#: Roles held by people outside the organisation — they only ever see their own data.
EXTERNAL_ROLES: frozenset[str] = frozenset({Role.CUSTOMER, Role.STUDENT})

# ---------------------------------------------------------------------------
# Modules and actions
# ---------------------------------------------------------------------------
MODULES: tuple[str, ...] = (
    "dashboard",
    "accounts",
    "customers",
    "students",
    "instructors",
    "crm",
    "locations",
    "lessons",
    "bookings",
    "surf_camps",
    "equipment",
    "rentals",
    "maintenance",
    "surf_conditions",
    "safety",
    "finance",
    "pos",
    "analytics",
    "reporting",
    "notifications",
    "backups",
    "audit",
    "ai",
    "ai_terminal",
    "help_center",
    "training",
    "onboarding",
    "settings",
)

ACTIONS: tuple[str, ...] = (
    "view",
    "add",
    "change",
    "delete",
    "export",
    "approve",
    "manage",
)

#: Capabilities that do not fit the ``module.action`` grid because they gate a
#: specific *kind of information* rather than an operation.
#:
#: ``finance.revenue`` is the important one. Taking a payment at a counter and
#: reading the takings of the school are different privileges: reception and
#: rental staff need ``finance.view`` to record money against a booking or a
#: hire, but the revenue dashboard, the payment-summary endpoint, the P&L
#: aggregates and instructor commission are gated on ``finance.revenue``, which
#: they do not hold.
EXTRA_CAPABILITIES: frozenset[str] = frozenset(
    {
        "finance.refund",
        "finance.revenue",
        "instructors.view_commission",
        "reporting.schedule",
    }
)

#: Capabilities that are inherently dangerous and are never granted implicitly.
PRIVILEGED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "backups.restore",
        "backups.delete",
        "ai_terminal.execute",
        "ai_terminal.approve",
        "ai_terminal.apply_patch",
        "settings.manage",
        "accounts.manage",
        "finance.refund",
        "audit.delete",
    }
)


def _all(module: str) -> set[str]:
    """Every standard action on *module*."""
    return {f"{module}.{action}" for action in ACTIONS}


def _view(*modules: str) -> set[str]:
    return {f"{m}.view" for m in modules}


def _crud(*modules: str) -> set[str]:
    caps: set[str] = set()
    for m in modules:
        caps |= {f"{m}.view", f"{m}.add", f"{m}.change"}
    return caps


# Capabilities every authenticated user has, regardless of role.
BASE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "dashboard.view",
        "help_center.view",
        "notifications.view",
        "surf_conditions.view",
    }
)
# NOTE: "training.view" is deliberately NOT here. The Training Center teaches
# staff how to operate the system; a paying customer has no business in it.
# Staff roles get it through the matrix below, and Role.STUDENT lists it
# explicitly because students do follow the surf-progress courses.

#: What every member of school personnel gets on top of the base set. The
#: Training Center is here rather than in BASE_CAPABILITIES so that customers,
#: who are also authenticated users, do not inherit it.
STAFF_BASE_CAPABILITIES: frozenset[str] = frozenset(
    BASE_CAPABILITIES | {"training.view"}
)

# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    # ---------------------------------------------------------------- admin
    Role.SUPER_ADMIN: frozenset(
        {f"{m}.{a}" for m in MODULES for a in ACTIONS}
        | PRIVILEGED_CAPABILITIES
        | EXTRA_CAPABILITIES
    ),
    # -------------------------------------------------------------- manager
    Role.MANAGER: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("customers")
        | _all("students")
        | _all("instructors")
        | _all("crm")
        | _all("locations")
        | _all("lessons")
        | _all("bookings")
        | _all("surf_camps")
        | _all("equipment")
        | _all("rentals")
        | _all("maintenance")
        | _all("safety")
        | _all("finance")
        | _all("pos")
        | _all("analytics")
        | _all("reporting")
        | _all("notifications")
        | _all("surf_conditions")
        | _view("audit", "backups", "accounts")
        | {
            "backups.add",
            "accounts.add",
            "accounts.change",
            "audit.export",
            "ai.view",
            "ai.change",
            "ai.manage",
            "settings.view",
            "settings.change",
            "onboarding.view",
            "onboarding.change",
            "finance.refund",
            "finance.revenue",
            "instructors.view_commission",
            "reporting.schedule",
        }
    ),
    # --------------------------------------------------- operations manager
    Role.OPERATIONS_MANAGER: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("lessons")
        | _all("bookings")
        | _all("surf_camps")
        | _all("locations")
        | _crud("customers", "students", "instructors", "equipment", "rentals", "maintenance")
        | _all("safety")
        | _view("finance", "analytics", "reporting", "crm", "audit")
        | {
            "analytics.export",
            "reporting.export",
            "reporting.add",
            "customers.export",
            "bookings.approve",
            "surf_camps.approve",
            "safety.approve",
            "notifications.add",
            "finance.revenue",
            "ai.view",
        }
    ),
    # ------------------------------------------------------ head instructor
    Role.HEAD_INSTRUCTOR: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("lessons")
        | _crud("bookings", "students", "instructors", "surf_camps")
        | _view("customers", "equipment", "rentals", "maintenance", "analytics")
        | _all("safety")
        | {
            "instructors.approve",
            "lessons.approve",
            "safety.approve",
            "equipment.change",
            "maintenance.add",
            "analytics.export",
            "reporting.view",
            "reporting.export",
            "notifications.add",
            "ai.view",
            "instructors.view_commission",
        }
    ),
    # ------------------------------------------------------- surf instructor
    Role.SURF_INSTRUCTOR: frozenset(
        STAFF_BASE_CAPABILITIES
        | _view("lessons", "bookings", "students", "customers", "equipment", "surf_camps", "locations")
        | {
            "lessons.change",
            "students.change",
            "students.add",
            "bookings.change",
            "safety.view",
            "safety.add",
            "maintenance.add",
            "maintenance.view",
            "equipment.change",
            "ai.view",
        }
    ),
    # -------------------------------------------------------------- lifeguard
    Role.LIFEGUARD: frozenset(
        STAFF_BASE_CAPABILITIES
        | _view("lessons", "bookings", "students", "locations", "equipment")
        | _all("safety")
        | {"notifications.add", "ai.view"}
    ),
    # -------------------------------------------------------------- reception
    Role.RECEPTION: frozenset(
        STAFF_BASE_CAPABILITIES
        | _crud("customers", "students", "bookings")
        | _view("lessons", "instructors", "surf_camps", "equipment", "rentals", "crm", "locations")
        | {
            "bookings.delete",
            "customers.export",
            "pos.view",
            "pos.add",
            "finance.view",
            "finance.add",
            "rentals.add",
            "rentals.change",
            "crm.add",
            "crm.change",
            "notifications.add",
            "reporting.view",
            "ai.view",
        }
    ),
    # ----------------------------------------------------------- rental staff
    Role.RENTAL_STAFF: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("rentals")
        | _view("customers", "students", "equipment", "bookings")
        | {
            "equipment.change",
            "maintenance.add",
            "maintenance.view",
            "pos.view",
            "pos.add",
            "finance.view",
            "customers.add",
            "customers.change",
            "reporting.view",
        }
    ),
    # ------------------------------------------------------ equipment manager
    Role.EQUIPMENT_MANAGER: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("equipment")
        | _all("maintenance")
        | _all("rentals")
        | _view("customers", "lessons", "bookings", "finance", "analytics", "locations")
        | {
            "analytics.export",
            "reporting.view",
            "reporting.export",
            "notifications.add",
            "ai.view",
        }
    ),
    # ----------------------------------------------------- maintenance staff
    Role.MAINTENANCE_STAFF: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("maintenance")
        | _view("equipment", "rentals")
        | {"equipment.change", "notifications.add", "reporting.view"}
    ),
    # ---------------------------------------------------------------- finance
    Role.FINANCE: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("finance")
        | _all("pos")
        | _all("reporting")
        | _view("customers", "students", "bookings", "rentals", "surf_camps", "instructors", "audit")
        | {
            "analytics.view",
            "analytics.export",
            "customers.export",
            "finance.refund",
            "finance.revenue",
            "instructors.view_commission",
            "audit.export",
            "ai.view",
        }
    ),
    # -------------------------------------------------------------- marketing
    Role.MARKETING: frozenset(
        STAFF_BASE_CAPABILITIES
        | _all("crm")
        | _view("customers", "students", "lessons", "surf_camps", "bookings", "analytics")
        | {
            "customers.change",
            "customers.export",
            "analytics.export",
            "reporting.view",
            "reporting.export",
            "notifications.add",
            "notifications.change",
            "ai.view",
        }
    ),
    # ----------------------------------------------------------- photographer
    Role.PHOTOGRAPHER: frozenset(
        STAFF_BASE_CAPABILITIES
        | _view("lessons", "bookings", "students", "customers", "surf_camps", "equipment", "locations")
        | {"equipment.change"}
    ),
    # --------------------------------------------------------------- customer
    Role.CUSTOMER: frozenset(
        {
            "dashboard.view",
            "help_center.view",
            "surf_conditions.view",
            "notifications.view",
            "bookings.view",
            "bookings.add",
            "lessons.view",
            "surf_camps.view",
            "rentals.view",
            "finance.view",
        }
    ),
    # ---------------------------------------------------------------- student
    Role.STUDENT: frozenset(
        {
            "dashboard.view",
            "help_center.view",
            "training.view",
            "surf_conditions.view",
            "notifications.view",
            "bookings.view",
            "lessons.view",
            "surf_camps.view",
        }
    ),
}


def capabilities_for(role: str) -> frozenset[str]:
    """Return the capability set granted by *role* (empty for unknown roles)."""
    return ROLE_CAPABILITIES.get(role, frozenset())


def all_capabilities() -> frozenset[str]:
    """Every capability referenced anywhere in the matrix."""
    caps: set[str] = set(BASE_CAPABILITIES) | set(EXTRA_CAPABILITIES)
    for value in ROLE_CAPABILITIES.values():
        caps |= set(value)
    return frozenset(caps)


#: Human-readable module labels, used by the role editor screen.
MODULE_LABELS: dict[str, object] = {
    "dashboard": _("Dashboard"),
    "accounts": _("Users & Roles"),
    "customers": _("Customers"),
    "students": _("Students"),
    "instructors": _("Instructors"),
    "crm": _("CRM"),
    "locations": _("Surf Locations"),
    "lessons": _("Lessons"),
    "bookings": _("Bookings"),
    "surf_camps": _("Surf Camps"),
    "equipment": _("Equipment"),
    "rentals": _("Rentals"),
    "maintenance": _("Maintenance"),
    "surf_conditions": _("Surf Conditions"),
    "safety": _("Safety"),
    "finance": _("Finance"),
    "pos": _("Point of Sale"),
    "analytics": _("Analytics"),
    "reporting": _("Reports"),
    "notifications": _("Notifications"),
    "backups": _("Backup & Restore"),
    "audit": _("Audit Log"),
    "ai": _("AI Assistant"),
    "ai_terminal": _("AI Development Terminal"),
    "help_center": _("Help Center"),
    "training": _("Training Center"),
    "onboarding": _("Onboarding"),
    "settings": _("Settings"),
}
