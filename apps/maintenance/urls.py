"""URL routing for the maintenance module.

``app_name`` must match the namespace used in ``config/urls.py``.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    # --- records -----------------------------------------------------------
    path("", views.MaintenanceRecordListView.as_view(), name="list"),
    path("new/", views.MaintenanceRecordCreateView.as_view(), name="create"),
    # --- predictive board & schedules (static prefixes first) --------------
    path("predictions/", views.MaintenancePredictionView.as_view(), name="predictions"),
    path("costs/", views.MaintenanceCostReportView.as_view(), name="cost_report"),
    path("schedules/", views.MaintenanceScheduleListView.as_view(), name="schedule_list"),
    path(
        "schedules/new/",
        views.MaintenanceScheduleCreateView.as_view(),
        name="schedule_create",
    ),
    path(
        "schedules/<int:pk>/edit/",
        views.MaintenanceScheduleUpdateView.as_view(),
        name="schedule_update",
    ),
    path(
        "schedules/<int:pk>/performed/",
        views.MaintenanceSchedulePerformedView.as_view(),
        name="schedule_performed",
    ),
    # --- record detail & actions -------------------------------------------
    path("<int:pk>/", views.MaintenanceRecordDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.MaintenanceRecordUpdateView.as_view(), name="update"),
    path("<int:pk>/start/", views.MaintenanceStartView.as_view(), name="start"),
    path("<int:pk>/hold/", views.MaintenanceHoldView.as_view(), name="hold"),
    path("<int:pk>/cancel/", views.MaintenanceCancelView.as_view(), name="cancel"),
    path("<int:pk>/complete/", views.MaintenanceCompleteView.as_view(), name="complete"),
]
