from __future__ import annotations

from django.urls import path

from . import views

app_name = "onboarding"

urlpatterns = [
    path("", views.OnboardingWizardView.as_view(), name="start"),
    path("banner/", views.banner, name="banner"),
    path("banner/dismiss/", views.dismiss_banner, name="dismiss_banner"),
    path("finish/", views.finish, name="finish"),
    path("skip/", views.skip, name="skip"),
    path("reopen/", views.reopen, name="reopen"),
    path("<slug:step>/", views.OnboardingWizardView.as_view(), name="step"),
]
