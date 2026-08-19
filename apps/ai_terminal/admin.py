from __future__ import annotations

from django.contrib import admin

from .models import CodeChangeProposal, TerminalCommand, TerminalSession


class TerminalCommandInline(admin.TabularInline):
    model = TerminalCommand
    extra = 0
    fields = ("command", "risk", "status", "exit_code", "duration_ms", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(TerminalSession)
class TerminalSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "is_active", "command_count", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "goal", "user__username")
    inlines = [TerminalCommandInline]


@admin.register(TerminalCommand)
class TerminalCommandAdmin(admin.ModelAdmin):
    """Read-only: this is the evidence trail for what the AI asked to run."""

    list_display = ("created_at", "command", "risk", "status", "exit_code", "requested_by")
    list_filter = ("risk", "status", "origin", "created_at")
    search_fields = ("command", "policy_rule", "requested_by__username")
    readonly_fields = tuple(f.name for f in TerminalCommand._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(CodeChangeProposal)
class CodeChangeProposalAdmin(admin.ModelAdmin):
    list_display = ("file_path", "change_type", "status", "approved_by", "created_at")
    list_filter = ("status", "change_type", "created_at")
    search_fields = ("file_path", "title", "summary")
    readonly_fields = ("unified_diff", "original_content", "applied_at", "checkpoint_branch")
