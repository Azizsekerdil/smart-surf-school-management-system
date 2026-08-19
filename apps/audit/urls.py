from __future__ import annotations

from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditLogListView.as_view(), name="list"),
    path("export/", views.AuditLogExportView.as_view(), name="export"),
    path("<int:pk>/", views.AuditLogDetailView.as_view(), name="detail"),
]
