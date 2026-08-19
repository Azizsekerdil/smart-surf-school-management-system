"""Role → model mapping for every provider.

This is the single place where model choices live. Every call site asks for a
*role* (``"assistant"``, ``"vision"``, …) and this module resolves it to a
concrete model id, with an ordered fallback chain.

**These choices come from measured probes, not from documentation.** See
``docs/research/VERIFIED_API_PROBES.md`` for the run that produced them. Models
the account cannot actually invoke (HTTP 404) and models that timed out were
removed, which is why some obvious-looking names are absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .providers.base import AIRole


@dataclass(frozen=True)
class ModelSpec:
    """One model choice for one role."""

    model_id: str
    #: Ordered fallbacks tried when the primary fails.
    fallbacks: tuple[str, ...] = ()
    #: Measured round-trip for a trivial prompt, milliseconds. 0 = unmeasured.
    measured_latency_ms: int = 0
    #: Safe for a request a human is waiting on?
    interactive: bool = True
    notes: str = ""
    #: Extra request fields this model needs.
    request_extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NVIDIA NIM  (integrate.api.nvidia.com)
# ---------------------------------------------------------------------------
# Nemotron models are reasoning models. Without `thinking: false` they burn
# seconds on chain-of-thought and leak it into the answer. Measured: 535 ms with
# the flag, timeout without it.
NO_THINKING = {"chat_template_kwargs": {"thinking": False}}

NVIDIA_MODELS: dict[str, ModelSpec] = {
    AIRole.ASSISTANT.value: ModelSpec(
        "nvidia/nemotron-3-super-120b-a12b",
        fallbacks=("z-ai/glm-5.2", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        measured_latency_ms=731,
        notes="Verified working, tool-calling confirmed. 12B active MoE.",
        request_extras=NO_THINKING,
    ),
    AIRole.FAST.value: ModelSpec(
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        fallbacks=("nvidia/nvidia-nemotron-nano-9b-v2", "z-ai/glm-5.2"),
        measured_latency_ms=535,
        notes="Fastest verified model. Used for routing and classification.",
        request_extras=NO_THINKING,
    ),
    AIRole.CODE.value: ModelSpec(
        "nvidia/nemotron-3-super-120b-a12b",
        fallbacks=("poolside/laguna-xs-2.1", "z-ai/glm-5.2"),
        measured_latency_ms=731,
        notes=(
            "Codestral and Granite-code return 404 for this account; "
            "laguna-xs-2.1 returns 503 under load, so it is a fallback only."
        ),
        request_extras=NO_THINKING,
    ),
    AIRole.VISION.value: ModelSpec(
        "nvidia/nemotron-nano-12b-v2-vl",
        fallbacks=(),
        measured_latency_ms=24748,
        interactive=False,
        notes="Works, but cold start is ~25s. Run it in the background.",
    ),
    AIRole.ANALYTICS.value: ModelSpec(
        "openai/gpt-oss-120b",
        fallbacks=("nvidia/nemotron-3-super-120b-a12b",),
        measured_latency_ms=17242,
        interactive=False,
        notes="Strong reasoning but 17s+ even at low effort. Batch only.",
        request_extras={"reasoning_effort": "low"},
    ),
    AIRole.MATH.value: ModelSpec(
        "nvidia/nemotron-3-super-120b-a12b",
        fallbacks=("openai/gpt-oss-120b",),
        measured_latency_ms=731,
        request_extras=NO_THINKING,
    ),
    AIRole.TRANSLATE.value: ModelSpec(
        "nvidia/riva-translate-4b-instruct-v2",
        fallbacks=("nvidia/nemotron-3.5-lightning-30b-a3b",),
        measured_latency_ms=389,
        notes="Purpose-built translation model, very fast.",
    ),
    AIRole.EMBEDDING.value: ModelSpec(
        "nvidia/llama-nemotron-embed-1b-v2",
        fallbacks=("nvidia/nemotron-3-embed-1b", "nvidia/nv-embedqa-e5-v5"),
        measured_latency_ms=311,
        notes="2048 dimensions. bge-m3 returns 500, arctic-embed-l returns 404.",
    ),
    AIRole.GUARD.value: ModelSpec(
        "nvidia/llama-3.1-nemoguard-8b-topic-control",
        fallbacks=("nvidia/nemotron-3.5-lightning-30b-a3b",),
        notes="Topic-control guard for the AI terminal.",
    ),
}

#: Embedding dimensions per model. An index built with one cannot be queried
#: with another, so this is checked before every search.
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "nvidia/llama-nemotron-embed-1b-v2": 2048,
    "nvidia/nemotron-3-embed-1b": 2048,
    "nvidia/nv-embedqa-e5-v5": 1024,
    "text-embedding-nomic-embed-text-v1.5": 768,
}

#: Approximate USD per 1M tokens (input, output). NVIDIA's developer tier bills
#: in credits rather than dollars, so these are *estimates* used to give the
#: operator a sense of scale — the UI labels them as such.
NVIDIA_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "nvidia/nemotron-3-super-120b-a12b": (Decimal("0.30"), Decimal("1.20")),
    "nvidia/nemotron-3.5-lightning-30b-a3b": (Decimal("0.08"), Decimal("0.30")),
    "nvidia/nvidia-nemotron-nano-9b-v2": (Decimal("0.04"), Decimal("0.16")),
    "nvidia/nemotron-nano-12b-v2-vl": (Decimal("0.10"), Decimal("0.40")),
    "openai/gpt-oss-120b": (Decimal("0.15"), Decimal("0.60")),
    "z-ai/glm-5.2": (Decimal("0.20"), Decimal("0.80")),
    "poolside/laguna-xs-2.1": (Decimal("0.10"), Decimal("0.40")),
    "nvidia/riva-translate-4b-instruct-v2": (Decimal("0.03"), Decimal("0.12")),
    "nvidia/llama-nemotron-embed-1b-v2": (Decimal("0.02"), Decimal("0.00")),
    "nvidia/nemotron-3-embed-1b": (Decimal("0.02"), Decimal("0.00")),
    "nvidia/nv-embedqa-e5-v5": (Decimal("0.02"), Decimal("0.00")),
}


# ---------------------------------------------------------------------------
# LM Studio (local, free)
# ---------------------------------------------------------------------------
LM_STUDIO_MODELS: dict[str, ModelSpec] = {
    AIRole.ASSISTANT.value: ModelSpec("google/gemma-4-12b-qat", notes="General local assistant."),
    AIRole.FAST.value: ModelSpec(
        "google/gemma-4-12b-qat", fallbacks=("moondream-2b-2025-04-14",)
    ),
    AIRole.CODE.value: ModelSpec("google/gemma-4-12b-qat"),
    AIRole.VISION.value: ModelSpec(
        "qwen/qwen3-vl-8b",
        fallbacks=("moondream-2b-2025-04-14",),
        notes="Qwen3-VL for detail; Moondream as the lightweight option.",
    ),
    AIRole.MATH.value: ModelSpec(
        "qwen2.5-math-7b-instruct", notes="Dedicated maths model for statistics narration."
    ),
    AIRole.ANALYTICS.value: ModelSpec(
        "qwen2.5-math-7b-instruct", fallbacks=("google/gemma-4-12b-qat",)
    ),
    AIRole.TRANSLATE.value: ModelSpec("google/gemma-4-12b-qat"),
    AIRole.EMBEDDING.value: ModelSpec(
        "text-embedding-nomic-embed-text-v1.5",
        notes="768 dimensions. Makes fully offline RAG possible.",
    ),
    AIRole.GUARD.value: ModelSpec("google/gemma-4-12b-qat"),
}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
ANTHROPIC_MODELS: dict[str, ModelSpec] = {
    AIRole.ASSISTANT.value: ModelSpec("claude-sonnet-5", fallbacks=("claude-haiku-4-5-20251001",)),
    AIRole.FAST.value: ModelSpec("claude-haiku-4-5-20251001"),
    AIRole.CODE.value: ModelSpec("claude-sonnet-5"),
    AIRole.VISION.value: ModelSpec("claude-sonnet-5"),
    AIRole.ANALYTICS.value: ModelSpec("claude-sonnet-5"),
    AIRole.MATH.value: ModelSpec("claude-sonnet-5"),
    AIRole.TRANSLATE.value: ModelSpec("claude-haiku-4-5-20251001"),
    AIRole.GUARD.value: ModelSpec("claude-haiku-4-5-20251001"),
}

ANTHROPIC_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5-20251001": (Decimal("1.00"), Decimal("5.00")),
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
CATALOG: dict[str, dict[str, ModelSpec]] = {
    "nvidia": NVIDIA_MODELS,
    "lmstudio": LM_STUDIO_MODELS,
    "anthropic": ANTHROPIC_MODELS,
}


def spec_for(provider: str, role: str) -> ModelSpec | None:
    """Return the :class:`ModelSpec` for *provider* / *role*, if any."""
    return CATALOG.get(provider, {}).get(role)


def model_chain(provider: str, role: str) -> list[str]:
    """Primary model followed by its fallbacks, in order."""
    spec = spec_for(provider, role)
    if spec is None:
        return []
    return [spec.model_id, *spec.fallbacks]


def embedding_dimensions(model_id: str) -> int:
    """Known vector width for *model_id* (0 when unknown)."""
    return EMBEDDING_DIMENSIONS.get(model_id, 0)


def interactive_roles() -> set[str]:
    """Roles safe to run while a user waits."""
    return {
        role
        for models in CATALOG.values()
        for role, spec in models.items()
        if spec.interactive
    }
