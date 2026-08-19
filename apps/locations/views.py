"""HTML views for surf locations.

The detail screen carries an integration point for the surf-conditions module:
``templates/locations/partials/spot_conditions.html`` resolves the optional URL
name ``surf_conditions:spot_panel``. While that route does not exist the panel
renders the spot's target conditions; once it does, HTMX loads the live reading
into the same container without either module importing the other.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import SurfLevel
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from . import selectors, services
from .forms import SpotFilterForm, SpotHazardForm, SurfSpotForm
from .models import SpotHazard, SurfSpot, compass_label

#: Card grid and data table show the same rows; the operator picks the shape.
VIEW_MODES = ("cards", "table")


class SurfSpotListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "locations.view"
    model = SurfSpot
    template_name = "locations/surfspot_list.html"
    partial_template_name = "locations/partials/spot_results.html"
    context_object_name = "spots"
    paginate_by = 25
    search_fields = ("name", "code", "description", "access_notes", "parking_info")

    def get_view_mode(self) -> str:
        mode = self.request.GET.get("view", "cards")
        return mode if mode in VIEW_MODES else "cards"

    def get_queryset(self):
        # Start from the prefetching selector rather than the bare model manager,
        # so neither the card grid nor the table costs a query per row.
        queryset = self.apply_search(selectors.spot_queryset())

        status = self.request.GET.get("status", "active")
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)

        level = self.request.GET.get("level", "")
        if level in dict(SurfLevel.choices):
            suitable = services.spots_suitable_for_level(level, include_inactive=True)
            queryset = queryset.filter(pk__in=suitable.values("pk"))

        break_type = self.request.GET.get("break_type", "")
        if break_type:
            queryset = queryset.filter(break_type=break_type)

        lifeguard = self.request.GET.get("lifeguard", "")
        if lifeguard == "yes":
            queryset = queryset.filter(lifeguard_on_duty=True)
        elif lifeguard == "no":
            queryset = queryset.filter(lifeguard_on_duty=False)

        return queryset.order_by("-is_primary", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = SpotFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "level": self.request.GET.get("level", ""),
                "break_type": self.request.GET.get("break_type", ""),
                "lifeguard": self.request.GET.get("lifeguard", ""),
                "status": self.request.GET.get("status", "active"),
            }
        )
        context["view_mode"] = self.get_view_mode()
        context["stats"] = services.spot_overview_stats()
        context["primary_spot"] = services.get_primary_spot()
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "level", "break_type", "lifeguard")
        ) or self.request.GET.get("status", "active") != "active"
        return context


class SurfSpotDetailView(CapabilityRequiredMixin, DetailView):
    capability = "locations.view"
    model = SurfSpot
    template_name = "locations/surfspot_detail.html"
    context_object_name = "spot"

    def get_queryset(self):
        return selectors.spot_detail_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spot: SurfSpot = context["spot"]

        context["open_hazards"] = selectors.hazards_for_spot(spot, only_active=True)
        context["cleared_hazards"] = spot.hazards.filter(is_active=False).order_by("-updated_at")
        context["blocking_hazards"] = services.blocking_hazards(spot)

        level_labels = dict(SurfLevel.choices)
        context["level_matrix"] = [
            {
                "value": value,
                "label": level_labels.get(value, value),
                "suitable": spot.suits_level(value),
                "max_group": services.max_group_size(spot, value),
                "max_group_minors": services.max_group_size(spot, value, has_minors=True),
            }
            for value in level_labels
        ]

        # Reference readiness check for an empty water and an unknown forecast:
        # it surfaces standing problems (archived spot, critical hazard, missing
        # hospital) without pretending to know today's conditions.
        context["readiness"] = services.assess_spot_for_group(
            spot, level=spot.min_level, group_size=1, occupied_students=0
        )
        context["offshore_compass"] = compass_label(spot.offshore_direction_deg)
        context["is_primary_spot"] = spot.is_primary
        return context


class SurfSpotCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "locations.add"
    model = SurfSpot
    form_class = SurfSpotForm
    template_name = "locations/surfspot_form.html"
    success_message = _lazy("Surf spot created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New surf spot")
        context["cancel_url"] = reverse("locations:list")
        return context

    def get_success_url(self):
        return reverse("locations:detail", kwargs={"pk": self.object.pk})


class SurfSpotUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "locations.change"
    model = SurfSpot
    form_class = SurfSpotForm
    template_name = "locations/surfspot_form.html"
    success_message = _lazy("Surf spot updated.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit surf spot")
        context["cancel_url"] = reverse("locations:detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        return reverse("locations:detail", kwargs={"pk": self.object.pk})


class SurfSpotDeleteView(CapabilityRequiredMixin, DeleteView):
    """Archive a spot. Soft delete — history in other modules must survive."""

    capability = "locations.delete"
    model = SurfSpot
    template_name = "locations/surfspot_confirm_delete.html"
    context_object_name = "spot"
    success_url = reverse_lazy("locations:list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed, reason = services.can_archive_spot(self.object)
        context["can_archive"] = allowed
        context["block_reason"] = reason
        context["open_hazards"] = selectors.hazards_for_spot(self.object)
        return context

    def form_valid(self, form):
        spot = self.get_object()
        try:
            services.archive_spot(spot, request=self.request, user=self.request.user)
        except ValidationError as error:
            for message in error.messages:
                messages.error(self.request, message)
            return redirect("locations:detail", pk=spot.pk)
        messages.success(
            self.request, _("Surf spot “%(name)s” archived.") % {"name": spot.name}
        )
        return HttpResponseRedirect(self.get_success_url())


class SetPrimarySpotView(CapabilityRequiredMixin, View):
    """POST-only: move the default-spot flag."""

    capability = "locations.manage"

    def post(self, request, pk: int, *args, **kwargs):
        spot = get_object_or_404(SurfSpot, pk=pk)
        try:
            services.set_primary_spot(spot, request=request, user=request.user)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("“%(name)s” is now the default surf spot.") % {"name": spot.name},
            )
        return redirect("locations:detail", pk=spot.pk)


# ---------------------------------------------------------------------------
# Hazards
# ---------------------------------------------------------------------------
class SpotHazardCreateView(CapabilityRequiredMixin, CreateView):
    capability = "locations.change"
    model = SpotHazard
    form_class = SpotHazardForm
    template_name = "locations/spothazard_form.html"

    def get_spot(self) -> SurfSpot:
        return get_object_or_404(SurfSpot, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["spot"] = self.get_spot()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spot = self.get_spot()
        context["spot"] = spot
        context["form_title"] = _("New hazard")
        context["cancel_url"] = reverse("locations:detail", kwargs={"pk": spot.pk})
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            self.request,
            action=AuditAction.CREATE,
            instance=self.object,
            description=_("Hazard %(name)s recorded at %(spot)s")
            % {"name": self.object.name, "spot": self.object.spot.name},
        )
        messages.success(self.request, _("Hazard recorded."))
        return response

    def get_success_url(self):
        return reverse("locations:detail", kwargs={"pk": self.object.spot_id})


class SpotHazardUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "locations.change"
    model = SpotHazard
    form_class = SpotHazardForm
    template_name = "locations/spothazard_form.html"
    success_message = _lazy("Hazard updated.")

    def get_queryset(self):
        return SpotHazard.objects.select_related("spot")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["spot"] = self.object.spot
        context["form_title"] = _("Edit hazard")
        context["cancel_url"] = reverse("locations:detail", kwargs={"pk": self.object.spot_id})
        return context

    def get_success_url(self):
        return reverse("locations:detail", kwargs={"pk": self.object.spot_id})


class SpotHazardToggleView(CapabilityRequiredMixin, View):
    """POST-only: clear a hazard, or reopen one that has come back."""

    capability = "locations.change"

    def post(self, request, pk: int, *args, **kwargs):
        hazard = get_object_or_404(SpotHazard.objects.select_related("spot"), pk=pk)
        services.set_hazard_active(
            hazard, is_active=not hazard.is_active, request=request, user=request.user
        )
        if hazard.is_active:
            messages.success(
                request, _("Hazard “%(name)s” reopened.") % {"name": hazard.name}
            )
        else:
            messages.success(
                request, _("Hazard “%(name)s” marked as cleared.") % {"name": hazard.name}
            )
        return redirect("locations:detail", pk=hazard.spot_id)
