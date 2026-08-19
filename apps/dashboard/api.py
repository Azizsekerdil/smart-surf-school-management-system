"""REST endpoints for the dashboard.

The dashboard owns no models, so this is a read-only, capability-scoped
projection of the same context the HTML screen renders. Two consumers need it:
the beach tablet, which polls the tiles, and the AI assistant, which is asked
"how does today look?" and must answer from the system of record rather than
from a model's memory.

Both endpoints run through :class:`CapabilityViewSetMixin`, so the API can
never return a number the HTML screen would have hidden.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import SHARED

from . import selectors, services


class TileSerializer(serializers.Serializer):
    """One stat tile. ``value`` is ``null`` when the school has no source yet."""

    key = serializers.CharField()
    label = serializers.CharField()
    icon = serializers.CharField()
    # Deliberately untyped: a count is an integer, revenue a Decimal, and an
    # absent source is null. Coercing them to one type would hide that.
    value = serializers.JSONField(allow_null=True)
    kind = serializers.ChoiceField(choices=["count", "money", "score", "ratio"])
    detail = serializers.CharField(allow_blank=True)
    tone = serializers.ChoiceField(choices=["default", "ok", "warning", "danger"])
    url = serializers.CharField(allow_null=True)
    note = serializers.CharField(allow_blank=True)


class LevelScoreSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()
    score = serializers.IntegerField(allow_null=True)
    safe = serializers.BooleanField()
    reason = serializers.CharField(allow_blank=True)
    source = serializers.ChoiceField(choices=["module", "computed", "none"])


class SurfPanelSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    observed_at = serializers.DateTimeField(allow_null=True, required=False)
    wave_height_m = serializers.FloatField(allow_null=True, required=False)
    wind_speed_kmh = serializers.FloatField(allow_null=True, required=False)
    water_temp_c = serializers.FloatField(allow_null=True, required=False)
    wetsuit = serializers.CharField(required=False)
    levels = LevelScoreSerializer(many=True, required=False)


class DashboardSummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    variant = serializers.CharField()
    tiles = TileSerializer(many=True)
    surf = SurfPanelSerializer(allow_null=True)


class SearchRowSerializer(serializers.Serializer):
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True, allow_null=True)
    meta = serializers.CharField(allow_blank=True, allow_null=True)
    url = serializers.CharField(allow_null=True)


class SearchGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    icon = serializers.CharField()
    rows = SearchRowSerializer(many=True)


class SearchResultSerializer(serializers.Serializer):
    term = serializers.CharField()
    total = serializers.IntegerField()
    direct_hit_url = serializers.CharField(allow_null=True)
    groups = SearchGroupSerializer(many=True)


class DashboardViewSet(CapabilityViewSetMixin, viewsets.ViewSet):
    """Read-only projection of the operations dashboard."""

    capability_prefix = "dashboard"
    # No queryset to narrow: ``list`` below builds a different payload for an
    # external account (``build_customer_dashboard``, which reads only that
    # person's records) and ``search`` is filtered per capability inside
    # ``services.global_search``. Declared so the structural test in
    # apps/accounts/tests/test_object_scoping.py can see a decision was made.
    external_access = SHARED

    @extend_schema(
        responses=DashboardSummarySerializer,
        description="Today's stat tiles for the calling user, already filtered by capability.",
    )
    def list(self, request):
        today = timezone.localdate()
        user = request.user

        if getattr(user, "is_external", False):
            context = services.build_customer_dashboard(user, today)
            tiles = context["tiles"]
            variant = context["dashboard_variant"]
            surf = context.get("surf")
        else:
            shared = services.shared_reads(user, today)
            surf = shared.get("surf")
            tiles = services.build_tiles(user, today, shared=shared)
            variant = "instructor" if selectors.instructor_for_user(user) else "staff"

        payload = {"date": today, "variant": variant, "tiles": tiles, "surf": surf}
        return Response(DashboardSummarySerializer(payload).data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="q",
                description=(
                    "Search term. At least "
                    f"{selectors.MIN_SEARCH_LENGTH} characters; each group returns at most "
                    f"{selectors.SEARCH_GROUP_LIMIT} rows."
                ),
                required=True,
                type=str,
            )
        ],
        responses=SearchResultSerializer,
        description="Global search across every module the caller may view.",
    )
    @action(detail=False, methods=["get"])
    def search(self, request):
        term = (request.query_params.get("q") or "").strip()
        results = services.global_search(request.user, term)
        results["direct_hit_url"] = selectors.direct_hit_url(request.user, term)
        return Response(SearchResultSerializer(results).data)


ROUTES = [
    ("dashboard", DashboardViewSet, "dashboard"),
]
