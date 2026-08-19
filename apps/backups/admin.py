from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import BackupRecord, RestoreRecord


class RestoreInline(admin.TabularInline):
    model = RestoreRecord
    fk_name = "backup"
    extra = 0
    fields = ("status", "started_at", "completed_at", "confirmed_by", "safety_backup")
    readonly_fields = fields
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(BackupRecord)
class BackupRecordAdmin(admin.ModelAdmin):
    list_display = (
        "backup_code",
        "created_at",
        "backup_type",
        "scope",
        "status",
        "size_display",
        "is_verified",
        "on_disk",
    )
    list_filter = ("status", "backup_type", "scope", "is_verified", "is_deleted")
    search_fields = ("backup_code", "notes", "error_message", "checksum_sha256")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    inlines = [RestoreInline]
    readonly_fields = (
        "public_id",
        "backup_code",
        "file_path",
        "file_size_bytes",
        "checksum_sha256",
        "database_engine",
        "django_version",
        "app_version",
        "started_at",
        "completed_at",
        "duration_ms",
        "error_message",
        "verified_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("backup_code", "backup_type", "scope", "status", "notes")}),
        (
            _("Artefact"),
            {"fields": ("file_path", "file_size_bytes", "checksum_sha256", "is_verified", "verified_at")},
        ),
        (_("Provenance"), {"fields": ("database_engine", "django_version", "app_version")}),
        (_("Timing"), {"fields": ("started_at", "completed_at", "duration_ms", "error_message")}),
        (_("Record"), {"fields": ("public_id", "created_by", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        # Deleted backups must stay reachable: their history is the evidence.
        return BackupRecord.all_objects.select_related("created_by")

    @admin.display(description=_("size"))
    def size_display(self, obj: BackupRecord) -> str:
        return obj.size_display

    @admin.display(description=_("on disk"), boolean=True)
    def on_disk(self, obj: BackupRecord) -> bool:
        return obj.exists_on_disk


@admin.register(RestoreRecord)
class RestoreRecordAdmin(admin.ModelAdmin):
    list_display = (
        "backup",
        "status",
        "started_at",
        "completed_at",
        "confirmed_by",
        "safety_backup",
    )
    list_filter = ("status",)
    search_fields = ("backup__backup_code", "confirmation_text", "error_message", "notes")
    ordering = ("-created_at",)
    autocomplete_fields = ("backup", "safety_backup")
    readonly_fields = (
        "public_id",
        "started_at",
        "completed_at",
        "duration_ms",
        "error_message",
        "confirmation_text",
        "pre_restore_checks",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return RestoreRecord.all_objects.select_related(
            "backup", "safety_backup", "confirmed_by"
        )

    def has_add_permission(self, request) -> bool:
        # A restore is performed, never typed into a form.
        return False
