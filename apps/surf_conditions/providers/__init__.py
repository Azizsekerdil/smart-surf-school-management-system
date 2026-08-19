"""Pluggable surf & weather data providers.

Adding a data source means writing one class that implements
:class:`~apps.surf_conditions.providers.base.BaseSurfProvider` and registering
it in :mod:`apps.surf_conditions.providers.registry`. Nothing else changes.

Two rules hold for every provider in here:

1. **It never raises.** A network outage returns ``None`` / ``[]`` and a log
   line. A surf school that loses its internet connection still opens the app.
2. **It never invents a value.** A field the source did not supply stays
   ``None`` all the way to the screen, where it renders as "—". A guessed wave
   height is a safety hazard, not a convenience.
"""

from .base import BaseSurfProvider, ConditionSnapshot
from .registry import available_providers, get_surf_provider, health_report

__all__ = [
    "BaseSurfProvider",
    "ConditionSnapshot",
    "available_providers",
    "get_surf_provider",
    "health_report",
]
