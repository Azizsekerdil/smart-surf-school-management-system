"""Celery tasks.

Celery is optional in this project (``CELERY_TASK_ALWAYS_EAGER`` when no broker
is configured), so every task here is a thin, idempotent wrapper around a
service function that also works when called directly.
"""

from __future__ import annotations

import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)


@shared_task(name="apps.notifications.tasks.send_lesson_reminders", ignore_result=True)
def send_lesson_reminders() -> int:
    """Remind attendees and coaches about lessons starting in 30–40 minutes.

    Scheduled every 10 minutes by ``config.celery``. Safe to run twice: each
    booking is claimed with a conditional update before anything is sent.
    """
    return services.send_lesson_reminders()


@shared_task(
    name="apps.notifications.tasks.deliver_notification_email",
    ignore_result=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def deliver_notification_email(notification_id: int) -> bool:
    """Send the e-mail copy of a stored notification."""
    return services.send_notification_email(notification_id)


@shared_task(name="apps.notifications.tasks.purge_old_notifications", ignore_result=True)
def purge_old_notifications(days: int = 180) -> int:
    """Retention sweep: soft-delete long-read notifications."""
    return services.purge_old_notifications(days=days)
