"""Background work for the surf conditions module.

``config.celery`` schedules :func:`refresh_all_spot_conditions` every 30 minutes.
Celery is optional in this project — with no broker reachable the project runs
``CELERY_TASK_ALWAYS_EAGER``, and a Windows deployment can instead run
``manage.py refresh_conditions`` from Task Scheduler. Both paths call the same
service function, so the two ways of running a school never drift apart.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_system_event

from . import services

logger = logging.getLogger("apps.surf_conditions")


@shared_task(name="apps.surf_conditions.tasks.refresh_all_spot_conditions", ignore_result=False)
def refresh_all_spot_conditions() -> dict:
    """Fetch, store and score the conditions at every active spot.

    Never raises: an unreachable weather service must not retry-storm a broker
    or leave a Celery worker in a failed state. Spots that could not be reached
    are named in the return value and in the audit trail.
    """
    result = services.refresh_all_spot_conditions()

    if result["failed"]:
        # A missing reading is an operational fact worth keeping: it explains
        # why the 08:00 briefing had no fresh numbers.
        record_system_event(
            action=AuditAction.SYSTEM,
            description=_(
                "Surf conditions could not be refreshed for %(count)s spot(s) via "
                "%(provider)s: %(spots)s"
            )
            % {
                "count": len(result["failed"]),
                "provider": result["provider"],
                "spots": ", ".join(result["failed"][:25]),
            },
        )

    logger.info(
        "Surf condition refresh finished: %s of %s spots updated via %s.",
        len(result["refreshed"]),
        result["spots"],
        result["provider"],
    )
    return result
