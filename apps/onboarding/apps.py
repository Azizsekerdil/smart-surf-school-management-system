from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.onboarding"
    verbose_name = _("Onboarding")
