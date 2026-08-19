"""Project-wide pytest fixtures.

Everything here exists to stop one test leaking into the next.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_caches():
    """Clear every cache before and after each test.

    Django rolls the database back between tests but leaves the cache alone, so
    anything memoised in one test is still there in the next. That produced a
    real false failure: the maintenance prediction board caches its ranked list,
    so a test asserting two items saw the single item an earlier test had left
    behind — and it passed in isolation, which is the worst kind of flake.

    Clearing on the way in and on the way out means a test never inherits state
    and never leaves any.
    """
    from django.core.cache import caches

    def clear_all() -> None:
        for alias in caches:
            try:
                caches[alias].clear()
            except Exception:  # noqa: BLE001, S110 - a cache backend must not break tests; deliberate best-effort cleanup; a failure here must not break the caller
                pass

    clear_all()
    yield
    clear_all()


@pytest.fixture(autouse=True)
def _reset_ai_providers():
    """Drop cached AI provider instances between tests.

    The registry memoises a provider per name, including the settings it was
    built from. A test that overrides those settings would otherwise be answered
    by an instance another test built.
    """
    try:
        from apps.ai.providers.registry import reset_providers
    except ImportError:  # pragma: no cover - AI app absent
        yield
        return

    reset_providers()
    yield
    reset_providers()
