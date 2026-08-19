"""Provider abstraction.

Every AI backend — local LM Studio, NVIDIA NIM, Anthropic, any OpenAI-compatible
endpoint — is reached through this one interface, so the rest of the application
never knows or cares which vendor answered.

Design decisions worth knowing
------------------------------
* **Roles, not model ids.** Call sites ask for ``"assistant"`` or ``"vision"``;
  the provider maps that to a concrete model. Re-tuning the model line-up is a
  one-file change.
* **Reasoning is separate.** Reasoning models return chain-of-thought in a
  distinct field. It is captured in :attr:`ChatResponse.reasoning` and never
  concatenated into the answer.
* **Failure is a value, not an exception.** A provider that is down returns a
  :class:`ChatResponse` with ``ok=False`` and a human-readable ``error``. The
  application must stay usable when every AI backend is offline.
"""

from __future__ import annotations

import abc
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class AIRole(str, Enum):
    """What the caller needs, independent of any vendor's model names."""

    ASSISTANT = "assistant"          # general business Q&A
    FAST = "fast"                    # routing, classification, short answers
    CODE = "code"                    # code generation and review
    VISION = "vision"                # images (equipment damage, photos)
    ANALYTICS = "analytics"          # statistics narration (batch, slow OK)
    MATH = "math"                    # arithmetic-heavy reasoning
    TRANSLATE = "translate"          # TR <-> EN
    EMBEDDING = "embedding"          # RAG vectors
    GUARD = "guard"                  # prompt-injection / content safety


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ChatMessage:
    role: str
    content: str
    #: Optional image data URIs for vision models.
    images: list[str] = field(default_factory=list)
    #: Tool-call plumbing (OpenAI shape).
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict:
        if self.images:
            parts: list[dict] = [{"type": "text", "text": self.content}]
            parts += [{"type": "image_url", "image_url": {"url": uri}} for uri in self.images]
            payload: dict[str, Any] = {"role": self.role, "content": parts}
        else:
            payload = {"role": self.role, "content": self.content}

        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ChatResponse:
    """The uniform result of a chat call."""

    ok: bool
    content: str = ""
    #: Chain-of-thought, when the model exposes it. Shown collapsed, never mixed
    #: into ``content``.
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    estimated_cost: Decimal = Decimal("0.0000")
    finish_reason: str = ""
    error: str = ""
    #: True when the request was served by a fallback after the primary failed.
    used_fallback: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class EmbeddingResponse:
    ok: bool
    vectors: list[list[float]] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    dimensions: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    error: str = ""


@dataclass
class HealthResult:
    ok: bool
    message: str = ""
    models: list[str] = field(default_factory=list)
    latency_ms: int = 0


class ProviderError(Exception):
    """Raised internally by a provider; converted to ``ok=False`` at the boundary."""


class BaseAIProvider(abc.ABC):
    """Interface every provider implements."""

    #: Stable identifier used in settings, the database and the UI.
    name: str = "base"
    #: Human-readable label.
    label: str = "Base provider"
    #: True when calls cost real money (drives the cost dashboard and routing).
    is_cloud: bool = False
    #: True when the provider can accept images.
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False
    supports_embeddings: bool = False

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # -- capability ---------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.config.get("ENABLED", False))

    @property
    def base_url(self) -> str:
        return (self.config.get("BASE_URL") or "").rstrip("/")

    @property
    def api_key(self) -> str:
        return self.config.get("API_KEY") or ""

    def model_for(self, role: str) -> str:
        """Return the model id this provider uses for *role*."""
        models = self.config.get("MODELS") or {}
        return models.get(role) or models.get("general") or ""

    # -- operations ---------------------------------------------------------
    @abc.abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        role: str = AIRole.ASSISTANT.value,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        timeout: int | None = None,
        **kwargs,
    ) -> ChatResponse:
        """Single-turn completion. Must never raise — return ``ok=False``."""

    def stream_chat(self, messages: list[ChatMessage], **kwargs) -> Iterator[str]:
        """Yield content deltas. Default: emit the non-streaming answer once."""
        response = self.chat(messages, **kwargs)
        if response.ok:
            yield response.content
        else:
            yield ""

    def embed(self, texts: list[str], *, input_type: str = "passage", **kwargs) -> EmbeddingResponse:
        return EmbeddingResponse(
            ok=False, provider=self.name, error=f"{self.label} does not support embeddings."
        )

    @abc.abstractmethod
    def health_check(self) -> HealthResult:
        """Cheap liveness probe. Must never raise."""

    def list_models(self) -> list[str]:
        return []

    # -- costing ------------------------------------------------------------
    #: USD per 1M tokens: {model_id: (input, output)}. Empty means free/local.
    PRICING: dict[str, tuple[Decimal, Decimal]] = {}
    DEFAULT_PRICING: tuple[Decimal, Decimal] = (Decimal("0"), Decimal("0"))

    def estimate_cost(self, model: str, usage: TokenUsage) -> Decimal:
        """Estimated USD cost. Local providers are free, so this returns 0."""
        if not self.is_cloud:
            return Decimal("0.0000")
        rate_in, rate_out = self.PRICING.get(model, self.DEFAULT_PRICING)
        cost = (
            Decimal(usage.prompt_tokens) * rate_in + Decimal(usage.completion_tokens) * rate_out
        ) / Decimal("1000000")
        return cost.quantize(Decimal("0.000001"))

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.__class__.__name__} name={self.name!r} enabled={self.enabled}>"
