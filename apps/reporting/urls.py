from __future__ import annotations

from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.ReportCatalogueView.as_view(), name="list"),
    # ``key`` is a catalogue key such as "daily_operations", not a database id.
    path("run/<slug:key>/", views.ReportRunView.as_view(), name="run"),
    # --- archive -----------------------------------------------------------
    path("history/", views.GeneratedReportListView.as_view(), name="history"),
    path(
        "history/<int:pk>/download/",
        views.GeneratedReportDownloadView.as_view(),
        name="download",
    ),
    # --- saved configurations ---------------------------------------------
    path("saved/", views.ReportDefinitionListView.as_view(), name="definition_list"),
    path("saved/new/", views.ReportDefinitionCreateView.as_view(), name="definition_create"),
    path(
        "saved/<int:pk>/edit/",
        views.ReportDefinitionUpdateView.as_view(),
        name="definition_update",
    ),
    path(
        "saved/<int:pk>/archive/",
        views.ReportDefinitionDeleteView.as_view(),
        name="definition_delete",
    ),
    path(
        "saved/<int:pk>/run/",
        views.ReportDefinitionRunView.as_view(),
        name="definition_run",
    ),
]
