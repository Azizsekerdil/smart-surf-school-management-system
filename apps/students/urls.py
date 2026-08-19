from __future__ import annotations

from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("", views.StudentListView.as_view(), name="list"),
    path("new/", views.StudentCreateView.as_view(), name="create"),
    path("register/", views.StudentRegisterView.as_view(), name="register"),
    path("assessments/", views.AssessmentListView.as_view(), name="assessment_list"),
    path("<int:pk>/", views.StudentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.StudentUpdateView.as_view(), name="update"),
    path(
        "<int:pk>/lessons/",
        views.StudentLessonHistoryView.as_view(),
        name="lesson_history",
    ),
    path(
        "<int:pk>/assess/",
        views.SkillAssessmentCreateView.as_view(),
        name="assessment_create",
    ),
    path(
        "<int:pk>/toggle-active/",
        views.StudentToggleActiveView.as_view(),
        name="toggle_active",
    ),
]
