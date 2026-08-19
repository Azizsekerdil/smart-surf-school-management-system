"""The dashboard deliberately defines no models.

Everything on the screen is a live read of another module's system of record.
Caching a copy here would create a second, silently stale source of truth for
numbers that staff act on — how many students are in the water, what is owed,
which board is broken — so the composition happens per request in
:mod:`apps.dashboard.selectors` and :mod:`apps.dashboard.services` instead.
"""

from __future__ import annotations
