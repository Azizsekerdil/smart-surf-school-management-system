"""Provider registry.

One place that knows which providers exist, how to build them from settings, and
which of them are usable right now. Adding a vendor is a two-line change here.
"""

from __future__ import annotations

import logging

from django.conf import settings

from .anthropic import AnthropicProvider
from .base import BaseAIProvider, HealthResult
from .lmstudio import LMStudioProvider
from .nvidia import NvidiaProvider
from .openai_compat import OpenAICompatibleProvider

logger = logging.getLogger("apps.ai")


class GenericOpenAIProvider(OpenAICompatibleProvider):
    """Any third-party OpenAI-compatible endpoint (vLLM, Ollama, Together, …)."""

    name = "openai_compat"
    label = "OpenAI-compatible endpoint"
    is_cloud = True
    supports_input_type = False


#: Registered provider classes, in the order they appear in the UI.
PROVIDER_CLASSES: dict[str, type[BaseAIProvider]] = {
    "lmstudio": LMStudioProvider,
    "nvidia": NvidiaProvider,
    "anthropic": AnthropicProvider,
    "openai_compat": GenericOpenAIProvider,
}

_instances: dict[str, BaseAIProvider] = {}


def _config_for(name: str) -> dict:
    """Settings for *name*, overlaid with any runtime override in the database."""
    config = dict((settings.AI.get("PROVIDERS") or {}).get(name, {}))

    # A database override lets an operator retarget a provider from the AI
    # Control Center without a restart. Secrets still come from the environment.
    try:
        from apps.ai.models import AIProviderConfig

        override = AIProviderConfig.objects.filter(provider=name).first()
        if override is not None:
            config.update(override.as_config_overlay())
    except Exception:  # noqa: BLE001, S110 - table may not exist yet (pre-migration); deliberate best-effort cleanup; a failure here must not break the caller  # nosec B110
        pass

    return config


def get_provider(name: str, *, fresh: bool = False) -> BaseAIProvider:
    """Return the provider instance called *name*.

    Raises :class:`KeyError` for an unknown name — callers should use
    :func:`available_providers` or catch it.
    """
    if fresh or name not in _instances:
        provider_class = PROVIDER_CLASSES.get(name)
        if provider_class is None:
            raise KeyError(f"Unknown AI provider: {name!r}")
        _instances[name] = provider_class(_config_for(name))
    return _instances[name]


def reset_providers() -> None:
    """Drop cached instances (called after a config change or in tests)."""
    _instances.clear()


def all_providers(*, fresh: bool = False) -> list[BaseAIProvider]:
    return [get_provider(name, fresh=fresh) for name in PROVIDER_CLASSES]


def enabled_providers(*, fresh: bool = False) -> list[BaseAIProvider]:
    """Providers that are configured — not necessarily reachable."""
    return [p for p in all_providers(fresh=fresh) if p.enabled]


def local_providers() -> list[BaseAIProvider]:
    return [p for p in enabled_providers() if not p.is_cloud]


def cloud_providers() -> list[BaseAIProvider]:
    return [p for p in enabled_providers() if p.is_cloud]


def health_report(*, fresh: bool = True) -> dict[str, HealthResult]:
    """Probe every registered provider. Never raises."""
    report: dict[str, HealthResult] = {}
    for provider in all_providers(fresh=fresh):
        try:
            report[provider.name] = provider.health_check()
        except Exception as exc:  # noqa: BLE001 - a probe must not break the page
            logger.warning("Health check crashed for %s: %s", provider.name, exc)
            report[provider.name] = HealthResult(
                ok=False, message=f"Health check failed: {type(exc).__name__}"
            )
    return report
