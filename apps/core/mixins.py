"""View mixins shared by every module.

These exist so all 20+ modules behave identically: the same search parameter,
the same HTMX partial-rendering rule, the same audit behaviour on create and
update, and the same date-range filter vocabulary.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.accounts import scoping
from apps.audit.models import AuditAction
from apps.audit.services import diff_instances, record_audit

from .utils import parse_date_range


class HtmxPartialMixin:
    """Render ``partial_template_name`` when the request comes from HTMX.

    Lets one view serve both a full page and a live-updating table fragment.
    """

    partial_template_name: str | None = None

    def get_template_names(self):
        if self.partial_template_name and getattr(self.request, "htmx", False):
            return [self.partial_template_name]
        return super().get_template_names()


class SearchableListMixin:
    """Adds ``?q=`` free-text search over ``search_fields``."""

    search_fields: tuple[str, ...] = ()
    search_param = "q"

    def get_search_term(self) -> str:
        return self.request.GET.get(self.search_param, "").strip()

    def apply_search(self, queryset):
        term = self.get_search_term()
        if not term or not self.search_fields:
            return queryset
        condition = Q()
        for field in self.search_fields:
            condition |= Q(**{f"{field}__icontains": term})
        return queryset.filter(condition)

    def get_queryset(self):
        return self.apply_search(super().get_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_term"] = self.get_search_term()
        return context


class DateRangeMixin:
    """Adds the standard ``today / 7 / 30 / 90 / 180 / 365 / custom`` filter."""

    date_field = "created_at"

    def get_date_range(self):
        return parse_date_range(self.request)

    def apply_date_range(self, queryset):
        start, end, _label = self.get_date_range()
        if start:
            queryset = queryset.filter(**{f"{self.date_field}__gte": start})
        if end:
            queryset = queryset.filter(**{f"{self.date_field}__lte": end})
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, label = self.get_date_range()
        context["range_start"] = start
        context["range_end"] = end
        context["range_label"] = label
        context["range_key"] = self.request.GET.get("range", "30")
        return context


class AuditedCreateMixin:
    """Stamps ``created_by`` and writes a ``create`` audit entry."""

    audit_action = AuditAction.CREATE
    success_message = _("Record created.")

    def form_valid(self, form):
        if hasattr(form.instance, "created_by_id") and self.request.user.is_authenticated:
            form.instance.created_by = self.request.user
        if hasattr(form.instance, "updated_by_id") and self.request.user.is_authenticated:
            form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        record_audit(
            self.request,
            action=self.audit_action,
            instance=self.object,
            description=_("%(model)s created")
            % {"model": self.object._meta.verbose_name.title()},
        )
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class AuditedUpdateMixin:
    """Stamps ``updated_by`` and records the field-level diff."""

    audit_action = AuditAction.UPDATE
    success_message = _("Changes saved.")

    def form_valid(self, form):
        before = self.model.all_objects.filter(pk=self.object.pk).first() if hasattr(
            self.model, "all_objects"
        ) else self.model.objects.filter(pk=self.object.pk).first()

        if hasattr(form.instance, "updated_by_id") and self.request.user.is_authenticated:
            form.instance.updated_by = self.request.user
        response = super().form_valid(form)

        changes = diff_instances(before, self.object, fields=form.changed_data or None)
        if changes:
            record_audit(
                self.request,
                action=self.audit_action,
                instance=self.object,
                changes=changes,
                description=_("%(model)s updated")
                % {"model": self.object._meta.verbose_name.title()},
            )
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class AuditedDeleteMixin:
    """Soft-deletes and records a ``delete`` audit entry."""

    audit_action = AuditAction.DELETE
    success_message = _("Record deleted.")

    def form_valid(self, form):
        obj = self.get_object()
        record_audit(
            self.request,
            action=self.audit_action,
            instance=obj,
            description=_("%(model)s deleted") % {"model": obj._meta.verbose_name.title()},
        )
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class OwnerScopedQuerysetMixin(scoping.OwnerScopedQuerySetMixin):
    """Restrict external users (customers/students) to their own records.

    This is the HTML-view face of :mod:`apps.accounts.scoping`; the REST API
    uses the same engine, so one declaration cannot drift from the other.

    Declare ownership in one of two ways::

        owner_lookup = "customer__user"                 # single path
        owner_lookups = ("customer__user", "student__customer__user")

    or open the surface deliberately with ``external_access = scoping.SHARED``
    for catalogue data.

    **Fail-closed.** A view that mixes this in without declaring anything shows
    an external user nothing at all. The previous version returned the
    *unfiltered* queryset in that case, which meant a missing ``owner_lookup``
    silently published the whole table; that is the defect this rewrite closes.

    Views that build a queryset without calling ``super().get_queryset()`` must
    wrap it in :meth:`scope` themselves.
    """

    #: Legacy single-path form, kept because it reads better for the common case.
    owner_lookup: str | None = None

    def scope(self, queryset):
        lookups = tuple(self.owner_lookups)
        if not lookups and self.owner_lookup:
            lookups = (self.owner_lookup,)
        access = self.external_access
        if access == scoping.DENY and lookups:
            access = scoping.OWN
        return scoping.scope_queryset(
            queryset, self.request.user, access=access, lookups=lookups
        )
