"""CRM screens.

Views orchestrate only: they read the request, call a service, and choose a
template. Every pipeline rule, every conversion decision and every campaign
transition lives in :mod:`apps.crm.services`.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.enums import BookingSource
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from .forms import (
    CampaignForm,
    CampaignStatusForm,
    InteractionForm,
    LeadConvertForm,
    LeadForm,
    LeadStatusForm,
    SegmentForm,
)
from .models import Campaign, Interaction, Lead, Segment
from .selectors import due_follow_ups, due_lead_actions, lead_funnel
from .services import (
    advance_lead_status,
    campaign_performance,
    complete_follow_up,
    convert_lead_to_customer,
    customer_retention_stats,
    log_interaction,
    preview_segment_size,
    resolve_segment,
    set_campaign_status,
)

ZERO = Decimal("0.00")

#: Cap the number of cards rendered per kanban column. A pipeline with 900 open
#: leads must not render 900 draggable cards on a beach tablet.
BOARD_COLUMN_LIMIT = 40


MONEY_OUTPUT = DecimalField(max_digits=14, decimal_places=2)


def _money_sum(queryset, field: str) -> Decimal:
    return queryset.aggregate(
        total=Coalesce(Sum(field), Value(ZERO), output_field=MONEY_OUTPUT)
    )["total"]


def _weighted_sum(queryset) -> Decimal:
    """Pipeline value discounted by win probability, summed in the database."""
    weighted = ExpressionWrapper(
        F("expected_value") * F("probability") / Value(Decimal("100")),
        output_field=MONEY_OUTPUT,
    )
    return queryset.aggregate(
        total=Coalesce(Sum(weighted), Value(ZERO), output_field=MONEY_OUTPUT)
    )["total"]


def _safe_redirect_target(request, fallback: str) -> str:
    """Return the referring page only when it belongs to this site."""
    referer = request.META.get("HTTP_REFERER") or ""
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return fallback


def _htmx(request) -> bool:
    return bool(getattr(request, "htmx", False))


def _trigger(response: HttpResponse, payload: dict) -> HttpResponse:
    """Attach an ``HX-Trigger`` payload so other fragments can refresh."""
    response["HX-Trigger"] = json.dumps(payload)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class CrmDashboardView(CapabilityRequiredMixin, DateRangeMixin, TemplateView):
    """Pipeline, follow-up queue and retention in one screen."""

    capability = "crm.view"
    template_name = "crm/dashboard.html"
    date_field = "created_at"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()

        leads = Lead.objects.all()
        period_leads = leads
        if start:
            period_leads = period_leads.filter(created_at__gte=start)
        if end:
            period_leads = period_leads.filter(created_at__lte=end)

        open_leads = leads.filter(status__in=Lead.OPEN_STATUSES)
        won_in_period = period_leads.filter(status=Lead.Status.WON)
        lost_in_period = period_leads.filter(status=Lead.Status.LOST)

        closed_count = won_in_period.count() + lost_in_period.count()
        win_rate = (
            round(won_in_period.count() / closed_count * 100, 1) if closed_count else None
        )

        weighted = _weighted_sum(open_leads)

        context.update(
            {
                "funnel": lead_funnel(),
                "open_lead_count": open_leads.count(),
                "pipeline_value": _money_sum(open_leads, "expected_value"),
                "weighted_pipeline": weighted,
                "new_lead_count": period_leads.count(),
                "won_count": won_in_period.count(),
                "lost_count": lost_in_period.count(),
                "won_value": _money_sum(won_in_period, "expected_value"),
                "win_rate": win_rate,
                "overdue_actions": due_lead_actions(within_days=0).count(),
                "lead_actions": due_lead_actions(within_days=7)[:10],
                "follow_ups": due_follow_ups(within_days=7).select_related("customer")[:10],
                "recent_interactions": (
                    Interaction.objects.select_related("lead", "handled_by", "customer")[:10]
                ),
                "retention": customer_retention_stats(start, end),
                "active_campaigns": (
                    Campaign.objects.filter(
                        status__in=(Campaign.Status.RUNNING, Campaign.Status.SCHEDULED)
                    ).select_related("target_segment")[:5]
                ),
                "source_breakdown": (
                    period_leads.values("source")
                    .annotate(count=Count("id"))
                    .order_by("-count")[:6]
                ),
                "segments": Segment.objects.all()[:6],
            }
        )
        return context


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
class LeadListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "crm.view"
    model = Lead
    template_name = "crm/lead_list.html"
    partial_template_name = "crm/partials/lead_table.html"
    context_object_name = "leads"
    paginate_by = 25
    search_fields = ("first_name", "last_name", "email", "phone", "interest", "next_action")

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("assigned_to", "converted_customer")
            .annotate(interaction_count=Count("interactions", distinct=True))
        )
        status = self.request.GET.get("status", "").strip()
        if status == "open":
            queryset = queryset.filter(status__in=Lead.OPEN_STATUSES)
        elif status in Lead.Status.values:
            queryset = queryset.filter(status=status)

        source = self.request.GET.get("source", "").strip()
        if source:
            queryset = queryset.filter(source=source)

        owner = self.request.GET.get("owner", "").strip()
        if owner == "me" and self.request.user.is_authenticated:
            queryset = queryset.filter(assigned_to=self.request.user)
        elif owner == "none":
            queryset = queryset.filter(assigned_to__isnull=True)
        elif owner.isdigit():
            queryset = queryset.filter(assigned_to_id=int(owner))

        if self.request.GET.get("due") == "1":
            queryset = queryset.filter(next_action_at__isnull=False).order_by("next_action_at")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "statuses": Lead.Status.choices,
                "sources": BookingSource.choices,
                "current_status": self.request.GET.get("status", ""),
                "current_source": self.request.GET.get("source", ""),
                "current_owner": self.request.GET.get("owner", ""),
                "only_due": self.request.GET.get("due") == "1",
            }
        )
        return context


class LeadBoardView(CapabilityRequiredMixin, TemplateView):
    """Kanban board — one column per pipeline stage."""

    capability = "crm.view"
    template_name = "crm/lead_board.html"

    def get_board_columns(self):
        queryset = Lead.objects.select_related("assigned_to")
        owner = self.request.GET.get("owner", "").strip()
        if owner == "me" and self.request.user.is_authenticated:
            queryset = queryset.filter(assigned_to=self.request.user)

        labels = dict(Lead.Status.choices)
        stages = list(Lead.STAGE_ORDER)

        columns = []
        for value, label in Lead.Status.choices:
            column_queryset = queryset.filter(status=value)
            total = column_queryset.count()

            # Which single-click moves make sense from this column. Winning is
            # deliberately not one of them: it goes through the convert screen.
            previous_status = next_status = None
            if value in stages:
                index = stages.index(value)
                if index > 0:
                    previous_status = stages[index - 1]
                if index + 1 < len(stages) and stages[index + 1] != Lead.Status.WON:
                    next_status = stages[index + 1]
            elif value == Lead.Status.LOST:
                previous_status = Lead.Status.CONTACTED

            columns.append(
                {
                    "status": value,
                    "label": label,
                    "count": total,
                    "value": _money_sum(column_queryset, "expected_value"),
                    "leads": list(column_queryset[:BOARD_COLUMN_LIMIT]),
                    "truncated": total > BOARD_COLUMN_LIMIT,
                    "prev_status": previous_status,
                    "prev_label": labels.get(previous_status, ""),
                    "next_status": next_status,
                    "next_label": labels.get(next_status, ""),
                    "can_lose": value in Lead.OPEN_STATUSES,
                    "can_convert": value == Lead.Status.PROPOSAL_SENT
                    or value == Lead.Status.QUALIFIED,
                }
            )
        return columns

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["columns"] = self.get_board_columns()
        context["column_limit"] = BOARD_COLUMN_LIMIT
        context["current_owner"] = self.request.GET.get("owner", "")
        return context

    def get_template_names(self):
        if _htmx(self.request):
            return ["crm/partials/lead_board.html"]
        return [self.template_name]

    @classmethod
    def render_board_fragment(cls, request) -> str:
        """Render just the board, for the response to a card move."""
        view = cls()
        view.request = request
        view.args = ()
        view.kwargs = {}
        return render_to_string(
            "crm/partials/lead_board.html", view.get_context_data(), request=request
        )


class LeadStatusView(CapabilityRequiredMixin, View):
    """POST target of a kanban card move (drag-and-drop or the arrow buttons)."""

    capability = "crm.change"

    @staticmethod
    def _error(message: str, status: int = 422) -> JsonResponse:
        # Shape understood by the global HTMX error handler in static/js/app.js.
        return JsonResponse(
            {"error": {"type": "validation_error", "message": message, "detail": {}}},
            status=status,
        )

    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        form = LeadStatusForm(request.POST)
        if not form.is_valid():
            return self._error(_("That is not a pipeline stage."), status=400)

        target = form.cleaned_data["status"]
        if target == Lead.Status.WON:
            # Winning means creating a customer — send the user to that screen.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("crm:lead_convert", kwargs={"pk": lead.pk})
            return response

        # ``HX-Prompt`` carries the reason typed into the browser prompt that the
        # "mark as lost" button opens, so the card needs no inline form.
        reason = form.cleaned_data.get("lost_reason") or request.headers.get("HX-Prompt", "")
        try:
            advance_lead_status(lead, target, user=request.user, lost_reason=reason)
        except ValidationError as exc:
            return self._error(" ".join(exc.messages))

        if not _htmx(request):
            messages.success(
                request,
                _("%(name)s moved to %(status)s.")
                % {"name": lead.full_name, "status": lead.get_status_display()},
            )
            return redirect(_safe_redirect_target(request, reverse("crm:lead_board")))

        return _trigger(
            HttpResponse(
                LeadBoardView.render_board_fragment(request), content_type="text/html"
            ),
            {
                "toast": {
                    "message": _("%(name)s moved to %(status)s.")
                    % {"name": lead.full_name, "status": lead.get_status_display()},
                    "level": "success",
                }
            },
        )


class LeadDetailView(CapabilityRequiredMixin, DetailView):
    capability = "crm.view"
    model = Lead
    template_name = "crm/lead_detail.html"
    context_object_name = "lead"

    def get_queryset(self):
        return Lead.objects.select_related("assigned_to", "converted_customer", "created_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["interactions"] = self.object.interactions.select_related("handled_by")[:50]
        context["statuses"] = Lead.Status.choices
        return context


class LeadInteractionsView(CapabilityRequiredMixin, DetailView):
    """HTMX fragment: the interaction timeline of one lead."""

    capability = "crm.view"
    model = Lead
    template_name = "crm/partials/interaction_timeline.html"
    context_object_name = "lead"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["interactions"] = self.object.interactions.select_related("handled_by")[:50]
        return context


class LeadCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "crm.add"
    model = Lead
    form_class = LeadForm
    template_name = "crm/lead_form.html"
    success_message = _("Lead created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("assigned_to", self.request.user.pk)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New lead")
        return context

    def get_success_url(self):
        return reverse("crm:lead_detail", kwargs={"pk": self.object.pk})


class LeadUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "crm.change"
    model = Lead
    form_class = LeadForm
    template_name = "crm/lead_form.html"
    context_object_name = "lead"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit lead")
        return context

    def get_success_url(self):
        return reverse("crm:lead_detail", kwargs={"pk": self.object.pk})


class LeadConvertView(CapabilityRequiredMixin, TemplateView):
    """Turn a lead into a customer — or link it to the one that already exists."""

    # Conversion writes a customer row, so it needs the customers permission too.
    all_capabilities = ("crm.change", "customers.add")
    template_name = "crm/lead_convert.html"

    def get_lead(self) -> Lead:
        return get_object_or_404(Lead.objects.select_related("converted_customer"), pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        from .services import find_matching_customer

        context = super().get_context_data(**kwargs)
        lead = self.get_lead()
        context["lead"] = lead
        context.setdefault("form", LeadConvertForm(lead=lead))
        context["duplicate"] = find_matching_customer(lead.email, lead.phone)
        context["interaction_count"] = lead.interactions.count()
        return context

    def post(self, request, *args, **kwargs):
        lead = self.get_lead()
        form = LeadConvertForm(request.POST, lead=lead)
        if form.is_valid():
            customer = (
                form.cleaned_data["customer"]
                if form.cleaned_data["mode"] == LeadConvertForm.MODE_LINK
                else None
            )
            try:
                created = convert_lead_to_customer(lead, user=request.user, customer=customer)
            except ValidationError as exc:
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                messages.success(
                    request,
                    _("%(lead)s is now the customer “%(customer)s”.")
                    % {"lead": lead.full_name, "customer": created},
                )
                return redirect("crm:lead_detail", pk=lead.pk)
        return self.render_to_response(self.get_context_data(form=form))


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
class InteractionListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "crm.view"
    model = Interaction
    template_name = "crm/interaction_list.html"
    partial_template_name = "crm/partials/interaction_table.html"
    context_object_name = "interactions"
    paginate_by = 25
    search_fields = ("subject", "body", "lead__first_name", "lead__last_name")

    def get_queryset(self):
        queryset = super().get_queryset().select_related("lead", "handled_by", "customer")
        kind = self.request.GET.get("kind", "").strip()
        if kind in Interaction.Kind.values:
            queryset = queryset.filter(kind=kind)
        sentiment = self.request.GET.get("sentiment", "").strip()
        if sentiment in Interaction.Sentiment.values:
            queryset = queryset.filter(sentiment=sentiment)
        if self.request.GET.get("follow_up") == "1":
            queryset = queryset.filter(follow_up_required=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "kinds": Interaction.Kind.choices,
                "sentiments": Interaction.Sentiment.choices,
                "current_kind": self.request.GET.get("kind", ""),
                "current_sentiment": self.request.GET.get("sentiment", ""),
                "only_follow_up": self.request.GET.get("follow_up") == "1",
            }
        )
        return context


class InteractionCreateView(CapabilityRequiredMixin, TemplateView):
    """Log an interaction. Opens as an HTMX modal from a lead or customer page.

    Callers pass ``?customer=<pk>`` or ``?lead=<pk>``; on success the view fires
    ``crm:interaction-logged`` so whichever timeline is on screen can refresh.
    """

    capability = "crm.add"
    template_name = "crm/interaction_form.html"
    modal_template_name = "crm/partials/interaction_modal.html"

    def get_template_names(self):
        if _htmx(self.request):
            return [self.modal_template_name]
        return [self.template_name]

    def get_initial(self) -> dict:
        initial = {}
        customer_id = self.request.GET.get("customer", "")
        lead_id = self.request.GET.get("lead", "")
        if customer_id.isdigit():
            initial["customer"] = int(customer_id)
        if lead_id.isdigit():
            initial["lead"] = int(lead_id)
        kind = self.request.GET.get("kind", "")
        if kind in Interaction.Kind.values:
            initial["kind"] = kind
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault(
            "form", InteractionForm(initial=self.get_initial(), user=self.request.user)
        )
        context["title"] = _("Log an interaction")
        lead_id = self.request.GET.get("lead", "")
        context["lead"] = (
            Lead.objects.filter(pk=int(lead_id)).first() if lead_id.isdigit() else None
        )
        return context

    def post(self, request, *args, **kwargs):
        form = InteractionForm(request.POST, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            try:
                interaction = log_interaction(
                    kind=data["kind"],
                    subject=data["subject"],
                    body=data.get("body", ""),
                    customer=data.get("customer"),
                    lead=data.get("lead"),
                    direction=data["direction"],
                    occurred_at=data.get("occurred_at"),
                    duration_minutes=data.get("duration_minutes"),
                    follow_up_required=data.get("follow_up_required", False),
                    follow_up_at=data.get("follow_up_at"),
                    sentiment=data.get("sentiment", ""),
                    user=request.user,
                )
            except ValidationError as exc:
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                if _htmx(request):
                    # Empty 200 body: the modal region is swapped with nothing,
                    # which closes the dialog. The trigger tells whichever
                    # timeline is on screen to reload itself.
                    return _trigger(
                        HttpResponse("", content_type="text/html"),
                        {
                            "toast": {
                                "message": _("Interaction logged."),
                                "level": "success",
                            },
                            "crm:interaction-logged": {"id": interaction.pk},
                        },
                    )
                messages.success(request, _("Interaction logged."))
                if interaction.lead_id:
                    return redirect("crm:lead_detail", pk=interaction.lead_id)
                return redirect("crm:interaction_list")
        return self.render_to_response(self.get_context_data(form=form))


class FollowUpCompleteView(CapabilityRequiredMixin, View):
    """Mark an outstanding follow-up as handled."""

    capability = "crm.change"

    def post(self, request, pk):
        interaction = get_object_or_404(Interaction, pk=pk)
        complete_follow_up(interaction, user=request.user)
        if _htmx(request):
            return _trigger(
                HttpResponse(status=204),
                {
                    "toast": {"message": _("Follow-up closed."), "level": "success"},
                    "crm:interaction-logged": {"id": interaction.pk},
                },
            )
        messages.success(request, _("Follow-up closed."))
        return redirect(_safe_redirect_target(request, reverse("crm:dashboard")))


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
class CampaignListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "crm.view"
    model = Campaign
    template_name = "crm/campaign_list.html"
    partial_template_name = "crm/partials/campaign_table.html"
    context_object_name = "campaigns"
    paginate_by = 25
    search_fields = ("name", "code", "message_subject")

    def get_queryset(self):
        queryset = super().get_queryset().select_related("target_segment")
        status = self.request.GET.get("status", "").strip()
        if status in Campaign.Status.values:
            queryset = queryset.filter(status=status)
        channel = self.request.GET.get("channel", "").strip()
        if channel in Campaign.Channel.values:
            queryset = queryset.filter(channel=channel)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        totals = Campaign.objects.exclude(status=Campaign.Status.DRAFT)
        context.update(
            {
                "statuses": Campaign.Status.choices,
                "channels": Campaign.Channel.choices,
                "current_status": self.request.GET.get("status", ""),
                "current_channel": self.request.GET.get("channel", ""),
                "total_spend": _money_sum(totals, "actual_spend"),
                "total_revenue": _money_sum(totals, "revenue_attributed"),
                "running_count": Campaign.objects.filter(
                    status=Campaign.Status.RUNNING
                ).count(),
            }
        )
        return context


class CampaignDetailView(CapabilityRequiredMixin, DetailView):
    capability = "crm.view"
    model = Campaign
    template_name = "crm/campaign_detail.html"
    context_object_name = "campaign"

    def get_queryset(self):
        return Campaign.objects.select_related("target_segment")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["performance"] = campaign_performance(self.object)
        context["next_statuses"] = [
            (value, dict(Campaign.Status.choices)[value])
            for value in self.object.allowed_next_statuses()
        ]
        return context


class CampaignCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "crm.add"
    model = Campaign
    form_class = CampaignForm
    template_name = "crm/campaign_form.html"
    success_message = _("Campaign created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New campaign")
        return context

    def get_success_url(self):
        return reverse("crm:campaign_detail", kwargs={"pk": self.object.pk})


class CampaignUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "crm.change"
    model = Campaign
    form_class = CampaignForm
    template_name = "crm/campaign_form.html"
    context_object_name = "campaign"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit campaign")
        return context

    def get_success_url(self):
        return reverse("crm:campaign_detail", kwargs={"pk": self.object.pk})


class CampaignStatusView(CapabilityRequiredMixin, View):
    capability = "crm.change"

    def post(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk)
        form = CampaignStatusForm(request.POST)
        if form.is_valid():
            try:
                set_campaign_status(campaign, form.cleaned_data["status"], user=request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(
                    request,
                    _("Campaign %(code)s is now %(status)s.")
                    % {"code": campaign.code, "status": campaign.get_status_display()},
                )
        else:
            messages.error(request, _("That is not a campaign status."))
        return redirect("crm:campaign_detail", pk=campaign.pk)


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
class SegmentListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "crm.view"
    model = Segment
    template_name = "crm/segment_list.html"
    partial_template_name = "crm/partials/segment_table.html"
    context_object_name = "segments"
    paginate_by = 25
    search_fields = ("name", "description")

    def get_queryset(self):
        return super().get_queryset().annotate(campaign_count=Count("campaigns", distinct=True))


class SegmentDetailView(CapabilityRequiredMixin, DetailView):
    capability = "crm.view"
    model = Segment
    template_name = "crm/segment_detail.html"
    context_object_name = "segment"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = resolve_segment(self.object)
        context["members"] = list(queryset[:25])
        context["member_count"] = self.object.cached_count
        context["issues"] = self.object.criteria_issues()
        context["rules"] = self.object.describe_criteria()
        context["campaigns"] = self.object.campaigns.all()[:10]
        return context


class SegmentCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "crm.add"
    model = Segment
    form_class = SegmentForm
    template_name = "crm/segment_form.html"
    success_message = _("Segment created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        """Pre-fill from the dashboard's “build a win-back segment” shortcut."""
        initial = super().get_initial()
        days = self.request.GET.get("no_visit_days", "")
        if days.isdigit() and 1 <= int(days) <= 3650:
            initial["no_visit_days"] = int(days)
            initial.setdefault("name", _("Win-back — not seen for %(n)s days") % {"n": days})
            initial.setdefault("marketing_consent", "true")
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New segment")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        resolve_segment(self.object)
        return response

    def get_success_url(self):
        return reverse("crm:segment_detail", kwargs={"pk": self.object.pk})


class SegmentUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "crm.change"
    model = Segment
    form_class = SegmentForm
    template_name = "crm/segment_form.html"
    context_object_name = "segment"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit segment")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        resolve_segment(self.object)
        return response

    def get_success_url(self):
        return reverse("crm:segment_detail", kwargs={"pk": self.object.pk})


class SegmentPreviewView(CapabilityRequiredMixin, View):
    """Live audience size for the criteria currently typed into the form."""

    capability = "crm.view"

    def post(self, request):
        from .selectors import criteria_runtime_issues, describe_criteria, validate_criteria

        form = SegmentForm(request.POST)
        # The name is irrelevant to a size preview, so form errors are ignored
        # here — only the rule fields matter, and each is validated on its own.
        form.is_valid()
        criteria = form.build_criteria(getattr(form, "cleaned_data", {}) or {})
        context = {
            "criteria": criteria,
            "rules": [],
            "issues": [],
            "count": 0,
            "has_rules": bool(criteria),
        }
        if criteria:
            problems = validate_criteria(criteria)
            if problems:
                context["issues"] = problems
            else:
                context["rules"] = describe_criteria(criteria)
                context["issues"] = criteria_runtime_issues(criteria)
                context["count"] = preview_segment_size(criteria)
        return render(request, "crm/partials/segment_preview.html", context)


class SegmentRefreshView(CapabilityRequiredMixin, View):
    """Recalculate and cache a segment's size."""

    capability = "crm.change"

    def post(self, request, pk):
        segment = get_object_or_404(Segment, pk=pk)
        resolve_segment(segment)
        messages.success(
            request,
            _("“%(name)s” now matches %(count)s customers.")
            % {"name": segment.name, "count": segment.cached_count},
        )
        return HttpResponseRedirect(reverse("crm:segment_detail", kwargs={"pk": segment.pk}))
