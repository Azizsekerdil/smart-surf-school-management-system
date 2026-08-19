"""REST API for the AI layer."""

from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import rag, services, tools
from .models import AIConversation, AIMessage, AIUsageRecord, RagDocument
from .providers.registry import health_report


class AIMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIMessage
        fields = [
            "id", "role", "content", "reasoning", "tool_name", "provider", "model",
            "prompt_tokens", "completion_tokens", "latency_ms", "used_fallback",
            "citations", "error", "created_at",
        ]


class AIConversationSerializer(serializers.ModelSerializer):
    messages = AIMessageSerializer(many=True, read_only=True)
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AIConversation
        fields = [
            "id", "title", "kind", "routing_mode", "is_pinned", "total_tokens",
            "total_cost", "message_count", "created_at", "updated_at", "messages",
        ]
        read_only_fields = ["total_tokens", "total_cost"]


class AIConversationViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Conversations are private: a user only ever sees their own."""

    capability_prefix = "ai"
    capability_overrides = {"ask": "ai.view", "tools": "ai.view", "health": "ai.view"}
    serializer_class = AIConversationSerializer
    filterset_fields = ["kind", "is_pinned"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        return AIConversation.objects.filter(user=self.request.user).prefetch_related("messages")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def ask(self, request, pk=None):
        """Send a message to the assistant and return its reply."""
        conversation = self.get_object()
        text = (request.data.get("message") or "").strip()
        if not text:
            return Response(
                {"error": {"type": "validation_error", "message": "message is required", "detail": {}}},
                status=400,
            )
        answer = services.run_assistant(
            request.user,
            conversation,
            text,
            use_rag=bool(request.data.get("use_rag", True)),
            routing_mode=request.data.get("routing_mode"),
        )
        return Response(AIMessageSerializer(answer).data)

    @action(detail=False, methods=["get"])
    def tools(self, request):
        """Tools this user is permitted to trigger through the assistant."""
        return Response(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "capability": tool.capability,
                    "tags": tool.tags,
                }
                for tool in tools.tools_for_user(request.user)
            ]
        )

    @action(detail=False, methods=["get"])
    def health(self, request):
        return Response(
            {
                name: {"ok": result.ok, "message": result.message, "latency_ms": result.latency_ms}
                for name, result in health_report().items()
            }
        )


class AIUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIUsageRecord
        fields = [
            "id", "provider", "model", "role", "operation", "is_cloud",
            "prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost",
            "latency_ms", "was_successful", "used_fallback", "created_at",
        ]


class AIUsageViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    capability_prefix = "ai"
    queryset = AIUsageRecord.objects.select_related("user")
    serializer_class = AIUsageSerializer
    filterset_fields = ["provider", "operation", "is_cloud", "was_successful"]
    ordering = ["-created_at"]


class RagDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RagDocument
        fields = [
            "id", "title", "source_type", "language", "content", "source_url",
            "is_indexed", "indexed_at", "chunk_count", "is_active", "updated_at",
        ]
        read_only_fields = ["is_indexed", "indexed_at", "chunk_count"]


class RagDocumentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "ai"
    capability_overrides = {"search": "ai.view", "reindex": "ai.change"}
    queryset = RagDocument.objects.all()
    serializer_class = RagDocumentSerializer
    filterset_fields = ["source_type", "language", "is_indexed", "is_active"]
    search_fields = ["title", "content"]

    def perform_create(self, serializer):
        document = serializer.save(created_by=self.request.user)
        rag.index_document(document)

    def perform_update(self, serializer):
        document = serializer.save(updated_by=self.request.user)
        rag.index_document(document, force=True)

    @action(detail=False, methods=["get"])
    def search(self, request):
        hits = rag.search(request.query_params.get("q", ""), limit=8)
        return Response(
            [
                {
                    "document_id": hit.document_id,
                    "title": hit.document_title,
                    "score": hit.score,
                    "content": hit.content[:600],
                }
                for hit in hits
            ]
        )

    @action(detail=False, methods=["post"])
    def reindex(self, request):
        return Response(rag.reindex_all(force=True))


ROUTES = [
    ("ai/conversations", AIConversationViewSet, "ai-conversation"),
    ("ai/usage", AIUsageViewSet, "ai-usage"),
    ("ai/knowledge", RagDocumentViewSet, "ai-knowledge"),
]
