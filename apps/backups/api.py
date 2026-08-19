"""REST API for backups.

Deliberately incomplete: **there is no restore endpoint**. Overwriting the live
system is a decision that must be made by a person looking at a screen that
tells them what they are about to destroy, with the backup code typed by hand.
An API token cannot type. Restores happen at ``/backups/<pk>/restore/`` or
through ``manage.py restore``, and nowhere else.

Deleting is also excluded: the retention sweep handles routine cleanup and a
human handles the rest, through the HTML confirmation page.
"""

from __future__ import annotations

from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import selectors, services
from .models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreRecord,
)


class BackupRecordSerializer(serializers.ModelSerializer):
    backup_type_label = serializers.CharField(source="get_backup_type_display", read_only=True)
    scope_label = serializers.CharField(source="get_scope_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    size_display = serializers.CharField(read_only=True)
    exists_on_disk = serializers.BooleanField(read_only=True)
    age_days = serializers.IntegerField(read_only=True)
    is_restorable = serializers.BooleanField(read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=""
    )

    class Meta:
        model = BackupRecord
        fields = [
            "id",
            "public_id",
            "backup_code",
            "backup_type",
            "backup_type_label",
            "scope",
            "scope_label",
            "status",
            "status_label",
            "file_size_bytes",
            "size_display",
            "checksum_sha256",
            "database_engine",
            "django_version",
            "app_version",
            "started_at",
            "completed_at",
            "duration_ms",
            "error_message",
            "notes",
            "is_verified",
            "verified_at",
            "exists_on_disk",
            "age_days",
            "is_restorable",
            "created_at",
            "created_by_username",
        ]
        # file_path is intentionally absent: the server's directory layout is
        # not something an API client needs, or should learn.
        read_only_fields = fields


class RestoreRecordSerializer(serializers.ModelSerializer):
    backup_code = serializers.CharField(source="backup.backup_code", read_only=True)
    safety_backup_code = serializers.CharField(
        source="safety_backup.backup_code", read_only=True, default=""
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    confirmed_by_username = serializers.CharField(
        source="confirmed_by.username", read_only=True, default=""
    )

    class Meta:
        model = RestoreRecord
        fields = [
            "id",
            "public_id",
            "backup",
            "backup_code",
            "safety_backup",
            "safety_backup_code",
            "status",
            "status_label",
            "started_at",
            "completed_at",
            "duration_ms",
            "error_message",
            "confirmed_by_username",
            "pre_restore_checks",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class CreateBackupSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=BackupScope.choices, default=BackupScope.FULL)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)


class BackupRecordViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read backups, take a new one, verify an existing one."""

    capability_prefix = "backups"
    capability_overrides = {
        "run": "backups.add",
        "verify": "backups.view",
        "statistics": "backups.view",
    }
    queryset = BackupRecord.objects.select_related("created_by")
    serializer_class = BackupRecordSerializer
    filterset_fields = ["status", "backup_type", "scope", "is_verified"]
    search_fields = ["backup_code", "notes"]
    ordering_fields = ["created_at", "completed_at", "file_size_bytes"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return selectors.backup_queryset()

    @extend_schema(
        request=CreateBackupSerializer,
        responses={201: BackupRecordSerializer},
        examples=[OpenApiExample("Full backup", value={"scope": "full", "notes": "before upgrade"})],
    )
    @action(detail=False, methods=["post"], url_path="run")
    def run(self, request):
        """Take a backup now. Returns the record, successful or not."""
        serializer = CreateBackupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = services.create_backup(
            BackupType.MANUAL,
            serializer.validated_data["scope"],
            user=request.user,
            notes=serializer.validated_data.get("notes", ""),
            request=request,
        )
        if record.status == BackupStatus.COMPLETED:
            services.verify_backup(record, user=request.user, request=request)
            record.refresh_from_db()
        return Response(
            BackupRecordSerializer(record).data,
            status=status.HTTP_201_CREATED
            if record.status == BackupStatus.COMPLETED
            else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @extend_schema(request=None, responses={200: BackupRecordSerializer})
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """Recompute the checksum and re-check the artefact's integrity."""
        record = self.get_object()
        verified, message = services.verify_backup(record, user=request.user, request=request)
        record.refresh_from_db()
        return Response(
            {
                "verified": verified,
                "detail": message,
                "backup": BackupRecordSerializer(record).data,
            },
            status=status.HTTP_200_OK if verified else status.HTTP_409_CONFLICT,
        )

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"])
    def statistics(self, request):
        """Counts, storage use and the health verdict used by the dashboard."""
        data = services.backup_statistics()
        latest = data["last_successful"]
        oldest = data["oldest"]
        return Response(
            {
                "total": data["total"],
                "completed": data["completed"],
                "failed": data["failed"],
                "corrupt": data["corrupt"],
                "verified": data["verified"],
                "missing_files": data["missing_files"],
                "total_bytes": data["total_bytes"],
                "total_display": data["total_display"],
                "free_bytes": data["free_bytes"],
                "health": data["health"],
                "health_message": data["health_message"],
                "engine": data["engine"],
                "restores": data["restores"],
                "last_successful": BackupRecordSerializer(latest).data if latest else None,
                "oldest": BackupRecordSerializer(oldest).data if oldest else None,
                "retention": services.retention_policy(),
                "note": _("Restoring a backup is not available through the API."),
            }
        )


class RestoreRecordViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """The restore history. Read-only by design — see the module docstring."""

    capability_prefix = "backups"
    queryset = RestoreRecord.objects.select_related("backup", "safety_backup", "confirmed_by")
    serializer_class = RestoreRecordSerializer
    filterset_fields = ["status", "backup"]
    search_fields = ["backup__backup_code", "notes"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return selectors.restore_queryset()


ROUTES = [
    ("backups", BackupRecordViewSet, "backup"),
    ("backup-restores", RestoreRecordViewSet, "backup-restore"),
]
