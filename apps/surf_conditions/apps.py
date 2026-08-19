from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SurfConditionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.surf_conditions"
    verbose_name = _("Surf Conditions")
