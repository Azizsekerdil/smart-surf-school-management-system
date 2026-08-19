from __future__ import annotations

from django.urls import path

from . import views

app_name = "lessons"

urlpatterns = [
    # --- timetable --------------------------------------------------------
    path("", views.LessonListView.as_view(), name="list"),
    path("day/", views.LessonDayView.as_view(), name="day"),
    path("new/", views.LessonCreateView.as_view(), name="create"),
    path("check-conflicts/", views.LessonConflictCheckView.as_view(), name="check_conflicts"),
    # --- lesson catalogue -------------------------------------------------
    path("types/", views.LessonTypeListView.as_view(), name="type_list"),
    path("types/new/", views.LessonTypeCreateView.as_view(), name="type_create"),
    path("types/<int:pk>/edit/", views.LessonTypeUpdateView.as_view(), name="type_update"),
    # --- one lesson -------------------------------------------------------
    path("<int:pk>/", views.LessonDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.LessonUpdateView.as_view(), name="update"),
    path("<int:pk>/cancel/", views.LessonCancelView.as_view(), name="cancel"),
    path("<int:pk>/complete/", views.LessonCompleteView.as_view(), name="complete"),
    path("<int:pk>/safety-check/", views.LessonSafetyCheckView.as_view(), name="safety_check"),
    path("<int:pk>/conditions/", views.LessonConditionsView.as_view(), name="capture_conditions"),
    # --- roster (HTMX) ----------------------------------------------------
    path("<int:pk>/roster/add/", views.AttendanceAddView.as_view(), name="attendance_add"),
    path(
        "<int:pk>/roster/<int:attendance_pk>/remove/",
        views.AttendanceRemoveView.as_view(),
        name="attendance_remove",
    ),
    path(
        "<int:pk>/roster/<int:attendance_pk>/check-in/",
        views.AttendanceCheckInView.as_view(),
        name="attendance_check_in",
    ),
    path(
        "<int:pk>/roster/<int:attendance_pk>/no-show/",
        views.AttendanceNoShowView.as_view(),
        name="attendance_no_show",
    ),
    path(
        "<int:pk>/roster/<int:attendance_pk>/equipment/",
        views.AttendanceEquipmentView.as_view(),
        name="attendance_equipment",
    ),
    path(
        "<int:pk>/roster/<int:attendance_pk>/feedback/",
        views.AttendanceFeedbackView.as_view(),
        name="attendance_feedback",
    ),
]
