"""REST API for the safety module.

The API enforces the same rule as the screens: ``WeatherWarningSerializer``
always exposes ``is_authoritative`` and ``awaiting_confirmation``, and the only
way to flip an AI suggestion into a real warning is the ``acknowledge`` action,
which runs through :func:`apps.safety.services.acknowledge_warning` and
therefore demands ``safety.approve``. There is no writable field that lets a
client fake a sign-off.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.enums import SurfLevel

from . import selectors, services
from .models import (
    EmergencyContact,
    EquipmentSafetyCheck,
    EvacuationPlan,
    LifeguardAssignment,
    SafetyIncident,
    StudentRestriction,
    WeatherWarning,
)


def _error(message: str, detail: dict | None = None, code: str = "validation_error") -> dict:
    return {"error": {"type": code, "message": str(message), "detail": detail or {}}}


class IncidentReviewRequestSerializer(serializers.Serializer):
    """Body of ``POST /safety-incidents/{id}/review/``."""

    root_cause = serializers.CharField(allow_blank=True, required=False)
    corrective_action = serializers.CharField(allow_blank=True, required=False)
    status = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class SafetyIncidentSerializer(serializers.ModelSerializer):
    incident_type_label = serializers.CharField(
        source="get_incident_type_display", read_only=True
    )
    severity_label = serializers.CharField(source="get_severity_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    spot_name = serializers.CharField(source="spot.name", read_only=True, default=None)
    reported_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    is_open = serializers.BooleanField(read_only=True)
    days_open = serializers.IntegerField(read_only=True)
    is_follow_up_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = SafetyIncident
        fields = [
            "id",
            "public_id",
            "incident_code",
            "occurred_at",
            "spot",
            "spot_name",
            "lesson",
            "incident_type",
            "incident_type_label",
            "severity",
            "severity_label",
            "status",
            "status_label",
            "people_involved",
            "staff_involved",
            "reported_by",
            "reported_by_name",
            "description",
            "immediate_action",
            "root_cause",
            "corrective_action",
            "medical_attention_required",
            "emergency_services_called",
            "conditions_at_time",
            "photo",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "follow_up_required",
            "follow_up_due",
            "is_open",
            "days_open",
            "is_follow_up_overdue",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "incident_code",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]

    def get_reported_by_name(self, obj) -> str:
        return obj.reported_by.get_display_name() if obj.reported_by_id else ""

    def get_reviewed_by_name(self, obj) -> str:
        return obj.reviewed_by.get_display_name() if obj.reviewed_by_id else ""

    def validate(self, attrs):
        instance = self.instance or SafetyIncident()
        for name, value in attrs.items():
            if name in {"people_involved", "staff_involved"}:
                continue
            setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class SafetyIncidentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Safety incidents — near misses included, and they matter most."""

    capability_prefix = "safety"
    capability_overrides = {"review": "safety.approve"}
    queryset = selectors.incident_queryset()
    serializer_class = SafetyIncidentSerializer
    filterset_fields = [
        "incident_type",
        "severity",
        "status",
        "spot",
        "medical_attention_required",
        "emergency_services_called",
        "follow_up_required",
    ]
    search_fields = ["incident_code", "description", "root_cause", "corrective_action"]
    ordering_fields = ["occurred_at", "severity", "status", "created_at"]
    ordering = ["-occurred_at"]

    def perform_create(self, serializer):
        incident = serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            reported_by=serializer.validated_data.get("reported_by") or self.request.user,
        )
        services.notify_incident(incident)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(responses=SafetyIncidentSerializer(many=True))
    @action(detail=False, methods=["get"])
    def open(self, request):
        """Incidents that still need someone to act."""
        queryset = self.filter_queryset(selectors.open_incidents())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(
        request=IncidentReviewRequestSerializer,
        responses=SafetyIncidentSerializer,
        description="Sign off an incident: root cause, corrective action, new status.",
    )
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        incident = self.get_object()
        body = IncidentReviewRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            services.review_incident(
                incident,
                user=request.user,
                root_cause=body.validated_data.get("root_cause", ""),
                corrective_action=body.validated_data.get("corrective_action", ""),
                status=body.validated_data.get("status") or None,
                request=request,
            )
        except PermissionDenied as error:
            return Response(
                _error(error, code="permission_denied"), status=status.HTTP_403_FORBIDDEN
            )
        except DjangoValidationError as error:
            return Response(
                _error("; ".join(error.messages)), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(self.get_serializer(incident).data)

    @extend_schema(
        parameters=[OpenApiParameter("spot", int, description="Limit to one surf spot.")]
    )
    @action(detail=False, methods=["get"], url_path="days-since-last")
    def days_since_last(self, request):
        """Days since the last incident — the number the briefing opens with."""
        spot = None
        raw = request.query_params.get("spot", "")
        if raw.isdigit():
            from apps.locations.models import SurfSpot

            spot = SurfSpot.objects.filter(pk=int(raw)).first()
        return Response(
            {
                "spot": spot.pk if spot else None,
                "days_since_last_incident": services.days_since_last_incident(spot=spot),
            }
        )


# ---------------------------------------------------------------------------
# Lifeguard assignments
# ---------------------------------------------------------------------------
class LifeguardAssignmentSerializer(serializers.ModelSerializer):
    spot_name = serializers.CharField(source="spot.name", read_only=True)
    lifeguard_name = serializers.SerializerMethodField()
    duration_minutes = serializers.IntegerField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = LifeguardAssignment
        fields = [
            "id",
            "public_id",
            "spot",
            "spot_name",
            "lifeguard",
            "lifeguard_name",
            "date",
            "start_time",
            "end_time",
            "is_confirmed",
            "duration_minutes",
            "is_current",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def get_lifeguard_name(self, obj) -> str:
        return obj.lifeguard.get_display_name() if obj.lifeguard_id else ""

    def validate(self, attrs):
        instance = self.instance or LifeguardAssignment()
        for name, value in attrs.items():
            setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class LifeguardAssignmentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Water-safety cover, one shift per row."""

    capability_prefix = "safety"
    capability_overrides = {"confirm": "safety.change"}
    queryset = selectors.assignment_queryset()
    serializer_class = LifeguardAssignmentSerializer
    filterset_fields = ["spot", "lifeguard", "date", "is_confirmed"]
    search_fields = ["notes", "spot__name", "lifeguard__username"]
    ordering_fields = ["date", "start_time"]
    ordering = ["date", "start_time"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(request=None, responses=LifeguardAssignmentSerializer)
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Confirm a shift so it starts counting as cover."""
        assignment = self.get_object()
        services.confirm_assignment(assignment, user=request.user, request=request)
        return Response(self.get_serializer(assignment).data)

    @extend_schema(responses=LifeguardAssignmentSerializer(many=True))
    @action(detail=False, methods=["get"])
    def today(self, request):
        """Today's roster."""
        return Response(self.get_serializer(services.cover_today(), many=True).data)


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------
class EmergencyContactSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    scope = serializers.CharField(source="scope_label", read_only=True)
    applies_everywhere = serializers.BooleanField(read_only=True)

    class Meta:
        model = EmergencyContact
        fields = [
            "id",
            "name",
            "organisation",
            "kind",
            "kind_label",
            "phone",
            "alternate_phone",
            "address",
            "notes",
            "spot",
            "scope",
            "applies_everywhere",
            "sort_order",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class EmergencyContactViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """The numbers dialled in an emergency."""

    capability_prefix = "safety"
    queryset = EmergencyContact.objects.select_related("spot").order_by(
        "sort_order", "kind", "name"
    )
    serializer_class = EmergencyContactSerializer
    filterset_fields = ["kind", "spot", "is_active"]
    search_fields = ["name", "organisation", "phone", "address"]
    ordering_fields = ["sort_order", "name", "kind"]
    ordering = ["sort_order", "kind", "name"]

    @extend_schema(
        parameters=[OpenApiParameter("spot", int, description="Surf spot id.")],
        responses=EmergencyContactSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def card(self, request):
        """Contacts for one spot: its own, plus the ones that apply everywhere."""
        spot = None
        raw = request.query_params.get("spot", "")
        if raw.isdigit():
            from apps.locations.models import SurfSpot

            spot = SurfSpot.objects.filter(pk=int(raw)).first()
        contacts = selectors.emergency_contacts(spot=spot)
        return Response(self.get_serializer(contacts, many=True).data)


# ---------------------------------------------------------------------------
# Evacuation plans
# ---------------------------------------------------------------------------
class EvacuationPlanSerializer(serializers.ModelSerializer):
    spot_name = serializers.CharField(source="spot.name", read_only=True)
    responsible_role_label = serializers.CharField(
        source="get_responsible_role_display", read_only=True
    )
    step_count = serializers.IntegerField(read_only=True)
    is_drill_overdue = serializers.BooleanField(read_only=True)
    days_until_drill = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = EvacuationPlan
        fields = [
            "id",
            "public_id",
            "spot",
            "spot_name",
            "title",
            "trigger_conditions",
            "assembly_point",
            "steps",
            "step_count",
            "responsible_role",
            "responsible_role_label",
            "document",
            "last_drill_date",
            "next_drill_due",
            "is_drill_overdue",
            "days_until_drill",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def validate_steps(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Steps must be an ordered list of lines."))
        cleaned = [str(step).strip() for step in value if str(step).strip()]
        if not cleaned:
            raise serializers.ValidationError(_("A plan with no steps is not a plan."))
        return cleaned


class EvacuationPlanViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """What everyone does when the beach has to be cleared."""

    capability_prefix = "safety"
    queryset = selectors.plan_queryset()
    serializer_class = EvacuationPlanSerializer
    filterset_fields = ["spot", "is_active", "responsible_role"]
    search_fields = ["title", "trigger_conditions", "assembly_point"]
    ordering_fields = ["title", "next_drill_due"]
    ordering = ["spot__name", "title"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(responses=EvacuationPlanSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="drills-due")
    def drills_due(self, request):
        """Plans whose next drill is due within 30 days, overdue first."""
        return Response(self.get_serializer(services.upcoming_drills(), many=True).data)


# ---------------------------------------------------------------------------
# Equipment safety checks
# ---------------------------------------------------------------------------
class EquipmentSafetyCheckSerializer(serializers.ModelSerializer):
    equipment_name = serializers.SerializerMethodField()
    checked_by_name = serializers.SerializerMethodField()
    failed_items = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = EquipmentSafetyCheck
        fields = [
            "id",
            "public_id",
            "equipment",
            "equipment_name",
            "checked_by",
            "checked_by_name",
            "checked_at",
            "passed",
            "checklist",
            "failed_items",
            "issues_found",
            "action_taken",
            "next_check_due",
            "is_overdue",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def get_equipment_name(self, obj) -> str:
        return str(obj.equipment) if obj.equipment_id else ""

    def get_checked_by_name(self, obj) -> str:
        return obj.checked_by.get_display_name() if obj.checked_by_id else ""

    def validate(self, attrs):
        instance = self.instance or EquipmentSafetyCheck()
        for name, value in attrs.items():
            setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class EquipmentSafetyCheckViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Pass/fail inspections of individual items."""

    capability_prefix = "safety"
    queryset = selectors.check_queryset()
    serializer_class = EquipmentSafetyCheckSerializer
    filterset_fields = ["equipment", "passed", "checked_by"]
    search_fields = ["equipment__name", "equipment__asset_code", "issues_found"]
    ordering_fields = ["checked_at", "next_check_due"]
    ordering = ["-checked_at"]

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            checked_by=serializer.validated_data.get("checked_by") or self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(responses=EquipmentSafetyCheckSerializer(many=True))
    @action(detail=False, methods=["get"])
    def overdue(self, request):
        """Items whose latest check named a due date that has now passed."""
        return Response(
            self.get_serializer(services.overdue_equipment_checks(), many=True).data
        )


# ---------------------------------------------------------------------------
# Weather warnings
# ---------------------------------------------------------------------------
class WeatherWarningSerializer(serializers.ModelSerializer):
    severity_label = serializers.CharField(source="get_severity_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    spot_name = serializers.CharField(source="spot.name", read_only=True, default=None)
    acknowledged_by_name = serializers.SerializerMethodField()
    is_authoritative = serializers.BooleanField(read_only=True)
    awaiting_confirmation = serializers.BooleanField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    is_in_force = serializers.BooleanField(read_only=True)
    display_title = serializers.CharField(read_only=True)

    class Meta:
        model = WeatherWarning
        fields = [
            "id",
            "public_id",
            "spot",
            "spot_name",
            "title",
            "display_title",
            "severity",
            "severity_label",
            "source",
            "source_label",
            "description",
            "starts_at",
            "ends_at",
            "is_active",
            "ai_suggested",
            "ai_rationale",
            "acknowledged_by",
            "acknowledged_by_name",
            "acknowledged_at",
            "is_authoritative",
            "awaiting_confirmation",
            "is_current",
            "is_in_force",
            "created_at",
            "updated_at",
        ]
        # A client can never write its own sign-off — that is what the
        # ``acknowledge`` action is for, and it checks ``safety.approve``.
        read_only_fields = [
            "id",
            "public_id",
            "acknowledged_by",
            "acknowledged_at",
            "created_at",
            "updated_at",
        ]

    def get_acknowledged_by_name(self, obj) -> str:
        return obj.acknowledged_by.get_display_name() if obj.acknowledged_by_id else ""

    def validate(self, attrs):
        instance = self.instance or WeatherWarning()
        for name, value in attrs.items():
            setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class WeatherWarningViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Weather warnings, including AI suggestions awaiting a human decision."""

    capability_prefix = "safety"
    capability_overrides = {
        "acknowledge": "safety.approve",
        "dismiss": "safety.approve",
    }
    queryset = selectors.warning_queryset()
    serializer_class = WeatherWarningSerializer
    filterset_fields = ["spot", "severity", "source", "is_active", "ai_suggested"]
    search_fields = ["title", "description", "ai_rationale"]
    ordering_fields = ["starts_at", "ends_at", "severity"]
    ordering = ["-starts_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(
        parameters=[OpenApiParameter("spot", int, description="Limit to one surf spot.")],
        responses=WeatherWarningSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def authoritative(self, request):
        """Warnings other modules may act on.

        Unconfirmed AI suggestions are **excluded**: read them from
        ``/pending/`` if you are building a sign-off screen.
        """
        spot = self._spot_param(request)
        return Response(
            self.get_serializer(services.authoritative_warnings(spot=spot), many=True).data
        )

    @extend_schema(responses=WeatherWarningSerializer(many=True))
    @action(detail=False, methods=["get"])
    def pending(self, request):
        """AI suggestions still waiting for a member of staff to sign them off."""
        spot = self._spot_param(request)
        return Response(
            self.get_serializer(services.pending_ai_warnings(spot=spot), many=True).data
        )

    @extend_schema(request=None, responses=WeatherWarningSerializer)
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """The human sign-off. Requires ``safety.approve``."""
        warning = self.get_object()
        try:
            services.acknowledge_warning(warning, request.user, request=request)
        except PermissionDenied as error:
            return Response(
                _error(error, code="permission_denied"), status=status.HTTP_403_FORBIDDEN
            )
        except DjangoValidationError as error:
            return Response(
                _error("; ".join(error.messages)), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(self.get_serializer(warning).data)

    @extend_schema(request=None, responses=WeatherWarningSerializer)
    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        """Reject a warning. Requires ``safety.approve``."""
        warning = self.get_object()
        try:
            services.dismiss_warning(warning, request.user, request=request)
        except PermissionDenied as error:
            return Response(
                _error(error, code="permission_denied"), status=status.HTTP_403_FORBIDDEN
            )
        except DjangoValidationError as error:
            return Response(
                _error("; ".join(error.messages)), status=status.HTTP_400_BAD_REQUEST
            )
        return Response(self.get_serializer(warning).data)

    @staticmethod
    def _spot_param(request):
        raw = request.query_params.get("spot", "")
        if not raw.isdigit():
            return None
        from apps.locations.models import SurfSpot

        return SurfSpot.objects.filter(pk=int(raw)).first()


# ---------------------------------------------------------------------------
# Student restrictions
# ---------------------------------------------------------------------------
class StudentRestrictionSerializer(serializers.ModelSerializer):
    restriction_type_label = serializers.CharField(
        source="get_restriction_type_display", read_only=True
    )
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    issued_by_name = serializers.SerializerMethodField()
    is_current = serializers.BooleanField(read_only=True)
    limit_summary = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = StudentRestriction
        fields = [
            "id",
            "public_id",
            "student",
            "student_name",
            "restriction_type",
            "restriction_type_label",
            "description",
            "max_wave_height_m",
            "max_wind_kmh",
            "requires_supervision",
            "cannot_surf",
            "starts_on",
            "ends_on",
            "issued_by",
            "issued_by_name",
            "is_active",
            "is_current",
            "limit_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "created_at", "updated_at"]

    def get_issued_by_name(self, obj) -> str:
        return obj.issued_by.get_display_name() if obj.issued_by_id else ""

    def validate(self, attrs):
        instance = self.instance or StudentRestriction()
        for name, value in attrs.items():
            setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class StudentRestrictionViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Limits that travel with one student."""

    capability_prefix = "safety"
    queryset = selectors.restriction_queryset()
    serializer_class = StudentRestrictionSerializer
    filterset_fields = ["student", "restriction_type", "is_active", "cannot_surf"]
    search_fields = ["description", "student__student_code"]
    ordering_fields = ["starts_on", "ends_on"]
    ordering = ["-starts_on"]

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            issued_by=serializer.validated_data.get("issued_by") or self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(responses=StudentRestrictionSerializer(many=True))
    @action(detail=False, methods=["get"])
    def current(self, request):
        """Restrictions in force today."""
        return Response(self.get_serializer(selectors.current_restrictions(), many=True).data)


# ---------------------------------------------------------------------------
# Read-only safety gates other modules call before they commit anything
# ---------------------------------------------------------------------------
class SafetyGateViewSet(CapabilityViewSetMixin, viewsets.ViewSet):
    """The two questions booking and scheduling ask before they commit.

    Both answers are computed from records and the school's own thresholds. An
    unconfirmed AI suggestion never contributes to either.
    """

    capability_prefix = "safety"

    @extend_schema(description="Index of the available safety gates.")
    def list(self, request):
        return Response(
            {
                "gates": {
                    "spot": request.build_absolute_uri("spot/"),
                    "student": request.build_absolute_uri("student/"),
                },
                "checked_at": timezone.now(),
            }
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("spot", int, description="Surf spot id.", required=True),
            OpenApiParameter("level", str, description="A SurfLevel value."),
        ]
    )
    @action(detail=False, methods=["get"])
    def spot(self, request):
        """Is this spot usable for this level right now?"""
        raw = request.query_params.get("spot", "")
        if not raw.isdigit():
            return Response(
                _error(_("Provide ?spot= with a surf spot id.")),
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.locations.models import SurfSpot

        spot_obj = SurfSpot.objects.filter(pk=int(raw)).first()
        if spot_obj is None:
            return Response(
                _error(_("No such surf spot."), code="not_found"),
                status=status.HTTP_404_NOT_FOUND,
            )

        level = request.query_params.get("level", SurfLevel.BEGINNER)
        if level not in dict(SurfLevel.choices):
            level = SurfLevel.BEGINNER

        verdict = services.assess_spot(spot_obj, level)
        payload = verdict.as_dict()
        payload.update(
            {
                "spot": spot_obj.pk,
                "level": level,
                "checked_at": timezone.now(),
                "pending_ai_warnings": services.pending_ai_warnings(spot=spot_obj).count(),
            }
        )
        return Response(payload)

    @extend_schema(
        parameters=[
            OpenApiParameter("student", int, description="Student id.", required=True),
            OpenApiParameter("wave_height_m", float, description="Wave height in metres."),
            OpenApiParameter("wind_speed_kmh", float, description="Wind speed in km/h."),
        ]
    )
    @action(detail=False, methods=["get"])
    def student(self, request):
        """May this student enter the water in these conditions?"""
        raw = request.query_params.get("student", "")
        if not raw.isdigit():
            return Response(
                _error(_("Provide ?student= with a student id.")),
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.students.models import Student

        student_obj = Student.objects.filter(pk=int(raw)).select_related("customer").first()
        if student_obj is None:
            return Response(
                _error(_("No such student."), code="not_found"),
                status=status.HTTP_404_NOT_FOUND,
            )

        condition: dict = {}
        for key in ("wave_height_m", "wind_speed_kmh"):
            value = request.query_params.get(key)
            if value not in (None, ""):
                try:
                    condition[key] = float(value)
                except ValueError:
                    return Response(
                        _error(
                            _("%(field)s must be a number.") % {"field": key},
                            detail={key: value},
                        ),
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        verdict = services.evaluate_student(student_obj, condition or None)
        payload = verdict.as_dict()
        payload.update(
            {
                "student": student_obj.pk,
                "conditions_used": condition,
                "checked_at": timezone.now(),
            }
        )
        return Response(payload)


ROUTES = [
    ("safety-incidents", SafetyIncidentViewSet, "safetyincident"),
    ("lifeguard-assignments", LifeguardAssignmentViewSet, "lifeguardassignment"),
    ("emergency-contacts", EmergencyContactViewSet, "emergencycontact"),
    ("evacuation-plans", EvacuationPlanViewSet, "evacuationplan"),
    ("equipment-safety-checks", EquipmentSafetyCheckViewSet, "equipmentsafetycheck"),
    ("weather-warnings", WeatherWarningViewSet, "weatherwarning"),
    ("student-restrictions", StudentRestrictionViewSet, "studentrestriction"),
    ("safety-gates", SafetyGateViewSet, "safetygate"),
]
