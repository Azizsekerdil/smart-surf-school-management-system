"""HTML views for surf conditions.

The dashboard is the screen a surf school opens first every morning, so three
things are non-negotiable in here:

* It renders from **stored** readings. Nothing on the page waits on an HTTP call
  to a weather service; the refresh is an explicit button, and a provider outage
  degrades to "last seen at 06:12", never to a spinner or a 500.
* The score is labelled **Computed**, and its factor breakdown is one click away
  on every gauge. A number a coach cannot interrogate is a number they should
  not act on.
* Anything written by a model lives in its own ``ai-surface`` block with the
  ``ai_chip``, is loaded separately, and never touches the score.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.enums import SurfLevel
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from . import selectors, services
from .forms import ConditionFilterForm, SurfConditionForm
from .models import SurfCondition
from .providers.registry import health_report

logger = logging.getLogger("apps.surf_conditions")


class SpotSelectionMixin:
    """Resolves ``?spot=`` to a surf spot, falling back to the school default."""

    def get_spot_queryset(self):
        return selectors.spots_with_conditions()

    def get_spot(self):
        from apps.locations.services import get_primary_spot

        spots = self.get_spot_queryset()
        requested = (self.request.GET.get("spot") or "").strip()
        if requested:
            spot = (
                spots.filter(pk=requested).first()
                if requested.isdigit()
                else spots.filter(slug=requested).first()
            )
            if spot is not None:
                return spot
        return get_primary_spot()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spots = list(self.get_spot_queryset())
        context["spots"] = spots
        context["selected_spot"] = getattr(self, "spot", None)
        return context


class ConditionDashboardView(CapabilityRequiredMixin, SpotSelectionMixin, TemplateView):
    """The morning screen: now, the scores, the week and the hourly chart."""

    capability = "surf_conditions.view"
    template_name = "surf_conditions/dashboard.html"

    def get_context_data(self, **kwargs):
        self.spot = self.get_spot()
        context = super().get_context_data(**kwargs)

        if self.spot is None:
            context["payload"] = None
            return context

        payload = services.dashboard_payload(self.spot)
        context["payload"] = payload
        context["condition"] = payload["condition"]
        context["scores"] = payload["scores"]
        context["forecasts"] = payload["forecasts"]
        context["chart"] = payload["chart"]
        context["attribution"] = payload["attribution"]
        context["provider_label"] = payload["provider_label"]
        context["provides_marine_data"] = payload["provides_marine_data"]
        context["licence_note"] = payload["licence_note"]
        context["weights"] = payload["weights"]
        context["level_choices"] = SurfLevel.choices
        context["today"] = timezone.localdate()
        # Chart legends are serialised as JSON rather than pasted into the
        # script: a translated label containing an apostrophe would otherwise
        # break the page.
        context["chart_text"] = {
            "wave": _("Wave height (m)"),
            "wind": _("Wind (km/h)"),
            "gust": _("Gusts (km/h)"),
        }
        return context


class SpotConditionPanelView(CapabilityRequiredMixin, TemplateView):
    """The embeddable "live conditions" panel the locations detail page pulls in.

    ``apps.locations`` reverses ``surf_conditions:spot_panel`` without importing
    this module; keeping the contract to a URL name is what lets both apps ship
    independently.
    """

    capability = "surf_conditions.view"
    template_name = "surf_conditions/partials/spot_panel.html"

    def get_context_data(self, **kwargs):
        from apps.locations.models import SurfSpot

        context = super().get_context_data(**kwargs)
        spot = get_object_or_404(SurfSpot, pk=self.kwargs["pk"])
        condition = services.current_or_nearest(spot)

        context["spot"] = spot
        context["condition"] = condition
        context["dashboard_url"] = f"{reverse('surf_conditions:dashboard')}?spot={spot.pk}"
        if condition is not None:
            stored = list(condition.scores.all())
            context["scores"] = sorted(
                stored, key=lambda score: -score.score
            )[:3]
            context["spot_level_scores"] = [
                score for score in stored if spot.suits_level(score.level)
            ]
        else:
            context["scores"] = []
            context["spot_level_scores"] = []
        return context


class ConditionHistoryView(
    CapabilityRequiredMixin,
    SpotSelectionMixin,
    SearchableListMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    ListView,
):
    """Every stored reading, filterable — the evidence trail behind past calls."""

    capability = "surf_conditions.view"
    model = SurfCondition
    template_name = "surf_conditions/condition_list.html"
    partial_template_name = "surf_conditions/partials/condition_table.html"
    context_object_name = "conditions"
    paginate_by = 25
    date_field = "recorded_at"
    search_fields = ("spot__name", "spot__code", "weather_description", "provider")

    def get_queryset(self):
        self.spot = self.get_spot()
        queryset = self.apply_search(
            selectors.condition_queryset(with_scores=False).filter(is_forecast=False)
        )

        requested = (self.request.GET.get("spot") or "").strip()
        if requested and self.spot is not None:
            queryset = queryset.filter(spot=self.spot)

        tide = (self.request.GET.get("tide") or "").strip()
        if tide:
            queryset = queryset.filter(tide_state=tide)

        source = (self.request.GET.get("source") or "").strip()
        if source:
            queryset = queryset.filter(source=source)

        return self.apply_date_range(queryset).order_by("-recorded_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spot_choices = [(str(spot.pk), f"{spot.code} · {spot.name}") for spot in context["spots"]]
        context["filter_form"] = ConditionFilterForm(
            spot_choices=spot_choices,
            initial={
                "spot": self.request.GET.get("spot", ""),
                "tide": self.request.GET.get("tide", ""),
                "source": self.request.GET.get("source", ""),
            },
        )
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "spot", "tide", "source", "range")
        )
        return context


class ConditionDetailView(CapabilityRequiredMixin, DetailView):
    """One reading, with the full factor breakdown for every level."""

    capability = "surf_conditions.view"
    model = SurfCondition
    template_name = "surf_conditions/condition_detail.html"
    context_object_name = "condition"

    def get_queryset(self):
        return selectors.condition_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        condition: SurfCondition = context["condition"]
        spot = condition.spot

        stored = {score.level: score for score in condition.scores.all()}
        rows = []
        for level, label in SurfLevel.choices:
            score = stored.get(level)
            if score is None:
                computed = services.calculate_surf_score(condition, level)
                if not computed["has_data"]:
                    continue
                rows.append(
                    {
                        "level": level,
                        "label": str(label),
                        "score": computed["score"],
                        "color": "slate",
                        "is_safe": computed["is_safe"],
                        "recommendation": computed["recommendation"],
                        "factors": computed["factors"],
                        "suits_spot": spot.suits_level(level),
                    }
                )
            else:
                rows.append(
                    {
                        "level": level,
                        "label": str(label),
                        "score": score.score,
                        "color": score.band_color,
                        "is_safe": score.is_safe_for_level,
                        "recommendation": score.recommendation,
                        "factors": score.factors or [],
                        "suits_spot": spot.suits_level(level),
                    }
                )
        context["scores"] = rows
        context["spot"] = spot
        context["weights"] = {
            key: int(round(value * 100)) for key, value in services.SCORE_WEIGHTS.items()
        }
        return context


class ConditionCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    """Log a reading by hand — what the coach actually saw at the water."""

    capability = "surf_conditions.add"
    model = SurfCondition
    form_class = SurfConditionForm
    template_name = "surf_conditions/condition_form.html"
    success_message = _lazy("Reading logged.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        requested = (self.request.GET.get("spot") or "").strip()
        if requested.isdigit():
            initial["spot"] = requested
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        # A hand-logged reading is scored by exactly the same arithmetic as a
        # fetched one; there is no second, softer code path.
        services.score_condition(self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Log a reading")
        context["cancel_url"] = reverse("surf_conditions:history")
        return context

    def get_success_url(self):
        return reverse("surf_conditions:detail", kwargs={"pk": self.object.pk})


class ConditionUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    """Correct a hand-logged reading. Provider readings stay untouched."""

    capability = "surf_conditions.change"
    model = SurfCondition
    form_class = SurfConditionForm
    template_name = "surf_conditions/condition_form.html"
    success_message = _lazy("Reading updated.")

    def get_queryset(self):
        # Editing a fetched reading would break the audit trail: the record is
        # supposed to say what the provider reported, not what we prefer.
        return SurfCondition.objects.filter(
            source=SurfCondition.Source.MANUAL, is_forecast=False
        ).select_related("spot")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        services.score_condition(self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit reading")
        context["cancel_url"] = reverse(
            "surf_conditions:detail", kwargs={"pk": self.object.pk}
        )
        return context

    def get_success_url(self):
        return reverse("surf_conditions:detail", kwargs={"pk": self.object.pk})


class RefreshConditionsView(CapabilityRequiredMixin, View):
    """POST-only: pull a fresh reading for one spot right now.

    Wired to an HTMX button so the answer swaps into the "now" card without a
    page reload. A provider that is down produces a message, never an error page.
    """

    capability = "surf_conditions.change"

    def post(self, request, pk: int, *args, **kwargs):
        from apps.locations.models import SurfSpot

        spot = get_object_or_404(SurfSpot, pk=pk)
        condition = services.refresh_spot_conditions(spot)

        if condition is None:
            record_audit(
                request,
                action=AuditAction.SYSTEM,
                instance=spot,
                description=_("Surf conditions refresh for %(spot)s returned no data.")
                % {"spot": spot.name},
            )
            message = _(
                "The weather service did not answer. The last stored reading is still shown."
            )
            if getattr(request, "htmx", False):
                return HttpResponse(
                    f'<div class="alert-warning">{message}</div>', status=200
                )
            messages.warning(request, message)
            return redirect(f"{reverse('surf_conditions:dashboard')}?spot={spot.pk}")

        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=condition,
            description=_("Surf conditions refreshed for %(spot)s.") % {"spot": spot.name},
        )

        if getattr(request, "htmx", False):
            payload = services.dashboard_payload(spot)
            return render(
                request,
                "surf_conditions/partials/now_card.html",
                {
                    "spot": spot,
                    "condition": payload["condition"],
                    "scores": payload["scores"],
                    "attribution": payload["attribution"],
                    "provider_label": payload["provider_label"],
                    "provides_marine_data": payload["provides_marine_data"],
                    "weights": payload["weights"],
                },
            )

        messages.success(request, _("Conditions refreshed for %(spot)s.") % {"spot": spot.name})
        return redirect(f"{reverse('surf_conditions:dashboard')}?spot={spot.pk}")


class ProviderHealthView(CapabilityRequiredMixin, TemplateView):
    """On-demand probe of every configured data source.

    Deliberately *not* part of the dashboard's own context: it makes real
    network calls, and the morning screen must never be slower than the stored
    data it is drawing.
    """

    capability = "surf_conditions.view"
    template_name = "surf_conditions/partials/provider_health.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["providers"] = health_report()
        context["checked_at"] = timezone.now()
        return context


class ConditionBriefingView(CapabilityRequiredMixin, TemplateView):
    """An AI-written paragraph *about* the computed numbers.

    The model receives the figures this module already computed and is asked to
    put them into two sentences. It cannot add a number, it never sets the score,
    and the output is rendered inside ``.ai-surface`` with the AI chip. If the
    assistant is offline the block simply does not appear.
    """

    capability = "ai.view"
    template_name = "surf_conditions/partials/ai_briefing.html"

    def get_context_data(self, **kwargs):
        from apps.locations.models import SurfSpot

        context = super().get_context_data(**kwargs)
        spot = get_object_or_404(SurfSpot, pk=self.kwargs["pk"])
        condition = services.current_or_nearest(spot)
        context["spot"] = spot

        if condition is None:
            context["narrative"] = ""
            context["is_ai_generated"] = False
            return context

        data = {
            "spot": spot.name,
            "recorded_at": condition.recorded_at.isoformat(),
            "is_forecast": condition.is_forecast,
            "wave_height_m": condition.wave_height_m,
            "swell_period_s": condition.effective_period_s,
            "wind_speed_kmh": condition.wind_speed_kmh,
            "wind_type": condition.wind_type,
            "tide_state": condition.tide_state,
            "water_temperature_c": condition.water_temperature_c,
            "weather": condition.weather_description,
            "computed_scores": [
                {
                    "level": score.level,
                    "score": score.score,
                    "is_safe": score.is_safe_for_level,
                }
                for score in condition.scores.all()
            ],
        }

        narrative, is_ai = "", False
        try:
            from apps.ai.services import summarise_for_dashboard

            narrative, is_ai = summarise_for_dashboard(
                self.request.user,
                (
                    "Brief the surf school staff on these conditions. Do not add or "
                    "change any number. Do not restate the scores as your own judgement."
                ),
                data,
            )
        except Exception as exc:  # noqa: BLE001 - the assistant is optional, the page is not
            logger.info("AI briefing unavailable for %s: %s", spot, exc)

        context["narrative"] = narrative
        context["is_ai_generated"] = is_ai
        context["condition"] = condition
        return context
