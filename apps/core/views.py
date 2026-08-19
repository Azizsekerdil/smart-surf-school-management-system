"""Core views: health check, settings screen and branded error pages."""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import requires_csrf_token
from django.views.generic import TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
class HealthCheckView(View):
    """Machine-readable health probe at ``/api/health/``.

    Anonymous callers get a minimal ``{"status": ...}`` payload so the endpoint
    is safe to expose to a load balancer; authenticated staff additionally see
    per-component detail and latency.
    """

    #: Components that must be healthy for the whole system to be "healthy".
    CRITICAL = {"database"}

    def get(self, request, *args, **kwargs):
        components: dict[str, dict] = {
            "database": self._check_database(),
            "cache": self._check_cache(),
            "celery": self._check_celery(),
            "lm_studio": self._check_lm_studio(),
            "nvidia": self._check_cloud_provider("nvidia"),
            "anthropic": self._check_cloud_provider("anthropic"),
            "surf_provider": self._check_surf_provider(),
        }

        critical_ok = all(
            components[name]["status"] == "ok" for name in self.CRITICAL if name in components
        )
        degraded = any(c["status"] in {"degraded", "unavailable"} for c in components.values())
        overall = "healthy" if critical_ok and not degraded else ("degraded" if critical_ok else "unhealthy")

        payload = {"status": overall, "version": settings.APP_VERSION}

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            payload["components"] = components
            payload["debug"] = settings.DEBUG

        return JsonResponse(payload, status=200 if critical_ok else 503)

    # -- individual probes -------------------------------------------------
    @staticmethod
    def _timed(func) -> dict:
        started = time.perf_counter()
        try:
            detail = func()
            status = detail.pop("status", "ok")
        except Exception as exc:  # noqa: BLE001 - a probe must never raise
            status, detail = "unavailable", {"error": type(exc).__name__}
        return {
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            **detail,
        }

    def _check_database(self) -> dict:
        def probe() -> dict:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return {"engine": connection.vendor}

        return self._timed(probe)

    def _check_cache(self) -> dict:
        def probe() -> dict:
            from django.core.cache import cache

            cache.set("health_check_probe", "ok", 10)
            value = cache.get("health_check_probe")
            backend = settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]
            return {"backend": backend, "status": "ok" if value == "ok" else "degraded"}

        return self._timed(probe)

    def _check_celery(self) -> dict:
        def probe() -> dict:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                return {"status": "ok", "mode": "eager (no worker required)"}
            from config import celery_app

            if celery_app is None:
                return {"status": "unavailable", "mode": "not configured"}
            replies = celery_app.control.ping(timeout=1.0)
            return {
                "status": "ok" if replies else "degraded",
                "mode": "worker",
                "workers": len(replies or []),
            }

        return self._timed(probe)

    def _check_lm_studio(self) -> dict:
        def probe() -> dict:
            from apps.ai.providers.registry import get_provider

            provider = get_provider("lmstudio")
            result = provider.health_check()
            return {
                "status": "ok" if result.ok else "unavailable",
                "models": result.models[:10],
                "message": result.message,
            }

        return self._timed(probe)

    def _check_cloud_provider(self, name: str) -> dict:
        def probe() -> dict:
            config = settings.AI["PROVIDERS"].get(name, {})
            if not config.get("ENABLED"):
                return {"status": "disabled", "message": "No API key configured"}
            from apps.ai.providers.registry import get_provider

            result = get_provider(name).health_check()
            return {"status": "ok" if result.ok else "unavailable", "message": result.message}

        return self._timed(probe)

    def _check_surf_provider(self) -> dict:
        def probe() -> dict:
            from apps.surf_conditions.providers.registry import get_surf_provider

            provider = get_surf_provider()
            ok, message = provider.health_check()
            return {"status": "ok" if ok else "degraded", "provider": provider.name, "message": message}

        return self._timed(probe)


# ---------------------------------------------------------------------------
# Settings screen
# ---------------------------------------------------------------------------
class SettingsView(CapabilityRequiredMixin, TemplateView):
    capability = "settings.view"
    template_name = "core/settings.html"

    def get_context_data(self, **kwargs):
        from .models import SystemSetting

        context = super().get_context_data(**kwargs)
        context["settings_by_group"] = {}
        for setting in SystemSetting.objects.all():
            context["settings_by_group"].setdefault(setting.group, []).append(setting)
        context["school"] = settings.SCHOOL
        context["database_engine"] = connection.vendor
        context["languages"] = settings.LANGUAGES
        return context


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
def _error_response(request, status: int, title: str, message: str):
    if request.path.startswith("/api/"):
        return JsonResponse(
            {"error": {"type": "error", "message": message, "detail": {}}}, status=status
        )
    return render(
        request,
        "errors/error.html",
        {"status_code": status, "error_title": title, "error_message": message},
        status=status,
    )


def bad_request(request, exception=None):  # noqa: ARG001
    return _error_response(
        request, 400, _("Bad request"), _("The request could not be understood.")
    )


@requires_csrf_token
def permission_denied(request, exception=None):
    message = str(exception) if exception else ""
    return _error_response(
        request,
        403,
        _("Access denied"),
        message or _("Your role does not grant access to this page."),
    )


def page_not_found(request, exception=None):  # noqa: ARG001
    return _error_response(
        request, 404, _("Page not found"), _("The page you are looking for does not exist.")
    )


@requires_csrf_token
def server_error(request):
    return _error_response(
        request,
        500,
        _("Something went wrong"),
        _("An unexpected error occurred. The incident has been logged."),
    )
