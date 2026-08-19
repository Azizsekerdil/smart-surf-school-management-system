from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    AIConversation,
    AIMessage,
    AIProviderConfig,
    AIUsageRecord,
    RagChunk,
    RagDocument,
)


@admin.register(AIProviderConfig)
class AIProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("provider", "is_enabled", "last_health_ok", "last_latency_ms", "last_health_at")
    list_filter = ("is_enabled", "last_health_ok")
    readonly_fields = ("last_health_ok", "last_health_message", "last_health_at", "last_latency_ms", "probed_models")
    fieldsets = (
        (None, {"fields": ("provider", "is_enabled")}),
        (_("Overrides"), {"fields": ("base_url_override", "model_overrides")}),
        (_("Budget"), {"fields": ("monthly_budget_usd",)}),
        (
            _("Last health check"),
            {"fields": ("last_health_ok", "last_health_message", "last_health_at", "last_latency_ms", "probed_models")},
        ),
    )


class AIMessageInline(admin.TabularInline):
    model = AIMessage
    extra = 0
    fields = ("role", "content", "provider", "model", "prompt_tokens", "completion_tokens", "latency_ms")
    readonly_fields = fields
    can_delete = False


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "kind", "total_tokens", "total_cost", "updated_at")
    list_filter = ("kind", "routing_mode", "created_at")
    search_fields = ("title", "user__username")
    inlines = [AIMessageInline]


@admin.register(AIUsageRecord)
class AIUsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "provider", "model", "operation", "total_tokens",
        "estimated_cost", "latency_ms", "was_successful",
    )
    list_filter = ("provider", "operation", "is_cloud", "was_successful", "created_at")
    search_fields = ("model", "user__username")
    date_hierarchy = "created_at"
    readonly_fields = tuple(f.name for f in AIUsageRecord._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False


class RagChunkInline(admin.TabularInline):
    model = RagChunk
    extra = 0
    fields = ("chunk_index", "content", "embedding_model", "embedding_dimensions")
    readonly_fields = fields
    can_delete = False


@admin.register(RagDocument)
class RagDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "source_type", "language", "is_indexed", "chunk_count", "updated_at")
    list_filter = ("source_type", "language", "is_indexed", "is_active")
    search_fields = ("title", "content")
    inlines = [RagChunkInline]
    readonly_fields = ("checksum", "is_indexed", "indexed_at", "chunk_count")
