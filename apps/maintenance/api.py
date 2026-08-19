"""REST API for maintenance records, schedules and the risk forecast."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.enums import EquipmentCondition

from . import selectors, services
from .models import MaintenanceRecord, MaintenanceSchedule


def _as_drf_error(exc: DjangoValidationError) -> serializers.ValidationError:
    """Translate a service-layer ValidationError into a DRF one."""
    if hasattr(exc, "message_dict"):
        return serializers.ValidationError(exc.message_dict)
    return serializers.ValidationError({"detail": exc.messages})


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class MaintenanceRecordSerializer(serializers.ModelSerializer):
    equipment_label = serializers.SerializerMethodField()
    damage_type_label = serializers.CharField(source="get_damage_type_display", read_only=True)
    severity_label = serializers.CharField(source="get_severity_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    reported_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    downtime_days = serializers.IntegerField(read_only=True)
    age_days = serializers.IntegerField(read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = MaintenanceRecord
        fields = [
            "id",
            "public_id",
            "record_code",
            "equipment",
            "equipment_label",
            "damage_type",
            "damage_type_label",
            "severity",
            "severity_label",
            "status",
            "status_label",
            "reported_by",
            "reported_by_name",
            "reported_at",
            "assigned_to",
            "assigned_to_name",
            "started_at",
            "completed_at",
            "description",
            "diagnosis",
            "resolution",
            "parts_used",
            "labour_hours",
            "parts_cost",
            "labour_cost",
            "total_cost",
            "photo_before",
            "photo_after",
            "rental_item",
            "made_unusable",
            "downtime_days",
            "age_days",
            "is_open",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "record_code",
            # The item a record belongs to and every workflow-owned value are
            # read-only here: they change through the service actions below so
            # the equipment status and the audit trail stay consistent.
            "equipment",
            "status",
            "started_at",
            "completed_at",
            "resolution",
            "labour_hours",
            "parts_cost",
            "labour_cost",
            "total_cost",
            "created_at",
            "updated_at",
        ]

    def get_equipment_label(self, obj) -> str:
        return str(obj.equipment) if obj.equipment_id else ""

    def get_reported_by_name(self, obj) -> str:
        return obj.reported_by.get_display_name() if obj.reported_by_id else ""

    def get_assigned_to_name(self, obj) -> str:
        return obj.assigned_to.get_display_name() if obj.assigned_to_id else ""


def _related_queryset(field_name: str):
    """Queryset of whatever model a MaintenanceRecord FK points at.

    Resolved when the serializer is instantiated rather than at import time, so
    this module never depends on the load order of the sibling apps.
    """
    return MaintenanceRecord._meta.get_field(field_name).related_model._default_manager.all()


class MaintenanceReportSerializer(serializers.Serializer):
    """Input for POST /maintenance/records/ — goes through the service layer."""

    damage_type = serializers.CharField()
    severity = serializers.CharField()
    description = serializers.CharField()
    make_unusable = serializers.BooleanField(default=True)
    force = serializers.BooleanField(default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The relational fields are built here rather than at class level:
        # DRF asserts a non-None queryset at field construction, and resolving
        # the related model at import time would couple this module to the app
        # load order.
        self.fields["equipment"] = serializers.PrimaryKeyRelatedField(
            queryset=_related_queryset("equipment")
        )
        self.fields["rental_item"] = serializers.PrimaryKeyRelatedField(
            queryset=_related_queryset("rental_item"), required=False, allow_null=True
        )
        self.fields["assigned_to"] = serializers.PrimaryKeyRelatedField(
            queryset=_related_queryset("assigned_to"), required=False, allow_null=True
        )


class MaintenanceCompletionSerializer(serializers.Serializer):
    resolution = serializers.CharField()
    parts_used = serializers.CharField(required=False, allow_blank=True)
    labour_hours = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=0
    )
    parts_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=0
    )
    labour_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    still_unusable = serializers.BooleanField(default=False)
    retire_equipment = serializers.BooleanField(default=False)
    condition_after = serializers.ChoiceField(
        choices=EquipmentCondition.choices, required=False, allow_blank=True
    )


class MaintenanceReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


class MaintenanceStartSerializer(serializers.Serializer):
    diagnosis = serializers.CharField(required=False, allow_blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"] = serializers.PrimaryKeyRelatedField(
            queryset=_related_queryset("assigned_to"), required=False, allow_null=True
        )


class MaintenanceScheduleSerializer(serializers.ModelSerializer):
    equipment_label = serializers.SerializerMethodField()
    is_due = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_until_due = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = MaintenanceSchedule
        fields = [
            "id",
            "public_id",
            "equipment",
            "equipment_label",
            "interval_days",
            "last_performed_on",
            "next_due_on",
            "check_items",
            "is_active",
            "is_due",
            "is_overdue",
            "days_until_due",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "next_due_on", "created_at", "updated_at"]

    def get_equipment_label(self, obj) -> str:
        return str(obj.equipment) if obj.equipment_id else ""

    def validate_check_items(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Provide the check list as a list of strings."))
        return [str(item).strip() for item in value if str(item).strip()]


class SchedulePerformedSerializer(serializers.Serializer):
    performed_on = serializers.DateField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class MaintenanceRecordViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Maintenance records. Creation and every transition run through services."""

    capability_prefix = "maintenance"
    capability_overrides = {
        "start": "maintenance.change",
        "complete": "maintenance.change",
        "hold": "maintenance.change",
        "cancel": "maintenance.change",
        "predictions": "maintenance.view",
        "cost_report": "maintenance.view",
    }
    serializer_class = MaintenanceRecordSerializer
    filterset_fields = ["status", "severity", "damage_type", "equipment", "assigned_to", "made_unusable"]
    search_fields = ["record_code", "description", "resolution"]
    ordering_fields = ["reported_at", "completed_at", "severity", "total_cost"]
    ordering = ["-reported_at"]

    def get_queryset(self):
        return selectors.records_queryset()

    def create(self, request, *args, **kwargs):
        serializer = MaintenanceReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            record = services.report_issue(
                equipment=data["equipment"],
                damage_type=data["damage_type"],
                severity=data["severity"],
                description=data["description"],
                user=request.user,
                make_unusable=data.get("make_unusable", True),
                rental_item=data.get("rental_item"),
                assigned_to=data.get("assigned_to"),
                request=request,
                force=data.get("force", False),
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(
            MaintenanceRecordSerializer(record).data, status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        """Maintenance records are financial history — cancel instead of deleting."""
        return Response(
            {
                "error": {
                    "type": "not_allowed",
                    "message": _(
                        "Maintenance records are never deleted. Cancel the record instead."
                    ),
                    "detail": {},
                }
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        record = self.get_object()
        serializer = MaintenanceStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.start_work(
                record,
                user=request.user,
                assigned_to=serializer.validated_data.get("assigned_to"),
                diagnosis=serializer.validated_data.get("diagnosis", ""),
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(MaintenanceRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        record = self.get_object()
        serializer = MaintenanceReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.put_on_hold(
                record,
                reason=serializer.validated_data["reason"],
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(MaintenanceRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        record = self.get_object()
        serializer = MaintenanceReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.cancel_maintenance(
                record,
                reason=serializer.validated_data["reason"],
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(MaintenanceRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        record = self.get_object()
        serializer = MaintenanceCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            services.complete_maintenance(
                record,
                resolution=data["resolution"],
                costs={
                    "labour_hours": data.get("labour_hours", record.labour_hours),
                    "parts_cost": data.get("parts_cost", record.parts_cost),
                    "labour_cost": data.get("labour_cost"),
                    "parts_used": data.get("parts_used", record.parts_used),
                },
                user=request.user,
                still_unusable=data.get("still_unusable", False),
                retire_equipment=data.get("retire_equipment", False),
                condition_after=data.get("condition_after") or None,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(MaintenanceRecordSerializer(record).data)

    @action(detail=False, methods=["get"])
    def predictions(self, request):
        """Deterministic risk forecast — statistics over recorded history only.

        ``?refresh=1`` recomputes instead of reading the cached nightly run.
        """
        payload = services.cached_maintenance_predictions(
            refresh=request.query_params.get("refresh") == "1"
        )
        predictions = services.annotate_prediction_texts(
            [dict(item) for item in payload.get("predictions", [])]
        )
        return Response(
            {
                "generated_at": payload.get("generated_at"),
                "method": "deterministic_statistics",
                "signal_weights": services.SIGNAL_WEIGHTS,
                "count": len(predictions),
                "results": predictions,
            }
        )

    @action(detail=False, methods=["get"], url_path="cost-report")
    def cost_report(self, request):
        from apps.core.utils import parse_date_range

        start, end, label = parse_date_range(request, default="90")
        report = services.maintenance_cost_report(start, end)
        report["range_label"] = label
        return Response(report)


class MaintenanceScheduleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Preventive maintenance plans."""

    capability_prefix = "maintenance"
    capability_overrides = {"performed": "maintenance.change", "due": "maintenance.view"}
    serializer_class = MaintenanceScheduleSerializer
    filterset_fields = ["is_active", "equipment"]
    ordering_fields = ["next_due_on", "interval_days"]
    ordering = ["next_due_on"]

    def get_queryset(self):
        return selectors.schedules_queryset()

    @action(detail=True, methods=["post"])
    def performed(self, request, pk=None):
        schedule = self.get_object()
        serializer = SchedulePerformedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.mark_schedule_performed(
                schedule,
                performed_on=serializer.validated_data.get("performed_on"),
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(MaintenanceScheduleSerializer(schedule).data)

    @action(detail=False, methods=["get"])
    def due(self, request):
        """Schedules that are due now (``?within=14`` for a look-ahead)."""
        try:
            within = int(request.query_params.get("within", 0))
        except (TypeError, ValueError):
            within = 0
        queryset = services.due_for_scheduled_maintenance(within_days=max(0, within))
        page = self.paginate_queryset(queryset)
        serializer = MaintenanceScheduleSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


ROUTES = [
    ("maintenance/records", MaintenanceRecordViewSet, "maintenance-record"),
    ("maintenance/schedules", MaintenanceScheduleViewSet, "maintenance-schedule"),
]
