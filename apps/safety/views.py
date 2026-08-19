"""HTML views for the safety module.

The dashboard is deliberately opinionated about one thing: confirmed warnings
and AI suggestions are rendered in two separate blocks, never merged into one
list. A member of staff must always be able to tell, at a glance, which
warnings the system is acting on and which ones are still only a proposal.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.core.enums import Severity, SurfLevel
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)
from apps.core.utils import parse_date_range

from . import selectors, services
from .forms import (
    EmergencyContactForm,
    EquipmentSafetyCheckForm,
    EvacuationPlanForm,
    IncidentFilterForm,
    IncidentReviewForm,
    LifeguardAssignmentForm,
    SafetyIncidentForm,
    StudentRestrictionForm,
    WeatherWarningForm,
)
from .models import (
    OPEN_INCIDENT_STATUSES,
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)


def _week_start(day: date) -> date:
    """Monday of the week containing *day*."""
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class SafetyDashboardView(CapabilityRequiredMixin, DateRangeMixin, TemplateView):
    """The morning-briefing screen."""

    capability = "safety.view"
    template_name = "safety/dashboard.html"
    date_field = "occurred_at"

    def get_date_range(self):
        # Safety trends are read over a season, not a fortnight.
        return parse_date_range(self.request, default="90")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()

        context["stats"] = services.safety_dashboard_stats(start, end)
        context["open_incidents"] = selectors.open_incidents()[:8]
        context["confirmed_warnings"] = list(services.authoritative_warnings())
        context["pending_warnings"] = list(services.pending_ai_warnings())
        context["cover_today"] = list(services.cover_today())
        context["overdue_checks"] = list(services.overdue_equipment_checks()[:8])
        context["overdue_drills"] = list(services.overdue_drills()[:5])
        context["upcoming_drills"] = list(services.upcoming_drills()[:5])
        context["today"] = timezone.localdate()
        return context


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class IncidentListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "safety.view"
    model = SafetyIncident
    template_name = "safety/incident_list.html"
    partial_template_name = "safety/partials/incident_table.html"
    context_object_name = "incidents"
    paginate_by = 25
    search_fields = (
        "incident_code",
        "description",
        "immediate_action",
        "root_cause",
        "corrective_action",
        "spot__name",
    )

    def get_queryset(self):
        queryset = self.apply_search(selectors.incident_queryset())

        incident_type = self.request.GET.get("incident_type", "")
        if incident_type in dict(SafetyIncident.IncidentType.choices):
            queryset = queryset.filter(incident_type=incident_type)

        severity = self.request.GET.get("severity", "")
        if severity in dict(Severity.choices):
            queryset = queryset.filter(severity=severity)

        status = self.request.GET.get("status", "")
        if status == "open":
            queryset = queryset.filter(status__in=OPEN_INCIDENT_STATUSES)
        elif status:
            queryset = queryset.filter(status=status)

        spot = self.request.GET.get("spot", "")
        if spot.isdigit():
            queryset = queryset.filter(spot_id=int(spot))

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = IncidentFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "incident_type": self.request.GET.get("incident_type", ""),
                "severity": self.request.GET.get("severity", ""),
                "status": self.request.GET.get("status", ""),
            }
        )
        context["has_filters"] = any(
            self.request.GET.get(key)
            for key in ("q", "incident_type", "severity", "status", "spot")
        )
        context["days_since_last_incident"] = services.days_since_last_incident()
        return context


class IncidentDetailView(CapabilityRequiredMixin, DetailView):
    capability = "safety.view"
    model = SafetyIncident
    template_name = "safety/incident_detail.html"
    context_object_name = "incident"

    def get_queryset(self):
        return selectors.incident_detail_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        incident: SafetyIncident = context["incident"]
        context["emergency_contacts"] = selectors.emergency_contacts(spot=incident.spot)
        context["related_incidents"] = (
            selectors.incidents_for_spot(incident.spot).exclude(pk=incident.pk)[:5]
            if incident.spot_id
            else []
        )
        return context


class IncidentCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = SafetyIncident
    form_class = SafetyIncidentForm
    template_name = "safety/incident_form.html"
    success_message = _lazy("Incident recorded and the duty managers notified.")
    #: Audited as a safety incident, which the audit module never prunes.
    audit_action = AuditAction.SAFETY_INCIDENT

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Report an incident")
        context["cancel_url"] = reverse("safety:incident_list")
        return context

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        response = super().form_valid(form)
        # AuditedCreateMixin has written the SAFETY_INCIDENT entry; the people
        # who have to act on it are told here.
        services.notify_incident(self.object)
        return response

    def get_success_url(self):
        return reverse("safety:incident_detail", kwargs={"pk": self.object.pk})


class IncidentUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = SafetyIncident
    form_class = SafetyIncidentForm
    template_name = "safety/incident_form.html"
    success_message = _lazy("Incident updated.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit incident")
        context["cancel_url"] = reverse("safety:incident_detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        return reverse("safety:incident_detail", kwargs={"pk": self.object.pk})


class IncidentReviewView(CapabilityRequiredMixin, DetailView):
    """The named sign-off: cause, corrective action, new status."""

    capability = "safety.approve"
    model = SafetyIncident
    template_name = "safety/incident_review.html"
    context_object_name = "incident"

    def get_queryset(self):
        return selectors.incident_detail_queryset()

    def get_form(self, data=None) -> IncidentReviewForm:
        incident = self.object
        return IncidentReviewForm(
            data,
            initial={
                "root_cause": incident.root_cause,
                "corrective_action": incident.corrective_action,
                "follow_up_required": incident.follow_up_required,
                "follow_up_due": incident.follow_up_due,
            },
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "form" not in context:
            context["form"] = self.get_form()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        try:
            services.review_incident(
                self.object,
                user=request.user,
                root_cause=form.cleaned_data["root_cause"],
                corrective_action=form.cleaned_data["corrective_action"],
                status=form.cleaned_data["status"],
                follow_up_required=form.cleaned_data["follow_up_required"],
                follow_up_due=form.cleaned_data["follow_up_due"],
                request=request,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(
            request,
            _("Incident %(code)s reviewed.") % {"code": self.object.incident_code},
        )
        return redirect("safety:incident_detail", pk=self.object.pk)


# ---------------------------------------------------------------------------
# Lifeguard roster
# ---------------------------------------------------------------------------
class LifeguardRosterView(CapabilityRequiredMixin, TemplateView):
    capability = "safety.view"
    template_name = "safety/roster.html"

    def get_week_start(self) -> date:
        raw = self.request.GET.get("week", "")
        try:
            return _week_start(date.fromisoformat(raw))
        except ValueError:
            return _week_start(timezone.localdate())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start = self.get_week_start()

        spot = None
        spot_id = self.request.GET.get("spot", "")
        if spot_id.isdigit():
            from apps.locations.models import SurfSpot

            spot = SurfSpot.objects.filter(pk=int(spot_id)).first()

        context["week"] = services.roster_for_week(start, spot=spot)
        context["previous_week"] = (start - timedelta(days=7)).isoformat()
        context["next_week"] = (start + timedelta(days=7)).isoformat()
        context["this_week"] = _week_start(timezone.localdate()).isoformat()
        context["selected_spot"] = spot
        context["spots"] = self._spots()
        context["uncovered_today"] = self._uncovered_spots()
        return context

    def _spots(self):
        from apps.locations.models import SurfSpot

        return SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")

    def _uncovered_spots(self) -> list:
        """Active spots with no confirmed shift on today's roster."""
        covered = set(
            services.cover_today().filter(is_confirmed=True).values_list("spot_id", flat=True)
        )
        return [spot for spot in self._spots() if spot.pk not in covered]


class LifeguardAssignmentCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = LifeguardAssignment
    form_class = LifeguardAssignmentForm
    template_name = "safety/assignment_form.html"
    success_message = _lazy("Shift added to the roster.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        initial = kwargs.setdefault("initial", {})
        raw = self.request.GET.get("date", "")
        try:
            initial["date"] = date.fromisoformat(raw)
        except ValueError:
            pass
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Add a lifeguard shift")
        context["cancel_url"] = reverse("safety:roster")
        return context

    def get_success_url(self):
        return f"{reverse('safety:roster')}?week={_week_start(self.object.date).isoformat()}"


class LifeguardAssignmentUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = LifeguardAssignment
    form_class = LifeguardAssignmentForm
    template_name = "safety/assignment_form.html"
    success_message = _lazy("Shift updated.")

    def get_queryset(self):
        return selectors.assignment_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit lifeguard shift")
        context["cancel_url"] = reverse("safety:roster")
        return context

    def get_success_url(self):
        return f"{reverse('safety:roster')}?week={_week_start(self.object.date).isoformat()}"


class LifeguardAssignmentConfirmView(CapabilityRequiredMixin, View):
    """POST-only: confirm a shift so it counts as cover."""

    capability = "safety.change"

    def post(self, request, pk: int, *args, **kwargs):
        assignment = get_object_or_404(selectors.assignment_queryset(), pk=pk)
        services.confirm_assignment(assignment, user=request.user, request=request)
        messages.success(
            request,
            _("Shift confirmed: %(shift)s") % {"shift": assignment},
        )
        return redirect(f"{reverse('safety:roster')}?week={_week_start(assignment.date).isoformat()}")


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------
class EmergencyContactListView(CapabilityRequiredMixin, SearchableListMixin, ListView):
    capability = "safety.view"
    model = EmergencyContact
    template_name = "safety/contact_list.html"
    context_object_name = "contacts"
    paginate_by = 50
    search_fields = ("name", "organisation", "phone", "alternate_phone", "address", "notes")

    def get_queryset(self):
        queryset = self.apply_search(
            EmergencyContact.objects.select_related("spot").order_by(
                "sort_order", "kind", "name"
            )
        )
        if self.request.GET.get("status", "active") == "active":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_archived"] = self.request.GET.get("status", "active") != "active"
        return context


class EmergencyContactCardView(CapabilityRequiredMixin, TemplateView):
    """The printable card that lives in the beach bag."""

    capability = "safety.view"
    template_name = "safety/contact_card.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spot = None
        spot_id = self.request.GET.get("spot", "")
        from apps.locations.models import SurfSpot

        if spot_id.isdigit():
            spot = SurfSpot.objects.filter(pk=int(spot_id)).first()

        context["spot"] = spot
        context["spots"] = SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")
        context["contacts"] = selectors.emergency_contacts(spot=spot)
        context["plans"] = (
            selectors.plan_queryset(only_active=True).filter(spot=spot) if spot else []
        )
        context["printed_at"] = timezone.localtime()
        return context


class EmergencyContactCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = EmergencyContact
    form_class = EmergencyContactForm
    template_name = "safety/contact_form.html"
    success_message = _lazy("Emergency contact saved.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New emergency contact")
        context["cancel_url"] = reverse("safety:contact_list")
        return context

    def get_success_url(self):
        return reverse("safety:contact_list")


class EmergencyContactUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = EmergencyContact
    form_class = EmergencyContactForm
    template_name = "safety/contact_form.html"
    success_message = _lazy("Emergency contact updated.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit emergency contact")
        context["cancel_url"] = reverse("safety:contact_list")
        return context

    def get_success_url(self):
        return reverse("safety:contact_list")


# ---------------------------------------------------------------------------
# Evacuation plans
# ---------------------------------------------------------------------------
class EvacuationPlanListView(CapabilityRequiredMixin, SearchableListMixin, ListView):
    capability = "safety.view"
    model = EvacuationPlan
    template_name = "safety/plan_list.html"
    context_object_name = "plans"
    paginate_by = 25
    search_fields = ("title", "trigger_conditions", "assembly_point", "spot__name")

    def get_queryset(self):
        return self.apply_search(selectors.plan_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["overdue_drills"] = list(services.overdue_drills())
        context["upcoming_drills"] = list(services.upcoming_drills())
        return context


class EvacuationPlanDetailView(CapabilityRequiredMixin, DetailView):
    capability = "safety.view"
    model = EvacuationPlan
    template_name = "safety/plan_detail.html"
    context_object_name = "plan"

    def get_queryset(self):
        return selectors.plan_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["contacts"] = selectors.emergency_contacts(spot=context["plan"].spot)
        return context


class EvacuationPlanCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = EvacuationPlan
    form_class = EvacuationPlanForm
    template_name = "safety/plan_form.html"
    success_message = _lazy("Evacuation plan created.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New evacuation plan")
        context["cancel_url"] = reverse("safety:plan_list")
        return context

    def get_success_url(self):
        return reverse("safety:plan_detail", kwargs={"pk": self.object.pk})


class EvacuationPlanUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = EvacuationPlan
    form_class = EvacuationPlanForm
    template_name = "safety/plan_form.html"
    success_message = _lazy("Evacuation plan updated.")

    def get_queryset(self):
        return selectors.plan_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit evacuation plan")
        context["cancel_url"] = reverse("safety:plan_detail", kwargs={"pk": self.object.pk})
        return context

    def get_success_url(self):
        return reverse("safety:plan_detail", kwargs={"pk": self.object.pk})


# ---------------------------------------------------------------------------
# Equipment safety checks
# ---------------------------------------------------------------------------
class EquipmentCheckListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "safety.view"
    model = EquipmentSafetyCheck
    template_name = "safety/check_list.html"
    partial_template_name = "safety/partials/check_table.html"
    context_object_name = "checks"
    paginate_by = 25
    search_fields = (
        "equipment__name",
        "equipment__asset_code",
        "issues_found",
        "action_taken",
    )

    def get_queryset(self):
        queryset = self.apply_search(selectors.check_queryset())
        outcome = self.request.GET.get("outcome", "")
        if outcome == "failed":
            queryset = queryset.filter(passed=False)
        elif outcome == "passed":
            queryset = queryset.filter(passed=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["overdue"] = list(services.overdue_equipment_checks())
        context["failed_open"] = services.failed_open_checks().count()
        context["outcome"] = self.request.GET.get("outcome", "")
        return context


class EquipmentCheckCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = EquipmentSafetyCheck
    form_class = EquipmentSafetyCheckForm
    template_name = "safety/check_form.html"
    success_message = _lazy("Safety check recorded.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        initial = kwargs.setdefault("initial", {})
        equipment_id = self.request.GET.get("equipment", "")
        if equipment_id.isdigit():
            initial["equipment"] = int(equipment_id)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Record a safety check")
        context["cancel_url"] = reverse("safety:check_list")
        return context

    def form_valid(self, form):
        form.instance.checked_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("safety:check_list")


# ---------------------------------------------------------------------------
# Weather warnings
# ---------------------------------------------------------------------------
class WeatherWarningListView(CapabilityRequiredMixin, SearchableListMixin, ListView):
    """Confirmed warnings and AI suggestions, in two clearly separated blocks."""

    capability = "safety.view"
    model = WeatherWarning
    template_name = "safety/warning_list.html"
    context_object_name = "warnings"
    paginate_by = 25
    search_fields = ("title", "description", "ai_rationale", "spot__name")

    def get_queryset(self):
        queryset = self.apply_search(selectors.warning_queryset())
        if self.request.GET.get("status", "active") == "active":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["confirmed_warnings"] = list(services.authoritative_warnings())
        context["pending_warnings"] = list(services.pending_ai_warnings())
        context["show_archived"] = self.request.GET.get("status", "active") != "active"
        return context


class WeatherWarningCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = WeatherWarning
    form_class = WeatherWarningForm
    template_name = "safety/warning_form.html"
    success_message = _lazy("Warning published.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New weather warning")
        context["cancel_url"] = reverse("safety:warning_list")
        return context

    def get_success_url(self):
        return reverse("safety:warning_list")


class WeatherWarningUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = WeatherWarning
    form_class = WeatherWarningForm
    template_name = "safety/warning_form.html"
    success_message = _lazy("Warning updated.")

    def get_queryset(self):
        return selectors.warning_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit weather warning")
        context["cancel_url"] = reverse("safety:warning_list")
        return context

    def get_success_url(self):
        return reverse("safety:warning_list")


class WeatherWarningAcknowledgeView(CapabilityRequiredMixin, View):
    """POST-only: the named staff sign-off that makes an AI suggestion real."""

    capability = "safety.approve"

    def post(self, request, pk: int, *args, **kwargs):
        warning = get_object_or_404(selectors.warning_queryset(), pk=pk)
        try:
            services.acknowledge_warning(warning, request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("“%(title)s” confirmed — it now counts as an active warning.")
                % {"title": warning.title},
            )
        return redirect("safety:warning_list")


class WeatherWarningDismissView(CapabilityRequiredMixin, View):
    """POST-only: reject a warning staff do not agree with."""

    capability = "safety.approve"

    def post(self, request, pk: int, *args, **kwargs):
        warning = get_object_or_404(selectors.warning_queryset(), pk=pk)
        try:
            services.dismiss_warning(warning, request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request, _("“%(title)s” dismissed.") % {"title": warning.title}
            )
        return redirect("safety:warning_list")


# ---------------------------------------------------------------------------
# Student restrictions
# ---------------------------------------------------------------------------
class StudentRestrictionListView(CapabilityRequiredMixin, SearchableListMixin, ListView):
    capability = "safety.view"
    model = StudentRestriction
    template_name = "safety/restriction_list.html"
    context_object_name = "restrictions"
    paginate_by = 25
    search_fields = (
        "description",
        "student__student_code",
        "student__customer__first_name",
        "student__customer__last_name",
    )

    def get_queryset(self):
        queryset = self.apply_search(selectors.restriction_queryset())
        scope = self.request.GET.get("scope", "current")
        today = timezone.localdate()
        if scope == "current":
            queryset = queryset.filter(is_active=True, starts_on__lte=today).filter(
                Q(ends_on__isnull=True) | Q(ends_on__gte=today)
            )
        elif scope == "blocking":
            queryset = queryset.filter(is_active=True, cannot_surf=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["scope"] = self.request.GET.get("scope", "current")
        context["blocking_count"] = (
            selectors.current_restrictions().filter(cannot_surf=True).count()
        )
        return context


class StudentRestrictionCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "safety.add"
    model = StudentRestriction
    form_class = StudentRestrictionForm
    template_name = "safety/restriction_form.html"
    success_message = _lazy("Restriction recorded.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        initial = kwargs.setdefault("initial", {})
        student_id = self.request.GET.get("student", "")
        if student_id.isdigit():
            initial["student"] = int(student_id)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New student restriction")
        context["cancel_url"] = reverse("safety:restriction_list")
        return context

    def form_valid(self, form):
        form.instance.issued_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("safety:restriction_list")


class StudentRestrictionUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "safety.change"
    model = StudentRestriction
    form_class = StudentRestrictionForm
    template_name = "safety/restriction_form.html"
    success_message = _lazy("Restriction updated.")

    def get_queryset(self):
        return selectors.restriction_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit restriction")
        context["cancel_url"] = reverse("safety:restriction_list")
        return context

    def get_success_url(self):
        return reverse("safety:restriction_list")


class StudentRestrictionLiftView(CapabilityRequiredMixin, View):
    """POST-only: lift a restriction, keeping the record on file."""

    capability = "safety.change"

    def post(self, request, pk: int, *args, **kwargs):
        restriction = get_object_or_404(selectors.restriction_queryset(), pk=pk)
        try:
            services.deactivate_restriction(restriction, user=request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("Restriction lifted for %(student)s.") % {"student": restriction.student},
            )
        return redirect("safety:restriction_list")


# ---------------------------------------------------------------------------
# Spot readiness check (HTMX panel other modules can pull in)
# ---------------------------------------------------------------------------
class SpotSafetyPanelView(CapabilityRequiredMixin, TemplateView):
    """Live go / no-go panel for one spot, rendered as an HTMX fragment."""

    capability = "safety.view"
    template_name = "safety/partials/spot_safety_panel.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.locations.models import SurfSpot

        spot = get_object_or_404(SurfSpot, pk=self.kwargs["pk"])
        level = self.request.GET.get("level", SurfLevel.BEGINNER)
        if level not in dict(SurfLevel.choices):
            level = SurfLevel.BEGINNER

        context["spot"] = spot
        context["level"] = level
        context["level_label"] = dict(SurfLevel.choices)[level]
        context["verdict"] = services.assess_spot(spot, level)
        context["pending_warnings"] = list(services.pending_ai_warnings(spot=spot))
        context["cover"] = list(services.lifeguard_cover(spot))
        return context
