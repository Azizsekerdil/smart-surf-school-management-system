"""HTML screens for the maintenance module.

Views orchestrate only: every state change is delegated to
:mod:`apps.maintenance.services`, which is also what the REST API calls, so the
two interfaces can never drift apart.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.enums import DamageType, GenericStatus, Severity
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)
from apps.core.utils import parse_date_range

from . import selectors, services
from .forms import (
    MaintenanceCompletionForm,
    MaintenanceReasonForm,
    MaintenanceRecordForm,
    MaintenanceReportForm,
    MaintenanceScheduleForm,
    MaintenanceStartForm,
    SchedulePerformedForm,
)
from .models import OPEN_STATUSES, MaintenanceRecord, MaintenanceSchedule

#: The three tabs on the record list and the statuses each one holds.
TABS: dict[str, tuple[str, ...]] = {
    "open": (GenericStatus.OPEN,),
    "in_progress": (GenericStatus.IN_PROGRESS, GenericStatus.ON_HOLD),
    "resolved": (GenericStatus.RESOLVED, GenericStatus.CLOSED),
    "cancelled": (GenericStatus.CANCELLED,),
    "all": tuple(GenericStatus.values),
}
DEFAULT_TAB = "open"


class MaintenanceRecordListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "maintenance.view"
    model = MaintenanceRecord
    template_name = "maintenance/maintenancerecord_list.html"
    partial_template_name = "maintenance/partials/record_table.html"
    context_object_name = "records"
    paginate_by = 25

    def get_tab(self) -> str:
        tab = (self.request.GET.get("tab") or DEFAULT_TAB).strip()
        return tab if tab in TABS else DEFAULT_TAB

    def get_queryset(self):
        # Built from the fields the equipment module actually exposes.
        self.search_fields = selectors.record_search_fields()
        queryset = selectors.records_queryset()
        queryset = self.apply_search(queryset)

        queryset = queryset.filter(status__in=TABS[self.get_tab()])

        severity = (self.request.GET.get("severity") or "").strip()
        if severity in Severity.values:
            queryset = queryset.filter(severity=severity)

        damage_type = (self.request.GET.get("damage_type") or "").strip()
        if damage_type in DamageType.values:
            queryset = queryset.filter(damage_type=damage_type)

        category = (self.request.GET.get("category") or "").strip()
        if category.isdigit() and selectors.equipment_has_category():
            queryset = queryset.filter(equipment__category_id=int(category))

        equipment = (self.request.GET.get("equipment") or "").strip()
        if equipment.isdigit():
            queryset = queryset.filter(equipment_id=int(equipment))

        assigned = (self.request.GET.get("assigned") or "").strip()
        if assigned == "unassigned":
            queryset = queryset.filter(assigned_to__isnull=True)
        elif assigned == "mine" and self.request.user.is_authenticated:
            queryset = queryset.filter(assigned_to=self.request.user)

        return queryset.order_by("-reported_at", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = selectors.record_status_counts()
        context.update(
            {
                "tab": self.get_tab(),
                "tab_labels": [
                    ("open", _("Open")),
                    ("in_progress", _("In progress")),
                    ("resolved", _("Resolved")),
                    ("cancelled", _("Cancelled")),
                    ("all", _("All")),
                ],
                "tab_counts": {
                    key: sum(counts.get(status, 0) for status in statuses)
                    for key, statuses in TABS.items()
                },
                "severities": Severity.choices,
                "damage_types": DamageType.choices,
                "categories": sorted(
                    selectors.category_name_map().items(), key=lambda pair: pair[1]
                ),
                "current_severity": self.request.GET.get("severity", ""),
                "current_damage_type": self.request.GET.get("damage_type", ""),
                "current_category": self.request.GET.get("category", ""),
                "current_assigned": self.request.GET.get("assigned", ""),
                "workload": selectors.open_workload(),
            }
        )
        return context


class MaintenanceRecordDetailView(CapabilityRequiredMixin, DetailView):
    capability = "maintenance.view"
    model = MaintenanceRecord
    template_name = "maintenance/maintenancerecord_detail.html"
    context_object_name = "record"

    def get_queryset(self):
        return selectors.records_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = context["record"]
        context["other_open"] = selectors.open_records_for_equipment(
            record.equipment_id, exclude_pk=record.pk
        )
        context["equipment_history"] = (
            MaintenanceRecord.objects.filter(equipment_id=record.equipment_id)
            .exclude(pk=record.pk)
            .order_by("-reported_at")[:10]
        )
        context["schedule"] = MaintenanceSchedule.objects.filter(
            equipment_id=record.equipment_id
        ).first()
        context["can_start"] = record.status in (GenericStatus.OPEN, GenericStatus.ON_HOLD)
        context["can_hold"] = record.status in (GenericStatus.OPEN, GenericStatus.IN_PROGRESS)
        context["can_complete"] = record.status in OPEN_STATUSES
        context["can_cancel"] = record.status in OPEN_STATUSES
        context["start_form"] = MaintenanceStartForm()
        context["reason_form"] = MaintenanceReasonForm()
        return context


class MaintenanceRecordCreateView(CapabilityRequiredMixin, FormView):
    """Report a problem — pre-fills the item when reached from an equipment page."""

    capability = "maintenance.add"
    template_name = "maintenance/maintenancerecord_form.html"
    form_class = MaintenanceReportForm

    def get_initial(self):
        initial = super().get_initial()
        equipment = (self.request.GET.get("equipment") or "").strip()
        if equipment.isdigit():
            initial["equipment"] = int(equipment)
        rental_item = (self.request.GET.get("rental_item") or "").strip()
        if rental_item.isdigit():
            initial["rental_item"] = int(rental_item)
        initial["made_unusable"] = True
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Report a problem")
        context["submit_label"] = _("Report problem")
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            record = services.report_issue(
                equipment=data["equipment"],
                damage_type=data["damage_type"],
                severity=data["severity"],
                description=data["description"],
                user=self.request.user,
                photo=data.get("photo_before"),
                make_unusable=data.get("made_unusable", True),
                rental_item=data.get("rental_item"),
                assigned_to=data.get("assigned_to"),
                request=self.request,
                force=data.get("force", False),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Maintenance record %(code)s created.") % {"code": record.record_code},
        )
        return redirect("maintenance:detail", pk=record.pk)


class MaintenanceRecordUpdateView(
    CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView
):
    capability = "maintenance.change"
    model = MaintenanceRecord
    form_class = MaintenanceRecordForm
    template_name = "maintenance/maintenancerecord_form.html"
    context_object_name = "record"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit maintenance record")
        context["submit_label"] = _("Save changes")
        return context

    def get_success_url(self):
        return reverse("maintenance:detail", kwargs={"pk": self.object.pk})


class MaintenanceStartView(CapabilityRequiredMixin, FormView):
    """POST-only action: move a record into active repair."""

    capability = "maintenance.change"
    form_class = MaintenanceStartForm
    template_name = "maintenance/maintenancerecord_detail.html"
    http_method_names = ["post"]

    def get_record(self) -> MaintenanceRecord:
        return get_object_or_404(MaintenanceRecord, pk=self.kwargs["pk"])

    def form_valid(self, form):
        record = self.get_record()
        try:
            services.start_work(
                record,
                user=self.request.user,
                assigned_to=form.cleaned_data.get("assigned_to"),
                diagnosis=form.cleaned_data.get("diagnosis", ""),
                request=self.request,
            )
        except ValidationError as exc:
            messages.error(self.request, "; ".join(exc.messages))
        else:
            messages.success(
                self.request,
                _("Work started on %(code)s.") % {"code": record.record_code},
            )
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )

    def form_invalid(self, form):
        messages.error(self.request, _("The form could not be processed."))
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )


class MaintenanceHoldView(CapabilityRequiredMixin, FormView):
    capability = "maintenance.change"
    form_class = MaintenanceReasonForm
    http_method_names = ["post"]
    template_name = "maintenance/maintenancerecord_detail.html"

    def form_valid(self, form):
        record = get_object_or_404(MaintenanceRecord, pk=self.kwargs["pk"])
        try:
            services.put_on_hold(
                record,
                reason=form.cleaned_data["reason"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            messages.error(self.request, "; ".join(exc.messages))
        else:
            messages.success(self.request, _("Record put on hold."))
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )

    def form_invalid(self, form):
        messages.error(self.request, _("A reason is required to put a record on hold."))
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )


class MaintenanceCancelView(CapabilityRequiredMixin, FormView):
    capability = "maintenance.change"
    form_class = MaintenanceReasonForm
    http_method_names = ["post"]
    template_name = "maintenance/maintenancerecord_detail.html"

    def form_valid(self, form):
        record = get_object_or_404(MaintenanceRecord, pk=self.kwargs["pk"])
        try:
            services.cancel_maintenance(
                record,
                reason=form.cleaned_data["reason"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            messages.error(self.request, "; ".join(exc.messages))
        else:
            messages.success(
                self.request,
                _("%(code)s cancelled.") % {"code": record.record_code},
            )
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )

    def form_invalid(self, form):
        messages.error(self.request, _("A reason is required to cancel a record."))
        return HttpResponseRedirect(
            reverse("maintenance:detail", kwargs={"pk": self.kwargs["pk"]})
        )


class MaintenanceCompleteView(CapabilityRequiredMixin, FormView):
    """Close a repair: resolution, costs, and what happens to the item."""

    capability = "maintenance.change"
    form_class = MaintenanceCompletionForm
    template_name = "maintenance/maintenancerecord_complete.html"

    @cached_property
    def record(self) -> MaintenanceRecord:
        # Resolved lazily so the capability check in the mixin's dispatch runs
        # before the database is touched.
        return get_object_or_404(selectors.records_queryset(), pk=self.kwargs["pk"])

    def get_initial(self):
        initial = super().get_initial()
        initial.update(
            {
                "labour_hours": self.record.labour_hours,
                "parts_cost": self.record.parts_cost,
                "parts_used": self.record.parts_used,
                "resolution": self.record.resolution,
            }
        )
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["record"] = self.record
        context["labour_rate"] = services.labour_hourly_rate()
        context["other_open"] = selectors.open_records_for_equipment(
            self.record.equipment_id, exclude_pk=self.record.pk
        )
        return context

    def form_valid(self, form):
        try:
            services.complete_maintenance(
                self.record,
                resolution=form.cleaned_data["resolution"],
                costs=form.as_costs(),
                user=self.request.user,
                still_unusable=form.cleaned_data.get("still_unusable", False),
                retire_equipment=form.cleaned_data.get("retire_equipment", False),
                condition_after=form.cleaned_data.get("condition_after") or None,
                photo_after=form.cleaned_data.get("photo_after"),
                request=self.request,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("%(code)s completed. Total cost %(cost)s.")
            % {"code": self.record.record_code, "cost": self.record.total_cost},
        )
        return redirect("maintenance:detail", pk=self.record.pk)


# ---------------------------------------------------------------------------
# Predictive board
# ---------------------------------------------------------------------------
class MaintenancePredictionView(CapabilityRequiredMixin, TemplateView):
    """Ranked cards showing which items the statistics say will need work.

    The numbers come from :func:`services.predict_maintenance_needs` — recorded
    history only. Nothing on this screen is generated by a language model.
    """

    capability = "maintenance.view"
    template_name = "maintenance/prediction_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        refresh = self.request.GET.get("refresh") == "1"
        payload = services.cached_maintenance_predictions(refresh=refresh)
        predictions = services.annotate_prediction_texts(
            [dict(item) for item in payload.get("predictions", [])]
        )

        minimum = (self.request.GET.get("min") or "").strip()
        try:
            threshold = float(minimum) if minimum else 0.0
        except ValueError:
            threshold = 0.0
        if threshold > 0:
            predictions = [p for p in predictions if p["risk_score"] >= threshold]

        category = (self.request.GET.get("category") or "").strip()
        if category.isdigit():
            predictions = [p for p in predictions if p["category_id"] == int(category)]

        context.update(
            {
                "predictions": predictions,
                "generated_at": payload.get("generated_at"),
                "threshold": threshold,
                "categories": sorted(
                    selectors.category_name_map().items(), key=lambda pair: pair[1]
                ),
                "current_category": category,
                "high_risk_count": sum(1 for p in predictions if p["risk_score"] >= 60),
                "signal_weights": services.SIGNAL_WEIGHTS,
            }
        )
        return context


# ---------------------------------------------------------------------------
# Preventive schedules
# ---------------------------------------------------------------------------
class MaintenanceScheduleListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "maintenance.view"
    model = MaintenanceSchedule
    template_name = "maintenance/maintenanceschedule_list.html"
    partial_template_name = "maintenance/partials/schedule_table.html"
    context_object_name = "schedules"
    paginate_by = 25

    def get_queryset(self):
        self.search_fields = tuple(
            field
            for field in selectors.record_search_fields()
            if field.startswith("equipment__")
        )
        queryset = selectors.schedules_queryset()
        queryset = self.apply_search(queryset)

        today = timezone.localdate()
        scope = (self.request.GET.get("scope") or "due").strip()
        if scope == "due":
            queryset = queryset.filter(
                is_active=True, next_due_on__isnull=False, next_due_on__lte=today
            )
        elif scope == "soon":
            queryset = queryset.filter(
                is_active=True,
                next_due_on__isnull=False,
                next_due_on__lte=today + timedelta(days=14),
            )
        elif scope == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset.order_by("next_due_on", "id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["scope"] = (self.request.GET.get("scope") or "due").strip()
        context["scope_labels"] = [
            ("due", _("Due now")),
            ("soon", _("Due soon")),
            ("all", _("All plans")),
            ("inactive", _("Paused")),
        ]
        context["due_count"] = services.due_for_scheduled_maintenance().count()
        context["soon_count"] = services.due_for_scheduled_maintenance(within_days=14).count()
        context["total_count"] = MaintenanceSchedule.objects.count()
        context["performed_form"] = SchedulePerformedForm()
        return context


class MaintenanceScheduleCreateView(
    CapabilityRequiredMixin, AuditedCreateMixin, CreateView
):
    capability = "maintenance.add"
    model = MaintenanceSchedule
    form_class = MaintenanceScheduleForm
    template_name = "maintenance/maintenanceschedule_form.html"
    success_url = reverse_lazy("maintenance:schedule_list")
    success_message = _("Service plan created.")

    def get_initial(self):
        initial = super().get_initial()
        equipment = (self.request.GET.get("equipment") or "").strip()
        if equipment.isdigit():
            initial["equipment"] = int(equipment)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New service plan")
        context["submit_label"] = _("Create plan")
        return context


class MaintenanceScheduleUpdateView(
    CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView
):
    capability = "maintenance.change"
    model = MaintenanceSchedule
    form_class = MaintenanceScheduleForm
    template_name = "maintenance/maintenanceschedule_form.html"
    context_object_name = "schedule"
    success_url = reverse_lazy("maintenance:schedule_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit service plan")
        context["submit_label"] = _("Save plan")
        return context


class MaintenanceSchedulePerformedView(CapabilityRequiredMixin, FormView):
    capability = "maintenance.change"
    form_class = SchedulePerformedForm
    http_method_names = ["post"]
    template_name = "maintenance/maintenanceschedule_list.html"

    def form_valid(self, form):
        schedule = get_object_or_404(MaintenanceSchedule, pk=self.kwargs["pk"])
        try:
            services.mark_schedule_performed(
                schedule,
                performed_on=form.cleaned_data.get("performed_on"),
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            messages.error(self.request, "; ".join(exc.messages))
        else:
            messages.success(
                self.request,
                _("Service recorded. Next due %(date)s.")
                % {"date": schedule.next_due_on},
            )
        return HttpResponseRedirect(reverse("maintenance:schedule_list"))

    def form_invalid(self, form):
        messages.error(self.request, _("That service date could not be accepted."))
        return HttpResponseRedirect(reverse("maintenance:schedule_list"))


# ---------------------------------------------------------------------------
# Cost report
# ---------------------------------------------------------------------------
class MaintenanceCostReportView(CapabilityRequiredMixin, TemplateView):
    capability = "maintenance.view"
    template_name = "maintenance/cost_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, label = parse_date_range(self.request, default="90")
        context["report"] = services.maintenance_cost_report(start, end)
        context["range_label"] = label
        context["range_key"] = self.request.GET.get("range", "90")
        context["damage_labels"] = dict(DamageType.choices)
        context["open_commitment"] = (
            MaintenanceRecord.objects.filter(status__in=OPEN_STATUSES)
            .aggregate(records=Count("id"), unusable=Count("id", filter=Q(made_unusable=True)))
        )
        return context
