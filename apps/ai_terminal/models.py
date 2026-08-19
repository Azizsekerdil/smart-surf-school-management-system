"""AI terminal data model: sessions, commands and code-change proposals."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, TimeStampedModel


class TerminalSession(BaseModel):
    """One working session in the development terminal."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="terminal_sessions",
        verbose_name=_("user"),
    )
    title = models.CharField(_("title"), max_length=200, blank=True)
    goal = models.TextField(
        _("goal"), blank=True, help_text=_("What the operator asked the agent to achieve.")
    )
    is_active = models.BooleanField(_("active"), default=True)
    closed_at = models.DateTimeField(_("closed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("terminal session")
        verbose_name_plural = _("terminal sessions")
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Session #{self.pk}"

    @property
    def command_count(self) -> int:
        return self.commands.count()


class TerminalCommand(TimeStampedModel):
    """A proposed command and everything that happened to it.

    Rows are never deleted: this is the evidence trail for what the AI asked to
    run and what a human decided about it.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", _("Proposed")
        AWAITING_APPROVAL = "awaiting_approval", _("Awaiting approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        BLOCKED = "blocked", _("Blocked by policy")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        TIMED_OUT = "timed_out", _("Timed out")
        CANCELLED = "cancelled", _("Cancelled")

    class Origin(models.TextChoices):
        USER = "user", _("Typed by a user")
        AGENT = "agent", _("Proposed by the AI agent")

    session = models.ForeignKey(
        TerminalSession, on_delete=models.CASCADE, related_name="commands"
    )
    origin = models.CharField(
        _("origin"), max_length=8, choices=Origin.choices, default=Origin.USER
    )
    command = models.TextField(_("command"))
    #: The validated argument vector actually handed to the OS.
    argv = models.JSONField(_("argument vector"), default=list, blank=True)
    rationale = models.TextField(
        _("rationale"), blank=True, help_text=_("Why the agent wants to run this.")
    )

    risk = models.CharField(_("risk"), max_length=20, default="blocked")
    policy_rule = models.CharField(_("matched policy rule"), max_length=100, blank=True)
    policy_reason = models.CharField(_("policy reason"), max_length=500, blank=True)

    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.PROPOSED, db_index=True
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="terminal_commands_requested",
        verbose_name=_("requested by"),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="terminal_commands_approved",
        verbose_name=_("approved by"),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    decision_note = models.CharField(_("decision note"), max_length=500, blank=True)
    #: When an approver edits the command before allowing it.
    edited_command = models.TextField(_("edited command"), blank=True)

    exit_code = models.IntegerField(_("exit code"), null=True, blank=True)
    stdout = models.TextField(_("standard output"), blank=True)
    stderr = models.TextField(_("standard error"), blank=True)
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), default=0)
    output_truncated = models.BooleanField(default=False)
    executed_at = models.DateTimeField(_("executed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("terminal command")
        verbose_name_plural = _("terminal commands")
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self) -> str:
        return f"{self.command[:60]} [{self.status}]"

    @property
    def effective_command(self) -> str:
        return self.edited_command or self.command

    @property
    def is_pending_decision(self) -> bool:
        return self.status == self.Status.AWAITING_APPROVAL

    @property
    def succeeded(self) -> bool:
        return self.status == self.Status.COMPLETED and self.exit_code == 0


class CodeChangeProposal(BaseModel):
    """A file edit the agent wants to make.

    Nothing is written to disk until a human approves. The original content is
    stored so the change can be reverted precisely.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        AWAITING_APPROVAL = "awaiting_approval", _("Awaiting approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        APPLIED = "applied", _("Applied")
        REVERTED = "reverted", _("Reverted")
        FAILED = "failed", _("Failed to apply")

    class ChangeType(models.TextChoices):
        CREATE = "create", _("Create file")
        MODIFY = "modify", _("Modify file")
        DELETE = "delete", _("Delete file")

    session = models.ForeignKey(
        TerminalSession, on_delete=models.CASCADE, related_name="proposals"
    )
    title = models.CharField(_("title"), max_length=200)
    summary = models.TextField(_("summary"), blank=True)
    file_path = models.CharField(_("file"), max_length=500)
    change_type = models.CharField(
        _("change type"), max_length=10, choices=ChangeType.choices, default=ChangeType.MODIFY
    )

    original_content = models.TextField(_("original content"), blank=True)
    proposed_content = models.TextField(_("proposed content"), blank=True)
    unified_diff = models.TextField(_("diff"), blank=True)

    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="code_proposals_approved",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    apply_error = models.CharField(max_length=500, blank=True)
    #: Git branch created before applying, so the change can be abandoned.
    checkpoint_branch = models.CharField(_("checkpoint branch"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("code change proposal")
        verbose_name_plural = _("code change proposals")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_change_type_display()}: {self.file_path}"

    @property
    def added_lines(self) -> int:
        return sum(
            1
            for line in (self.unified_diff or "").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    @property
    def removed_lines(self) -> int:
        return sum(
            1
            for line in (self.unified_diff or "").splitlines()
            if line.startswith("-") and not line.startswith("---")
        )

    def mark_applied(self) -> None:
        self.status = self.Status.APPLIED
        self.applied_at = timezone.now()
        self.save(update_fields=["status", "applied_at", "updated_at"])
