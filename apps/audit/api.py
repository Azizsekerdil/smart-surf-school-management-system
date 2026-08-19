"""Read-only REST API for the audit log."""

from __future__ import annotations

from rest_framework import serializers, viewsets

from apps.accounts.permissions import CapabilityViewSetMixin

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    model_label = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "username",
            "user_role",
            "action",
            "action_label",
            "description",
            "changes",
            "object_repr",
            "object_id",
            "model_label",
            "source",
            "source_label",
            "ip_address",
            "request_path",
            "request_id",
            "is_sensitive",
        ]

    def get_model_label(self, obj) -> str:
        return f"{obj.content_type.app_label}.{obj.content_type.model}" if obj.content_type else ""


class AuditLogViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Audit entries are immutable — only list and retrieve are exposed."""

    capability_prefix = "audit"
    queryset = AuditLog.objects.select_related("user", "content_type")
    serializer_class = AuditLogSerializer
    filterset_fields = ["action", "source", "username", "is_sensitive"]
    search_fields = ["username", "description", "object_repr", "request_path"]
    ordering_fields = ["created_at", "action"]
    ordering = ["-created_at"]


ROUTES = [
    ("audit", AuditLogViewSet, "audit-log"),
]
