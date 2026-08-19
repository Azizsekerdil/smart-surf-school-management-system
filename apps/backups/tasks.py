"""Scheduled backups.

``config/celery.py`` wires this in as ``daily-backup`` (03:00) and
``weekly-backup`` (Sunday 03:30). With no Redis the project runs Celery eagerly,
so the same call still executes — and a school with neither Redis nor Celery runs
``manage.py backup --type daily`` from Windows Task Scheduler instead. All three
paths land on exactly this logic.
"""

from __future__ import annotations

import logging

from celery import shared_task

from . import services
from .models import BackupScope, BackupStatus, BackupType

logger = logging.getLogger("apps.backups")

#: Scheduler cadence -> the record type and how much to copy. Daily runs cover
#: the whole system; the weekly and monthly copies are the long-horizon ones a
#: school falls back on when a problem is only noticed weeks later.
FREQUENCY_MAP: dict[str, tuple[str, str]] = {
    "daily": (BackupType.DAILY, BackupScope.FULL),
    "weekly": (BackupType.WEEKLY, BackupScope.FULL),
    "monthly": (BackupType.MONTHLY, BackupScope.FULL),
    "manual": (BackupType.MANUAL, BackupScope.FULL),
}


@shared_task(name="apps.backups.tasks.run_scheduled_backup", ignore_result=True)
def run_scheduled_backup(frequency: str = "daily") -> dict:
    """Take a scheduled backup, verify it, then prune to the retention policy.

    Returns a JSON-serialisable summary (the configured result serializer is
    JSON, so nothing here may be a model instance).
    """
    key = (frequency or "daily").strip().lower()
    if key not in FREQUENCY_MAP:
        logger.error("Unknown backup frequency %r; falling back to daily", frequency)
        key = "daily"
    backup_type, scope = FREQUENCY_MAP[key]

    record = services.create_backup(
        backup_type,
        scope,
        user=None,
        notes=f"Scheduled {key} backup",
    )

    verified = False
    verify_detail = ""
    if record.status == BackupStatus.COMPLETED:
        verified, verify_detail = services.verify_backup(record)

    # Retention runs whatever happened above: a failed run must not stop the
    # sweep, or a full disk would keep every future run failing too.
    retention = services.apply_retention_policy()

    summary = {
        "frequency": key,
        "backup_code": record.backup_code,
        "status": record.status,
        "scope": record.scope,
        "size_bytes": record.file_size_bytes,
        "duration_ms": record.duration_ms,
        "verified": verified,
        "detail": str(verify_detail or record.error_message),
        "retention_deleted": retention["deleted"],
        "retention_freed_bytes": retention["freed_bytes"],
    }
    if record.status == BackupStatus.COMPLETED and verified:
        logger.info("Scheduled %s backup %s ok (%s)", key, record.backup_code, record.size_display)
    else:
        logger.error("Scheduled %s backup problem: %s", key, summary["detail"])
    return summary
