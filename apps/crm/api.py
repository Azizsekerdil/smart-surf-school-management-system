"""REST API for the CRM."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.utils import parse_date_range

from .models import Campaign, Interaction, Lead, Segment
from .selectors import customer_model, lead_funnel, validate_criteria
from .services import (
    DEFAULT_CHURN_DAYS,
    advance_lead_status,
    campaign_performance,
    complete_follow_up,
    convert_lead_to_customer,
    customer_retention_stats,
    resolve_segment,
    set_campaign_status,
)


def _plain(value):
    """Make a service result JSON-safe without losing decimal precision."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _error(message: str, kind: str = "validation_error") -> Response:
    return Response(
        {"error": {"type": kind, "message": message, "detail": {}}},
        status=status.HTTP_400_BAD_REQUEST,
    )


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class LeadSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    assigned_to_name = serializers.CharField(
        source="assigned_to.get_display_name", read_only=True, default=""
    )
    weighted_value = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Lead
        fields = [
            "id",
            "public_id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "source",
            "source_label",
            "interest",
            "status",
            "status_label",
            "assigned_to",
            "assigned_to_name",
            "expected_value",
            "probability",
            "weighted_value",
            "is_open",
            "next_action",
            "next_action_at",
            "converted_customer",
            "converted_at",
            "lost_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "converted_customer", "converted_at"]

    def _current(self, attrs, name, default=""):
        if name in attrs:
            return attrs[name]
        return getattr(self.instance, name, default)

    def validate(self, attrs):
        """Mirror the model rules that a partial payload could otherwise skip."""
        email = (self._current(attrs, "email") or "").strip()
        phone = (self._current(attrs, "phone") or "").strip()
        if not email and not phone:
            raise serializers.ValidationError(
                {"email": "Give at least an e-mail address or a phone number."}
            )

        new_status = self._current(attrs, "status", Lead.Status.NEW)
        if new_status == Lead.Status.WON and not getattr(
            self.instance, "converted_customer_id", None
        ):
            raise serializers.ValidationError(
                {"status": "Use the convert action to win a lead."}
            )
        if new_status == Lead.Status.LOST and not (
            self._current(attrs, "lost_reason") or ""
        ).strip():
            raise serializers.ValidationError(
                {"lost_reason": "Record why the lead was lost."}
            )
        return attrs


class InteractionSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    contact_display = serializers.CharField(read_only=True)
    handled_by_name = serializers.CharField(
        source="handled_by.get_display_name", read_only=True, default=""
    )

    class Meta:
        model = Interaction
        fields = [
            "id",
            "public_id",
            "kind",
            "kind_label",
            "direction",
            "subject",
            "body",
            "customer",
            "lead",
            "contact_display",
            "occurred_at",
            "duration_minutes",
            "handled_by",
            "handled_by_name",
            "follow_up_required",
            "follow_up_at",
            "sentiment",
            "created_at",
        ]
        read_only_fields = ["id", "public_id", "created_at"]

    def validate(self, attrs):
        if not attrs.get("customer") and not attrs.get("lead") and self.instance is None:
            raise serializers.ValidationError(
                "Attach the interaction to a customer or to a lead."
            )
        return attrs


class SegmentSerializer(serializers.ModelSerializer):
    rules = serializers.SerializerMethodField()
    issues = serializers.SerializerMethodField()

    class Meta:
        model = Segment
        fields = [
            "id",
            "public_id",
            "name",
            "description",
            "criteria",
            "rules",
            "issues",
            "is_dynamic",
            "cached_count",
            "last_calculated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "cached_count", "last_calculated_at"]

    def get_rules(self, obj) -> list[str]:
        return obj.describe_criteria()

    def get_issues(self, obj) -> list[str]:
        return obj.criteria_issues()

    def validate_criteria(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Criteria must be an object.")
        problems = validate_criteria(value)
        if problems:
            raise serializers.ValidationError(problems)
        return value


class CampaignSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    segment_name = serializers.CharField(
        source="target_segment.name", read_only=True, default=""
    )
    roi = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    conversion_rate = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    open_rate = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id",
            "public_id",
            "name",
            "code",
            "channel",
            "channel_label",
            "status",
            "status_label",
            "start_date",
            "end_date",
            "budget",
            "actual_spend",
            "target_segment",
            "segment_name",
            "message_subject",
            "message_body",
            "sent_count",
            "opened_count",
            "converted_count",
            "revenue_attributed",
            "roi",
            "conversion_rate",
            "open_rate",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id"]
        extra_kwargs = {"code": {"required": False}}

    def validate(self, attrs):
        start = attrs.get("start_date") or getattr(self.instance, "start_date", None)
        end = attrs.get("end_date") or getattr(self.instance, "end_date", None)
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "The end date cannot be before the start date."}
            )
        sent = attrs.get("sent_count", getattr(self.instance, "sent_count", 0))
        for field in ("opened_count", "converted_count"):
            value = attrs.get(field, getattr(self.instance, field, 0))
            if value > sent:
                raise serializers.ValidationError({field: "Cannot exceed the number sent."})
        return attrs


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class LeadViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Sales pipeline."""

    capability_prefix = "crm"
    capability_overrides = {
        "advance": "crm.change",
        "convert": "crm.change",
        "funnel": "crm.view",
    }
    queryset = Lead.objects.select_related("assigned_to", "converted_customer")
    serializer_class = LeadSerializer
    filterset_fields = ["status", "source", "assigned_to"]
    search_fields = ["first_name", "last_name", "email", "phone", "interest"]
    ordering_fields = ["created_at", "next_action_at", "expected_value", "probability"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        """Move a lead to another pipeline stage."""
        lead = self.get_object()
        try:
            advance_lead_status(
                lead,
                request.data.get("status", ""),
                user=request.user,
                lost_reason=request.data.get("lost_reason", ""),
            )
        except DjangoValidationError as exc:
            return _error(" ".join(exc.messages))
        return Response(self.get_serializer(lead).data)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """Convert a lead into a customer (or link an existing one)."""
        lead = self.get_object()
        customer = None
        customer_id = request.data.get("customer")
        if customer_id:
            model = customer_model()
            customer = model.objects.filter(pk=customer_id).first() if model else None
            if customer is None:
                return _error("Customer not found.", kind="not_found")
        try:
            created = convert_lead_to_customer(lead, user=request.user, customer=customer)
        except DjangoValidationError as exc:
            return _error(" ".join(exc.messages))
        return Response(
            {"lead": self.get_serializer(lead).data, "customer_id": created.pk}
        )

    @action(detail=False, methods=["get"])
    def funnel(self, request):
        """Lead counts and value per pipeline stage."""
        return Response(
            [
                {**row, "label": str(row["label"]), "value": str(row["value"])}
                for row in lead_funnel(self.filter_queryset(self.get_queryset()))
            ]
        )


class InteractionViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Contact history for leads and customers."""

    capability_prefix = "crm"
    capability_overrides = {"complete_follow_up": "crm.change"}
    queryset = Interaction.objects.select_related("lead", "customer", "handled_by")
    serializer_class = InteractionSerializer
    filterset_fields = ["kind", "direction", "sentiment", "follow_up_required", "lead", "customer"]
    search_fields = ["subject", "body"]
    ordering_fields = ["occurred_at", "created_at"]
    ordering = ["-occurred_at"]

    def perform_create(self, serializer):
        serializer.save(
            handled_by=serializer.validated_data.get("handled_by") or self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="complete-follow-up")
    def complete_follow_up(self, request, pk=None):
        interaction = complete_follow_up(self.get_object(), user=request.user)
        return Response(self.get_serializer(interaction).data)


class SegmentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Saved customer audiences."""

    capability_prefix = "crm"
    capability_overrides = {"refresh": "crm.change", "members": "crm.view"}
    queryset = Segment.objects.all()
    serializer_class = SegmentSerializer
    filterset_fields = ["is_dynamic"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "cached_count", "last_calculated_at"]
    ordering = ["name"]

    def perform_create(self, serializer):
        segment = serializer.save(created_by=self.request.user, updated_by=self.request.user)
        resolve_segment(segment)

    def perform_update(self, serializer):
        segment = serializer.save(updated_by=self.request.user)
        resolve_segment(segment)

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        """Recalculate the cached audience size."""
        segment = self.get_object()
        resolve_segment(segment)
        return Response(self.get_serializer(segment).data)

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        """Identifiers and display names of the matching customers."""
        segment = self.get_object()
        queryset = resolve_segment(segment)
        page = queryset[:200]
        return Response(
            {
                "count": segment.cached_count,
                "truncated": segment.cached_count > 200,
                "results": [{"id": obj.pk, "label": str(obj)} for obj in page],
            }
        )


class CampaignViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Marketing campaigns and their measured results."""

    capability_prefix = "crm"
    capability_overrides = {"performance": "crm.view", "set_status": "crm.change"}
    queryset = Campaign.objects.select_related("target_segment")
    serializer_class = CampaignSerializer
    filterset_fields = ["status", "channel", "target_segment"]
    search_fields = ["name", "code", "message_subject"]
    ordering_fields = ["start_date", "end_date", "budget", "revenue_attributed"]
    ordering = ["-start_date"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=["get"])
    def performance(self, request, pk=None):
        """Reach, rates, spend, revenue and ROI for one campaign."""
        data = campaign_performance(self.get_object())
        data.pop("campaign", None)
        return Response({key: _plain(value) for key, value in data.items()})

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        campaign = self.get_object()
        try:
            set_campaign_status(campaign, request.data.get("status", ""), user=request.user)
        except DjangoValidationError as exc:
            return _error(" ".join(exc.messages))
        return Response(self.get_serializer(campaign).data)


class RetentionViewSet(CapabilityViewSetMixin, viewsets.ViewSet):
    """Read-only retention statistics for the CRM and analytics screens."""

    capability_prefix = "crm"

    def list(self, request):
        start, end, label = parse_date_range(request)
        churn_days = request.query_params.get("churn_days", "")
        stats = customer_retention_stats(
            start,
            end,
            churn_days=int(churn_days) if churn_days.isdigit() else DEFAULT_CHURN_DAYS,
        )
        stats.pop("churn_queryset", None)
        stats["range_label"] = label
        return Response({key: _plain(value) for key, value in stats.items()})


ROUTES = [
    ("crm/leads", LeadViewSet, "crm-lead"),
    ("crm/interactions", InteractionViewSet, "crm-interaction"),
    ("crm/segments", SegmentViewSet, "crm-segment"),
    ("crm/campaigns", CampaignViewSet, "crm-campaign"),
    ("crm/retention", RetentionViewSet, "crm-retention"),
]
