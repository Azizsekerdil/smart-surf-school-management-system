from __future__ import annotations

from django.urls import path

from . import views

app_name = "backups"

urlpatterns = [
    path("", views.BackupListView.as_view(), name="list"),
    path("new/", views.BackupCreateView.as_view(), name="create"),
    path("restores/", views.RestoreListView.as_view(), name="restore_list"),
    path("settings/", views.RetentionSettingsView.as_view(), name="settings"),
    path("settings/apply-retention/", views.RetentionRunView.as_view(), name="retention_run"),
    path("<int:pk>/", views.BackupDetailView.as_view(), name="detail"),
    path("<int:pk>/verify/", views.BackupVerifyView.as_view(), name="verify"),
    path("<int:pk>/download/", views.BackupDownloadView.as_view(), name="download"),
    path("<int:pk>/delete/", views.BackupDeleteView.as_view(), name="delete"),
    path("<int:pk>/restore/", views.BackupRestoreView.as_view(), name="restore"),
]
