"""Project configuration package for the Smart Surf School Management System."""

from __future__ import annotations

# Celery is optional: the whole application must keep working when Celery/Redis
# are not installed or not running (offline-first requirement).
try:  # pragma: no cover - import guard
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except Exception:  # noqa: BLE001 - deliberately broad, Celery is optional
    celery_app = None  # type: ignore[assignment]
    __all__ = ()
