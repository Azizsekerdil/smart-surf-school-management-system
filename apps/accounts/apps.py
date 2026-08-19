from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = _("Users & Roles")

    def ready(self) -> None:  # pragma: no cover - signal wiring
        from . import signals  # noqa: F401
