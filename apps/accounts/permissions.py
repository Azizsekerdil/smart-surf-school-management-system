"""Access-control helpers shared by HTML views and the REST API.

Usage
-----
HTML view::

    class BookingListView(CapabilityRequiredMixin, ListView):
        capability = "bookings.view"

DRF viewset::

    class BookingViewSet(CapabilityViewSetMixin, ModelViewSet):
        capability_prefix = "bookings"

Both paths consult the same :meth:`User.has_capability`, so the UI can never
show an action the API would reject, and vice versa.
"""

from __future__ import annotations

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _
from rest_framework import permissions


# ---------------------------------------------------------------------------
# HTML views
# ---------------------------------------------------------------------------
class CapabilityRequiredMixin(AccessMixin):
    """Require an authenticated user holding ``capability``.

    Set ``capability`` for a single requirement or ``any_capabilities`` /
    ``all_capabilities`` for combinations.
    """

    capability: str | None = None
    any_capabilities: tuple[str, ...] = ()
    all_capabilities: tuple[str, ...] = ()

    def get_required_capabilities(self) -> tuple[str, ...]:
        if self.capability:
            return (self.capability,)
        return self.all_capabilities or self.any_capabilities

    def has_capability_permission(self) -> bool:
        user = self.request.user
        if not user.is_authenticated:
            return False
        if self.capability:
            return user.has_capability(self.capability)
        if self.all_capabilities:
            return user.has_all_capabilities(*self.all_capabilities)
        if self.any_capabilities:
            return user.has_any_capability(*self.any_capabilities)
        # No requirement declared -> authenticated access only.
        return True

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not self.has_capability_permission():
            raise PermissionDenied(
                _("Your role (%(role)s) does not grant access to this screen.")
                % {"role": getattr(request.user, "role_label", "-")}
            )
        return super().dispatch(request, *args, **kwargs)


class StaffOnlyMixin(AccessMixin):
    """Restrict a view to school personnel (not customers/students)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not getattr(request.user, "is_staff_member", False) and not request.user.is_superuser:
            raise PermissionDenied(_("This screen is available to staff members only."))
        return super().dispatch(request, *args, **kwargs)


class SuperAdminRequiredMixin(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not getattr(request.user, "is_super_admin", False):
            raise PermissionDenied(_("Super Admin privileges are required."))
        return super().dispatch(request, *args, **kwargs)


def require_capability(user, capability: str) -> None:
    """Raise :class:`PermissionDenied` unless *user* holds *capability*."""
    if not (user and user.is_authenticated and user.has_capability(capability)):
        raise PermissionDenied(
            _("Missing required permission: %(cap)s") % {"cap": capability}
        )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
#: HTTP method -> capability action
METHOD_ACTION_MAP = {
    "GET": "view",
    "HEAD": "view",
    "OPTIONS": "view",
    "POST": "add",
    "PUT": "change",
    "PATCH": "change",
    "DELETE": "delete",
}


class HasCapability(permissions.BasePermission):
    """Map the HTTP method onto ``<capability_prefix>.<action>``.

    The view must define ``capability_prefix`` (e.g. ``"bookings"``). A view may
    also define ``capability_overrides = {"custom_action": "bookings.approve"}``
    for extra DRF actions.
    """

    message = _("Your role does not permit this operation.")

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False

        prefix = getattr(view, "capability_prefix", None)
        if not prefix:
            return True  # view opted out of capability checks

        overrides = getattr(view, "capability_overrides", {}) or {}
        action = getattr(view, "action", None)
        if action and action in overrides:
            return user.has_capability(overrides[action])

        method_action = METHOD_ACTION_MAP.get(request.method, "view")
        return user.has_capability(f"{prefix}.{method_action}")


class IsOwnerOrHasCapability(permissions.BasePermission):
    """Object-level rule for customer/student self-service endpoints.

    External users (customers, students) may only touch rows linked to
    themselves; staff fall back to the normal capability check.
    """

    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_staff_member", False) or user.is_superuser:
            prefix = getattr(view, "capability_prefix", None)
            if not prefix:
                return True
            return user.has_capability(f"{prefix}.{METHOD_ACTION_MAP.get(request.method, 'view')}")

        for attribute in ("user", "owner", "created_by"):
            related = getattr(obj, attribute, None)
            if related is not None and related == user:
                return True
        # Customer/student profile links.
        for attribute in ("customer", "student"):
            related = getattr(obj, attribute, None)
            if related is not None and getattr(related, "user_id", None) == user.id:
                return True
        return False


class ReadOnly(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.method in permissions.SAFE_METHODS


class CapabilityViewSetMixin:
    """Attach :class:`HasCapability` to a viewset."""

    permission_classes = [permissions.IsAuthenticated, HasCapability]
    capability_prefix: str | None = None
    capability_overrides: dict[str, str] = {}
