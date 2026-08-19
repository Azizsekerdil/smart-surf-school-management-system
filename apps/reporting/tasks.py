"""Background delivery of scheduled reports.

Celery is optional in this project (``CELERY_TASK_ALWAYS_EAGER`` when there is
no broker), so the same work is also reachable through the
``run_scheduled_reports`` management command. On the Windows machine a surf
school actually runs this on, Task Scheduler calling the command every five
minutes is the supported setup.
"""

from __future__ import annotations

import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)

#: Must match how often the scheduler ticks, so a job is neither missed nor
#: fired twice. ``run_scheduled_reports`` also de-duplicates within the window.
DEFAULT_WINDOW_MINUTES = 5


@shared_task(name="reporting.run_scheduled_reports")
def run_scheduled_reports(window_minutes: int = DEFAULT_WINDOW_MINUTES) -> dict:
    """Generate and e-mail every report whose schedule fired in the window."""
    results = services.run_scheduled_reports(window_minutes=window_minutes)
    succeeded = [report for report in results if report.is_downloadable]
    failed = [report for report in results if not report.is_downloadable]

    if failed:
        logger.warning(
            "Scheduled reports failed: %s",
            ", ".join(f"{report.report_key}: {report.error_message}" for report in failed),
        )
    return {
        "generated": len(succeeded),
        "failed": len(failed),
        "keys": [report.report_key for report in results],
    }
