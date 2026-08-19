from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import SpotHazard, SurfSpot


class SpotHazardInline(admin.TabularInline):
    model = SpotHazard
    extra = 0
    fields = ("name", "severity", "is_active", "applies_from_tide", "applies_to_tide")
    show_change_link = True


@admin.register(SurfSpot)
class SurfSpotAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "break_type",
        "bottom_type",
        "level_range_display",
        "capacity",
        "lifeguard_on_duty",
        "is_primary",
        "is_active",
    )
    list_filter = (
        "is_active",
        "is_primary",
        "break_type",
        "bottom_type",
        "lifeguard_on_duty",
        "min_level",
        "max_level",
    )
    search_fields = ("name", "code", "slug", "description", "access_notes", "nearest_hospital")
    ordering = ("-is_primary", "name")
    readonly_fields = ("public_id", "code", "slug", "created_at", "updated_at")
    inlines = [SpotHazardInline]

    fieldsets = (
        (None, {"fields": ("name", "code", "slug", "description", "photo")}),
        (
            _("Geography"),
            {"fields": ("latitude", "longitude", "altitude", "beach_facing_deg")},
        ),
        (_("The wave"), {"fields": ("break_type", "bottom_type", "min_level", "max_level")}),
        (
            _("Ideal conditions"),
            {"fields": ("ideal_tide", "ideal_wind", "ideal_swell_direction_deg")},
        ),
        (
            _("Operations"),
            {"fields": ("capacity", "is_active", "is_primary", "parking_info", "access_notes")},
        ),
        (
            _("Safety"),
            {
                "fields": (
                    "lifeguard_on_duty",
                    "nearest_hospital",
                    "nearest_hospital_phone",
                    "emergency_notes",
                )
            },
        ),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        # Archived spots must remain reachable from the admin.
        return SurfSpot.all_objects.all()

    @admin.display(description=_("levels"))
    def level_range_display(self, obj) -> str:
        return obj.level_range_display


@admin.register(SpotHazard)
class SpotHazardAdmin(admin.ModelAdmin):
    list_display = ("name", "spot", "severity", "is_active", "tide_window_display", "updated_at")
    list_filter = ("severity", "is_active", "spot")
    search_fields = ("name", "description", "spot__name", "spot__code")
    autocomplete_fields = ("spot",)
    ordering = ("spot__name", "name")

    @admin.display(description=_("tide window"))
    def tide_window_display(self, obj) -> str:
        return obj.tide_window_display
