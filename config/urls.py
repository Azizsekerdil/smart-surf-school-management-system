"""Root URL configuration.

Layout
------
``/``            HTML application (Django templates + HTMX), i18n-prefixed
``/api/v1/``     REST API (DRF), never i18n-prefixed
``/api/health/`` machine-readable health probe
``/admin/``      Django admin (staff only)
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.core import views as core_views

admin.site.site_header = "Smart Surf School — Administration"
admin.site.site_title = "Surf School Admin"
admin.site.index_title = "Operations"

# ---------------------------------------------------------------------------
# Non-localised routes (API, health, language switch, media)
# ---------------------------------------------------------------------------
urlpatterns = [
    path("api/health/", core_views.HealthCheckView.as_view(), name="health-check"),
    path("api/v1/", include("config.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="api-schema"),
        name="api-redoc",
    ),
    path("i18n/", include("django.conf.urls.i18n")),
]

# ---------------------------------------------------------------------------
# Localised application routes  ->  /tr/... and /en/...
# ---------------------------------------------------------------------------
urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
    path("", include("apps.dashboard.urls", namespace="dashboard")),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    # --- CRM / people ------------------------------------------------------
    path("customers/", include("apps.customers.urls", namespace="customers")),
    path("students/", include("apps.students.urls", namespace="students")),
    path("instructors/", include("apps.instructors.urls", namespace="instructors")),
    path("crm/", include("apps.crm.urls", namespace="crm")),
    # --- operations --------------------------------------------------------
    path("locations/", include("apps.locations.urls", namespace="locations")),
    path("lessons/", include("apps.lessons.urls", namespace="lessons")),
    path("bookings/", include("apps.bookings.urls", namespace="bookings")),
    path("surf-camps/", include("apps.surf_camps.urls", namespace="surf_camps")),
    # --- equipment ---------------------------------------------------------
    path("equipment/", include("apps.equipment.urls", namespace="equipment")),
    path("rentals/", include("apps.rentals.urls", namespace="rentals")),
    path("maintenance/", include("apps.maintenance.urls", namespace="maintenance")),
    # --- surf / safety -----------------------------------------------------
    path("surf-conditions/", include("apps.surf_conditions.urls", namespace="surf_conditions")),
    path("safety/", include("apps.safety.urls", namespace="safety")),
    # --- business ----------------------------------------------------------
    path("finance/", include("apps.finance.urls", namespace="finance")),
    path("pos/", include("apps.pos.urls", namespace="pos")),
    path("analytics/", include("apps.analytics.urls", namespace="analytics")),
    path("reports/", include("apps.reporting.urls", namespace="reporting")),
    # --- platform ----------------------------------------------------------
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
    path("backups/", include("apps.backups.urls", namespace="backups")),
    path("audit/", include("apps.audit.urls", namespace="audit")),
    # --- AI ----------------------------------------------------------------
    path("ai/", include("apps.ai.urls", namespace="ai")),
    path("ai-terminal/", include("apps.ai_terminal.urls", namespace="ai_terminal")),
    # --- guidance ----------------------------------------------------------
    path("help/", include("apps.help_center.urls", namespace="help_center")),
    path("training/", include("apps.training.urls", namespace="training")),
    path("onboarding/", include("apps.onboarding.urls", namespace="onboarding")),
    path("settings/", include("apps.core.urls", namespace="core")),
    prefix_default_language=True,
)

# ---------------------------------------------------------------------------
# Development-only static/media serving
# ---------------------------------------------------------------------------
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
