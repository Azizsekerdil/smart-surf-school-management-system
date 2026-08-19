"""Records of what was backed up, and of every attempt to put it back.

Two rules shape these models:

1. **The row is not the backup.** ``BackupRecord`` describes a file on disk that
   may have been moved, truncated or silently corrupted since. Every property
   that talks about the artefact (:attr:`~BackupRecord.exists_on_disk`,
   :attr:`~BackupRecord.is_restorable`) asks the filesystem, never the database.
2. **A restore is an event with a paper trail.** ``RestoreRecord`` keeps the
   confirmation the operator typed, the checks that ran before anything was
   overwritten, and the safety backup taken first — so "who replaced the live
   data, when, and what could we have gone back to?" always has an answer.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel

#: Binary units. Backup sizes are reported the way disk vendors do not.
_KIB = 1024
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")

#: Longest a backup may stay in RUNNING before the UI calls it stalled. A run
#: that outlives this almost certainly died with its process (power cut, a
#: killed Task Scheduler job) and will never write its own failure row.
STALE_RUN_MINUTES = 180


def human_size(num_bytes: int | None) -> str:
    """Render a byte count as ``1.4 GB``.

    Returns ``—`` for an unknown size so a template never prints ``0 B`` for a
    backup that simply has not finished writing yet.
    """
    if num_bytes is None:
        return "—"
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if size < 0:
        return "—"
    if size < _KIB:
        return f"{int(size)} B"
    for unit in _SIZE_UNITS[1:]:
        size /= _KIB
        if size < _KIB:
            precision = 0 if size >= 100 else 1
            return f"{size:.{precision}f} {unit}"
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


class BackupType(models.TextChoices):
    MANUAL = "manual", _("Manual")
    DAILY = "daily", _("Daily (scheduled)")
    WEEKLY = "weekly", _("Weekly (scheduled)")
    MONTHLY = "monthly", _("Monthly (scheduled)")
    PRE_RESTORE = "pre_restore", _("Safety copy before a restore")


class BackupScope(models.TextChoices):
    DATABASE = "database", _("Database only")
    MEDIA = "media", _("Uploaded files only")
    FULL = "full", _("Full — database, files and configuration manifest")
    CONFIG = "config", _("Configuration manifest only")


class BackupStatus(models.TextChoices):
    PENDING = "pending", _("Queued")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    CORRUPT = "corrupt", _("Corrupt — do not restore")


class RestoreStatus(models.TextChoices):
    PENDING = "pending", _("Queued")
    VERIFYING = "verifying", _("Verifying the backup")
    RUNNING = "running", _("Restoring")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    ROLLED_BACK = "rolled_back", _("Failed — rolled back to the safety copy")


#: Scopes whose artefact actually contains data that can be written back.
#: A CONFIG backup is a manifest of environment variable *names*; there is
#: nothing in it to restore, and pretending otherwise would be dangerous.
RESTORABLE_SCOPES = (BackupScope.DATABASE, BackupScope.MEDIA, BackupScope.FULL)

#: Backup types that a retention sweep must never remove on its own: a human
#: asked for the manual ones, and a pre-restore copy is somebody's undo button.
PROTECTED_BACKUP_TYPES = (BackupType.MANUAL, BackupType.PRE_RESTORE)


class BackupRecord(BaseModel):
    """One backup artefact: what it covers, where it lives, whether it is sound."""

    backup_code = models.CharField(
        _("backup code"),
        max_length=32,
        unique=True,
        db_index=True,
        help_text=_("Reference such as BKP-20260815-001. Typed by hand to confirm a restore."),
    )
    backup_type = models.CharField(
        _("type"),
        max_length=16,
        choices=BackupType.choices,
        default=BackupType.MANUAL,
        db_index=True,
    )
    scope = models.CharField(
        _("scope"),
        max_length=16,
        choices=BackupScope.choices,
        default=BackupScope.FULL,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=BackupStatus.choices,
        default=BackupStatus.PENDING,
        db_index=True,
    )

    # --- the artefact -----------------------------------------------------
    file_path = models.CharField(
        _("file path"),
        max_length=500,
        blank=True,
        help_text=_("Absolute path of the artefact on the backup volume."),
    )
    file_size_bytes = models.BigIntegerField(_("size (bytes)"), default=0)
    checksum_sha256 = models.CharField(
        _("SHA-256 checksum"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Recomputed on every verification. A mismatch means the file changed."),
    )

    # --- provenance -------------------------------------------------------
    database_engine = models.CharField(
        _("database engine"),
        max_length=100,
        blank=True,
        help_text=_("A PostgreSQL dump cannot be restored into SQLite, or the reverse."),
    )
    django_version = models.CharField(_("Django version"), max_length=20, blank=True)
    app_version = models.CharField(_("application version"), max_length=20, blank=True)

    # --- timing -----------------------------------------------------------
    started_at = models.DateTimeField(_("started at"), null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), default=0)
    error_message = models.TextField(_("error"), blank=True)

    # --- operator context -------------------------------------------------
    notes = models.TextField(_("notes"), blank=True)
    is_verified = models.BooleanField(
        _("verified"),
        default=False,
        db_index=True,
        help_text=_("Set when the checksum and an engine-level integrity check both passed."),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)

    class Meta:
        verbose_name = _("backup")
        verbose_name_plural = _("backups")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["backup_type", "-created_at"]),
            models.Index(fields=["scope", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.backup_code} · {self.get_scope_display()}"

    # -- artefact ----------------------------------------------------------
    @property
    def path(self) -> Path | None:
        """The artefact as a :class:`~pathlib.Path`, or ``None`` if unrecorded."""
        return Path(self.file_path) if self.file_path else None

    @property
    def exists_on_disk(self) -> bool:
        """Is the file actually there right now?

        Asked of the filesystem every time — a backup volume can be unmounted,
        pruned by a housekeeping script, or eaten by a sync client.
        """
        target = self.path
        if target is None:
            return False
        try:
            return target.is_file()
        except OSError:
            return False

    @property
    def size_display(self) -> str:
        return human_size(self.file_size_bytes)

    @property
    def size_on_disk(self) -> int | None:
        """Current on-disk size, or ``None`` when the file is gone."""
        target = self.path
        if target is None:
            return None
        try:
            return target.stat().st_size
        except OSError:
            return None

    @property
    def file_name(self) -> str:
        target = self.path
        return target.name if target is not None else ""

    # -- timing ------------------------------------------------------------
    @property
    def age_days(self) -> int:
        """Whole days since the backup was taken.

        Measured from ``completed_at`` when the run finished, otherwise from
        ``created_at``, so a failed run still reports a sensible age.
        """
        moment = self.completed_at or self.created_at
        if moment is None:
            return 0
        return max((timezone.now() - moment).days, 0)

    @property
    def duration_display(self) -> str:
        if not self.duration_ms:
            return "—"
        seconds = self.duration_ms / 1000
        if seconds < 1:
            return f"{self.duration_ms} ms"
        if seconds < 60:
            return f"{seconds:.1f} s"
        minutes, rest = divmod(int(seconds), 60)
        return f"{minutes}m {rest}s"

    # -- state -------------------------------------------------------------
    @property
    def is_successful(self) -> bool:
        return self.status == BackupStatus.COMPLETED

    @property
    def is_stalled(self) -> bool:
        """A run that has been RUNNING far too long to still be alive."""
        if self.status not in (BackupStatus.RUNNING, BackupStatus.PENDING):
            return False
        started = self.started_at or self.created_at
        if started is None:
            return False
        return (timezone.now() - started).total_seconds() > STALE_RUN_MINUTES * 60

    @property
    def is_restorable(self) -> bool:
        """May this artefact be written back over the live system?"""
        return (
            self.status == BackupStatus.COMPLETED
            and self.scope in RESTORABLE_SCOPES
            and self.exists_on_disk
        )

    @property
    def engine_matches_current(self) -> bool:
        """Was this taken on the database engine the site is running now?"""
        if not self.database_engine:
            return False
        return self.database_engine == settings.DATABASES["default"]["ENGINE"]

    @property
    def is_protected_from_retention(self) -> bool:
        return self.backup_type in PROTECTED_BACKUP_TYPES

    @property
    def covers_database(self) -> bool:
        return self.scope in (BackupScope.DATABASE, BackupScope.FULL)

    @property
    def covers_media(self) -> bool:
        return self.scope in (BackupScope.MEDIA, BackupScope.FULL)


class RestoreRecord(BaseModel):
    """One attempt to put a backup back over the live system."""

    backup = models.ForeignKey(
        "backups.BackupRecord",
        verbose_name=_("backup"),
        on_delete=models.PROTECT,
        related_name="restores",
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=RestoreStatus.choices,
        default=RestoreStatus.PENDING,
        db_index=True,
    )
    safety_backup = models.ForeignKey(
        "backups.BackupRecord",
        verbose_name=_("safety copy"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="protected_restores",
        help_text=_("Taken automatically immediately before the restore began."),
    )

    started_at = models.DateTimeField(_("started at"), null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    duration_ms = models.PositiveIntegerField(_("duration (ms)"), default=0)
    error_message = models.TextField(_("error"), blank=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("confirmed by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backup_restores_confirmed",
    )
    confirmation_text = models.CharField(
        _("typed confirmation"),
        max_length=64,
        blank=True,
        help_text=_("What the operator typed. Must match the backup code exactly."),
    )
    pre_restore_checks = models.JSONField(
        _("pre-restore checks"),
        default=dict,
        blank=True,
        help_text=_("Ordered result of every gate the restore had to pass."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("restore")
        verbose_name_plural = _("restores")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        code = self.backup.backup_code if self.backup_id else "?"
        return f"{code} → {self.get_status_display()}"

    @property
    def duration_display(self) -> str:
        if not self.duration_ms:
            return "—"
        seconds = self.duration_ms / 1000
        if seconds < 60:
            return f"{seconds:.1f} s"
        minutes, rest = divmod(int(seconds), 60)
        return f"{minutes}m {rest}s"

    @property
    def is_finished(self) -> bool:
        return self.status in (
            RestoreStatus.COMPLETED,
            RestoreStatus.FAILED,
            RestoreStatus.ROLLED_BACK,
        )

    @property
    def check_rows(self) -> list[dict]:
        """``pre_restore_checks`` flattened for display, in the order it ran."""
        rows: list[dict] = []
        for name, payload in (self.pre_restore_checks or {}).items():
            if isinstance(payload, dict):
                rows.append(
                    {
                        "name": name,
                        "passed": bool(payload.get("passed")),
                        "detail": payload.get("detail", ""),
                    }
                )
            else:
                rows.append({"name": name, "passed": bool(payload), "detail": ""})
        return rows
