from __future__ import annotations

from django.urls import path

from . import views

app_name = "safety"

urlpatterns = [
    path("", views.SafetyDashboardView.as_view(), name="dashboard"),
    # --- incidents --------------------------------------------------------
    path("incidents/", views.IncidentListView.as_view(), name="incident_list"),
    path("incidents/new/", views.IncidentCreateView.as_view(), name="incident_create"),
    path("incidents/<int:pk>/", views.IncidentDetailView.as_view(), name="incident_detail"),
    path("incidents/<int:pk>/edit/", views.IncidentUpdateView.as_view(), name="incident_update"),
    path("incidents/<int:pk>/review/", views.IncidentReviewView.as_view(), name="incident_review"),
    # --- lifeguard roster -------------------------------------------------
    path("roster/", views.LifeguardRosterView.as_view(), name="roster"),
    path("roster/new/", views.LifeguardAssignmentCreateView.as_view(), name="roster_assign"),
    path(
        "roster/<int:pk>/edit/",
        views.LifeguardAssignmentUpdateView.as_view(),
        name="assignment_update",
    ),
    path(
        "roster/<int:pk>/confirm/",
        views.LifeguardAssignmentConfirmView.as_view(),
        name="assignment_confirm",
    ),
    # --- emergency contacts ----------------------------------------------
    path("contacts/", views.EmergencyContactListView.as_view(), name="contact_list"),
    path("contacts/card/", views.EmergencyContactCardView.as_view(), name="contact_card"),
    path("contacts/new/", views.EmergencyContactCreateView.as_view(), name="contact_create"),
    path(
        "contacts/<int:pk>/edit/",
        views.EmergencyContactUpdateView.as_view(),
        name="contact_update",
    ),
    # --- evacuation plans -------------------------------------------------
    path("plans/", views.EvacuationPlanListView.as_view(), name="plan_list"),
    path("plans/new/", views.EvacuationPlanCreateView.as_view(), name="plan_create"),
    path("plans/<int:pk>/", views.EvacuationPlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:pk>/edit/", views.EvacuationPlanUpdateView.as_view(), name="plan_update"),
    # --- equipment safety checks -----------------------------------------
    path("checks/", views.EquipmentCheckListView.as_view(), name="check_list"),
    path("checks/new/", views.EquipmentCheckCreateView.as_view(), name="check_create"),
    # --- weather warnings -------------------------------------------------
    path("warnings/", views.WeatherWarningListView.as_view(), name="warning_list"),
    path("warnings/new/", views.WeatherWarningCreateView.as_view(), name="warning_create"),
    path(
        "warnings/<int:pk>/edit/",
        views.WeatherWarningUpdateView.as_view(),
        name="warning_update",
    ),
    path(
        "warnings/<int:pk>/confirm/",
        views.WeatherWarningAcknowledgeView.as_view(),
        name="warning_acknowledge",
    ),
    path(
        "warnings/<int:pk>/dismiss/",
        views.WeatherWarningDismissView.as_view(),
        name="warning_dismiss",
    ),
    # --- student restrictions --------------------------------------------
    path("restrictions/", views.StudentRestrictionListView.as_view(), name="restriction_list"),
    path(
        "restrictions/new/",
        views.StudentRestrictionCreateView.as_view(),
        name="restriction_create",
    ),
    path(
        "restrictions/<int:pk>/edit/",
        views.StudentRestrictionUpdateView.as_view(),
        name="restriction_update",
    ),
    path(
        "restrictions/<int:pk>/lift/",
        views.StudentRestrictionLiftView.as_view(),
        name="restriction_lift",
    ),
    # --- embeddable panel -------------------------------------------------
    path("spots/<int:pk>/panel/", views.SpotSafetyPanelView.as_view(), name="spot_panel"),
]
