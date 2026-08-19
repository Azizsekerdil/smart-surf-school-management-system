from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import ConditionForecast, SurfCondition, SurfScore


class SurfScoreInline(admin.TabularInline):
    model = SurfScore
    extra = 0
    fields = ("level", "score", "is_safe_for_level", "is_ai_generated", "recommendation")
    readonly_fields = ("level", "score", "is_safe_for_level", "is_ai_generated", "recommendation")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        # Scores are computed, never typed in. Adding one by hand would put an
        # unexplainable number next to the computed ones.
        return False


@admin.register(SurfCondition)
class SurfConditionAdmin(admin.ModelAdmin):
    list_display = (
        "recorded_at",
        "spot",
        "is_forecast",
        "wave_height_m",
        "effective_period_display",
        "wind_speed_kmh",
        "wind_type",
        "tide_state",
        "water_temperature_c",
        "provider",
    )
    list_filter = ("is_forecast", "spot", "provider", "source", "wind_type", "tide_state")
    search_fields = ("spot__name", "spot__code", "provider", "weather_description")
    date_hierarchy = "recorded_at"
    ordering = ("-recorded_at",)
    autocomplete_fields = ("spot",)
    inlines = [SurfScoreInline]
    readonly_fields = ("public_id", "created_at", "updated_at", "raw_payload")

    fieldsets = (
        (None, {"fields": ("spot", "recorded_at", "is_forecast", "source", "provider")}),
        (
            _("The wave"),
            {"fields": ("wave_height_m", "wave_period_s", "wave_direction_deg")},
        ),
        (
            _("The swell"),
            {
                "fields": (
                    "swell_height_m",
                    "swell_period_s",
                    "swell_direction_deg",
                    "wind_wave_height_m",
                )
            },
        ),
        (
            _("The wind"),
            {"fields": ("wind_speed_kmh", "wind_gust_kmh", "wind_direction_deg", "wind_type")},
        ),
        (_("The tide"), {"fields": ("sea_level_height_msl_m", "tide_state")}),
        (_("Temperature"), {"fields": ("air_temperature_c", "water_temperature_c")}),
        (
            _("The sky"),
            {
                "fields": (
                    "weather_code",
                    "weather_description",
                    "uv_index",
                    "precipitation_mm",
                    "cloud_cover_pct",
                    "visibility_km",
                    "sunrise",
                    "sunset",
                )
            },
        ),
        (_("Record"), {"fields": ("public_id", "created_at", "updated_at", "raw_payload")}),
    )

    def get_queryset(self, request):
        return SurfCondition.all_objects.select_related("spot")

    @admin.display(description=_("period (s)"))
    def effective_period_display(self, obj) -> str:
        value = obj.effective_period_s
        return "—" if value is None else f"{value:.1f}"


@admin.register(SurfScore)
class SurfScoreAdmin(admin.ModelAdmin):
    list_display = ("condition", "level", "score", "is_safe_for_level", "is_ai_generated")
    list_filter = ("level", "is_safe_for_level", "is_ai_generated", "condition__spot")
    search_fields = ("condition__spot__name", "recommendation")
    ordering = ("-condition__recorded_at", "level")
    readonly_fields = ("condition", "level", "score", "factors", "is_ai_generated")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(ConditionForecast)
class ConditionForecastAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "spot",
        "best_level",
        "best_window_display",
        "wave_height_max",
        "wind_speed_max",
        "generated_at",
    )
    list_filter = ("spot", "best_level")
    search_fields = ("spot__name", "spot__code")
    date_hierarchy = "date"
    ordering = ("-date", "spot__name")
    autocomplete_fields = ("spot",)
    readonly_fields = ("public_id", "generated_at", "summary", "created_at", "updated_at")

    def get_queryset(self, request):
        return ConditionForecast.all_objects.select_related("spot")

    @admin.display(description=_("best window"))
    def best_window_display(self, obj) -> str:
        return obj.best_window_display

    @admin.display(description=_("max wave (m)"))
    def wave_height_max(self, obj):
        return obj.wave_height_max

    @admin.display(description=_("max wind (km/h)"))
    def wind_speed_max(self, obj):
        return obj.wind_speed_max
