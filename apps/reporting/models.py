"""Saved report configurations and the archive of everything that was exported.

``ReportDefinition`` is a *named* combination of report + format + filters, e.g.
"Monday morning ops sheet". ``GeneratedReport`` is the receipt: which report was
run, by whom, over which filters, how long it took, and the file itself.

The archive exists for two reasons. Legally, an export of customer data is a
disclosure event and has to be traceable — the matching ``AuditAction.EXPORT``
entry points here. Practically, month-end numbers get re-sent constantly, and
re-downloading the exact file that was already handed to the accountant beats
regenerating one that might now differ.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class ReportFormat(models.TextChoices):
    """Output formats — the keys match ``exporters.registry.EXPORT_FORMATS``."""

    PDF = "pdf", _("PDF")
    EXCEL = "excel", _("Excel")
    CSV = "csv", _("CSV")


class ReportStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")


class ReportDefinition(BaseModel):
    """A saved, re-runnable report configuration."""

    name = models.CharField(_("name"), max_length=150, db_index=True)
    code = models.SlugField(
        _("code"),
        max_length=60,
        unique=True,
        help_text=_("Stable identifier used by the API and the scheduler."),
    )
    report_key = models.CharField(
        _("report"),
        max_length=60,
        db_index=True,
        help_text=_("Key of the builder in the report catalogue, e.g. daily_operations."),
    )
    description = models.TextField(_("description"), blank=True)

    default_format = models.CharField(
        _("default format"),
        max_length=10,
        choices=ReportFormat.choices,
        default=ReportFormat.PDF,
    )
    default_filters = models.JSONField(
        _("default filters"),
        default=dict,
        blank=True,
        help_text=_('Filter values applied when this report is run, e.g. {"range": "30"}.'),
    )
    required_capability = models.CharField(
        _("required capability"),
        max_length=60,
        blank=True,
        help_text=_(
            "Capability a user must hold to run this report. Left blank it falls "
            "back to the capability declared by the report itself."
        ),
    )

    is_scheduled = models.BooleanField(
        _("run on a schedule"),
        default=False,
        db_index=True,
        help_text=_("Requires a schedule expression and at least one recipient."),
    )
    schedule_cron = models.CharField(
        _("schedule"),
        max_length=100,
        blank=True,
        help_text=_('Five-field cron expression: minute hour day month weekday, e.g. "0 7 * * 1".'),
    )
    recipients = models.JSONField(
        _("recipients"),
        default=list,
        blank=True,
        help_text=_("E-mail addresses the generated file is sent to."),
    )
    last_run_at = models.DateTimeField(_("last run at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("report definition")
        verbose_name_plural = _("report definitions")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["report_key", "is_active"]),
            models.Index(fields=["is_scheduled", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def recipient_list(self) -> list[str]:
        """Recipients as a clean list, whatever shape the JSON field holds."""
        raw = self.recipients
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.replace(";", ",").split(",")]
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @property
    def filter_dict(self) -> dict:
        return self.default_filters if isinstance(self.default_filters, dict) else {}


class GeneratedReport(BaseModel):
    """One export: the file, its provenance and how it went."""

    definition = models.ForeignKey(
        ReportDefinition,
        verbose_name=_("definition"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
        help_text=_("Set when the export came from a saved configuration."),
    )
    report_key = models.CharField(_("report"), max_length=60, db_index=True)
    title = models.CharField(_("title"), max_length=200)
    format = models.CharField(
        _("format"), max_length=10, choices=ReportFormat.choices, default=ReportFormat.PDF
    )
    filters_used = models.JSONField(_("filters used"), default=dict, blank=True)

    file = models.FileField(_("file"), upload_to="reports/%Y/%m/", blank=True)
    file_size_bytes = models.PositiveBigIntegerField(_("file size (bytes)"), default=0)
    row_count = models.PositiveIntegerField(_("rows"), default=0)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("generated by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
    )
    generation_ms = models.PositiveIntegerField(
        _("generation time (ms)"),
        default=0,
        help_text=_("Wall-clock time spent querying and rendering."),
    )
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=ReportStatus.choices,
        default=ReportStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("generated report")
        verbose_name_plural = _("generated reports")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["report_key", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_format_display()})"

    # --- derived ----------------------------------------------------------
    @property
    def is_downloadable(self) -> bool:
        return self.status == ReportStatus.COMPLETED and bool(self.file)

    @property
    def file_size_display(self) -> str:
        """Human-readable size — operators judge "did it work" by the size."""
        size = float(self.file_size_bytes or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @property
    def format_icon(self) -> str:
        """Icon name for this format, so templates need no lookup table."""
        from .exporters.registry import format_icon  # noqa: PLC0415 - avoids an import cycle

        return format_icon(self.format)

    @property
    def duration_display(self) -> str:
        milliseconds = self.generation_ms or 0
        if milliseconds < 1000:
            return f"{milliseconds} ms"
        return f"{milliseconds / 1000:.1f} s"

    def download_filename(self) -> str:
        """Filename offered to the browser.

        Rebuilt from the title and the export date rather than reusing the
        stored name: storage may have appended a collision suffix, and a file
        sitting in someone's Downloads folder has to say which day it covers.
        """
        from django.utils.text import slugify  # noqa: PLC0415 - keeps model import light

        from .exporters.registry import EXPORT_FORMATS  # noqa: PLC0415 - avoids an import cycle

        exporter = EXPORT_FORMATS.get(self.format)
        extension = exporter.file_extension if exporter else (self.format or "dat")
        stamp = timezone.localtime(self.created_at) if self.created_at else timezone.localtime()
        slug = slugify(self.title) or slugify(self.report_key) or "report"
        return f"{slug}-{stamp:%Y-%m-%d}.{extension}"
