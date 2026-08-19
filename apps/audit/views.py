"""Audit log browsing and export."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import DetailView, ListView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.csv_safety import safe_csv_writer

from .models import AuditAction, AuditLog, AuditSource
from .services import record_audit


class AuditLogListView(CapabilityRequiredMixin, ListView):
    capability = "audit.view"
    model = AuditLog
    template_name = "audit/audit_list.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("user", "content_type")

        search = self.request.GET.get("q", "").strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(description__icontains=search)
                | Q(object_repr__icontains=search)
                | Q(request_path__icontains=search)
            )

        action = self.request.GET.get("action", "").strip()
        if action:
            queryset = queryset.filter(action=action)

        source = self.request.GET.get("source", "").strip()
        if source:
            queryset = queryset.filter(source=source)

        user_id = self.request.GET.get("user", "").strip()
        if user_id.isdigit():
            queryset = queryset.filter(user_id=int(user_id))

        model_label = self.request.GET.get("model", "").strip()
        if model_label and "." in model_label:
            app_label, model_name = model_label.split(".", 1)
            content_type = ContentType.objects.filter(
                app_label=app_label, model=model_name
            ).first()
            if content_type:
                queryset = queryset.filter(content_type=content_type)

        period = self.request.GET.get("period", "30").strip()
        if period.isdigit() and int(period) > 0:
            queryset = queryset.filter(
                created_at__gte=timezone.now() - timedelta(days=int(period))
            )

        if self.request.GET.get("sensitive") == "1":
            queryset = queryset.filter(is_sensitive=True)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["actions"] = AuditAction.choices
        context["sources"] = AuditSource.choices
        context["filters"] = {
            "q": self.request.GET.get("q", ""),
            "action": self.request.GET.get("action", ""),
            "source": self.request.GET.get("source", ""),
            "period": self.request.GET.get("period", "30"),
            "sensitive": self.request.GET.get("sensitive", ""),
        }
        return context

    def get_template_names(self):
        if self.request.htmx:
            return ["audit/partials/audit_table.html"]
        return [self.template_name]


class AuditLogDetailView(CapabilityRequiredMixin, DetailView):
    capability = "audit.view"
    model = AuditLog
    template_name = "audit/audit_detail.html"
    context_object_name = "entry"

    def get_queryset(self):
        return AuditLog.objects.select_related("user", "content_type")


class AuditLogExportView(CapabilityRequiredMixin, ListView):
    """CSV export of the currently filtered audit view."""

    capability = "audit.export"
    model = AuditLog

    def get(self, request, *args, **kwargs):
        list_view = AuditLogListView()
        list_view.request = request
        queryset = list_view.get_queryset()[:50000]

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        stamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="audit_log_{stamp}.csv"'
        response.write("﻿")  # BOM so Excel detects UTF-8

        writer = safe_csv_writer(response, delimiter=";")
        writer.writerow(
            [
                _("Timestamp"),
                _("User"),
                _("Role"),
                _("Action"),
                _("Object"),
                _("Description"),
                _("Changes"),
                _("Source"),
                _("IP address"),
                _("Path"),
            ]
        )
        for entry in queryset.iterator(chunk_size=1000):
            writer.writerow(
                [
                    entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    entry.username,
                    entry.user_role,
                    entry.get_action_display(),
                    entry.object_repr,
                    entry.description,
                    "; ".join(
                        f"{k}: {v[0]} -> {v[1]}" for k, v in (entry.changes or {}).items()
                    ),
                    entry.get_source_display(),
                    entry.ip_address or "",
                    entry.request_path,
                ]
            )

        record_audit(
            request,
            action=AuditAction.EXPORT,
            description=_("Audit log exported to CSV (%(count)s rows)")
            % {"count": queryset.count()},
        )
        return response
