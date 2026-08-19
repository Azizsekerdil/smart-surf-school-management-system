"""NVIDIA NIM provider (build.nvidia.com / integrate.api.nvidia.com).

Vendor quirks handled here, all confirmed by live probes
(``docs/research/VERIFIED_API_PROBES.md``):

1. **Reasoning must be switched off explicitly.** Nemotron models default to
   chain-of-thought; without ``chat_template_kwargs={"thinking": false}`` a
   trivial prompt took longer than 90 s and the thinking text appeared in the
   answer. With it: 535 ms and a clean answer.
2. **Reasoning arrives in ``reasoning_content``**, never inside ``content`` —
   handled by the shared parser.
3. **Embeddings need ``input_type``** (``"query"`` vs ``"passage"``). Omitting it
   silently degrades recall rather than erroring.
4. **The model list over-reports.** ``/v1/models`` returns 102 ids, but several
   answer ``404 Not found for account``. Availability must be probed, so
   :meth:`probe_model` exists and the Control Center uses it.
"""

from __future__ import annotations

import time
from decimal import Decimal

import httpx

from ..models_catalog import NVIDIA_MODELS, NVIDIA_PRICING
from .base import AIRole, ChatMessage, HealthResult
from .openai_compat import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    name = "nvidia"
    label = "NVIDIA NIM"
    is_cloud = True
    supports_vision = True
    supports_tools = True
    supports_streaming = True
    supports_embeddings = True
    supports_input_type = True
    default_timeout = 120

    PRICING = NVIDIA_PRICING
    DEFAULT_PRICING = (Decimal("0.20"), Decimal("0.80"))

    def model_for(self, role: str) -> str:
        configured = (self.config.get("MODELS") or {}).get(role)
        if configured:
            return configured
        spec = NVIDIA_MODELS.get(role)
        return spec.model_id if spec else ""

    def build_chat_payload(self, messages, model, temperature, max_tokens, tools, **kwargs):
        payload = super().build_chat_payload(
            messages, model, temperature, max_tokens, tools, **kwargs
        )
        # Apply the per-model request extras (thinking off, reasoning effort...)
        for spec in NVIDIA_MODELS.values():
            if spec.model_id == model and spec.request_extras:
                for key, value in spec.request_extras.items():
                    payload.setdefault(key, value)
                break
        else:
            # Unknown model: default to reasoning off, which is safe for every
            # Nemotron model and ignored by the others.
            payload.setdefault("chat_template_kwargs", {"thinking": False})

        # An explicit caller request to think overrides the default.
        if kwargs.get("thinking") is True:
            payload["chat_template_kwargs"] = {"thinking": True}
        return payload

    # -- availability -------------------------------------------------------
    def probe_model(self, model: str, timeout: int = 45) -> tuple[bool, str, int]:
        """Actually invoke *model* once. Returns ``(ok, message, latency_ms)``.

        The catalogue lists models this account cannot call, so the Control
        Center probes rather than trusting ``/v1/models``.
        """
        started = time.perf_counter()
        response = self.chat(
            [ChatMessage(role="user", content="Reply with exactly: OK")],
            model=model,
            temperature=0,
            max_tokens=16,
            timeout=timeout,
        )
        latency = response.latency_ms or self._elapsed_ms(started)
        if response.ok:
            return True, f"{response.usage.total_tokens} tokens", latency
        return False, response.error, latency

    def health_check(self) -> HealthResult:
        if not self.api_key:
            return HealthResult(
                ok=False,
                message=(
                    "No NVIDIA_API_KEY configured. Create a key at "
                    "https://build.nvidia.com and put it in .env — never in the code."
                ),
            )

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return HealthResult(
                ok=False,
                message=f"NVIDIA API unreachable ({type(exc).__name__}). Check your connection.",
                latency_ms=self._elapsed_ms(started),
            )

        latency = self._elapsed_ms(started)
        if response.status_code != 200:
            return HealthResult(
                ok=False, message=self._describe_http_error(response), latency_ms=latency
            )

        try:
            models = sorted(
                str(m.get("id")) for m in (response.json().get("data") or []) if m.get("id")
            )
        except ValueError:
            models = []

        return HealthResult(
            ok=True,
            message=(
                f"{len(models)} models listed. Note: the catalogue lists more models than "
                "an account can invoke — use “Probe models” to confirm real availability."
            ),
            models=models,
            latency_ms=latency,
        )

    # -- embeddings ---------------------------------------------------------
    def embed(self, texts, *, input_type: str = "passage", **kwargs):
        # NVIDIA rejects "NONE" truncation on long inputs with a 400; "END"
        # keeps a long document chunk from failing a whole batch.
        kwargs.setdefault("truncate", "END")
        self.extra_embedding_body = {"truncate": kwargs.pop("truncate")}
        return super().embed(texts, input_type=input_type, **kwargs)

    def model_for_embedding(self) -> str:
        return self.model_for(AIRole.EMBEDDING.value)
