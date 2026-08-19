"""Shared transport for every OpenAI-compatible endpoint.

LM Studio, NVIDIA NIM and any generic OpenAI-compatible server all speak the
same wire protocol, so the HTTP work lives here once and the concrete providers
only supply credentials, model maps and vendor quirks.

``httpx`` is used directly rather than the ``openai`` SDK because two of the
fields we depend on are not OpenAI-standard:

* ``chat_template_kwargs={"thinking": false}`` — the only reliable way to stop
  NVIDIA Nemotron models burning seconds on chain-of-thought (measured: 535 ms
  with it, timeout without it).
* ``input_type`` / ``truncate`` on embeddings — required by NVIDIA retrieval
  embedders and silently degrading if omitted.

Going through the SDK would mean wrapping both in ``extra_body`` for no benefit.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator

import httpx

from .base import (
    AIRole,
    BaseAIProvider,
    ChatMessage,
    ChatResponse,
    EmbeddingResponse,
    HealthResult,
    TokenUsage,
)

logger = logging.getLogger("apps.ai")


class OpenAICompatibleProvider(BaseAIProvider):
    """Base class for OpenAI-compatible HTTP endpoints."""

    supports_tools = True
    supports_streaming = True
    supports_embeddings = True

    #: Extra JSON fields merged into every chat request (vendor quirks).
    extra_chat_body: dict = {}
    #: Extra JSON fields merged into every embedding request.
    extra_embedding_body: dict = {}
    #: Default request timeout in seconds.
    default_timeout: int = 120

    # -- HTTP ---------------------------------------------------------------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, path: str, payload: dict, timeout: int) -> httpx.Response:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            return client.post(url, json=payload, headers=self._headers())

    @staticmethod
    def _describe_http_error(response: httpx.Response) -> str:
        """Turn a provider error into something an operator can act on."""
        status = response.status_code
        try:
            body = response.json()
            detail = (
                body.get("error", {}).get("message")
                if isinstance(body.get("error"), dict)
                else body.get("error") or body.get("detail") or body.get("message")
            )
        except (ValueError, AttributeError):
            detail = (response.text or "")[:300]

        if status == 401:
            return "Authentication failed — check the API key."
        if status == 403:
            return "Access denied for this model or account."
        if status == 404:
            return f"Model or endpoint not available for this account. {detail or ''}".strip()
        if status == 429:
            return "Rate limit reached. Try again shortly or switch model."
        if status == 503:
            return f"Provider capacity exhausted. {detail or ''}".strip()
        return f"HTTP {status}: {detail or 'unknown error'}"

    # -- chat ---------------------------------------------------------------
    def build_chat_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        **kwargs,
    ) -> dict:
        payload: dict = {
            "model": model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs.pop("response_format")
        payload.update(self.extra_chat_body)
        # Explicit per-call overrides win over the class defaults.
        for key in ("chat_template_kwargs", "reasoning_effort", "top_p", "stop", "seed"):
            if key in kwargs and kwargs[key] is not None:
                payload[key] = kwargs[key]
        return payload

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
        model = model or self.model_for(role)
        if not model:
            return ChatResponse(
                ok=False,
                provider=self.name,
                error=f"No model configured for role '{role}' on {self.label}.",
            )
        if not self.base_url:
            return ChatResponse(
                ok=False, provider=self.name, model=model, error=f"{self.label} has no base URL."
            )

        payload = self.build_chat_payload(
            messages, model, temperature, max_tokens or 2048, tools, **kwargs
        )
        started = time.perf_counter()

        try:
            response = self._post("/chat/completions", payload, timeout or self.default_timeout)
        except httpx.TimeoutException:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=self._elapsed_ms(started),
                error=f"{self.label} timed out after {timeout or self.default_timeout}s.",
            )
        except httpx.HTTPError as exc:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=self._elapsed_ms(started),
                error=f"{self.label} is unreachable: {type(exc).__name__}",
            )

        latency = self._elapsed_ms(started)

        if response.status_code != 200:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=latency,
                error=self._describe_http_error(response),
            )

        try:
            body = response.json()
        except ValueError:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=latency,
                error="Provider returned a non-JSON response.",
            )

        return self.parse_chat_response(body, model, latency)

    def parse_chat_response(self, body: dict, model: str, latency_ms: int) -> ChatResponse:
        choices = body.get("choices") or []
        if not choices:
            return ChatResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=latency_ms,
                error="Provider returned no choices.",
            )

        message = choices[0].get("message") or {}
        usage_body = body.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_body.get("prompt_tokens") or 0),
            completion_tokens=int(usage_body.get("completion_tokens") or 0),
        )

        # Reasoning models expose chain-of-thought separately; keep it separate.
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""

        return ChatResponse(
            ok=True,
            content=(message.get("content") or "").strip(),
            reasoning=(reasoning or "").strip(),
            tool_calls=message.get("tool_calls") or [],
            provider=self.name,
            model=body.get("model") or model,
            usage=usage,
            latency_ms=latency_ms,
            estimated_cost=self.estimate_cost(model, usage),
            finish_reason=choices[0].get("finish_reason") or "",
            raw=body,
        )

    # -- streaming ----------------------------------------------------------
    def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        role: str = AIRole.ASSISTANT.value,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: int | None = None,
        **kwargs,
    ) -> Iterator[str]:
        model = model or self.model_for(role)
        if not model or not self.base_url:
            yield ""
            return

        payload = self.build_chat_payload(
            messages, model, temperature, max_tokens or 2048, None, **kwargs
        )
        payload["stream"] = True

        try:
            with httpx.Client(timeout=timeout or self.default_timeout) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        yield f"[{self._describe_http_error(response)}]"
                        return
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        # Skip reasoning deltas — the answer only.
                        piece = delta.get("content")
                        if piece:
                            yield piece
        except httpx.HTTPError as exc:
            logger.warning("Streaming failed on %s: %s", self.name, type(exc).__name__)
            yield f"[{self.label} stream interrupted]"

    # -- embeddings ---------------------------------------------------------
    def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "passage",
        model: str | None = None,
        timeout: int | None = None,
        **kwargs,
    ) -> EmbeddingResponse:
        model = model or self.model_for(AIRole.EMBEDDING.value)
        if not model or not self.base_url:
            return EmbeddingResponse(
                ok=False,
                provider=self.name,
                error=f"No embedding model configured for {self.label}.",
            )
        if not texts:
            return EmbeddingResponse(ok=True, provider=self.name, model=model, vectors=[])

        payload: dict = {"model": model, "input": texts, "encoding_format": "float"}
        payload.update(self.extra_embedding_body)
        if self.extra_embedding_body.get("input_type") is not None or "input_type" in kwargs:
            payload["input_type"] = kwargs.get("input_type", input_type)
        elif self.supports_input_type:
            payload["input_type"] = input_type

        started = time.perf_counter()
        try:
            response = self._post("/embeddings", payload, timeout or self.default_timeout)
        except httpx.HTTPError as exc:
            return EmbeddingResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=self._elapsed_ms(started),
                error=f"{self.label} unreachable: {type(exc).__name__}",
            )

        latency = self._elapsed_ms(started)
        if response.status_code != 200:
            return EmbeddingResponse(
                ok=False,
                provider=self.name,
                model=model,
                latency_ms=latency,
                error=self._describe_http_error(response),
            )

        try:
            body = response.json()
        except ValueError:
            return EmbeddingResponse(
                ok=False, provider=self.name, model=model, error="Non-JSON embedding response."
            )

        rows = sorted(body.get("data") or [], key=lambda d: d.get("index", 0))
        vectors = [row.get("embedding") or [] for row in rows]
        usage_body = body.get("usage") or {}

        return EmbeddingResponse(
            ok=bool(vectors),
            vectors=vectors,
            provider=self.name,
            model=body.get("model") or model,
            dimensions=len(vectors[0]) if vectors and vectors[0] else 0,
            usage=TokenUsage(prompt_tokens=int(usage_body.get("prompt_tokens") or 0)),
            latency_ms=latency,
            error="" if vectors else "Provider returned no vectors.",
        )

    #: NVIDIA retrieval embedders need ``input_type``; LM Studio rejects it.
    supports_input_type: bool = False

    # -- health & discovery -------------------------------------------------
    def list_models(self) -> list[str]:
        if not self.base_url:
            return []
        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
            if response.status_code != 200:
                return []
            return sorted(
                str(item.get("id"))
                for item in (response.json().get("data") or [])
                if item.get("id")
            )
        except (httpx.HTTPError, ValueError):
            return []

    def health_check(self) -> HealthResult:
        if not self.enabled:
            return HealthResult(ok=False, message=f"{self.label} is not enabled.")
        if not self.base_url:
            return HealthResult(ok=False, message=f"{self.label} has no base URL configured.")

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.TimeoutException:
            return HealthResult(
                ok=False,
                message=f"{self.label} did not respond within 10s.",
                latency_ms=self._elapsed_ms(started),
            )
        except httpx.HTTPError as exc:
            return HealthResult(
                ok=False,
                message=f"{self.label} unreachable ({type(exc).__name__}).",
                latency_ms=self._elapsed_ms(started),
            )

        latency = self._elapsed_ms(started)
        if response.status_code != 200:
            return HealthResult(
                ok=False, message=self._describe_http_error(response), latency_ms=latency
            )

        try:
            models = [str(m.get("id")) for m in (response.json().get("data") or []) if m.get("id")]
        except ValueError:
            models = []

        return HealthResult(
            ok=True,
            message=f"{len(models)} model(s) available.",
            models=sorted(models),
            latency_ms=latency,
        )
