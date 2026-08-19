from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"
    label = "ai"
    verbose_name = _("Artificial Intelligence")

    def ready(self) -> None:  # pragma: no cover - registration side effect
        # Importing the module runs the @register decorators that populate the
        # tool registry the assistant offers to the model.
        from . import tools  # noqa: F401
