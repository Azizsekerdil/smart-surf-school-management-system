#!/usr/bin/env python
"""Standalone NVIDIA NIM integration test.

Exercises the whole surface the application relies on and prints a result table:

    connection · authentication · model list · chat completion · streaming ·
    tool calling · embeddings · timeout handling · error handling

Run it from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\test_nvidia_ai.py
    .\\.venv\\Scripts\\python.exe scripts\\test_nvidia_ai.py --probe-all
    .\\.venv\\Scripts\\python.exe scripts\\test_nvidia_ai.py --json

The API key is read from the environment (``NVIDIA_API_KEY``, or ``.env``) and is
never printed — not in output, not in an error message.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    import httpx
except ImportError:  # pragma: no cover - dependency guard
    sys.exit("httpx is required. Run: .\\.venv\\Scripts\\python.exe -m pip install httpx")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_environment() -> None:
    """Read .env without requiring Django to be configured."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_environment()

API_KEY = os.environ.get("NVIDIA_API_KEY", "")
BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")

CHAT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
FAST_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
EMBED_MODEL = "nvidia/llama-nemotron-embed-1b-v2"

# Reasoning models must be told not to think, or a trivial prompt takes minutes.
NO_THINKING = {"chat_template_kwargs": {"thinking": False}}


# ---------------------------------------------------------------------------
# Result plumbing
# ---------------------------------------------------------------------------
GREEN, RED, YELLOW, GREY, BOLD, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[1m", "\033[0m"
)
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    try:
        import colorama  # noqa: F401

        colorama.just_fix_windows_console()
    except ImportError:
        GREEN = RED = YELLOW = GREY = BOLD = RESET = ""


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    latency_ms: int = 0
    data: dict = field(default_factory=dict)


RESULTS: list[Result] = []


def record(name: str, ok: bool, detail: str = "", latency_ms: int = 0, **data) -> Result:
    result = Result(name, ok, detail, latency_ms, data)
    RESULTS.append(result)
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    timing = f"{GREY}{latency_ms:>6} ms{RESET}" if latency_ms else " " * 9
    print(f"  [{mark}] {name:<26} {timing}  {detail}")
    return result


def headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def scrub(text: str) -> str:
    """Remove the key from any text before it is printed."""
    if API_KEY and API_KEY in text:
        text = text.replace(API_KEY, "***REDACTED***")
    return text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_configuration() -> bool:
    print(f"\n{BOLD}1. Configuration{RESET}")
    if not API_KEY:
        record(
            "API key present", False,
            "NVIDIA_API_KEY is not set. Add it to .env or the environment.",
        )
        return False
    record("API key present", True, f"length {len(API_KEY)}, prefix ok: {API_KEY.startswith('nvapi-')}")
    record("Base URL", True, BASE_URL)
    if not API_KEY.startswith("nvapi-"):
        record("API key format", False, "Expected a key starting with 'nvapi-'")
    return True


def test_connection() -> bool:
    print(f"\n{BOLD}2. Connection and authentication{RESET}")
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(f"{BASE_URL}/models", headers=headers())
    except httpx.TimeoutException:
        record("Reachability", False, "Timed out after 20s", int((time.perf_counter() - started) * 1000))
        return False
    except httpx.HTTPError as exc:
        record("Reachability", False, f"{type(exc).__name__}: {scrub(str(exc))}")
        return False

    latency = int((time.perf_counter() - started) * 1000)

    if response.status_code == 401:
        record("Authentication", False, "401 — the key was rejected", latency)
        return False
    if response.status_code != 200:
        record("Reachability", False, f"HTTP {response.status_code}", latency)
        return False

    record("Reachability", True, "endpoint responded", latency)
    record("Authentication", True, "key accepted")

    try:
        models = [m["id"] for m in response.json().get("data", []) if m.get("id")]
    except (ValueError, KeyError):
        record("Model list", False, "Response was not the expected JSON shape")
        return True

    record("Model list", True, f"{len(models)} model(s) listed", models=models)
    print(
        f"       {YELLOW}note{RESET} the catalogue lists more models than an account can "
        f"always invoke — see the availability probe below."
    )
    return True


def chat(model: str, messages: list[dict], timeout: int = 60, **extra) -> tuple[bool, dict, int, str]:
    payload = {"model": model, "messages": messages, "max_tokens": 64, "temperature": 0, **extra}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{BASE_URL}/chat/completions", json=payload, headers=headers())
    except httpx.TimeoutException:
        return False, {}, int((time.perf_counter() - started) * 1000), f"timed out after {timeout}s"
    except httpx.HTTPError as exc:
        return False, {}, 0, f"{type(exc).__name__}"

    latency = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        return False, {}, latency, scrub(response.text[:180])
    return True, response.json(), latency, ""


def test_chat() -> None:
    print(f"\n{BOLD}3. Chat completion{RESET}")

    ok, body, latency, error = chat(
        CHAT_MODEL, [{"role": "user", "content": "Reply with exactly: OK"}], **NO_THINKING
    )
    if not ok:
        record("Chat completion", False, error, latency)
        return

    message = body["choices"][0]["message"]
    usage = body.get("usage", {})
    record(
        "Chat completion", True,
        f"'{(message.get('content') or '').strip()[:30]}' "
        f"(in {usage.get('prompt_tokens', 0)} / out {usage.get('completion_tokens', 0)} tokens)",
        latency,
    )

    # The whole point of the thinking flag: reasoning must not leak into content.
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    record(
        "Reasoning suppressed", not reasoning.strip(),
        "reasoning_content is empty with thinking=false"
        if not reasoning.strip()
        else f"unexpected reasoning of {len(reasoning)} chars",
    )

    ok, body, latency, error = chat(
        FAST_MODEL, [{"role": "user", "content": "Reply with exactly: OK"}], **NO_THINKING
    )
    record("Fast model", ok, error or "routing model responsive", latency)

    ok, body, latency, error = chat(
        CHAT_MODEL,
        [{"role": "user", "content": "Bugün deniz nasıl? Tek cümle."}],
        **NO_THINKING,
    )
    record("Turkish prompt", ok, error or "accepted UTF-8 input", latency)


def test_tool_calling() -> None:
    print(f"\n{BOLD}4. Tool calling{RESET}")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "count_bookings",
                "description": "Count the bookings on a given date.",
                "parameters": {
                    "type": "object",
                    "properties": {"date": {"type": "string", "description": "ISO date"}},
                    "required": ["date"],
                },
            },
        }
    ]
    ok, body, latency, error = chat(
        CHAT_MODEL,
        [{"role": "user", "content": "How many bookings are there on 2026-08-20? Use the tool."}],
        timeout=90,
        tools=tools,
        tool_choice="auto",
        **NO_THINKING,
    )
    if not ok:
        record("Tool calling", False, error, latency)
        return

    calls = body["choices"][0]["message"].get("tool_calls") or []
    if calls:
        name = calls[0].get("function", {}).get("name", "?")
        record("Tool calling", True, f"model requested '{name}'", latency)
    else:
        record("Tool calling", False, "no tool call was produced", latency)


def test_streaming() -> None:
    print(f"\n{BOLD}5. Streaming{RESET}")
    payload = {
        "model": FAST_MODEL,
        "messages": [{"role": "user", "content": "Count from 1 to 5, separated by spaces."}],
        "max_tokens": 64,
        "temperature": 0,
        "stream": True,
        **NO_THINKING,
    }
    started = time.perf_counter()
    chunks = 0
    first_token_ms = 0
    try:
        with httpx.Client(timeout=60) as client, client.stream(
            "POST", f"{BASE_URL}/chat/completions", json=payload, headers=headers()
        ) as response:
            if response.status_code != 200:
                response.read()
                record("Streaming", False, f"HTTP {response.status_code}")
                return
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    parsed = json.loads(data)
                except ValueError:
                    continue
                delta = (parsed.get("choices") or [{}])[0].get("delta", {})
                if delta.get("content"):
                    chunks += 1
                    if first_token_ms == 0:
                        first_token_ms = int((time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        record("Streaming", False, f"{type(exc).__name__}")
        return

    total = int((time.perf_counter() - started) * 1000)
    record(
        "Streaming", chunks > 0,
        f"{chunks} content chunk(s), first token at {first_token_ms} ms",
        total,
    )


def test_embeddings() -> None:
    print(f"\n{BOLD}6. Embeddings{RESET}")
    payload = {
        "model": EMBED_MODEL,
        "input": ["Dalga yüksekliği 1.2 metre", "Wave height is 1.2 metres"],
        "encoding_format": "float",
        "input_type": "passage",
        "truncate": "END",
    }
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=45) as client:
            response = client.post(f"{BASE_URL}/embeddings", json=payload, headers=headers())
    except httpx.HTTPError as exc:
        record("Embeddings", False, f"{type(exc).__name__}")
        return

    latency = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        record("Embeddings", False, f"HTTP {response.status_code}: {scrub(response.text[:120])}", latency)
        return

    rows = response.json().get("data", [])
    if not rows:
        record("Embeddings", False, "no vectors returned", latency)
        return

    dimensions = len(rows[0].get("embedding", []))
    record("Embeddings", True, f"{len(rows)} vector(s), {dimensions} dimensions", latency)
    record(
        "Embedding dimension recorded", dimensions > 0,
        f"the index must be built and queried with the same {dimensions}-dim model",
    )


def test_error_handling() -> None:
    print(f"\n{BOLD}7. Error handling{RESET}")

    ok, _body, latency, error = chat("nvidia/this-model-does-not-exist", [{"role": "user", "content": "hi"}])
    record(
        "Unknown model rejected", not ok,
        "clean error returned" if not ok else "unexpectedly succeeded",
        latency,
    )

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(f"{BASE_URL}/models", headers={"Authorization": "Bearer nvapi-invalid"})
        rejected = response.status_code in (401, 403)
        record(
            "Bad key rejected", rejected,
            f"HTTP {response.status_code}", int((time.perf_counter() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        record("Bad key rejected", False, f"{type(exc).__name__}")

    ok, _body, latency, error = chat(
        CHAT_MODEL, [{"role": "user", "content": "Write a 2000 word essay."}], timeout=1
    )
    record(
        "Timeout handled", not ok and "timed out" in error,
        "a 1s timeout was raised cleanly" if not ok else "completed within 1s",
        latency,
    )


def test_model_availability(models: list[str], probe_all: bool) -> None:
    print(f"\n{BOLD}8. Model availability{RESET}")
    print(
        f"       {GREY}The catalogue lists models an account may not be able to invoke.{RESET}"
    )

    targets = models if probe_all else [
        CHAT_MODEL, FAST_MODEL, "openai/gpt-oss-120b", "z-ai/glm-5.2",
        "nvidia/nemotron-nano-12b-v2-vl", "nvidia/riva-translate-4b-instruct-v2",
        "poolside/laguna-xs-2.1", "mistralai/codestral-22b-instruct-v0.1",
    ]

    available, unavailable = [], []
    for model in targets:
        ok, _body, latency, error = chat(
            model, [{"role": "user", "content": "Reply with exactly: OK"}], timeout=40, **NO_THINKING
        )
        if ok:
            available.append((model, latency))
            print(f"  [{GREEN}OK  {RESET}] {model:<48} {GREY}{latency:>6} ms{RESET}")
        else:
            unavailable.append((model, error))
            print(f"  [{RED}FAIL{RESET}] {model:<48} {error[:60]}")

    record(
        "Availability probe", bool(available),
        f"{len(available)} usable, {len(unavailable)} unavailable",
        available=[m for m, _ in available],
        unavailable=[m for m, _ in unavailable],
    )


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Test the NVIDIA NIM integration.")
    parser.add_argument("--probe-all", action="store_true", help="Probe every catalogued model (slow).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--skip-slow", action="store_true", help="Skip streaming and availability probes.")
    options = parser.parse_args()

    print(f"{BOLD}NVIDIA NIM integration test{RESET}")
    print(f"{GREY}{BASE_URL}{RESET}")

    if not test_configuration():
        print(f"\n{RED}Cannot continue without an API key.{RESET}")
        print("Get one at https://build.nvidia.com and add it to .env as NVIDIA_API_KEY.")
        return 2

    if not test_connection():
        print(f"\n{RED}Could not reach the NVIDIA API.{RESET}")
        return 1

    models = next((r.data.get("models", []) for r in RESULTS if r.name == "Model list"), [])

    test_chat()
    test_tool_calling()
    if not options.skip_slow:
        test_streaming()
    test_embeddings()
    test_error_handling()
    if not options.skip_slow:
        test_model_availability(models, options.probe_all)

    passed = sum(1 for r in RESULTS if r.ok)
    failed = len(RESULTS) - passed

    print(f"\n{BOLD}{'=' * 62}{RESET}")
    print(f"  {GREEN}{passed} passed{RESET}   {RED if failed else GREY}{failed} failed{RESET}")
    if failed:
        print("\n  Failures:")
        for result in RESULTS:
            if not result.ok:
                print(f"    - {result.name}: {result.detail}")
    print(f"{BOLD}{'=' * 62}{RESET}")

    if options.json:
        print(
            json.dumps(
                {
                    "base_url": BASE_URL,
                    "passed": passed,
                    "failed": failed,
                    "results": [
                        {
                            "name": r.name,
                            "ok": r.ok,
                            "detail": r.detail,
                            "latency_ms": r.latency_ms,
                            **r.data,
                        }
                        for r in RESULTS
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
