"""LM Studio — local, free, offline-capable provider.

LM Studio exposes an OpenAI-compatible server (default ``http://localhost:1234/v1``).
Because it runs on the operator's own machine it costs nothing, keeps school data
on-premises, and keeps the assistant working with no internet connection — which
is the whole point of the offline-first requirement.
"""

from __future__ import annotations

from ..models_catalog import LM_STUDIO_MODELS
from .base import HealthResult
from .openai_compat import OpenAICompatibleProvider


class LMStudioProvider(OpenAICompatibleProvider):
    name = "lmstudio"
    label = "LM Studio (local)"
    is_cloud = False
    supports_vision = True
    supports_tools = True
    supports_streaming = True
    supports_embeddings = True
    #: LM Studio rejects the NVIDIA-specific ``input_type`` field.
    supports_input_type = False
    default_timeout = 180  # local models on CPU can be slow but are free

    def model_for(self, role: str) -> str:
        # An explicit setting always wins, so an operator can point a role at a
        # model they just downloaded without touching code.
        configured = (self.config.get("MODELS") or {}).get(role)
        if configured:
            return configured
        spec = LM_STUDIO_MODELS.get(role)
        return spec.model_id if spec else (self.config.get("MODELS") or {}).get("general", "")

    @property
    def enabled(self) -> bool:
        # The local provider is always considered "configured"; whether it is
        # actually running is what health_check() reports.
        return bool(self.base_url)

    def health_check(self) -> HealthResult:
        result = super().health_check()
        if not result.ok and "unreachable" in result.message.lower():
            result.message = (
                "LM Studio is not running. Start it and enable the local server "
                "(Developer → Start Server) on "
                f"{self.base_url or 'http://localhost:1234/v1'}."
            )
        return result
