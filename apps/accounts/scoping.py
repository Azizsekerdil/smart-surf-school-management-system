"""Row-level ownership scoping for external (customer / student) accounts.

Why this module exists
----------------------
:class:`apps.accounts.permissions.HasCapability` answers *"may this role touch
this kind of record?"*. It is a **view-level** check and it deliberately knows
nothing about individual rows. That is the correct division of labour, but it
leaves a second question unanswered: *"may this particular user see **this**
row?"*.

For school personnel the answer is "yes" — a receptionist is supposed to see
every invoice. For the two external roles (:class:`~apps.accounts.constants.Role`
``CUSTOMER`` and ``STUDENT``) the answer must be "only the rows that are about
me". Those roles hold ``finance.view``, ``rentals.view``, ``lessons.view`` and
``surf_camps.view`` so that the self-service portal can work at all, which means
the capability check alone lets them enumerate every record in the school —
including other people's payment history and, through camp participants and
lesson attendance, **children's records**.

This module supplies the missing half. Every list/detail surface that an
external role can reach declares how a row is linked back to a person, and the
queryset is narrowed *before* the database is asked for anything. Narrowing the
queryset (rather than filtering after the fact) means:

* list endpoints cannot leak other people's rows,
* detail endpoints return **404**, not 403 — an external user cannot even probe
  which primary keys exist,
* ``get_object()`` in DRF and ``get_object()`` in Django's generic views both
  route through ``get_queryset()``, so one declaration covers both, and
* counts, aggregates and pagination totals are correct for the caller.

Fail-closed by design
---------------------
The default for a view that mixes this in is :data:`DENY` — an external user
sees nothing. A surface becomes reachable only by an explicit declaration. A
typo in a lookup therefore hides data; it never exposes it.

Usage
-----
::

    class InvoiceViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, ModelViewSet):
        capability_prefix = "finance"
        external_access = OWN
        owner_lookups = ("customer__user",)

    class PricePackageViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, ModelViewSet):
        capability_prefix = "finance"
        external_access = SHARED      # a price list is catalogue data

    class ExpenseViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, ModelViewSet):
        capability_prefix = "finance"
        # external_access defaults to DENY — the school's own outgoings

Custom DRF ``@action`` handlers and any view that builds a queryset outside
``get_queryset()`` must pass it through :meth:`OwnerScopedQuerySetMixin.scope`
explicitly; ``apps.accounts.tests.test_scoping`` asserts that the ones that
exist today do.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions as drf_permissions

__all__ = [
    "DENY",
    "OWN",
    "SHARED",
    "DenyExternalUsers",
    "OwnerScopedQuerySetMixin",
    "StaffOnlyActionsMixin",
    "is_external_user",
    "scope_queryset",
]

#: External users may not see a single row of this model.
DENY = "deny"
#: External users see only rows linked to them through ``owner_lookups``.
OWN = "own"
#: The rows are catalogue/reference data with no personal dimension
#: (price lists, lesson types, published camp programmes).
SHARED = "shared"


def is_external_user(user) -> bool:
    """True for an authenticated customer or student (not personnel).

    Anonymous users are *not* external users; they have no rows at all and the
    surrounding ``IsAuthenticated`` / ``LoginRequired`` layer rejects them
    first. Treating them as "not external" here keeps this helper honest: it
    answers a question about roles, not about authentication.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if getattr(user, "is_superuser", False):
        return False
    return not getattr(user, "is_staff_member", False)


def scope_queryset(
    queryset: QuerySet,
    user,
    *,
    access: str = DENY,
    lookups: tuple[str, ...] = (),
) -> QuerySet:
    """Return *queryset* narrowed to what *user* is allowed to see.

    Personnel and superusers get the queryset unchanged. Anonymous callers get
    nothing. External users get ``none()`` unless *access* opens the surface.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return queryset.none()
    if not is_external_user(user):
        return queryset
    if access == SHARED:
        return queryset
    if access != OWN or not lookups:
        return queryset.none()

    condition = Q()
    for lookup in lookups:
        condition |= Q(**{lookup: user})
    # ``distinct()`` matters: several lookups traverse reverse many-relations
    # (a lesson reached through its attendances), which would otherwise repeat
    # a row once per matching child and inflate pagination counts.
    return queryset.filter(condition).distinct()


class OwnerScopedQuerySetMixin:
    """Narrow ``get_queryset()`` to the requesting user's own rows.

    Works for DRF viewsets and Django class-based views alike: both expose
    ``self.request`` and both funnel list *and* detail access through
    ``get_queryset()``.

    Place it **first** in the base-class list so its ``get_queryset()`` wraps
    the view's own.
    """

    #: One of :data:`DENY`, :data:`OWN`, :data:`SHARED`.
    external_access: str = DENY
    #: ORM lookups from this model to ``accounts.User``, OR-ed together.
    owner_lookups: tuple[str, ...] = ()

    def scope(self, queryset: QuerySet) -> QuerySet:
        """Apply this view's ownership rule to an arbitrary queryset.

        Use it inside custom actions that do not go through
        ``get_queryset()`` — an "overdue invoices" endpoint, a report feed, an
        HTMX partial that starts from a selector function.
        """
        return scope_queryset(
            queryset,
            getattr(self.request, "user", None),
            access=self.external_access,
            lookups=tuple(self.owner_lookups),
        )

    def get_queryset(self):  # type: ignore[override]
        return self.scope(super().get_queryset())


class DenyExternalUsers(drf_permissions.BasePermission):
    """Refuse customers and students outright.

    Used for endpoints whose *whole purpose* is an operational overview —
    a camp roster, a daily register, a revenue summary. There is no
    "own rows" projection of those; the honest answer is 403.
    """

    message = _("This endpoint is available to school personnel only.")

    def has_permission(self, request, view) -> bool:
        return not is_external_user(getattr(request, "user", None))


class StaffOnlyActionsMixin:
    """Close the named DRF actions to external accounts.

    ``staff_only_actions`` lists ``@action`` method names (or standard viewset
    actions) that must never answer a customer or student, however the
    capability matrix is configured.
    """

    staff_only_actions: tuple[str, ...] = ()

    def get_permissions(self):  # type: ignore[override]
        permissions_list = list(super().get_permissions())
        if getattr(self, "action", None) in tuple(self.staff_only_actions):
            permissions_list.append(DenyExternalUsers())
        return permissions_list
