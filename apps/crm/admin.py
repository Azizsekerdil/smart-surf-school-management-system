from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Campaign, Interaction, Lead, Segment


class InteractionInline(admin.TabularInline):
    model = Interaction
    extra = 0
    fields = ("occurred_at", "kind", "direction", "subject", "handled_by", "follow_up_required")
    ordering = ("-occurred_at",)
    show_change_link = True


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "status",
        "source",
        "assigned_to",
        "expected_value",
        "probability",
        "next_action_at",
        "created_at",
    )
    list_filter = ("status", "source", "assigned_to", "is_deleted", "created_at")
    search_fields = ("first_name", "last_name", "email", "phone", "interest", "next_action")
    date_hierarchy = "created_at"
    autocomplete_fields = ("assigned_to",)
    readonly_fields = ("public_id", "converted_at", "created_at", "updated_at")
    inlines = (InteractionInline,)
    fieldsets = (
        (None, {"fields": ("first_name", "last_name", "email", "phone")}),
        (_("Opportunity"), {"fields": ("source", "interest", "expected_value", "probability")}),
        (_("Pipeline"), {"fields": ("status", "assigned_to", "next_action", "next_action_at")}),
        (_("Outcome"), {"fields": ("converted_customer", "converted_at", "lost_reason")}),
        (
            _("System"),
            {
                "classes": ("collapse",),
                "fields": ("public_id", "is_deleted", "created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description=_("name"), ordering="first_name")
    def full_name(self, obj: Lead) -> str:
        return obj.full_name

    def get_queryset(self, request):
        return Lead.all_objects.select_related("assigned_to", "converted_customer")


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "kind",
        "direction",
        "subject",
        "contact_display",
        "handled_by",
        "follow_up_required",
        "sentiment",
    )
    list_filter = ("kind", "direction", "sentiment", "follow_up_required", "occurred_at")
    search_fields = ("subject", "body", "lead__first_name", "lead__last_name")
    date_hierarchy = "occurred_at"
    autocomplete_fields = ("handled_by",)
    readonly_fields = ("public_id", "created_at", "updated_at")

    @admin.display(description=_("contact"))
    def contact_display(self, obj: Interaction) -> str:
        return obj.contact_display

    def get_queryset(self, request):
        return Interaction.all_objects.select_related("lead", "customer", "handled_by")


@admin.register(Segment)
class SegmentAdmin(admin.ModelAdmin):
    list_display = ("name", "is_dynamic", "cached_count", "last_calculated_at", "updated_at")
    list_filter = ("is_dynamic", "is_deleted")
    search_fields = ("name", "description")
    readonly_fields = ("public_id", "cached_count", "last_calculated_at", "created_at", "updated_at")
    actions = ("recalculate",)

    @admin.action(description=_("Recalculate the audience size"))
    def recalculate(self, request, queryset):
        from .services import resolve_segment

        for segment in queryset:
            resolve_segment(segment)
        self.message_user(
            request,
            _("%(count)s segment(s) recalculated.") % {"count": queryset.count()},
        )

    def get_queryset(self, request):
        return Segment.all_objects.all()


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "channel",
        "status",
        "start_date",
        "end_date",
        "budget",
        "actual_spend",
        "revenue_attributed",
    )
    list_filter = ("status", "channel", "start_date", "is_deleted")
    search_fields = ("name", "code", "message_subject", "message_body")
    date_hierarchy = "start_date"
    readonly_fields = ("public_id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "code", "channel", "status")}),
        (_("Schedule"), {"fields": ("start_date", "end_date")}),
        (_("Audience & message"), {"fields": ("target_segment", "message_subject", "message_body")}),
        (_("Money"), {"fields": ("budget", "actual_spend", "revenue_attributed")}),
        (_("Results"), {"fields": ("sent_count", "opened_count", "converted_count")}),
        (
            _("System"),
            {
                "classes": ("collapse",),
                "fields": ("public_id", "is_deleted", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return Campaign.all_objects.select_related("target_segment")
