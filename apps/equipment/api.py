"""REST API for the equipment fleet.

The write path deliberately refuses ``status``: a status move must go through
:func:`apps.equipment.services.change_status` so the state machine and the audit
trail apply to API clients exactly as they do to the web UI. The
``change-status`` action is the supported way in.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from .models import Equipment, EquipmentCategory, EquipmentPhoto
from .services import (
    available_equipment,
    change_status,
    fleet_summary,
    recommend_board,
    recommend_wetsuit,
    utilisation_report,
)


class EquipmentCategorySerializer(serializers.ModelSerializer):
    full_path = serializers.CharField(read_only=True)
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = EquipmentCategory
        fields = [
            "id",
            "code",
            "name",
            "parent",
            "full_path",
            "icon",
            "sort_order",
            "is_active",
            "item_count",
        ]


class EquipmentPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentPhoto
        fields = ["id", "image", "caption", "is_primary", "taken_at"]


class EquipmentSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_code = serializers.CharField(source="category.code", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    condition_label = serializers.CharField(source="get_condition_display", read_only=True)
    photos = EquipmentPhotoSerializer(many=True, read_only=True)
    is_available = serializers.BooleanField(read_only=True)
    needs_maintenance = serializers.BooleanField(read_only=True)
    age_days = serializers.IntegerField(read_only=True)
    depreciation_percent = serializers.DecimalField(
        max_digits=6, decimal_places=2, read_only=True
    )
    utilisation_rate = serializers.DecimalField(
        max_digits=6, decimal_places=2, read_only=True
    )
    qr_payload = serializers.CharField(read_only=True)

    class Meta:
        model = Equipment
        fields = [
            "id",
            "public_id",
            "asset_code",
            "category",
            "category_name",
            "category_code",
            "name",
            "brand",
            "model",
            "serial_number",
            "size_label",
            "length_cm",
            "width_cm",
            "thickness_cm",
            "volume_litres",
            "wetsuit_thickness",
            "suitable_min_level",
            "suitable_max_level",
            "min_rider_weight_kg",
            "max_rider_weight_kg",
            "purchase_date",
            "purchase_price",
            "current_value",
            "supplier",
            "status",
            "status_label",
            "condition",
            "condition_label",
            "storage_location",
            "is_rentable",
            "is_lesson_stock",
            "rental_price_hourly",
            "rental_price_daily",
            "rental_price_weekly",
            "deposit_amount",
            "total_rentals",
            "total_rental_hours",
            "last_maintenance_date",
            "next_maintenance_date",
            "notes",
            "retired_at",
            "retired_reason",
            "photos",
            "is_available",
            "needs_maintenance",
            "age_days",
            "depreciation_percent",
            "utilisation_rate",
            "qr_payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "total_rentals",
            "total_rental_hours",
            "retired_at",
            "retired_reason",
            "created_at",
            "updated_at",
        ]


class EquipmentWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = [
            "asset_code",
            "category",
            "name",
            "brand",
            "model",
            "serial_number",
            "size_label",
            "length_cm",
            "width_cm",
            "thickness_cm",
            "volume_litres",
            "wetsuit_thickness",
            "suitable_min_level",
            "suitable_max_level",
            "min_rider_weight_kg",
            "max_rider_weight_kg",
            "purchase_date",
            "purchase_price",
            "current_value",
            "supplier",
            "condition",
            "storage_location",
            "is_rentable",
            "is_lesson_stock",
            "rental_price_hourly",
            "rental_price_daily",
            "rental_price_weekly",
            "deposit_amount",
            "last_maintenance_date",
            "next_maintenance_date",
            "notes",
        ]
        extra_kwargs = {"asset_code": {"required": False, "allow_blank": True}}

    def validate(self, attrs):
        instance = Equipment(**{**self._instance_values(), **attrs})
        instance.pk = self.instance.pk if self.instance else None
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs

    def _instance_values(self) -> dict:
        if self.instance is None:
            return {}
        return {
            field.name: getattr(self.instance, field.name)
            for field in Equipment._meta.concrete_fields
            if field.name != "id"
        }


class EquipmentCategoryViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """The equipment taxonomy."""

    capability_prefix = "equipment"
    queryset = EquipmentCategory.objects.select_related("parent").all()
    serializer_class = EquipmentCategorySerializer
    filterset_fields = ["is_active", "parent"]
    search_fields = ["code", "name"]
    ordering_fields = ["sort_order", "name", "code"]
    ordering = ["sort_order", "name"]


class EquipmentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD plus the availability and sizing endpoints the tablets use."""

    capability_prefix = "equipment"
    capability_overrides = {
        "change_status": "equipment.change",
        "available": "equipment.view",
        "recommend_board": "equipment.view",
        "recommend_wetsuit": "equipment.view",
        "utilisation": "equipment.view",
        "summary": "equipment.view",
    }
    queryset = Equipment.objects.select_related("category").prefetch_related("photos")
    serializer_class = EquipmentSerializer
    filterset_fields = [
        "status",
        "condition",
        "category",
        "is_rentable",
        "is_lesson_stock",
        "suitable_min_level",
    ]
    search_fields = ["asset_code", "name", "brand", "model", "serial_number"]
    ordering_fields = ["asset_code", "name", "current_value", "next_maintenance_date"]
    ordering = ["asset_code"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return EquipmentWriteSerializer
        return EquipmentSerializer

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user if self.request.user.is_authenticated else None,
            updated_by=self.request.user if self.request.user.is_authenticated else None,
        )

    def perform_update(self, serializer):
        serializer.save(
            updated_by=self.request.user if self.request.user.is_authenticated else None
        )

    # ------------------------------------------------------------------ actions
    @action(detail=False, methods=["get"])
    def available(self, request):
        """Gear that can be handed out for an optional ``start``/``end`` window."""
        start = parse_datetime(request.query_params.get("start", "") or "")
        end = parse_datetime(request.query_params.get("end", "") or "")
        queryset = available_equipment(
            category=request.query_params.get("category") or None,
            start=start,
            end=end,
            level=request.query_params.get("level") or None,
            rider_weight_kg=request.query_params.get("weight_kg") or None,
        )
        page = self.paginate_queryset(queryset)
        serializer = EquipmentSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        """Move one item through the equipment state machine."""
        equipment = self.get_object()
        try:
            change_status(
                equipment,
                request.data.get("status", ""),
                user=request.user,
                reason=request.data.get("reason", ""),
                request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": " ".join(str(message) for message in exc.messages),
                        "detail": getattr(exc, "message_dict", {}),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(EquipmentSerializer(equipment).data)

    @action(detail=False, methods=["get"], url_path="recommend-board")
    def recommend_board(self, request):
        """Board suggestion for a rider weight and level, with the reasoning."""
        result = recommend_board(
            request.query_params.get("weight_kg"),
            request.query_params.get("level", ""),
        )
        return Response(
            {
                "equipment": (
                    EquipmentSerializer(result.equipment).data if result.equipment else None
                ),
                "target_volume_litres": result.target_volume_litres,
                "recommended_length_cm": result.recommended_length_cm,
                "soft_top_required": result.soft_top_required,
                "reasoning": result.reasoning,
                "alternatives": EquipmentSerializer(result.alternatives, many=True).data,
                "is_recommendation": True,
            }
        )

    @action(detail=False, methods=["get"], url_path="recommend-wetsuit")
    def recommend_wetsuit(self, request):
        """Wetsuit suggestion for a water temperature and size."""
        result = recommend_wetsuit(
            request.query_params.get("water_temp_c"),
            request.query_params.get("size", ""),
        )
        return Response(
            {
                "equipment": (
                    EquipmentSerializer(result.equipment).data if result.equipment else None
                ),
                "thickness": result.thickness,
                "recommendation": result.recommendation,
                "required_accessories": result.required_accessories,
                "reasoning": result.reasoning,
                "alternatives": EquipmentSerializer(result.alternatives, many=True).data,
                "is_recommendation": True,
            }
        )

    @action(detail=False, methods=["get"])
    def utilisation(self, request):
        """Per-item usage over ``start``/``end`` (defaults to the last 30 days)."""
        rows = utilisation_report(
            start=parse_datetime(request.query_params.get("start", "") or ""),
            end=parse_datetime(request.query_params.get("end", "") or ""),
            category=request.query_params.get("category") or None,
        )
        return Response(
            [
                {
                    "id": row["equipment"].pk,
                    "asset_code": row["asset_code"],
                    "name": row["name"],
                    "category": row["category"],
                    "status": row["status"],
                    "rentals": row["rentals"],
                    "hours": row["hours"],
                    "utilisation_percent": row["utilisation_percent"],
                    "is_lifetime": row["is_lifetime"],
                    "window_days": row["window_days"],
                }
                for row in rows
            ]
        )

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Headline fleet counts for the dashboard tiles."""
        return Response(fleet_summary())


ROUTES = [
    ("equipment", EquipmentViewSet, "equipment"),
    ("equipment-categories", EquipmentCategoryViewSet, "equipment-category"),
]
