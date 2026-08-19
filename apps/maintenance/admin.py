"""Django admin registrations for the maintenance module."""

from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import MaintenanceRecord, MaintenanceSchedule


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "record_code",
        "equipment",
        "damage_type",
        "severity",
        "status",
        "reported_at",
        "assigned_to",
        "total_cost",
        "made_unusable",
    )
    list_filter = (
        "status",
        "severity",
        "damage_type",
        "made_unusable",
        "is_deleted",
        ("reported_at", admin.DateFieldListFilter),
    )
    search_fields = ("record_code", "description", "diagnosis", "resolution")
    # raw_id rather than autocomplete: an autocomplete field would impose a
    # `search_fields` requirement on another module's ModelAdmin, and a system
    # check must never fail because of a neighbouring app's choices.
    raw_id_fields = ("equipment", "rental_item")
    autocomplete_fields = ("reported_by", "assigned_to")
    date_hierarchy = "reported_at"
    readonly_fields = (
        "record_code",
        "public_id",
        "total_cost",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )
    ordering = ("-reported_at",)
    list_select_related = ("equipment", "assigned_to", "reported_by")
    fieldsets = (
        (None, {"fields": ("record_code", "equipment", "rental_item", "made_unusable")}),
        (
            _("Problem"),
            {"fields": ("damage_type", "severity", "status", "description", "photo_before")},
        ),
        (
            _("Work"),
            {
                "fields": (
                    "reported_by",
                    "reported_at",
                    "assigned_to",
                    "started_at",
                    "completed_at",
                    "diagnosis",
                    "resolution",
                    "parts_used",
                    "photo_after",
                )
            },
        ),
        (
            _("Cost"),
            {"fields": ("labour_hours", "parts_cost", "labour_cost", "total_cost")},
        ),
        (
            _("Record"),
            {
                "classes": ("collapse",),
                "fields": ("public_id", "created_at", "updated_at", "created_by", "updated_by"),
            },
        ),
    )


@admin.register(MaintenanceSchedule)
class MaintenanceScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "equipment",
        "interval_days",
        "last_performed_on",
        "next_due_on",
        "is_active",
    )
    list_filter = ("is_active", "is_deleted", ("next_due_on", admin.DateFieldListFilter))
    search_fields = ("equipment__id",)
    raw_id_fields = ("equipment",)
    readonly_fields = ("public_id", "created_at", "updated_at", "created_by", "updated_by")
    ordering = ("next_due_on",)
    list_select_related = ("equipment",)
