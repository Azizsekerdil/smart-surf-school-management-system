from __future__ import annotations

from django.urls import path

from . import views

app_name = "instructors"

urlpatterns = [
    # --- team ---------------------------------------------------------------
    path("", views.InstructorListView.as_view(), name="list"),
    path("new/", views.InstructorCreateView.as_view(), name="create"),
    path("export/", views.InstructorExportView.as_view(), name="export"),
    path("who-is-free/", views.AvailabilityBoardView.as_view(), name="availability_board"),
    # --- absence across the whole team --------------------------------------
    path("time-off/", views.TimeOffListView.as_view(), name="timeoff_list"),
    path("time-off/<int:pk>/approve/", views.TimeOffApproveView.as_view(), name="timeoff_approve"),
    path("time-off/<int:pk>/cancel/", views.TimeOffCancelView.as_view(), name="timeoff_cancel"),
    # --- certifications ------------------------------------------------------
    path(
        "certifications/<int:pk>/edit/",
        views.CertificationUpdateView.as_view(),
        name="certification_update",
    ),
    path(
        "certifications/<int:pk>/verify/",
        views.CertificationVerifyView.as_view(),
        name="certification_verify",
    ),
    path(
        "certifications/<int:pk>/delete/",
        views.CertificationDeleteView.as_view(),
        name="certification_delete",
    ),
    # --- availability slots --------------------------------------------------
    path(
        "availability/<int:pk>/toggle/",
        views.AvailabilitySlotToggleView.as_view(),
        name="availability_slot_toggle",
    ),
    path(
        "availability/<int:pk>/delete/",
        views.AvailabilitySlotDeleteView.as_view(),
        name="availability_slot_delete",
    ),
    # --- one instructor ------------------------------------------------------
    path("<int:pk>/", views.InstructorDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.InstructorUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.InstructorDeleteView.as_view(), name="delete"),
    path(
        "<int:pk>/booking-availability/",
        views.InstructorBookingToggleView.as_view(),
        name="toggle_booking",
    ),
    path(
        "<int:pk>/certifications/new/",
        views.CertificationCreateView.as_view(),
        name="certification_create",
    ),
    path(
        "<int:pk>/availability/",
        views.AvailabilityEditorView.as_view(),
        name="availability_editor",
    ),
    path(
        "<int:pk>/availability/add/",
        views.AvailabilitySlotCreateView.as_view(),
        name="availability_slot_create",
    ),
    path("<int:pk>/time-off/new/", views.TimeOffCreateView.as_view(), name="timeoff_create"),
    path("<int:pk>/reviews/new/", views.PerformanceReviewCreateView.as_view(), name="review_create"),
]
