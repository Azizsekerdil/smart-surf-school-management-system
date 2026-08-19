"""REST API for the report catalogue, saved configurations and the archive.

The catalogue endpoint exists so an external client (or the AI assistant) can
discover which reports it may run without hard-coding the list, and the ``run``
action returns the archive record rather than the bytes: the file is fetched
from ``download_url``, which goes through the same capability check as the HTML
screen.
"""

from __future__ import annotations

from django.urls import reverse
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import selectors, services
from .exporters.registry import available_formats
from .models import GeneratedReport, ReportDefinition, ReportFormat
from .reports import AREA_LABELS, get_report, reports_for_user


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class ReportCatalogueSerializer(serializers.Serializer):
    """One entry of the report catalogue (read-only, not model-backed)."""

    key = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    area = serializers.CharField()
    area_label = serializers.CharField()
    capability = serializers.CharField()
    icon = serializers.CharField()
    filter_fields = serializers.ListField(child=serializers.CharField())
    default_format = serializers.CharField()


class ReportDefinitionSerializer(serializers.ModelSerializer):
    report_title = serializers.SerializerMethodField()
    format_label = serializers.CharField(source="get_default_format_display", read_only=True)
    last_run_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ReportDefinition
        fields = [
            "id",
            "public_id",
            "name",
            "code",
            "report_key",
            "report_title",
            "description",
            "default_format",
            "format_label",
            "default_filters",
            "required_capability",
            "is_scheduled",
            "schedule_cron",
            "recipients",
            "is_active",
            "last_run_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "public_id", "last_run_at", "created_at", "updated_at"]

    def get_report_title(self, obj: ReportDefinition) -> str:
        spec = get_report(obj.report_key)
        return str(spec.title) if spec else ""

    def validate_report_key(self, value: str) -> str:
        if get_report(value) is None:
            raise serializers.ValidationError("Unknown report key.")
        return value

    def validate_recipients(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Recipients must be a list of e-mail addresses.")
        return [str(item).strip() for item in value if str(item).strip()]

    def validate_schedule_cron(self, value: str) -> str:
        from .cron import CronError, parse_cron  # noqa: PLC0415 - keeps import graph flat

        if not value:
            return ""
        try:
            parse_cron(value)
        except CronError as error:
            raise serializers.ValidationError(str(error)) from error
        return value

    def validate(self, attrs):
        scheduled = attrs.get("is_scheduled", getattr(self.instance, "is_scheduled", False))
        cron = attrs.get("schedule_cron", getattr(self.instance, "schedule_cron", ""))
        recipients = attrs.get("recipients", getattr(self.instance, "recipients", []) or [])
        if scheduled and not cron:
            raise serializers.ValidationError(
                {"schedule_cron": "A scheduled report needs a schedule expression."}
            )
        if scheduled and not recipients:
            raise serializers.ValidationError(
                {"recipients": "A scheduled report needs at least one recipient."}
            )
        return attrs


class GeneratedReportSerializer(serializers.ModelSerializer):
    report_title = serializers.CharField(source="title", read_only=True)
    format_label = serializers.CharField(source="get_format_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    generated_by_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedReport
        fields = [
            "id",
            "public_id",
            "definition",
            "report_key",
            "report_title",
            "format",
            "format_label",
            "filters_used",
            "file_size_bytes",
            "row_count",
            "generation_ms",
            "status",
            "status_label",
            "error_message",
            "generated_by",
            "generated_by_name",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_generated_by_name(self, obj: GeneratedReport) -> str:
        return obj.generated_by.get_full_name() if obj.generated_by else ""

    def get_download_url(self, obj: GeneratedReport) -> str | None:
        if not obj.is_downloadable:
            return None
        url = reverse("reporting:download", kwargs={"pk": obj.pk})
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class RunReportSerializer(serializers.Serializer):
    """Payload for running a catalogue report directly."""

    report_key = serializers.CharField()
    format = serializers.ChoiceField(
        choices=[key for key, _label in available_formats()], default=ReportFormat.PDF
    )
    filters = serializers.DictField(required=False, default=dict)

    def validate_report_key(self, value: str) -> str:
        if get_report(value) is None:
            raise serializers.ValidationError("Unknown report key.")
        return value


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class ReportDefinitionViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Saved report configurations."""

    capability_prefix = "reporting"
    capability_overrides = {
        "run": "reporting.export",
        "catalogue": "reporting.view",
    }
    queryset = ReportDefinition.objects.select_related("created_by")
    serializer_class = ReportDefinitionSerializer
    filterset_fields = ["report_key", "default_format", "is_scheduled", "is_active"]
    search_fields = ["name", "code", "report_key", "description"]
    ordering = ["name"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @extend_schema(
        responses=ReportCatalogueSerializer(many=True),
        description="Reports the authenticated user is allowed to run.",
    )
    @action(detail=False, methods=["get"])
    def catalogue(self, request):
        payload = [
            {
                "key": spec.key,
                "title": str(spec.title),
                "description": str(spec.description),
                "area": spec.area,
                "area_label": str(AREA_LABELS.get(spec.area, spec.area)),
                "capability": spec.capability,
                "icon": spec.icon,
                "filter_fields": list(spec.filter_fields),
                "default_format": spec.default_format,
            }
            for spec in reports_for_user(request.user)
        ]
        return Response(ReportCatalogueSerializer(payload, many=True).data)

    @extend_schema(
        request=None,
        responses=GeneratedReportSerializer,
        description="Run this saved configuration and return the archive record.",
        examples=[OpenApiExample("Run", value={})],
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        definition = self.get_object()
        generated = services.generate_report(
            definition.report_key,
            definition.default_format,
            definition.filter_dict,
            user=request.user,
            definition=definition,
            request=request,
        )
        serializer = GeneratedReportSerializer(generated, context={"request": request})
        code = status.HTTP_201_CREATED if generated.is_downloadable else status.HTTP_400_BAD_REQUEST
        return Response(serializer.data, status=code)


class GeneratedReportViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """The export archive. Files are fetched from ``download_url``."""

    capability_prefix = "reporting"
    capability_overrides = {"run": "reporting.export"}
    queryset = GeneratedReport.objects.select_related("definition", "generated_by")
    serializer_class = GeneratedReportSerializer
    filterset_fields = ["report_key", "format", "status", "definition"]
    search_fields = ["title", "report_key"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return selectors.generated_reports()

    @extend_schema(
        request=RunReportSerializer,
        responses=GeneratedReportSerializer,
        description="Run a catalogue report with ad-hoc filters.",
    )
    @action(detail=False, methods=["post"])
    def run(self, request):
        payload = RunReportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        generated = services.generate_report(
            payload.validated_data["report_key"],
            payload.validated_data["format"],
            payload.validated_data.get("filters") or {},
            user=request.user,
            request=request,
        )
        serializer = GeneratedReportSerializer(generated, context={"request": request})
        code = status.HTTP_201_CREATED if generated.is_downloadable else status.HTTP_400_BAD_REQUEST
        return Response(serializer.data, status=code)


ROUTES = [
    ("report-definitions", ReportDefinitionViewSet, "reportdefinition"),
    ("generated-reports", GeneratedReportViewSet, "generatedreport"),
]
