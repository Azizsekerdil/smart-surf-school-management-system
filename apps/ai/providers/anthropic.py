"""Anthropic Claude provider.

Anthropic's Messages API is not OpenAI-shaped, so this provider translates in
both directions rather than reusing the shared transport:

* the system prompt is a top-level ``system`` field, not a message
* content is a list of typed blocks
* tool calls come back as ``tool_use`` blocks
* authentication uses ``x-api-key`` plus an ``anthropic-version`` header
"""

from __future__ import annotations

import time
from decimal import Decimal

import httpx

from ..models_catalog import ANTHROPIC_MODELS, ANTHROPIC_PRICING
from .base import (
    AIRole,
    BaseAIProvider,
    ChatMessage,
    ChatResponse,
    HealthResult,
    TokenUsage,
)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseAIProvider):
    name = "anthropic"
    label = "Anthropic Claude"
    is_cloud = True
    supports_vision = True
    supports_tools = True
    supports_streaming = True
    supports_embeddings = False

    PRICING = ANTHROPIC_PRICING
    DEFAULT_PRICING = (Decimal("3.00"), Decimal("15.00"))

    def model_for(self, role: str) -> str:
        configured = (self.config.get("MODELS") or {}).get(role)
        if configured:
            return configured
        spec = ANTHROPIC_MODELS.get(role)
        return spec.model_id if spec else (self.config.get("MODELS") or {}).get("general", "")

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _split_messages(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
        """Extract the system prompt and convert the rest to Anthropic blocks."""
        system_parts: list[str] = []
        converted: list[dict] = []

        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue

            if message.role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": message.content,
                            }
                        ],
                    }
                )
                continue

            blocks: list[dict] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for uri in message.images:
                if uri.startswith("data:"):
                    header, _, data = uri.partition(",")
                    media_type = header.split(";")[0].removeprefix("data:") or "image/jpeg"
                    blocks.append(
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        }
                    )
                else:
                    blocks.append({"type": "image", "source": {"type": "url", "url": uri}})

            converted.append({"role": message.role, "content": blocks or [{"type": "text", "text": ""}]})

        return "\n\n".join(p for p in system_parts if p), converted

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
        if not self.api_key:
            return ChatResponse(
                ok=False,
                provider=self.name,
                error="No ANTHROPIC_API_KEY configured. Add it to .env to enable Claude.",
            )

        model = model or self.model_for(role)
        system_prompt, converted = self._split_messages(messages)

        payload: dict = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or 2048,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            # Translate OpenAI tool schemas into Anthropic's shape.
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object"}),
                }
                for t in tools
                if t.get("type") == "function"
            ]

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout or 120) as client:
                response = client.post(
                    f"{self.base_url}/messages", json=payload, headers=self._headers()
                )
        except httpx.TimeoutException:
            return ChatResponse(
                ok=False, provider=self.name, model=model, error="Claude timed out."
            )
        except httpx.HTTPError as exc:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                error=f"Claude unreachable ({type(exc).__name__}).",
            )

        latency = self._elapsed_ms(started)

        if response.status_code != 200:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = response.text[:200]
            message = {
                401: "Authentication failed — check ANTHROPIC_API_KEY.",
                429: "Rate limit or quota exceeded.",
                529: "Claude is overloaded; try again shortly.",
            }.get(response.status_code, f"HTTP {response.status_code}: {detail}")
            return ChatResponse(
                ok=False, provider=self.name, model=model, latency_ms=latency, error=message
            )

        body = response.json()
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in body.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": __import__("json").dumps(block.get("input") or {}),
                        },
                    }
                )

        usage_body = body.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_body.get("input_tokens") or 0),
            completion_tokens=int(usage_body.get("output_tokens") or 0),
        )

        return ChatResponse(
            ok=True,
            content="".join(text_parts).strip(),
            tool_calls=tool_calls,
            provider=self.name,
            model=body.get("model") or model,
            usage=usage,
            latency_ms=latency,
            estimated_cost=self.estimate_cost(model, usage),
            finish_reason=body.get("stop_reason") or "",
            raw=body,
        )

    def health_check(self) -> HealthResult:
        if not self.api_key:
            return HealthResult(ok=False, message="No ANTHROPIC_API_KEY configured.")

        started = time.perf_counter()
        response = self.chat(
            [ChatMessage(role="user", content="ping")],
            model=self.model_for(AIRole.FAST.value),
            max_tokens=8,
            temperature=0,
            timeout=20,
        )
        latency = response.latency_ms or self._elapsed_ms(started)
        if response.ok:
            return HealthResult(
                ok=True,
                message=f"Reachable ({response.model}).",
                models=[response.model],
                latency_ms=latency,
            )
        return HealthResult(ok=False, message=response.error, latency_ms=latency)

    def list_models(self) -> list[str]:
        return sorted({spec.model_id for spec in ANTHROPIC_MODELS.values()})
