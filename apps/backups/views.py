"""HTML screens for backup and restore.

The restore flow is deliberately the slowest path in the product. It is a
separate page, it lists what will be destroyed, it refuses to enable its button
until the operator has typed the backup code, and it still re-checks everything
server-side afterwards — the client-side gate is a courtesy, never the control.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.mixins import HtmxPartialMixin, SearchableListMixin

from . import selectors, services
from .forms import (
    BackupCreateForm,
    BackupFilterForm,
    RestoreConfirmationForm,
    RetentionPolicyForm,
)
from .models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreRecord,
    RestoreStatus,
    human_size,
)

#: Badge palettes. The shared ``status_badge`` tag only knows the vocabulary
#: common to every module, so backup-specific states get their colours here.
BACKUP_STATUS_COLORS = {
    BackupStatus.PENDING: "slate",
    BackupStatus.RUNNING: "sky",
    BackupStatus.COMPLETED: "emerald",
    BackupStatus.FAILED: "rose",
    BackupStatus.CORRUPT: "rose",
}

RESTORE_STATUS_COLORS = {
    RestoreStatus.PENDING: "slate",
    RestoreStatus.VERIFYING: "sky",
    RestoreStatus.RUNNING: "amber",
    RestoreStatus.COMPLETED: "emerald",
    RestoreStatus.FAILED: "rose",
    RestoreStatus.ROLLED_BACK: "violet",
}

HEALTH_COLORS = {
    services.HEALTH_HEALTHY: "emerald",
    services.HEALTH_WARNING: "amber",
    services.HEALTH_CRITICAL: "rose",
}


class BadgePaletteMixin:
    """Puts the badge palettes into the template context."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_colors"] = BACKUP_STATUS_COLORS
        context["restore_status_colors"] = RESTORE_STATUS_COLORS
        context["health_colors"] = HEALTH_COLORS
        return context


class BackupListView(
    CapabilityRequiredMixin,
    BadgePaletteMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    """Every backup on record, with the storage picture above it."""

    capability = "backups.view"
    model = BackupRecord
    template_name = "backups/backuprecord_list.html"
    partial_template_name = "backups/partials/backup_table.html"
    context_object_name = "backups"
    paginate_by = 25
    search_fields = ("backup_code", "notes", "error_message")

    def get_queryset(self):
        queryset = self.apply_search(selectors.backup_queryset())

        backup_type = self.request.GET.get("backup_type", "")
        if backup_type in BackupType.values:
            queryset = queryset.filter(backup_type=backup_type)

        scope = self.request.GET.get("scope", "")
        if scope in BackupScope.values:
            queryset = queryset.filter(scope=scope)

        status = self.request.GET.get("status", "")
        if status in BackupStatus.values:
            queryset = queryset.filter(status=status)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        statistics = services.backup_statistics()
        context["stats"] = statistics
        context["storage_rows"] = selectors.storage_by_scope(statistics)
        context["chart_labels"] = [row["label"] for row in context["storage_rows"]]
        context["chart_values"] = [row["bytes"] for row in context["storage_rows"]]
        context["create_form"] = BackupCreateForm()
        context["filter_form"] = BackupFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "backup_type": self.request.GET.get("backup_type", ""),
                "scope": self.request.GET.get("scope", ""),
                "status": self.request.GET.get("status", ""),
            }
        )
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "backup_type", "scope", "status")
        )
        context["retention"] = services.retention_policy()
        return context


class BackupCreateView(CapabilityRequiredMixin, View):
    """POST-only: take a backup now, with the scope the operator picked."""

    capability = "backups.add"

    def post(self, request, *args, **kwargs):
        form = BackupCreateForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Choose what to back up before starting."))
            return redirect("backups:list")

        record = services.create_backup(
            BackupType.MANUAL,
            form.cleaned_data["scope"],
            user=request.user,
            notes=form.cleaned_data.get("notes", ""),
            request=request,
        )
        if record.status == BackupStatus.COMPLETED:
            services.verify_backup(record, user=request.user, request=request)
            messages.success(
                request,
                _("Backup %(code)s completed — %(size)s in %(duration)s.")
                % {
                    "code": record.backup_code,
                    "size": record.size_display,
                    "duration": record.duration_display,
                },
            )
        else:
            messages.error(
                request,
                _("Backup %(code)s failed: %(error)s")
                % {"code": record.backup_code, "error": record.error_message},
            )
        return redirect("backups:detail", pk=record.pk)


class BackupDetailView(CapabilityRequiredMixin, BadgePaletteMixin, DetailView):
    capability = "backups.view"
    model = BackupRecord
    template_name = "backups/backuprecord_detail.html"
    context_object_name = "backup"

    def get_queryset(self):
        return selectors.backup_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        backup: BackupRecord = context["backup"]
        context["restores"] = selectors.restores_for_backup(backup)
        context["can_be_deleted"], context["delete_block_reason"] = services.can_delete_backup(
            backup
        )
        context["current_engine"] = services.database_engine()
        context["size_on_disk_display"] = human_size(backup.size_on_disk)
        return context


class BackupVerifyView(CapabilityRequiredMixin, View):
    """POST-only: re-prove that the artefact on disk is intact.

    Verification only reads the file; the stored flag is a cached result of that
    read, so anyone who may look at backups may run it.
    """

    capability = "backups.view"

    def post(self, request, pk: int, *args, **kwargs):
        backup = get_object_or_404(BackupRecord, pk=pk)
        verified, message = services.verify_backup(backup, user=request.user, request=request)
        if verified:
            messages.success(
                request,
                _("%(code)s verified — %(detail)s")
                % {"code": backup.backup_code, "detail": message},
            )
        else:
            messages.error(
                request,
                _("%(code)s did NOT verify — %(detail)s")
                % {"code": backup.backup_code, "detail": message},
            )
        return redirect("backups:detail", pk=backup.pk)


class BackupDownloadView(CapabilityRequiredMixin, View):
    """Stream a backup artefact off the server.

    A database dump is the whole school in one file, so this needs the export
    capability rather than plain view, and every download is audited.
    """

    capability = "backups.export"

    def get(self, request, pk: int, *args, **kwargs):
        backup = get_object_or_404(BackupRecord, pk=pk)
        path = backup.path
        if path is None or not backup.exists_on_disk:
            raise Http404(_("This backup has no file on disk."))

        # The stored path must still live under the configured backup volume:
        # a row that points anywhere else is not something to hand out.
        try:
            root = services.backup_root().resolve()
            resolved = path.resolve()
        except OSError as error:
            raise Http404(_("The backup file could not be opened.")) from error
        if root not in resolved.parents:
            raise PermissionDenied(_("This backup is stored outside the backup volume."))

        record_audit(
            request,
            action=AuditAction.EXPORT,
            instance=backup,
            description=_("Backup %(code)s downloaded (%(size)s)")
            % {"code": backup.backup_code, "size": backup.size_display},
        )
        return FileResponse(
            resolved.open("rb"),
            as_attachment=True,
            filename=resolved.name,
            content_type="application/octet-stream",
        )


class BackupDeleteView(CapabilityRequiredMixin, BadgePaletteMixin, DetailView):
    """Confirmation page, then a POST that removes the artefact."""

    capability = "backups.delete"
    model = BackupRecord
    template_name = "backups/backuprecord_confirm_delete.html"
    context_object_name = "backup"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allowed, reason = services.can_delete_backup(context["backup"])
        context["can_be_deleted"] = allowed
        context["delete_block_reason"] = reason
        return context

    def post(self, request, pk: int, *args, **kwargs):
        backup = self.get_object()
        try:
            freed = services.delete_backup(backup, request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return redirect("backups:detail", pk=backup.pk)
        messages.success(
            request,
            _("Backup %(code)s deleted — %(size)s reclaimed.")
            % {"code": backup.backup_code, "size": human_size(freed)},
        )
        return redirect("backups:list")


class BackupRestoreView(CapabilityRequiredMixin, BadgePaletteMixin, DetailView):
    """The high-friction restore screen.

    GET shows exactly what is about to be overwritten and how much work would be
    lost. POST re-runs every gate server-side through
    :func:`apps.backups.services.restore_backup`.
    """

    capability = "backups.restore"
    model = BackupRecord
    template_name = "backups/restore_confirm.html"
    context_object_name = "backup"

    def get_form(self, data=None) -> RestoreConfirmationForm:
        return RestoreConfirmationForm(data, backup=self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        backup: BackupRecord = context["backup"]
        context.setdefault("form", self.get_form())
        context["at_risk"] = selectors.records_at_risk(backup.completed_at)
        context["current_engine"] = services.database_engine()
        context["media_root"] = str(settings.MEDIA_ROOT)
        context["database_name"] = services.database_name()
        context["is_restorable"] = backup.is_restorable
        context["previous_restores"] = selectors.restores_for_backup(backup)[:5]
        return context

    def post(self, request, pk: int, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form(request.POST)
        if not form.is_valid():
            context = self.get_context_data(object=self.object, form=form)
            return self.render_to_response(context)

        restore = services.restore_backup(
            self.object,
            request.user,
            form.cleaned_data["confirmation_text"],
            request=request,
            notes=form.cleaned_data.get("notes", ""),
        )

        if restore.status == RestoreStatus.COMPLETED:
            messages.success(
                request,
                _(
                    "Backup %(code)s has been restored. A safety copy of the "
                    "previous state was saved as %(safety)s."
                )
                % {
                    "code": self.object.backup_code,
                    "safety": restore.safety_backup.backup_code
                    if restore.safety_backup
                    else "—",
                },
            )
        elif restore.status == RestoreStatus.ROLLED_BACK:
            messages.warning(
                request,
                _("The restore failed and the system was rolled back: %(error)s")
                % {"error": restore.error_message},
            )
        else:
            messages.error(
                request,
                _("The restore did not run: %(error)s") % {"error": restore.error_message},
            )
        return redirect("backups:detail", pk=self.object.pk)


class RestoreListView(CapabilityRequiredMixin, BadgePaletteMixin, SearchableListMixin, ListView):
    """Every restore ever attempted — the record nobody wants to need."""

    capability = "backups.view"
    model = RestoreRecord
    template_name = "backups/restorerecord_list.html"
    context_object_name = "restores"
    paginate_by = 25
    search_fields = ("backup__backup_code", "confirmation_text", "error_message", "notes")

    def get_queryset(self):
        return self.apply_search(selectors.restore_queryset())


class RetentionSettingsView(CapabilityRequiredMixin, BadgePaletteMixin, TemplateView):
    """How long scheduled backups are kept, and what the next sweep would remove."""

    capability = "backups.manage"
    template_name = "backups/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        policy = services.retention_policy()
        context.setdefault("form", RetentionPolicyForm(initial=policy))
        context["policy"] = policy
        context["preview"] = services.retention_preview()
        context["preview_bytes"] = sum(
            (row.file_size_bytes or 0) for row in context["preview"]
        )
        context["preview_display"] = human_size(context["preview_bytes"])
        context["stats"] = services.backup_statistics()
        return context

    def post(self, request, *args, **kwargs):
        form = RetentionPolicyForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        services.save_retention_policy(
            form.cleaned_data, user=request.user, request=request
        )
        messages.success(request, _("Retention policy saved."))
        return redirect("backups:settings")


class RetentionRunView(CapabilityRequiredMixin, View):
    """POST-only: apply the retention policy immediately."""

    capability = "backups.manage"

    def post(self, request, *args, **kwargs):
        summary = services.apply_retention_policy(user=request.user, request=request)
        if summary["errors"]:
            messages.warning(
                request,
                _("Retention sweep finished with problems: %(errors)s")
                % {"errors": "; ".join(summary["errors"][:3])},
            )
        elif summary["deleted"]:
            messages.success(
                request,
                _("Retention sweep removed %(count)s backup(s) and reclaimed %(size)s.")
                % {"count": summary["deleted"], "size": summary["freed_display"]},
            )
        else:
            messages.info(request, _("Nothing to remove — every backup is within policy."))
        return redirect("backups:settings")
