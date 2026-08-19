from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HelpCenterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.help_center"
    verbose_name = _("Help Center")
