"""Smart AI router.

Chooses *which provider and model* answers a request, then executes it with an
ordered fallback chain so a single outage never surfaces as an error.

Routing policy
--------------
``local_only``  never leaves the machine — free, private, works offline.
``cloud_only``  always uses a cloud provider (best quality).
``auto``        cost-aware default:

    simple / classification / translation  -> local first
    vision                                 -> local vision, cloud as fallback
    maths & statistics narration           -> local maths model first
    hard reasoning, long context, tools    -> cloud first, local as fallback

The router is also the single place that records token usage and cost, so no
call can escape the AI Usage dashboard.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.conf import settings

from .models_catalog import model_chain
from .providers.base import AIRole, BaseAIProvider, ChatMessage, ChatResponse, EmbeddingResponse
from .providers.registry import enabled_providers, get_provider

logger = logging.getLogger("apps.ai")


class RoutingMode:
    AUTO = "auto"
    LOCAL_ONLY = "local_only"
    CLOUD_ONLY = "cloud_only"

    CHOICES = (
        (AUTO, "Automatic"),
        (LOCAL_ONLY, "Local only"),
        (CLOUD_ONLY, "Cloud only"),
    )


@dataclass
class RoutingDecision:
    provider_name: str
    model: str
    role: str
    reason: str
    fallbacks: list[tuple[str, str]]  # [(provider, model), ...]


# ---------------------------------------------------------------------------
# Complexity heuristics
# ---------------------------------------------------------------------------
_HARD_SIGNALS = (
    "analiz", "analyz", "analys", "compare", "karşılaştır", "karsilastir",
    "trend", "forecast", "tahmin", "why", "neden", "explain", "açıkla", "acikla",
    "strategy", "strateji", "optimi", "recommend", "öner", "oner", "plan",
    "correlat", "regression", "predict",
)
_SIMPLE_SIGNALS = (
    "kaç", "kac", "how many", "list", "listele", "göster", "goster", "show",
    "when", "ne zaman", "who", "kim", "what is", "nedir", "count", "toplam",
)


def estimate_complexity(prompt: str, *, has_tools: bool = False, history_length: int = 0) -> str:
    """Return ``"simple"``, ``"moderate"`` or ``"hard"``.

    Deliberately a heuristic, not a model call: routing must not itself cost a
    round-trip.
    """
    text = (prompt or "").lower()
    words = len(re.findall(r"\w+", text))

    if has_tools or history_length > 6:
        return "hard"
    if words > 120:
        return "hard"
    if any(signal in text for signal in _HARD_SIGNALS):
        return "hard"
    if words <= 25 and any(signal in text for signal in _SIMPLE_SIGNALS):
        return "simple"
    if words <= 40:
        return "moderate"
    return "hard"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class AIRouter:
    """Selects providers and executes requests with fallback."""

    def __init__(self, mode: str | None = None):
        self.mode = mode or settings.AI.get("ROUTING_MODE", RoutingMode.AUTO)

    # -- selection ----------------------------------------------------------
    def _candidate_providers(self, role: str, complexity: str) -> list[BaseAIProvider]:
        providers = [p for p in enabled_providers() if self._supports(p, role)]
        if not providers:
            return []

        local = [p for p in providers if not p.is_cloud]
        cloud = [p for p in providers if p.is_cloud]

        if self.mode == RoutingMode.LOCAL_ONLY:
            return local
        if self.mode == RoutingMode.CLOUD_ONLY:
            return cloud or local  # never leave the user with nothing

        # --- auto ---------------------------------------------------------
        prefer_local_roles = {
            AIRole.FAST.value,
            AIRole.TRANSLATE.value,
            AIRole.MATH.value,
            AIRole.EMBEDDING.value,
            AIRole.GUARD.value,
        }
        if role in prefer_local_roles or complexity == "simple":
            return local + cloud
        if role == AIRole.VISION.value:
            return local + cloud
        # Hard reasoning, analytics and tool use benefit most from the cloud.
        return cloud + local

    @staticmethod
    def _supports(provider: BaseAIProvider, role: str) -> bool:
        if role == AIRole.EMBEDDING.value:
            return provider.supports_embeddings
        if role == AIRole.VISION.value:
            return provider.supports_vision
        return True

    def decide(
        self, role: str, prompt: str = "", *, has_tools: bool = False, history_length: int = 0
    ) -> RoutingDecision | None:
        complexity = estimate_complexity(
            prompt, has_tools=has_tools, history_length=history_length
        )
        candidates = self._candidate_providers(role, complexity)
        if not candidates:
            return None

        chain: list[tuple[str, str]] = []
        for provider in candidates:
            models = model_chain(provider.name, role) or [provider.model_for(role)]
            for model in models:
                if model:
                    chain.append((provider.name, model))

        if not chain:
            return None

        primary_provider, primary_model = chain[0]
        reason = (
            f"mode={self.mode}, role={role}, complexity={complexity} → "
            f"{primary_provider}:{primary_model}"
        )
        return RoutingDecision(
            provider_name=primary_provider,
            model=primary_model,
            role=role,
            reason=reason,
            fallbacks=chain[1:],
        )

    # -- execution ----------------------------------------------------------
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        role: str = AIRole.ASSISTANT.value,
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: int | None = None,
        max_attempts: int = 3,
        **kwargs,
    ) -> ChatResponse:
        """Run a chat request, walking the fallback chain on failure."""
        prompt = messages[-1].content if messages else ""
        decision = self.decide(
            role, prompt, has_tools=bool(tools), history_length=len(messages)
        )

        if decision is None:
            return ChatResponse(
                ok=False,
                error=(
                    "No AI provider is available. Start LM Studio for offline use, "
                    "or configure a cloud provider in the AI Control Center."
                ),
            )

        attempts: list[tuple[str, str]] = [
            (decision.provider_name, decision.model),
            *decision.fallbacks,
        ][:max_attempts]

        last_error = ""
        for index, (provider_name, model) in enumerate(attempts):
            try:
                provider = get_provider(provider_name)
            except KeyError:
                continue

            response = provider.chat(
                messages,
                role=role,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens or settings.AI.get("MAX_TOKENS", 2048),
                tools=tools if provider.supports_tools else None,
                timeout=timeout or settings.AI.get("REQUEST_TIMEOUT", 120),
                **kwargs,
            )
            if response.ok:
                response.used_fallback = index > 0
                if response.used_fallback:
                    logger.info(
                        "AI fallback used", extra={"provider": provider_name, "model": model}
                    )
                return response

            last_error = response.error
            logger.warning(
                "AI attempt failed",
                extra={"provider": provider_name, "model": model, "reason": response.error},
            )

        return ChatResponse(
            ok=False,
            error=last_error or "Every configured AI provider failed.",
            provider=decision.provider_name,
            model=decision.model,
        )

    def embed(
        self, texts: list[str], *, input_type: str = "passage", max_attempts: int = 3
    ) -> EmbeddingResponse:
        decision = self.decide(AIRole.EMBEDDING.value)
        if decision is None:
            return EmbeddingResponse(
                ok=False,
                error=(
                    "No embedding provider available. LM Studio with "
                    "text-embedding-nomic-embed-text-v1.5 gives you offline RAG."
                ),
            )

        attempts = [(decision.provider_name, decision.model), *decision.fallbacks][:max_attempts]
        last_error = ""
        for provider_name, model in attempts:
            try:
                provider = get_provider(provider_name)
            except KeyError:
                continue
            response = provider.embed(texts, input_type=input_type, model=model)
            if response.ok:
                return response
            last_error = response.error

        return EmbeddingResponse(ok=False, error=last_error or "All embedding providers failed.")


#: Shared instance for callers that do not need a custom mode.
def get_router(mode: str | None = None) -> AIRouter:
    return AIRouter(mode)
