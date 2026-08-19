from __future__ import annotations

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.AnalyticsDashboardView.as_view(), name="dashboard"),
    path("summary/", views.StatisticalSummaryView.as_view(), name="summary"),
    path("narrative/", views.AINarrativeView.as_view(), name="narrative"),
    path("export/", views.AnalyticsExportView.as_view(), name="export"),
]
