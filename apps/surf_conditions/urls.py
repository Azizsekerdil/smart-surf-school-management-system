from __future__ import annotations

from django.urls import path

from . import views

app_name = "surf_conditions"

urlpatterns = [
    path("", views.ConditionDashboardView.as_view(), name="dashboard"),
    path("history/", views.ConditionHistoryView.as_view(), name="history"),
    path("log/", views.ConditionCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ConditionDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ConditionUpdateView.as_view(), name="update"),
    # --- HTMX fragments ---------------------------------------------------
    # ``spot_panel`` is the contract apps.locations reverses by name.
    path("spots/<int:pk>/panel/", views.SpotConditionPanelView.as_view(), name="spot_panel"),
    path("spots/<int:pk>/refresh/", views.RefreshConditionsView.as_view(), name="refresh"),
    path("spots/<int:pk>/briefing/", views.ConditionBriefingView.as_view(), name="ai_briefing"),
    path("providers/health/", views.ProviderHealthView.as_view(), name="provider_health"),
]
