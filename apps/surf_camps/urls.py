from __future__ import annotations

from django.urls import path

from . import views

app_name = "surf_camps"

urlpatterns = [
    # --- camps ------------------------------------------------------------
    path("", views.SurfCampListView.as_view(), name="list"),
    path("new/", views.SurfCampCreateView.as_view(), name="create"),
    path("<int:pk>/", views.SurfCampDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.SurfCampUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.SurfCampDeleteView.as_view(), name="delete"),
    # --- camp actions -----------------------------------------------------
    path("<int:pk>/publish/", views.CampPublishView.as_view(), name="publish"),
    path("<int:pk>/cancel/", views.CampCancelView.as_view(), name="cancel"),
    path(
        "<int:pk>/programme/generate/",
        views.CampGenerateProgrammeView.as_view(),
        name="generate_programme",
    ),
    # --- participants -----------------------------------------------------
    path(
        "<int:pk>/participants/",
        views.ParticipantPanelView.as_view(),
        name="participant_panel",
    ),
    path(
        "<int:pk>/participants/add/",
        views.ParticipantCreateView.as_view(),
        name="participant_create",
    ),
    path(
        "participants/<int:pk>/edit/",
        views.ParticipantUpdateView.as_view(),
        name="participant_update",
    ),
    path(
        "participants/<int:pk>/remove/",
        views.ParticipantRemoveView.as_view(),
        name="participant_remove",
    ),
    path(
        "participants/<int:pk>/status/",
        views.ParticipantStatusView.as_view(),
        name="participant_status",
    ),
    # --- programme --------------------------------------------------------
    path("<int:pk>/days/new/", views.CampDayCreateView.as_view(), name="day_create"),
    path("days/<int:pk>/edit/", views.CampDayUpdateView.as_view(), name="day_update"),
    path("days/<int:pk>/delete/", views.CampDayDeleteView.as_view(), name="day_delete"),
    path(
        "days/<int:pk>/activities/new/",
        views.CampActivityCreateView.as_view(),
        name="activity_create",
    ),
    path(
        "activities/<int:pk>/edit/",
        views.CampActivityUpdateView.as_view(),
        name="activity_update",
    ),
    path(
        "activities/<int:pk>/delete/",
        views.CampActivityDeleteView.as_view(),
        name="activity_delete",
    ),
    # --- operations -------------------------------------------------------
    path("<int:pk>/roster/", views.CampRosterView.as_view(), name="roster"),
    path("<int:pk>/export/", views.CampParticipantExportView.as_view(), name="export"),
]
