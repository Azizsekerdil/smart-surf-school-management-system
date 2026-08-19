"""HTML views: the report catalogue, the run screen, the archive and saved configs.

The run screen deliberately previews before it exports. Generating a 20 000-row
PDF is slow, lands in the audit log as a data disclosure and clutters the
archive; letting the operator see the first rows and the totals first means far
fewer of those are made by accident.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from apps.accounts.permissions import CapabilityRequiredMixin, require_capability
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)
from apps.core.utils import parse_date_range

from . import selectors, services
from .forms import GeneratedReportFilterForm, ReportDefinitionForm, ReportFilterForm
from .models import GeneratedReport, ReportDefinition
from .reports import AREA_ICONS, AREA_LABELS, get_report, grouped_reports, reports_for_user


class ReportCatalogueView(CapabilityRequiredMixin, TemplateView):
    """The catalogue: every report this user may run, grouped by area."""

    capability = "reporting.view"
    template_name = "reporting/report_catalogue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["groups"] = grouped_reports(self.request.user)
        context["report_count"] = len(reports_for_user(self.request.user))
        context["recent_reports"] = selectors.recent_for_user(self.request.user, limit=5)
        context["saved_definitions"] = selectors.definitions().filter(is_active=True)[:8]
        context["scheduled_count"] = ReportDefinition.objects.filter(
            is_scheduled=True, is_active=True
        ).count()
        return context


class ReportRunView(CapabilityRequiredMixin, HtmxPartialMixin, TemplateView):
    """Pick filters, preview the result, then export it.

    ``GET``  renders the filter form (and the preview when it is submitted).
    ``POST`` generates the file and streams it back.
    """

    capability = "reporting.view"
    template_name = "reporting/report_run.html"
    partial_template_name = "reporting/partials/report_preview.html"

    def get_spec(self):
        spec = get_report(self.kwargs["key"])
        if spec is None:
            raise Http404(_("Unknown report."))
        if not self.request.user.has_capability(spec.capability):
            raise PermissionDenied(
                _("Your role does not grant access to the data behind this report.")
            )
        return spec

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        spec = self.get_spec()
        form = kwargs.get("form") or ReportFilterForm(
            data=self.request.GET or None, spec=spec
        )

        context["spec"] = spec
        context["form"] = form
        context["area_label"] = AREA_LABELS.get(spec.area, spec.area)
        context["area_icon"] = AREA_ICONS.get(spec.area, "file-text")
        context["may_export"] = self.request.user.has_capability("reporting.export")
        context["may_save"] = self.request.user.has_capability("reporting.add")

        # An unbound form still previews, using the report's own defaults: a run
        # screen that shows nothing until you press a button hides the very
        # thing the operator came to check.
        if form.is_bound and not form.is_valid():
            context["preview"] = None
            context["preview_error"] = ""
            context["filters"] = {}
        else:
            filters = form.filter_values() if form.is_bound else dict(spec.default_filters)
            _spec, data, error = services.preview_report(spec.key, filters, self.request.user)
            context["preview"] = data
            context["preview_error"] = error
            context["filters"] = filters

        context["save_query"] = urlencode(context["filters"])
        return context

    def post(self, request, *args, **kwargs):
        spec = self.get_spec()
        require_capability(request.user, "reporting.export")

        form = ReportFilterForm(data=request.POST, spec=spec)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        generated = services.generate_report(
            spec.key,
            form.chosen_format,
            form.filter_values(),
            user=request.user,
            request=request,
        )
        if not generated.is_downloadable:
            messages.error(
                request,
                generated.error_message or _("The report could not be generated."),
            )
            return redirect("reporting:run", key=spec.key)

        return _download_response(generated)


class GeneratedReportListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    """Everything that has ever been exported, with re-download."""

    capability = "reporting.view"
    model = GeneratedReport
    template_name = "reporting/generatedreport_list.html"
    partial_template_name = "reporting/partials/history_table.html"
    context_object_name = "reports"
    paginate_by = 25
    search_fields = ("title", "report_key", "definition__name")

    def get_queryset(self):
        queryset = self.apply_search(selectors.generated_reports())

        report_key = self.request.GET.get("report_key", "")
        if report_key:
            queryset = queryset.filter(report_key=report_key)

        fmt = self.request.GET.get("format", "")
        if fmt:
            queryset = queryset.filter(format=fmt)

        status = self.request.GET.get("status", "")
        if status:
            queryset = queryset.filter(status=status)

        start, end, _label = parse_date_range(self.request)
        if start:
            queryset = queryset.filter(created_at__gte=start)
        if end:
            queryset = queryset.filter(created_at__lte=end)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, label = parse_date_range(self.request)
        # ``object_list`` is the full filtered queryset; the page slice lives in
        # ``reports``. Statistics must describe the filter, not the page.
        queryset = self.object_list

        context["filter_form"] = GeneratedReportFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "report_key": self.request.GET.get("report_key", ""),
                "format": self.request.GET.get("format", ""),
                "status": self.request.GET.get("status", ""),
                "range": self.request.GET.get("range", "30"),
            }
        )
        context["stats"] = selectors.history_stats(queryset)
        context["chart"] = selectors.exports_per_day(
            queryset,
            start.date() if start else None,
            end.date() if end else None,
        )
        context["range_label"] = label
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "report_key", "format", "status")
        )
        return context


class GeneratedReportDownloadView(CapabilityRequiredMixin, View):
    """Re-download an archived export, re-checking the report's own capability."""

    capability = "reporting.export"

    def get(self, request, pk: int, *args, **kwargs):
        generated = get_object_or_404(
            GeneratedReport.objects.select_related("definition"), pk=pk
        )
        if not generated.is_downloadable:
            messages.error(
                request,
                generated.error_message or _("This export produced no file."),
            )
            return redirect("reporting:history")

        # The archive must not become a way around the capability that guarded
        # the data when it was first exported.
        spec = get_report(generated.report_key)
        if spec is not None and not request.user.has_capability(spec.capability):
            raise PermissionDenied(
                _("Your role does not grant access to the data in this export.")
            )

        record_audit(
            request,
            action=AuditAction.EXPORT,
            instance=generated,
            description=_("Re-downloaded export “%(title)s”") % {"title": generated.title},
        )
        return _download_response(generated)


# ---------------------------------------------------------------------------
# Saved configurations
# ---------------------------------------------------------------------------
class ReportDefinitionListView(
    CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView
):
    capability = "reporting.view"
    model = ReportDefinition
    template_name = "reporting/reportdefinition_list.html"
    partial_template_name = "reporting/partials/definition_table.html"
    context_object_name = "definitions"
    paginate_by = 25
    search_fields = ("name", "code", "report_key", "description")

    def get_queryset(self):
        return self.apply_search(selectors.definitions())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["scheduled_count"] = ReportDefinition.objects.filter(
            is_scheduled=True, is_active=True
        ).count()
        return context


class ReportDefinitionCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "reporting.add"
    model = ReportDefinition
    form_class = ReportDefinitionForm
    template_name = "reporting/reportdefinition_form.html"
    success_message = _lazy("Saved report created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        """Pre-fill from the run screen's "save this configuration" link."""
        initial = super().get_initial()
        key = self.request.GET.get("report_key", "")
        spec = get_report(key)
        if spec is not None:
            initial["report_key"] = spec.key
            initial["name"] = str(spec.title)
            initial["description"] = str(spec.description)
            initial["default_format"] = self.request.GET.get("format", spec.default_format)
            reserved = {"report_key", "format", "csrfmiddlewaretoken"}
            initial["default_filters"] = {
                key_: value
                for key_, value in self.request.GET.items()
                if key_ not in reserved and value not in ("", None)
            }
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New saved report")
        context["cancel_url"] = reverse("reporting:definition_list")
        return context

    def get_success_url(self):
        return reverse("reporting:definition_list")


class ReportDefinitionUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "reporting.change"
    model = ReportDefinition
    form_class = ReportDefinitionForm
    template_name = "reporting/reportdefinition_form.html"
    success_message = _lazy("Saved report updated.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit saved report")
        context["cancel_url"] = reverse("reporting:definition_list")
        context["recent_runs"] = self.object.generated_reports.all()[:5]
        return context

    def get_success_url(self):
        return reverse("reporting:definition_list")


class ReportDefinitionDeleteView(CapabilityRequiredMixin, DeleteView):
    """Archive a saved configuration. Past exports keep their history."""

    capability = "reporting.delete"
    model = ReportDefinition
    template_name = "reporting/reportdefinition_confirm_delete.html"
    context_object_name = "definition"
    success_url = reverse_lazy("reporting:definition_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["run_count"] = self.object.generated_reports.count()
        return context

    def form_valid(self, form):
        definition = self.object
        record_audit(
            self.request,
            action=AuditAction.DELETE,
            instance=definition,
            description=_("Saved report “%(name)s” archived") % {"name": definition.name},
        )
        response = super().form_valid(form)
        messages.success(
            self.request,
            _("Saved report “%(name)s” archived.") % {"name": definition.name},
        )
        return response


class ReportDefinitionRunView(CapabilityRequiredMixin, View):
    """POST-only: run a saved configuration exactly as stored."""

    capability = "reporting.export"

    def post(self, request, pk: int, *args, **kwargs):
        definition = get_object_or_404(ReportDefinition, pk=pk, is_active=True)
        spec = get_report(definition.report_key)
        if spec is None:
            messages.error(
                request,
                _("“%(name)s” points at a report that no longer exists.")
                % {"name": definition.name},
            )
            return redirect("reporting:definition_list")

        generated = services.generate_report(
            definition.report_key,
            definition.default_format,
            definition.filter_dict,
            user=request.user,
            definition=definition,
            request=request,
        )
        if not generated.is_downloadable:
            messages.error(
                request, generated.error_message or _("The report could not be generated.")
            )
            return redirect("reporting:definition_list")
        return _download_response(generated)


# ---------------------------------------------------------------------------
# Shared response helper
# ---------------------------------------------------------------------------
def _download_response(generated: GeneratedReport) -> FileResponse:
    """Stream a stored export with a dated, unambiguous filename."""
    from .exporters.registry import EXPORT_FORMATS  # noqa: PLC0415 - avoids an import cycle

    exporter_class = EXPORT_FORMATS.get(generated.format)
    content_type = exporter_class.content_type if exporter_class else "application/octet-stream"

    return FileResponse(
        generated.file.open("rb"),
        as_attachment=True,
        filename=generated.download_filename(),
        content_type=content_type,
    )
