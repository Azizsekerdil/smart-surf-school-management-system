"""Scheduled work for the hire counter.

Wired into Celery beat by ``config/celery.py`` as ``overdue-rental-check``,
hourly. With Celery running eagerly (no Redis) the same call still works, so a
school without a broker is not left blind to overdue gear.
"""

from __future__ import annotations

import logging

from celery import shared_task

from . import services

logger = logging.getLogger("apps.rentals")


@shared_task(name="apps.rentals.tasks.flag_overdue_rentals", ignore_result=True)
def flag_overdue_rentals() -> int:
    """Flag active hires that are past their due-back time.

    Returns the number of contracts moved to *overdue*. Idempotent: a contract
    already flagged is not touched again.
    """
    count = services.flag_overdue_rentals()
    if count:
        logger.info("Flagged %s overdue rental(s)", count)
    return count
