from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import CampActivity, CampDay, CampParticipant, SurfCamp


class CampDayInline(admin.TabularInline):
    model = CampDay
    extra = 0
    fields = ("date", "day_number", "title", "spot", "weather_note")
    ordering = ("date",)
    show_change_link = True


class CampParticipantInline(admin.TabularInline):
    model = CampParticipant
    extra = 0
    fields = ("student", "status", "room_type", "room_number", "needs_transfer", "amount_paid")
    # raw_id rather than autocomplete: it does not require the students admin to
    # declare search_fields, so this module cannot break another app's checks.
    raw_id_fields = ("student",)
    show_change_link = True


class CampActivityInline(admin.TabularInline):
    model = CampActivity
    extra = 0
    fields = ("start_time", "end_time", "title", "activity_type", "instructor", "location")
    ordering = ("start_time",)


@admin.register(SurfCamp)
class SurfCampAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "start_date",
        "end_date",
        "status",
        "capacity",
        "booked",
        "price",
        "is_active",
    )
    list_filter = ("status", "is_active", "min_level", "max_level", "start_date", "spot")
    search_fields = ("code", "name", "accommodation_name", "description")
    date_hierarchy = "start_date"
    ordering = ("-start_date",)
    filter_horizontal = ("instructors",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (CampDayInline, CampParticipantInline)
    fieldsets = (
        (None, {"fields": ("name", "code", "description", "photo", "status", "is_active")}),
        (_("Dates & place"), {"fields": ("start_date", "end_date", "spot")}),
        (
            _("Capacity & level"),
            {"fields": ("capacity", "min_participants", "min_level", "max_level")},
        ),
        (_("Pricing"), {"fields": ("price", "deposit_amount", "single_room_supplement")}),
        (
            _("Package"),
            {
                "fields": (
                    "includes_accommodation",
                    "includes_meals",
                    "includes_transfer",
                    "includes_equipment",
                    "includes_insurance",
                )
            },
        ),
        (
            _("Logistics"),
            {
                "fields": (
                    "accommodation_name",
                    "accommodation_address",
                    "meal_plan",
                    "transfer_pickup_point",
                    "transfer_notes",
                )
            },
        ),
        (_("Team"), {"fields": ("lead_instructor", "instructors")}),
        (_("System"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    @admin.display(description=_("booked"))
    def booked(self, obj) -> str:
        return f"{obj.participant_count}/{obj.capacity}"


@admin.register(CampParticipant)
class CampParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "camp",
        "status",
        "room_type",
        "room_number",
        "needs_transfer",
        "amount_paid",
        "deposit_paid",
    )
    list_filter = ("status", "room_type", "needs_transfer", "deposit_paid", "camp")
    search_fields = ("room_number", "dietary_requirements", "arrival_flight", "departure_flight")
    raw_id_fields = ("student", "camp", "booking")
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_select_related = ("camp", "student")


@admin.register(CampDay)
class CampDayAdmin(admin.ModelAdmin):
    list_display = ("camp", "date", "day_number", "title", "spot")
    list_filter = ("camp", "date")
    search_fields = ("title", "description", "weather_note")
    date_hierarchy = "date"
    ordering = ("camp", "date")
    inlines = (CampActivityInline,)
    list_select_related = ("camp", "spot")


@admin.register(CampActivity)
class CampActivityAdmin(admin.ModelAdmin):
    list_display = ("camp_day", "start_time", "end_time", "title", "activity_type", "instructor")
    list_filter = ("activity_type", "camp_day__camp")
    search_fields = ("title", "location", "notes")
    ordering = ("camp_day", "start_time")
    list_select_related = ("camp_day", "camp_day__camp", "instructor")
