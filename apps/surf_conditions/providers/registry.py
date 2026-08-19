"""Which surf data provider answers, and why.

One place knows the provider line-up, how to build each one from
``settings.SURF``, and which of them is appropriate for the way this deployment
is licensed. Adding a source is a one-line change to :data:`PROVIDER_CLASSES`.

The licence rule, spelled out
-----------------------------
Open-Meteo's *data* is CC BY 4.0 and may be used commercially, but their *free
hosted API* is for non-commercial use. A school running this software as a
business therefore has two lawful options: buy an Open-Meteo key, or use met.no.
When ``SURF["COMMERCIAL_MODE"]`` is on and no key is present, this module picks
met.no and logs the reason, rather than quietly leaving the school in breach.
That downgrade costs the wave model, which is why the log line and the UI both
say what happened.
"""

from __future__ import annotations

import logging

from django.conf import settings

from .base import BaseSurfProvider
from .metno import MetNoProvider
from .open_meteo import OpenMeteoProvider

logger = logging.getLogger("apps.surf_conditions")

#: Registered provider classes, in the order they appear in the UI.
PROVIDER_CLASSES: dict[str, type[BaseSurfProvider]] = {
    OpenMeteoProvider.name: OpenMeteoProvider,
    MetNoProvider.name: MetNoProvider,
}

#: Spellings people actually type in ``.env`` mapped onto the canonical names.
PROVIDER_ALIASES: dict[str, str] = {
    "open_meteo": OpenMeteoProvider.name,
    "openmeteo": OpenMeteoProvider.name,
    "open-meteo": OpenMeteoProvider.name,
    "met.no": MetNoProvider.name,
    "met_no": MetNoProvider.name,
    "metno": MetNoProvider.name,
}

DEFAULT_PROVIDER = OpenMeteoProvider.name

_instances: dict[str, BaseSurfProvider] = {}


def _surf_settings() -> dict:
    return dict(getattr(settings, "SURF", {}) or {})


def canonical_name(name: str | None) -> str:
    """Normalise a configured provider name; unknown names fall back to default."""
    if not name:
        return DEFAULT_PROVIDER
    key = str(name).strip().lower()
    key = PROVIDER_ALIASES.get(key, key)
    if key not in PROVIDER_CLASSES:
        logger.warning(
            "Unknown surf provider %r configured; falling back to %s.", name, DEFAULT_PROVIDER
        )
        return DEFAULT_PROVIDER
    return key


def commercial_downgrade_reason() -> str | None:
    """Why the configured provider was overridden, or ``None`` if it was not.

    Exposed so the UI can explain a missing wave forecast instead of just
    showing empty cells.
    """
    config = _surf_settings()
    if not config.get("COMMERCIAL_MODE"):
        return None
    if (config.get("OPEN_METEO_API_KEY") or "").strip():
        return None
    if canonical_name(config.get("PROVIDER")) != OpenMeteoProvider.name:
        return None
    return (
        "Commercial mode is on and no Open-Meteo API key is configured. "
        "Open-Meteo's free hosted service is licensed for non-commercial use, "
        "so met.no is used instead. met.no has no wave model, so surf scores "
        "cannot be computed until an Open-Meteo key is added."
    )


def resolve_provider_name(name: str | None = None) -> str:
    """The provider that should answer, after the licence rule is applied."""
    if name:
        return canonical_name(name)

    config = _surf_settings()
    configured = canonical_name(config.get("PROVIDER"))

    reason = commercial_downgrade_reason()
    if reason is not None:
        logger.info("Surf provider switched from open-meteo to metno. %s", reason)
        return MetNoProvider.name
    return configured


def get_surf_provider(name: str | None = None, *, fresh: bool = False) -> BaseSurfProvider:
    """Return the provider instance to use.

    With no argument it reads ``settings.SURF["PROVIDER"]`` and applies the
    commercial-licence rule. Passing a name bypasses the rule, which is what the
    health screen and the tests want.
    """
    resolved = resolve_provider_name(name)
    if fresh or resolved not in _instances:
        _instances[resolved] = PROVIDER_CLASSES[resolved](_surf_settings())
    return _instances[resolved]


def reset_providers() -> None:
    """Drop cached instances (after a settings change, and between tests)."""
    _instances.clear()


def available_providers(*, fresh: bool = False) -> list[BaseSurfProvider]:
    """Every registered provider, built from the current settings."""
    if fresh:
        reset_providers()
    return [get_surf_provider(name) for name in PROVIDER_CLASSES]


def health_report(*, fresh: bool = True) -> dict[str, dict]:
    """Probe every provider. Never raises.

    Returns ``{name: {"ok", "message", "label", "attribution", "is_active",
    "provides_marine_data", "requires_api_key"}}``.
    """
    active = resolve_provider_name()
    report: dict[str, dict] = {}
    for provider in available_providers(fresh=fresh):
        try:
            ok, message = provider.health_check()
        except Exception as exc:  # noqa: BLE001 - a probe must not break the page
            logger.warning("Health check crashed for %s: %s", provider.name, exc)
            ok, message = False, f"Health check failed: {type(exc).__name__}"
        report[provider.name] = {
            "ok": bool(ok),
            "message": message,
            "label": provider.label,
            "attribution": provider.attribution,
            "is_active": provider.name == active,
            "provides_marine_data": provider.provides_marine_data,
            "requires_api_key": provider.requires_api_key,
            "is_configured": provider.is_configured,
        }
    return report
