"""Celery application.

Celery is entirely optional. When Redis is unavailable the project runs with
``CELERY_TASK_ALWAYS_EAGER = True`` and every ``.delay()`` executes inline, so
scheduled work (backups, notifications, condition refresh) still happens.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("surf_school")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "refresh-surf-conditions": {
        "task": "apps.surf_conditions.tasks.refresh_all_spot_conditions",
        "schedule": crontab(minute="*/30"),
    },
    "daily-backup": {
        "task": "apps.backups.tasks.run_scheduled_backup",
        "schedule": crontab(hour=3, minute=0),
        "kwargs": {"frequency": "daily"},
    },
    "weekly-backup": {
        "task": "apps.backups.tasks.run_scheduled_backup",
        "schedule": crontab(hour=3, minute=30, day_of_week=0),
        "kwargs": {"frequency": "weekly"},
    },
    "lesson-reminders": {
        "task": "apps.notifications.tasks.send_lesson_reminders",
        "schedule": crontab(minute="*/10"),
    },
    "overdue-rental-check": {
        "task": "apps.rentals.tasks.flag_overdue_rentals",
        "schedule": crontab(minute=0, hour="*"),
    },
    "maintenance-forecast": {
        "task": "apps.maintenance.tasks.refresh_maintenance_predictions",
        "schedule": crontab(hour=4, minute=0),
    },
    "certification-expiry-check": {
        "task": "apps.instructors.tasks.check_certification_expiry",
        "schedule": crontab(hour=6, minute=0),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # pragma: no cover - diagnostic helper
    return f"Celery request: {self.request!r}"
