from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import GeneratedReport, ReportDefinition


@admin.register(ReportDefinition)
class ReportDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "report_key",
        "default_format",
        "is_scheduled",
        "schedule_cron",
        "last_run_at",
        "is_active",
    )
    list_filter = ("is_scheduled", "is_active", "default_format", "report_key")
    search_fields = ("name", "code", "report_key", "description")
    readonly_fields = ("public_id", "last_run_at", "created_at", "updated_at")
    ordering = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "code", "report_key", "description", "is_active")}),
        (
            _("Output"),
            {"fields": ("default_format", "default_filters", "required_capability")},
        ),
        (_("Schedule"), {"fields": ("is_scheduled", "schedule_cron", "recipients", "last_run_at")}),
        (
            _("Record"),
            {
                "classes": ("collapse",),
                "fields": ("public_id", "created_by", "updated_by", "created_at", "updated_at"),
            },
        ),
    )


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    """Read-mostly: the archive is evidence of a data export, not a scratchpad."""

    list_display = (
        "title",
        "report_key",
        "format",
        "status",
        "row_count",
        "file_size_bytes",
        "generation_ms",
        "generated_by",
        "created_at",
    )
    list_filter = ("status", "format", "report_key", "created_at")
    search_fields = ("title", "report_key", "error_message")
    readonly_fields = (
        "public_id",
        "definition",
        "report_key",
        "title",
        "format",
        "filters_used",
        "file",
        "file_size_bytes",
        "row_count",
        "generated_by",
        "generation_ms",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
