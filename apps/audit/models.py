"""Audit log model.

Every entry answers: **who** did **what**, to **which object**, **when**, from
**where**, and **what changed**. Entries are append-only — the admin and the API
both refuse updates, and only a Super Admin may prune old rows.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    CREATE = "create", _("Created")
    UPDATE = "update", _("Updated")
    DELETE = "delete", _("Deleted")
    VIEW = "view", _("Viewed")
    LOGIN = "login", _("Signed in")
    LOGOUT = "logout", _("Signed out")
    LOGIN_FAILED = "login_failed", _("Sign-in failed")
    PASSWORD_CHANGE = "password_change", _("Password changed")
    PERMISSION_CHANGE = "permission_change", _("Permissions changed")
    PAYMENT = "payment", _("Payment recorded")
    REFUND = "refund", _("Refund issued")
    BOOKING_CHANGE = "booking_change", _("Booking changed")
    BOOKING_CANCEL = "booking_cancel", _("Booking cancelled")
    RENTAL_OUT = "rental_out", _("Equipment checked out")
    RENTAL_RETURN = "rental_return", _("Equipment returned")
    BACKUP_CREATE = "backup_create", _("Backup created")
    BACKUP_RESTORE = "backup_restore", _("Backup restored")
    BACKUP_DELETE = "backup_delete", _("Backup deleted")
    EXPORT = "export", _("Data exported")
    AI_QUERY = "ai_query", _("AI query")
    AI_ACTION = "ai_action", _("AI-performed change")
    AI_COMMAND_PROPOSED = "ai_command_proposed", _("AI command proposed")
    AI_COMMAND_APPROVED = "ai_command_approved", _("AI command approved")
    AI_COMMAND_REJECTED = "ai_command_rejected", _("AI command rejected")
    AI_COMMAND_EXECUTED = "ai_command_executed", _("AI command executed")
    SAFETY_INCIDENT = "safety_incident", _("Safety incident recorded")
    SETTINGS_CHANGE = "settings_change", _("Settings changed")
    SYSTEM = "system", _("System event")


class AuditSource(models.TextChoices):
    WEB = "web", _("Web interface")
    API = "api", _("REST API")
    ADMIN = "admin", _("Django admin")
    AI = "ai", _("AI assistant")
    AI_TERMINAL = "ai_terminal", _("AI development terminal")
    SYSTEM = "system", _("Background task")
    CLI = "cli", _("Command line")


class AuditLog(models.Model):
    # --- who --------------------------------------------------------------
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    #: Kept even if the user row is later removed.
    username = models.CharField(_("username"), max_length=150, blank=True, db_index=True)
    user_role = models.CharField(_("role"), max_length=32, blank=True)

    # --- what -------------------------------------------------------------
    action = models.CharField(
        _("action"), max_length=32, choices=AuditAction.choices, db_index=True
    )
    description = models.TextField(_("description"), blank=True)
    changes = models.JSONField(
        _("changes"),
        default=dict,
        blank=True,
        help_text=_('Mapping of field -> [old, new]. Secrets are redacted before storage.'),
    )

    # --- which object -----------------------------------------------------
    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.CharField(_("object id"), max_length=64, blank=True, db_index=True)
    target = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField(_("object"), max_length=250, blank=True)

    # --- where / when -----------------------------------------------------
    source = models.CharField(
        _("source"), max_length=16, choices=AuditSource.choices, default=AuditSource.WEB
    )
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(_("user agent"), max_length=400, blank=True)
    request_path = models.CharField(_("path"), max_length=400, blank=True)
    request_id = models.CharField(_("request id"), max_length=36, blank=True, db_index=True)
    created_at = models.DateTimeField(_("timestamp"), auto_now_add=True, db_index=True)

    # --- integrity --------------------------------------------------------
    is_sensitive = models.BooleanField(
        _("sensitive"),
        default=False,
        help_text=_("Marks entries that must be retained for compliance."),
    )

    class Meta:
        verbose_name = _("audit entry")
        verbose_name_plural = _("audit log")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        who = self.username or _("system")
        return f"{self.created_at:%Y-%m-%d %H:%M} · {who} · {self.get_action_display()}"

    @property
    def changed_fields(self) -> list[str]:
        return sorted((self.changes or {}).keys())

    def save(self, *args, **kwargs):
        # Append-only: refuse to modify an existing entry.
        if self.pk is not None:
            raise ValueError("Audit entries are immutable and cannot be updated.")
        super().save(*args, **kwargs)
