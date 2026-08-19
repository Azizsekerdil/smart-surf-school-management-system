"""Read queries for the backup screens.

The interesting one is :func:`records_created_since`, which powers the "this is
what you are about to throw away" panel on the restore confirmation page. It
walks the model registry rather than importing any business app, so this module
keeps its promise of depending on nothing downstream.
"""

from __future__ import annotations

import logging

from django.apps import apps as django_apps
from django.db.models import QuerySet

from .models import BackupRecord, BackupStatus, RestoreRecord

logger = logging.getLogger("apps.backups")

#: Never listed in the "records at risk" panel: their own rows are reinstated
#: after a restore, so counting them would only confuse the operator.
_EXCLUDED_APP_LABELS = {"backups"}

#: How many model rows the confirmation screen lists before it stops.
AT_RISK_LIMIT = 15


def backup_queryset() -> QuerySet[BackupRecord]:
    return BackupRecord.objects.select_related("created_by").order_by("-created_at", "-id")


def restore_queryset() -> QuerySet[RestoreRecord]:
    return RestoreRecord.objects.select_related(
        "backup", "safety_backup", "confirmed_by"
    ).order_by("-created_at", "-id")


def restores_for_backup(record: BackupRecord) -> QuerySet[RestoreRecord]:
    return (
        record.restores.select_related("safety_backup", "confirmed_by")
        .order_by("-created_at", "-id")
    )


def completed_backups() -> QuerySet[BackupRecord]:
    return BackupRecord.objects.filter(status=BackupStatus.COMPLETED)


def storage_by_scope(statistics: dict) -> list[dict]:
    """Flatten ``backup_statistics()['by_scope']`` into chart-ready rows."""
    rows = [
        {"scope": scope, "label": data["label"], "bytes": data["bytes"], "count": data["count"]}
        for scope, data in statistics.get("by_scope", {}).items()
        if data["count"]
    ]
    return sorted(rows, key=lambda row: row["bytes"], reverse=True)


def _auditable_models():
    """Concrete local models that stamp a creation time."""
    for model in django_apps.get_models():
        meta = model._meta
        if meta.abstract or meta.proxy or meta.swapped:
            continue
        if not model.__module__.startswith("apps."):
            continue
        if meta.app_label in _EXCLUDED_APP_LABELS:
            continue
        if not any(field.name == "created_at" for field in meta.fields):
            continue
        yield model


def _counts_created_since(moment) -> list[dict]:
    """Row counts per model for everything created after *moment*."""
    if moment is None:
        return []

    rows: list[dict] = []
    for model in _auditable_models():
        try:
            count = model._default_manager.filter(created_at__gt=moment).count()
        except Exception:  # noqa: BLE001 - an unmanaged model must not break the page
            logger.debug("Skipping %s in the at-risk count", model._meta.label, exc_info=True)
            continue
        if count:
            rows.append(
                {
                    "label": str(model._meta.verbose_name_plural).title(),
                    "app": str(model._meta.app_config.verbose_name),
                    "count": count,
                }
            )
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows


def records_at_risk(moment) -> dict:
    """What a restore back to *moment* would erase.

    Returns the busiest models first, so the operator sees the expensive loss
    ("41 bookings") before the trivial one ("1 tag"), plus the honest totals for
    everything that did not fit in the list.
    """
    rows = _counts_created_since(moment)
    shown = rows[:AT_RISK_LIMIT]
    return {
        "rows": shown,
        "total_records": sum(row["count"] for row in rows),
        "total_models": len(rows),
        "hidden_models": max(len(rows) - len(shown), 0),
    }
