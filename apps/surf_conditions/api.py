"""REST API for surf conditions, scores and daily forecasts.

The mobile check-in screen, the lessons module and any future beach tablet read
conditions through here. Two design points:

* Derived values (knots, feet, wetsuit, compass points, staleness) are serialised
  server-side. A client that re-implements the unit conversion will eventually
  disagree with the dashboard, and on a safety number that is not acceptable.
* Every score carries ``is_ai_generated``, which is always ``False`` for the
  computed score. A client can therefore assert the provenance of a number
  rather than assume it.
"""

from __future__ import annotations

from datetime import date as date_cls

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import SHARED, OwnerScopedQuerySetMixin
from apps.core.enums import SurfLevel

from . import selectors, services
from .models import ConditionForecast, SurfCondition, SurfScore
from .providers.registry import get_surf_provider, health_report


class SurfScoreSerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source="get_level_display", read_only=True)
    band = serializers.CharField(read_only=True)
    band_label = serializers.CharField(read_only=True)

    class Meta:
        model = SurfScore
        fields = [
            "id",
            "condition",
            "level",
            "level_label",
            "score",
            "band",
            "band_label",
            "factors",
            "recommendation",
            "is_safe_for_level",
            "is_ai_generated",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SurfConditionSerializer(serializers.ModelSerializer):
    """Read representation, including everything derived from the raw values."""

    spot_name = serializers.CharField(source="spot.name", read_only=True)
    spot_code = serializers.CharField(source="spot.code", read_only=True)
    tide_label = serializers.CharField(source="get_tide_state_display", read_only=True)
    wind_type_label = serializers.CharField(source="get_wind_type_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    wind_knots = serializers.FloatField(read_only=True)
    gust_knots = serializers.FloatField(read_only=True)
    wave_height_ft = serializers.FloatField(read_only=True)
    swell_height_ft = serializers.FloatField(read_only=True)
    effective_period_s = serializers.FloatField(read_only=True)
    wind_compass = serializers.CharField(read_only=True)
    swell_compass = serializers.CharField(read_only=True)
    recommended_wetsuit = serializers.CharField(read_only=True)
    is_stale = serializers.BooleanField(read_only=True)
    age_minutes = serializers.IntegerField(read_only=True)
    has_wave_data = serializers.BooleanField(read_only=True)
    scores = SurfScoreSerializer(many=True, read_only=True)

    class Meta:
        model = SurfCondition
        fields = [
            "id",
            "public_id",
            "spot",
            "spot_name",
            "spot_code",
            "recorded_at",
            "is_forecast",
            "is_stale",
            "age_minutes",
            "source",
            "source_label",
            "provider",
            "wave_height_m",
            "wave_height_ft",
            "wave_period_s",
            "wave_direction_deg",
            "swell_height_m",
            "swell_height_ft",
            "swell_period_s",
            "swell_direction_deg",
            "swell_compass",
            "effective_period_s",
            "wind_wave_height_m",
            "wind_speed_kmh",
            "wind_knots",
            "wind_gust_kmh",
            "gust_knots",
            "wind_direction_deg",
            "wind_compass",
            "wind_type",
            "wind_type_label",
            "sea_level_height_msl_m",
            "tide_state",
            "tide_label",
            "air_temperature_c",
            "water_temperature_c",
            "recommended_wetsuit",
            "weather_code",
            "weather_description",
            "uv_index",
            "precipitation_mm",
            "cloud_cover_pct",
            "visibility_km",
            "sunrise",
            "sunset",
            "has_wave_data",
            "scores",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]


class SurfConditionWriteSerializer(serializers.ModelSerializer):
    """Hand-logged readings only. Provider rows are written by the fetcher."""

    class Meta:
        model = SurfCondition
        fields = [
            "spot",
            "recorded_at",
            "wave_height_m",
            "wave_period_s",
            "wave_direction_deg",
            "swell_height_m",
            "swell_period_s",
            "swell_direction_deg",
            "wind_speed_kmh",
            "wind_gust_kmh",
            "wind_direction_deg",
            "tide_state",
            "air_temperature_c",
            "water_temperature_c",
            "weather_description",
            "precipitation_mm",
            "visibility_km",
        ]

    def validate(self, attrs):
        wave = attrs.get("wave_height_m", getattr(self.instance, "wave_height_m", None))
        wind = attrs.get("wind_speed_kmh", getattr(self.instance, "wind_speed_kmh", None))
        if wave is None and wind is None:
            raise serializers.ValidationError(
                {"wave_height_m": _("Record at least a wave height or a wind speed.")}
            )
        if wave is not None and (wave < 0 or wave > 25):
            raise serializers.ValidationError(
                {"wave_height_m": _("Enter a wave height between 0 and 25 m.")}
            )
        if wind is not None and (wind < 0 or wind > 250):
            raise serializers.ValidationError(
                {"wind_speed_kmh": _("Enter a wind speed between 0 and 250 km/h.")}
            )
        gust = attrs.get("wind_gust_kmh", getattr(self.instance, "wind_gust_kmh", None))
        if gust is not None and wind is not None and gust < wind:
            raise serializers.ValidationError(
                {"wind_gust_kmh": _("A gust cannot be weaker than the average wind speed.")}
            )
        return attrs


class ConditionForecastSerializer(serializers.ModelSerializer):
    spot_name = serializers.CharField(source="spot.name", read_only=True)
    best_level_label = serializers.CharField(source="get_best_level_display", read_only=True)
    best_window = serializers.CharField(source="best_window_display", read_only=True)
    wave_height_max = serializers.FloatField(read_only=True)
    wave_height_min = serializers.FloatField(read_only=True)
    wind_speed_max = serializers.FloatField(read_only=True)
    best_score = serializers.IntegerField(read_only=True)
    weather_description = serializers.CharField(read_only=True)

    class Meta:
        model = ConditionForecast
        fields = [
            "id",
            "public_id",
            "spot",
            "spot_name",
            "date",
            "generated_at",
            "best_window_start",
            "best_window_end",
            "best_window",
            "best_level",
            "best_level_label",
            "best_score",
            "wave_height_max",
            "wave_height_min",
            "wind_speed_max",
            "weather_description",
            "summary",
        ]
        read_only_fields = fields


def _error(message: str, error_type: str = "validation_error", detail: dict | None = None) -> dict:
    return {"error": {"type": error_type, "message": str(message), "detail": detail or {}}}


class SurfConditionViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Stored readings of the ocean and the sky."""

    capability_prefix = "surf_conditions"
    # The surf report is what a customer logs in for; it names spots, not
    # people.
    external_access = SHARED
    capability_overrides = {
        "refresh": "surf_conditions.change",
        "providers": "surf_conditions.view",
    }
    queryset = selectors.condition_queryset()
    serializer_class = SurfConditionSerializer
    filterset_fields = ["spot", "is_forecast", "source", "provider", "tide_state", "wind_type"]
    search_fields = ["spot__name", "spot__code", "weather_description"]
    ordering_fields = ["recorded_at", "wave_height_m", "wind_speed_kmh"]
    ordering = ["-recorded_at"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return SurfConditionWriteSerializer
        return SurfConditionSerializer

    def perform_create(self, serializer):
        condition = serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            source=SurfCondition.Source.MANUAL,
            provider="manual",
            is_forecast=False,
        )
        services.score_condition(condition)

    def perform_update(self, serializer):
        condition = serializer.save(updated_by=self.request.user)
        services.score_condition(condition)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in {"update", "partial_update", "destroy"}:
            # A provider reading is evidence of what was reported; only a
            # hand-logged one may be corrected through the API.
            queryset = queryset.filter(source=SurfCondition.Source.MANUAL)
        return queryset

    # -- extra actions -----------------------------------------------------
    @extend_schema(
        parameters=[
            OpenApiParameter("spot", int, description="Spot id. Defaults to the primary spot.")
        ],
        responses=SurfConditionSerializer,
    )
    @action(detail=False, methods=["get"])
    def current(self, request):
        """The best available description of *now* at one spot."""
        spot = self._resolve_spot(request)
        if spot is None:
            return Response(
                _error(_("No active surf spot configured."), "not_found"),
                status=status.HTTP_404_NOT_FOUND,
            )
        condition = services.current_or_nearest(spot)
        if condition is None:
            return Response(
                _error(
                    _("No conditions have been fetched for this spot yet."), "not_found"
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            SurfConditionSerializer(condition, context=self.get_serializer_context()).data
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("spot", int, description="Spot id."),
            OpenApiParameter("level", str, description="A SurfLevel value."),
            OpenApiParameter("date", str, description="ISO date; defaults to today."),
        ]
    )
    @action(detail=False, methods=["get"])
    def score(self, request):
        """The computed score for one spot, level and day, with its factors."""
        spot = self._resolve_spot(request)
        if spot is None:
            return Response(
                _error(_("No active surf spot configured."), "not_found"),
                status=status.HTTP_404_NOT_FOUND,
            )

        level = request.query_params.get("level", SurfLevel.BEGINNER)
        if level not in dict(SurfLevel.choices):
            return Response(
                _error(
                    _("Provide ?level= with a valid surf level."),
                    detail={"level": sorted(dict(SurfLevel.choices))},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_date = request.query_params.get("date", "")
        if raw_date:
            try:
                day = date_cls.fromisoformat(raw_date)
            except ValueError:
                return Response(
                    _error(_("Provide ?date= as YYYY-MM-DD.")),
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            day = None

        payload = services.conditions_for_tool(spot_query=spot.code, target_date=day)
        if payload.get("status") != "ok":
            return Response(payload, status=status.HTTP_404_NOT_FOUND)

        chosen = next(
            (row for row in payload["scores_by_level"] if row["level"] == level), None
        )
        return Response(
            {
                "spot": payload["spot"],
                "date": payload["date"],
                "level": level,
                "conditions": payload["conditions"],
                "score": chosen,
                "best_window": payload["best_window"],
                "is_computed": True,
                "is_ai_generated": False,
                "attribution": payload["attribution"],
            }
        )

    @extend_schema(request=None, responses=SurfConditionSerializer)
    @action(detail=False, methods=["post"])
    def refresh(self, request):
        """Fetch a fresh reading for one spot (or every spot when none is given)."""
        spot_id = request.data.get("spot") or request.query_params.get("spot")
        if not spot_id:
            return Response(services.refresh_all_spot_conditions())

        spot = self._resolve_spot(request, explicit=spot_id)
        if spot is None:
            return Response(
                _error(_("Unknown surf spot."), "not_found"), status=status.HTTP_404_NOT_FOUND
            )
        condition = services.refresh_spot_conditions(spot)
        if condition is None:
            return Response(
                _error(
                    _("The weather service did not answer. The last stored reading is unchanged."),
                    "upstream_unavailable",
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            SurfConditionSerializer(condition, context=self.get_serializer_context()).data
        )

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"])
    def providers(self, request):
        """Which data source is answering, whether it is up, and its credit line."""
        active = get_surf_provider()
        return Response(
            {
                "active": active.name,
                "attribution": active.attribution,
                "provides_marine_data": active.provides_marine_data,
                "providers": health_report(),
            }
        )

    # -- helpers -----------------------------------------------------------
    def _resolve_spot(self, request, explicit=None):
        from apps.locations.models import SurfSpot
        from apps.locations.services import get_primary_spot

        raw = explicit if explicit is not None else request.query_params.get("spot")
        if raw:
            try:
                return SurfSpot.objects.filter(pk=int(raw)).first()
            except (TypeError, ValueError):
                return SurfSpot.objects.filter(is_active=True, code__iexact=str(raw)).first()
        return get_primary_spot()


class SurfScoreViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Computed suitability scores. Read-only — a score is never typed in."""

    capability_prefix = "surf_conditions"
    external_access = SHARED
    queryset = SurfScore.objects.select_related("condition", "condition__spot").order_by(
        "-condition__recorded_at", "level"
    )
    serializer_class = SurfScoreSerializer
    filterset_fields = ["level", "is_safe_for_level", "condition", "condition__spot"]
    search_fields = ["recommendation", "condition__spot__name"]
    ordering_fields = ["score", "level"]


class ConditionForecastViewSet(
    OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet
):
    """Cached daily rollups: the week strip and the best window per day."""

    capability_prefix = "surf_conditions"
    external_access = SHARED
    queryset = ConditionForecast.objects.select_related("spot").order_by("spot__name", "date")
    serializer_class = ConditionForecastSerializer
    filterset_fields = ["spot", "date", "best_level"]
    search_fields = ["spot__name", "spot__code"]
    ordering_fields = ["date"]


ROUTES = [
    ("surf-conditions", SurfConditionViewSet, "surfcondition"),
    ("surf-scores", SurfScoreViewSet, "surfscore"),
    ("condition-forecasts", ConditionForecastViewSet, "conditionforecast"),
]
