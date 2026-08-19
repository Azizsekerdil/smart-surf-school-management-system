"""REST API routing with per-app auto-discovery.

Each app publishes its endpoints by defining ``ROUTES`` in ``apps/<name>/api.py``::

    ROUTES = [
        ("bookings", BookingViewSet, "booking"),
    ]

This module imports every local app's ``api`` module and registers whatever it
finds. A missing ``api.py`` is fine; a broken one raises loudly in DEBUG so the
mistake is caught immediately, and is skipped with an error log in production so
one bad module cannot take the whole API down.
"""

from __future__ import annotations

import importlib
import logging

from django.apps import apps as django_apps
from django.conf import settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

logger = logging.getLogger(__name__)

router = DefaultRouter()
router.trailing_slash = "/"

_registered: list[str] = []
_skipped: list[tuple[str, str]] = []

for app_config in django_apps.get_app_configs():
    if not app_config.name.startswith("apps."):
        continue

    module_name = f"{app_config.name}.api"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # The app genuinely has no API module — that is allowed.
        if exc.name in {module_name, f"{app_config.name}.api"}:
            continue
        # A real missing import *inside* api.py — surface it.
        if settings.DEBUG:
            raise
        logger.error("Skipping %s: %s", module_name, exc)
        _skipped.append((module_name, str(exc)))
        continue
    except Exception as exc:  # noqa: BLE001
        if settings.DEBUG:
            raise
        logger.error("Skipping %s: %s", module_name, exc)
        _skipped.append((module_name, str(exc)))
        continue

    routes = getattr(module, "ROUTES", None)
    if not routes:
        continue

    for entry in routes:
        try:
            prefix, viewset, basename = entry
        except (TypeError, ValueError):
            logger.error("Malformed ROUTES entry in %s: %r", module_name, entry)
            continue
        router.register(prefix, viewset, basename=basename)
        _registered.append(prefix)

urlpatterns = [
    # --- token authentication for programmatic clients --------------------
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token-verify"),
    # --- auto-discovered resources ----------------------------------------
    path("", include(router.urls)),
]

logger.debug("API routes registered: %s", ", ".join(sorted(_registered)))
