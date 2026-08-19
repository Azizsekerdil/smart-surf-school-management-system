from __future__ import annotations

from django.urls import path

from . import views

app_name = "training"

urlpatterns = [
    path("", views.TrainingHomeView.as_view(), name="home"),
    path("progress/", views.MyProgressView.as_view(), name="progress"),
    path("<int:pk>/", views.TrainingCourseDetailView.as_view(), name="course"),
    path("<int:pk>/start/", views.CourseStartView.as_view(), name="course_start"),
    path("<int:pk>/reset/", views.CourseResetView.as_view(), name="course_reset"),
    path("step/<int:pk>/", views.TrainingStepView.as_view(), name="step"),
    path("step/<int:pk>/complete/", views.StepCompleteView.as_view(), name="step_complete"),
]
