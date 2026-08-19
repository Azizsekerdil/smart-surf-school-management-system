"""Background work for the maintenance module.

Celery is optional in this project (``CELERY_TASK_ALWAYS_EAGER`` when no broker
is reachable), so every task here is safe to run inline as well.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_system_event

from . import services

logger = logging.getLogger("apps.maintenance")


@shared_task(ignore_result=False)
def refresh_maintenance_predictions() -> dict:
    """Recompute the risk forecast and park it in the cache.

    Scheduled nightly by ``config.celery`` so the "Predicted maintenance" board
    opens instantly. The computation is pure statistics over the school's own
    records — no external call, no model inference — so it is safe to re-run at
    any time.
    """
    payload = services.store_maintenance_predictions()
    predictions = payload["predictions"]
    high_risk = [p for p in predictions if p["risk_score"] >= 60]
    logger.info(
        "Maintenance forecast refreshed: %s items scored, %s at or above 60.",
        len(predictions),
        len(high_risk),
    )
    return {
        "generated_at": payload["generated_at"],
        "scored": len(predictions),
        "high_risk": len(high_risk),
    }


@shared_task(ignore_result=True)
def report_due_scheduled_maintenance(within_days: int = 0) -> int:
    """Write an audit entry listing the preventive services that are due.

    Gives the school a dated, immutable record that the system flagged the work,
    which matters when an incident is investigated months later.
    """
    due = list(services.due_for_scheduled_maintenance(within_days=within_days))
    if due:
        record_system_event(
            action=AuditAction.SYSTEM,
            description=_("%(count)s preventive services are due: %(items)s")
            % {
                "count": len(due),
                "items": ", ".join(str(schedule.equipment) for schedule in due[:25]),
            },
        )
    return len(due)
