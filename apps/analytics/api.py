"""REST API for analytics.

Two resources:

``/api/v1/metric-snapshots/``  the stored snapshot table (CRUD for the nightly
                              job and for corrections).
``/api/v1/analytics/``         the live dashboard figures, so a mobile client or
                              a TV in the staff room can render the same numbers
                              the web dashboard shows — computed by the same
                              service functions, never re-derived.
"""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.core.utils import parse_date_range

from . import services, statistics
from .models import MetricSnapshot


class MetricSnapshotSerializer(serializers.ModelSerializer):
    granularity_label = serializers.CharField(source="get_granularity_display", read_only=True)
    span_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = MetricSnapshot
        fields = [
            "id",
            "metric_key",
            "period_start",
            "period_end",
            "span_days",
            "granularity",
            "granularity_label",
            "value",
            "count",
            "dimensions",
            "computed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "span_days"]

    def validate(self, attrs):
        start = attrs.get("period_start") or getattr(self.instance, "period_start", None)
        end = attrs.get("period_end") or getattr(self.instance, "period_end", None)
        if start and end and end < start:
            raise serializers.ValidationError(
                {"period_end": "The period end cannot be before the period start."}
            )
        return attrs


class MetricSnapshotViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Stored metric values — the audit trail behind the charts."""

    capability_prefix = "analytics"
    queryset = MetricSnapshot.objects.all()
    serializer_class = MetricSnapshotSerializer
    filterset_fields = ["metric_key", "granularity", "period_start", "period_end"]
    search_fields = ["metric_key"]
    ordering_fields = ["period_start", "period_end", "value", "computed_at"]
    ordering = ["-period_start", "metric_key"]


def _serialise(value):
    """Make a metric dict JSON-safe (lazy translations, Decimals, nested lists)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)  # lazy translation proxies and dates


class AnalyticsViewSet(CapabilityViewSetMixin, viewsets.ViewSet):
    """Live dashboard figures for the requested period."""

    capability_prefix = "analytics"

    @extend_schema(
        parameters=[
            OpenApiParameter("range", str, description="today, 7, 30, 90, 180, 365 or custom"),
            OpenApiParameter("start", str, description="ISO date, used when range=custom"),
            OpenApiParameter("end", str, description="ISO date, used when range=custom"),
        ],
        responses={200: dict},
    )
    def list(self, request):
        """Every headline metric for the requested period."""
        start, end, label = parse_date_range(request)
        start, end = services.normalise_range(start, end)
        metrics = services.dashboard_metrics(start, end)
        return Response(
            {
                "period": {
                    "label": str(label),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "metrics": _serialise(metrics),
            }
        )

    @extend_schema(
        parameters=[OpenApiParameter("days", int, description="Forecast horizon, 1–365")],
        responses={200: dict},
    )
    @action(detail=False, methods=["get"])
    def forecast(self, request):
        """Revenue projection, always accompanied by its reliability verdict."""
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        return Response(_serialise(services.revenue_forecast(days=days)))

    @extend_schema(
        parameters=[
            OpenApiParameter("metric", str, description="Series key, e.g. revenue or bookings"),
            OpenApiParameter("range", str, description="today, 7, 30, 90, 180, 365 or custom"),
        ],
        responses={200: dict},
    )
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Descriptive statistics for one series over the requested period."""
        start, end, label = parse_date_range(request)
        start, end = services.normalise_range(start, end)
        metrics = services.dashboard_metrics(start, end)
        allowed = {key for key, _label in services.ANALYSABLE_METRICS}
        requested = request.query_params.get("metric", "revenue")
        metric_key = requested if requested in allowed else "revenue"
        return Response(
            {
                "period": {"label": str(label)},
                "summary": _serialise(services.statistical_summary(metrics, metric_key)),
                "available_metrics": [key for key, _label in services.ANALYSABLE_METRICS],
            }
        )

    @extend_schema(responses={200: dict})
    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        """What this engine can do — useful for clients building their own UI."""
        return Response(
            {
                "metrics": [
                    {"key": key, "label": str(label)}
                    for key, label in services.ANALYSABLE_METRICS
                ],
                "forecast_methods": ["linear", "mean", "naive"],
                "max_forecast_periods": statistics.MAX_FORECAST_PERIODS,
                "min_history_multiple": statistics.MIN_HISTORY_MULTIPLE,
            }
        )


ROUTES = [
    ("metric-snapshots", MetricSnapshotViewSet, "metricsnapshot"),
    ("analytics", AnalyticsViewSet, "analytics"),
]
