from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import MetricSnapshot


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "metric_key",
        "granularity",
        "period_start",
        "period_end",
        "value",
        "count",
        "computed_at",
    )
    list_filter = ("granularity", "metric_key", "computed_at")
    search_fields = ("metric_key",)
    date_hierarchy = "period_start"
    ordering = ("-period_start", "metric_key")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ()
    fieldsets = (
        (
            _("Measure"),
            {"fields": ("metric_key", "granularity", "dimensions")},
        ),
        (
            _("Window"),
            {"fields": ("period_start", "period_end")},
        ),
        (
            _("Result"),
            {"fields": ("value", "count", "computed_at")},
        ),
        (
            _("Bookkeeping"),
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
