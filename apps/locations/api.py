"""REST API for surf spots and spot hazards.

Other modules (surf conditions, lessons, bookings, the mobile check-in screen)
read spots through this API, so the serializers expose the derived values —
wind classification bearings, level range, hazard counts — rather than making
every client re-implement them.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.enums import SurfLevel, TideState

from . import selectors, services
from .models import SpotHazard, SurfSpot


class SpotHazardSerializer(serializers.ModelSerializer):
    severity_label = serializers.CharField(source="get_severity_display", read_only=True)
    tide_window = serializers.CharField(source="tide_window_display", read_only=True)
    spot_name = serializers.CharField(source="spot.name", read_only=True)

    class Meta:
        model = SpotHazard
        fields = [
            "id",
            "spot",
            "spot_name",
            "name",
            "severity",
            "severity_label",
            "description",
            "is_active",
            "applies_from_tide",
            "applies_to_tide",
            "tide_window",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = SpotHazard(**{**self._instance_data(), **attrs})
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs

    def _instance_data(self) -> dict:
        if self.instance is None:
            return {}
        return {
            field: getattr(self.instance, field)
            for field in ("spot", "name", "severity", "description", "is_active",
                          "applies_from_tide", "applies_to_tide")
        }


class SurfSpotSerializer(serializers.ModelSerializer):
    """Read representation, including everything derived from the geometry."""

    break_type_label = serializers.CharField(source="get_break_type_display", read_only=True)
    bottom_type_label = serializers.CharField(source="get_bottom_type_display", read_only=True)
    ideal_tide_label = serializers.CharField(source="get_ideal_tide_display", read_only=True)
    ideal_wind_label = serializers.CharField(source="get_ideal_wind_display", read_only=True)
    level_range = serializers.CharField(source="level_range_display", read_only=True)
    suitable_levels = serializers.ListField(child=serializers.CharField(), read_only=True)
    facing_compass = serializers.CharField(read_only=True)
    offshore_direction_deg = serializers.FloatField(read_only=True)
    offshore_compass = serializers.CharField(read_only=True)
    map_url = serializers.CharField(read_only=True)
    active_hazards = serializers.SerializerMethodField()

    class Meta:
        model = SurfSpot
        fields = [
            "id",
            "public_id",
            "code",
            "slug",
            "name",
            "description",
            "latitude",
            "longitude",
            "altitude",
            "beach_facing_deg",
            "facing_compass",
            "offshore_direction_deg",
            "offshore_compass",
            "break_type",
            "break_type_label",
            "bottom_type",
            "bottom_type_label",
            "min_level",
            "max_level",
            "level_range",
            "suitable_levels",
            "ideal_tide",
            "ideal_tide_label",
            "ideal_wind",
            "ideal_wind_label",
            "ideal_swell_direction_deg",
            "capacity",
            "is_active",
            "is_primary",
            "parking_info",
            "access_notes",
            "photo",
            "lifeguard_on_duty",
            "nearest_hospital",
            "nearest_hospital_phone",
            "emergency_notes",
            "map_url",
            "active_hazards",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "code", "slug", "created_at", "updated_at"]

    def get_active_hazards(self, obj) -> list:
        hazards = getattr(obj, "prefetched_active_hazards", None)
        if hazards is None:
            hazards = obj.hazards.filter(is_active=True)
        return SpotHazardSerializer(hazards, many=True, context=self.context).data


class SurfSpotWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SurfSpot
        fields = [
            "name",
            "description",
            "latitude",
            "longitude",
            "altitude",
            "beach_facing_deg",
            "break_type",
            "bottom_type",
            "min_level",
            "max_level",
            "ideal_tide",
            "ideal_wind",
            "ideal_swell_direction_deg",
            "capacity",
            "is_active",
            "is_primary",
            "parking_info",
            "access_notes",
            "photo",
            "lifeguard_on_duty",
            "nearest_hospital",
            "nearest_hospital_phone",
            "emergency_notes",
        ]

    def validate(self, attrs):
        instance = self.instance or SurfSpot()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class SurfSpotViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Surf spots: the breaks the school operates on."""

    capability_prefix = "locations"
    capability_overrides = {"set_primary": "locations.manage"}
    queryset = selectors.spot_queryset()
    serializer_class = SurfSpotSerializer
    filterset_fields = [
        "break_type",
        "bottom_type",
        "min_level",
        "max_level",
        "is_active",
        "is_primary",
        "lifeguard_on_duty",
    ]
    search_fields = ["name", "code", "description", "access_notes"]
    ordering_fields = ["name", "code", "capacity", "created_at"]
    ordering = ["-is_primary", "name"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return SurfSpotWriteSerializer
        return SurfSpotSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        services.archive_spot(instance, request=self.request, user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except DjangoValidationError as error:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "; ".join(error.messages),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # -- extra actions -----------------------------------------------------
    @extend_schema(responses=SurfSpotSerializer)
    @action(detail=False, methods=["get"])
    def primary(self, request):
        """The school's default spot."""
        spot = services.get_primary_spot()
        if spot is None:
            return Response(
                {
                    "error": {
                        "type": "not_found",
                        "message": _("No active surf spot configured."),
                        "detail": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(SurfSpotSerializer(spot, context=self.get_serializer_context()).data)

    @extend_schema(
        parameters=[
            OpenApiParameter("level", str, description="A SurfLevel value.", required=True)
        ],
        responses=SurfSpotSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def suitable(self, request):
        """Spots whose accepted level range contains ``?level=``."""
        level = request.query_params.get("level", "")
        if level not in dict(SurfLevel.choices):
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": _("Provide ?level= with a valid surf level."),
                        "detail": {"level": sorted(dict(SurfLevel.choices))},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        spots = services.spots_suitable_for_level(level).prefetch_related(
            selectors.active_hazard_prefetch()
        )
        serializer = SurfSpotSerializer(spots, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @extend_schema(request=None, responses=SurfSpotSerializer)
    @action(detail=True, methods=["post"], url_path="set-primary")
    def set_primary(self, request, pk=None):
        """Make this spot the school's default."""
        spot = self.get_object()
        try:
            services.set_primary_spot(spot, request=request, user=request.user)
        except DjangoValidationError as error:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "; ".join(error.messages),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(SurfSpotSerializer(spot, context=self.get_serializer_context()).data)

    @extend_schema(responses=SpotHazardSerializer(many=True))
    @action(detail=True, methods=["get"])
    def hazards(self, request, pk=None):
        """Every hazard on record for this spot, most serious first."""
        spot = self.get_object()
        only_active = request.query_params.get("active", "1") not in {"0", "false", "no"}
        queryset = selectors.hazards_for_spot(spot, only_active=only_active)
        return Response(
            SpotHazardSerializer(queryset, many=True, context=self.get_serializer_context()).data
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "direction", float, description="Wind bearing in degrees (from).", required=True
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path="classify-wind")
    def classify_wind(self, request, pk=None):
        """Classify a wind bearing against this spot's orientation."""
        raw = request.query_params.get("direction", "")
        try:
            direction = float(raw)
        except (TypeError, ValueError):
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": _("Provide ?direction= in degrees (0-360)."),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        spot = self.get_object()
        wind_type = services.classify_wind_for_spot(spot, direction)
        return Response(
            {
                "spot": spot.pk,
                "direction_deg": direction % 360.0,
                "wind_type": wind_type,
                "is_clean": services.wind_is_clean_for_spot(spot, direction),
                "beach_facing_deg": spot.beach_facing_deg,
                "offshore_direction_deg": spot.offshore_direction_deg,
            }
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("level", str, description="Group's surf level."),
            OpenApiParameter("group_size", int, description="Students to put in the water."),
            OpenApiParameter("occupied", int, description="Students already in the water."),
            OpenApiParameter("minors", bool, description="Group contains under-18s."),
            OpenApiParameter("tide", str, description="Current TideState."),
            OpenApiParameter("wind", float, description="Wind bearing in degrees (from)."),
        ]
    )
    @action(detail=True, methods=["get"])
    def assess(self, request, pk=None):
        """Go / caution / no-go for a specific group at this spot right now."""
        spot = self.get_object()
        params = request.query_params

        level = params.get("level", SurfLevel.BEGINNER)
        if level not in dict(SurfLevel.choices):
            level = SurfLevel.BEGINNER

        def _int(name: str, default: int) -> int:
            try:
                return max(0, int(params.get(name, default)))
            except (TypeError, ValueError):
                return default

        tide = params.get("tide", "")
        if tide not in dict(TideState.choices):
            tide = None

        wind = params.get("wind", "")
        try:
            wind_deg = float(wind) if wind != "" else None
        except (TypeError, ValueError):
            wind_deg = None

        assessment = services.assess_spot_for_group(
            spot,
            level=level,
            group_size=_int("group_size", 1) or 1,
            occupied_students=_int("occupied", 0),
            has_minors=params.get("minors", "").lower() in {"1", "true", "yes"},
            tide_state=tide,
            wind_direction_deg=wind_deg,
        )
        return Response(assessment.as_dict())


class SpotHazardViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Hazards attached to a surf spot."""

    capability_prefix = "locations"
    queryset = SpotHazard.objects.select_related("spot").order_by("spot__name", "name")
    serializer_class = SpotHazardSerializer
    filterset_fields = ["spot", "severity", "is_active"]
    search_fields = ["name", "description", "spot__name"]
    ordering_fields = ["name", "severity", "created_at"]

    def perform_destroy(self, instance):
        """Never hard-delete a hazard — deactivate it, so the record survives."""
        services.set_hazard_active(
            instance, is_active=False, request=self.request, user=self.request.user
        )


ROUTES = [
    ("surf-spots", SurfSpotViewSet, "surfspot"),
    ("spot-hazards", SpotHazardViewSet, "spothazard"),
]
