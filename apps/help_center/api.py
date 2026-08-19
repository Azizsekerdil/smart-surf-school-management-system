"""REST API for Help Center content.

The API exists so the manual is reachable from anywhere the UI is not — a
kiosk at the counter, the AI assistant looking up a procedure before answering,
or a future mobile client. Bodies are exposed both as Markdown source and as
already-sanitised HTML, so no client has to re-implement the sanitiser.
"""

from __future__ import annotations

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import SHARED, OwnerScopedQuerySetMixin

from . import selectors, services
from .models import HelpArticle, HelpCategory


class HelpCategorySerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    article_count = serializers.SerializerMethodField()

    class Meta:
        model = HelpCategory
        fields = [
            "id",
            "code",
            "name",
            "name_en",
            "name_tr",
            "icon",
            "sort_order",
            "is_active",
            "article_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_article_count(self, obj) -> int:
        annotated = getattr(obj, "article_count", None)
        if annotated is not None:
            return annotated
        return obj.articles.filter(is_published=True).count()


class HelpArticleListSerializer(serializers.ModelSerializer):
    """Compact representation — no bodies, so a list response stays small."""

    title = serializers.CharField(read_only=True)
    excerpt = serializers.CharField(read_only=True)
    category_code = serializers.CharField(source="category.code", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    reading_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = HelpArticle
        fields = [
            "id",
            "public_id",
            "slug",
            "title",
            "excerpt",
            "category",
            "category_code",
            "category_name",
            "related_module",
            "keywords",
            "sort_order",
            "is_published",
            "view_count",
            "helpful_count",
            "not_helpful_count",
            "reading_minutes",
            "updated_at",
        ]
        read_only_fields = fields


class HelpArticleSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    body = serializers.CharField(read_only=True)
    body_html = serializers.SerializerMethodField()
    table_of_contents = serializers.SerializerMethodField()
    category_code = serializers.CharField(source="category.code", read_only=True)
    reading_minutes = serializers.IntegerField(read_only=True)
    helpful_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = HelpArticle
        fields = [
            "id",
            "public_id",
            "category",
            "category_code",
            "slug",
            "title",
            "title_en",
            "title_tr",
            "body",
            "body_en",
            "body_tr",
            "body_html",
            "table_of_contents",
            "keywords",
            "related_module",
            "sort_order",
            "is_published",
            "view_count",
            "helpful_count",
            "not_helpful_count",
            "helpful_percent",
            "reading_minutes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "slug",
            "view_count",
            "helpful_count",
            "not_helpful_count",
            "created_at",
            "updated_at",
        ]

    def get_body_html(self, obj) -> str:
        """Sanitised HTML for the active language — never the raw source."""
        return str(obj.rendered_body())

    def get_table_of_contents(self, obj) -> list:
        return obj.table_of_contents()


class HelpCategoryViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Sections of the manual."""

    capability_prefix = "help_center"
    # Published guidance, identical for every reader.
    external_access = SHARED
    queryset = HelpCategory.objects.all()
    serializer_class = HelpCategorySerializer
    filterset_fields = ["is_active"]
    search_fields = ["code", "name_en", "name_tr"]
    ordering_fields = ["sort_order", "code"]
    ordering = ["sort_order", "code"]
    lookup_field = "pk"

    def get_queryset(self):
        if self.action == "list":
            return selectors.categories_with_counts()
        return super().get_queryset()


class HelpArticleViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Articles, with a search endpoint and a helpfulness vote."""

    capability_prefix = "help_center"
    external_access = SHARED
    capability_overrides = {"feedback": "help_center.view"}
    queryset = HelpArticle.objects.select_related("category")
    serializer_class = HelpArticleSerializer
    filterset_fields = ["category", "is_published", "related_module"]
    search_fields = ["title_en", "title_tr", "keywords", "body_en", "body_tr"]
    ordering_fields = ["sort_order", "view_count", "updated_at"]
    ordering = ["category__sort_order", "sort_order"]
    lookup_field = "slug"

    def get_serializer_class(self):
        if self.action == "list":
            return HelpArticleListSerializer
        return HelpArticleSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Search term, matched in both languages."),
            OpenApiParameter("category", str, description="Restrict to one category code."),
        ],
        responses=HelpArticleListSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def search(self, request):
        """Ranked search across titles, keywords and both language bodies."""
        results = services.search_articles(
            request.query_params.get("q", ""),
            category_code=request.query_params.get("category", ""),
        )
        page = self.paginate_queryset(results)
        serializer = HelpArticleListSerializer(
            page if page is not None else results, many=True, context=self.get_serializer_context()
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(
        request=serializers.Serializer,
        responses={200: serializers.Serializer},
        description="Record whether the article answered the reader's question.",
    )
    @action(detail=True, methods=["post"])
    def feedback(self, request, slug: str | None = None):
        """Vote once per session; sending the same vote twice changes nothing."""
        article = self.get_object()
        raw = request.data.get("helpful")
        if isinstance(raw, str):
            helpful = raw.strip().lower() in {"1", "true", "yes", "helpful"}
        else:
            helpful = bool(raw)

        vote = services.record_feedback(article, request, helpful=helpful)
        return Response(
            {
                "slug": article.slug,
                "vote": vote,
                "helpful_count": article.helpful_count,
                "not_helpful_count": article.not_helpful_count,
                "helpful_percent": article.helpful_percent,
                "detail": _("Thank you — your feedback was recorded."),
            }
        )


ROUTES = [
    ("help-categories", HelpCategoryViewSet, "helpcategory"),
    ("help-articles", HelpArticleViewSet, "helparticle"),
]
