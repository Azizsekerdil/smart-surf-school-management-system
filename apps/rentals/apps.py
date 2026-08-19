from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RentalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rentals"
    verbose_name = _("Rentals")
