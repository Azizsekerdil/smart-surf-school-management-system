from __future__ import annotations

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: audit entries are append-only by design."""

    list_display = ("created_at", "username", "action", "object_repr", "source", "ip_address")
    list_filter = ("action", "source", "is_sensitive", "created_at")
    search_fields = ("username", "description", "object_repr", "request_path", "request_id")
    date_hierarchy = "created_at"
    readonly_fields = tuple(f.name for f in AuditLog._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Only a Super Admin may prune the audit log, and the deletion itself is logged.
        return bool(getattr(request.user, "is_super_admin", False))
