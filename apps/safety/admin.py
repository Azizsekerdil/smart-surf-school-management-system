from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)


@admin.register(SafetyIncident)
class SafetyIncidentAdmin(admin.ModelAdmin):
    list_display = (
        "incident_code",
        "occurred_at",
        "incident_type",
        "severity",
        "status",
        "spot",
        "medical_attention_required",
        "follow_up_required",
    )
    list_filter = (
        "incident_type",
        "severity",
        "status",
        "medical_attention_required",
        "emergency_services_called",
        "follow_up_required",
        "spot",
    )
    search_fields = ("incident_code", "description", "root_cause", "corrective_action")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    autocomplete_fields = ("spot",)
    filter_horizontal = ("people_involved", "staff_involved")
    readonly_fields = ("public_id", "incident_code", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("incident_code", "occurred_at", "incident_type", "severity", "status")}),
        (_("Where"), {"fields": ("spot", "lesson", "conditions_at_time")}),
        (_("Who"), {"fields": ("people_involved", "staff_involved", "reported_by")}),
        (
            _("What happened"),
            {
                "fields": (
                    "description",
                    "immediate_action",
                    "medical_attention_required",
                    "emergency_services_called",
                    "photo",
                )
            },
        ),
        (_("Review"), {"fields": ("root_cause", "corrective_action", "reviewed_by", "reviewed_at")}),
        (_("Follow-up"), {"fields": ("follow_up_required", "follow_up_due")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return SafetyIncident.all_objects.select_related("spot", "reported_by", "reviewed_by")


@admin.register(LifeguardAssignment)
class LifeguardAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "start_time", "end_time", "spot", "lifeguard", "is_confirmed")
    list_filter = ("is_confirmed", "spot", "date")
    search_fields = ("lifeguard__username", "lifeguard__first_name", "lifeguard__last_name", "notes")
    date_hierarchy = "date"
    ordering = ("-date", "start_time")
    autocomplete_fields = ("spot",)

    def get_queryset(self, request):
        return LifeguardAssignment.all_objects.select_related("spot", "lifeguard")


@admin.register(EmergencyContact)
class EmergencyContactAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "phone", "organisation", "scope_label", "sort_order", "is_active")
    list_filter = ("kind", "is_active", "spot")
    search_fields = ("name", "organisation", "phone", "alternate_phone", "address")
    ordering = ("sort_order", "kind", "name")
    autocomplete_fields = ("spot",)

    @admin.display(description=_("scope"))
    def scope_label(self, obj) -> str:
        return obj.scope_label


@admin.register(EvacuationPlan)
class EvacuationPlanAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "spot",
        "assembly_point",
        "responsible_role",
        "last_drill_date",
        "next_drill_due",
        "is_active",
    )
    list_filter = ("is_active", "spot", "responsible_role")
    search_fields = ("title", "trigger_conditions", "assembly_point")
    ordering = ("spot__name", "title")
    autocomplete_fields = ("spot",)
    readonly_fields = ("public_id", "created_at", "updated_at")

    def get_queryset(self, request):
        return EvacuationPlan.all_objects.select_related("spot")


@admin.register(EquipmentSafetyCheck)
class EquipmentSafetyCheckAdmin(admin.ModelAdmin):
    list_display = ("equipment", "checked_at", "passed", "checked_by", "next_check_due")
    list_filter = ("passed", "checked_by", "next_check_due")
    search_fields = ("equipment__name", "equipment__asset_code", "issues_found", "action_taken")
    date_hierarchy = "checked_at"
    ordering = ("-checked_at",)
    readonly_fields = ("public_id", "created_at", "updated_at")

    def get_queryset(self, request):
        return EquipmentSafetyCheck.all_objects.select_related("equipment", "checked_by")


@admin.register(WeatherWarning)
class WeatherWarningAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "severity",
        "source",
        "spot",
        "starts_at",
        "ends_at",
        "is_active",
        "authoritative",
    )
    list_filter = ("severity", "source", "is_active", "ai_suggested", "spot")
    search_fields = ("title", "description", "ai_rationale")
    date_hierarchy = "starts_at"
    ordering = ("-starts_at",)
    autocomplete_fields = ("spot",)
    readonly_fields = ("public_id", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("title", "spot", "severity", "source", "description")}),
        (_("Window"), {"fields": ("starts_at", "ends_at", "is_active")}),
        (
            _("AI suggestion"),
            {
                "fields": ("ai_suggested", "ai_rationale"),
                "description": _(
                    "An AI-suggested warning is not authoritative until a named member "
                    "of staff confirms it below."
                ),
            },
        ),
        (_("Staff confirmation"), {"fields": ("acknowledged_by", "acknowledged_at")}),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    @admin.display(description=_("authoritative"), boolean=True)
    def authoritative(self, obj) -> bool:
        return obj.is_authoritative

    def get_queryset(self, request):
        return WeatherWarning.all_objects.select_related("spot", "acknowledged_by")


@admin.register(StudentRestriction)
class StudentRestrictionAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "restriction_type",
        "cannot_surf",
        "requires_supervision",
        "max_wave_height_m",
        "max_wind_kmh",
        "starts_on",
        "ends_on",
        "is_active",
    )
    list_filter = ("restriction_type", "is_active", "cannot_surf", "requires_supervision")
    search_fields = (
        "description",
        "student__student_code",
        "student__customer__first_name",
        "student__customer__last_name",
    )
    ordering = ("-starts_on",)
    readonly_fields = ("public_id", "created_at", "updated_at")

    def get_queryset(self, request):
        return StudentRestriction.all_objects.select_related("student__customer", "issued_by")
