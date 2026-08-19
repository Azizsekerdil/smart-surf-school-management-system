# NVIDIA NIM Model Selection — Smart Surf School Management System

**Date:** 2026-08-15
**Target stack:** Python 3.11 · Django 5 · DRF · HTMX · Alpine.js · Tailwind · SQLite (dev) / PostgreSQL (prod) · Windows 11 native (no Docker)
**Provider:** NVIDIA API Catalog (build.nvidia.com) — hosted NIM endpoints
**Scope:** models chosen **only** from the 102 model ids exposed by our account.

---

## 0. TL;DR — Role → Model Matrix

| # | Role | PRIMARY | FALLBACK | Endpoint |
|---|------|---------|----------|----------|
| 1 | General reasoning / business Q&A | `nvidia/nemotron-3-super-120b-a12b` | `openai/gpt-oss-120b` | `/v1/chat/completions` |
| 2 | Fast routing + short tasks | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nemotron-3-nano-30b-a3b` | `/v1/chat/completions` |
| 3 | Coding + code review | `poolside/laguna-xs-2.1` | `nvidia/nemotron-3-super-120b-a12b` | `/v1/chat/completions` |
| 4 | Vision (damage photos) | `nvidia/nemotron-nano-12b-v2-vl` | `meta/muse-glimmer-30b` | `/v1/chat/completions` (image parts) |
| 5 | Document / OCR parsing | `nvidia/nemotron-parse` | `nvidia/nemotron-nano-12b-v2-vl` | **NOT chat** — dedicated infer endpoint |
| 6 | Analytics & math/stats | `openai/gpt-oss-120b` (`reasoning_effort=high`) | `nvidia/nemotron-3-super-120b-a12b` | `/v1/chat/completions` |
| 7 | Embeddings for RAG | `nvidia/llama-nemotron-embed-1b-v2` | `baai/bge-m3` | **`/v1/embeddings`** |
| 8 | Reranking | *(none in our list)* → LLM-rerank with `nvidia/nemotron-3.5-lightning-30b-a3b` | embedding-cosine rescoring | `/v1/chat/completions` |
| 9 | Safety / prompt-injection guard | `nvidia/llama-3.1-nemoguard-8b-topic-control` + `nvidia/nemotron-3.5-content-safety` | `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` | `/v1/chat/completions` |
| 10 | TR↔EN translation | `nvidia/riva-translate-4b-instruct-v2` | `nvidia/riva-translate-4b-instruct-v1.1` | `/v1/chat/completions` (special system msg) |

---

## 1. Verified API Facts (read this before writing any client code)

### 1.1 Base URLs

| Purpose | URL |
|---|---|
| OpenAI-compatible chat + completions + embeddings | `https://integrate.api.nvidia.com/v1` |
| Reranking NIMs (**not in our model list**) | `https://ai.api.nvidia.com/v1/retrieval/{model}/reranking` (some NIMs expose `/v1/ranking`) |
| Legacy VLM endpoints (neva, vila, fuyu, kosmos-2, deplot) | `https://ai.api.nvidia.com/v1/vlm/{org}/{model}` |

API key format: `nvapi-...`. Set it as `NVIDIA_API_KEY` in the Windows environment (or `.env` + `django-environ`); never commit it.

Minimal client (this is the only client we need for 8 of the 10 roles):

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],   # nvapi-...
)
resp = client.chat.completions.create(
    model="nvidia/nemotron-3-super-120b-a12b",
    messages=[{"role": "user", "content": "..."}],
    temperature=0.6, top_p=0.95, max_tokens=1024,
    stream=True,
)
```

Because the surface is OpenAI-compatible, streaming, function/tool calling and `response_format` (JSON/structured output) work with the standard SDK on the chat models. **Everything else in our stack (HTMX SSE streaming, LiteLLM, LangChain) can point at the same base URL with no code change.**

### 1.2 Embeddings — the part people get wrong

* Endpoint is **`/v1/embeddings`**, never `/v1/chat/completions`.
* NVIDIA retrieval embedders are **asymmetric**: they need `input_type` = `"query"` (at search time) or `"passage"` (at index time). Models documented as requiring it include `nvidia/nemotron-3-embed-1b`, the `NV-EmbedQA` family and the `E5` family. Getting this wrong silently degrades recall — it does not error.
* `input_type` and `truncate` are **not** OpenAI-standard fields, so with the OpenAI Python SDK they must go through `extra_body`:

```python
emb = client.embeddings.create(
    model="nvidia/llama-nemotron-embed-1b-v2",
    input=["Rüzgar 15 knot, dalga 1.2 m"],
    encoding_format="float",
    extra_body={"input_type": "passage", "truncate": "END"},
)
```

* `truncate` values: `"NONE"` (default — **raises** if the input exceeds max length), `"START"`, `"END"`. For production ingestion always pass `"END"` so a long PDF chunk cannot 400 the whole batch.
* Alternative for strictly-OpenAI clients: append `-query` / `-passage` to the model name instead of sending `input_type`.
* Embeddings do **not** support `stream=True`.

### 1.3 Streaming

* All chat/instruct models on `/v1/chat/completions` support `stream=True` (SSE). This is what we wire into HTMX for the assistant and the AI terminal.
* Embedding, rerank, parse/OCR, CLIP and detector endpoints are **request/response only** — no streaming.
* Reasoning models (Nemotron 3 family, gpt-oss, inkling) emit reasoning tokens in the stream; strip/collapse them in the UI or disable reasoning for chat-latency paths.

### 1.4 Models in our list that are **NOT chat models** — do not call `chat.completions` on these

| Model id | What it actually is | Correct call |
|---|---|---|
| `nvidia/nemotron-3-embed-1b`, `nvidia/llama-nemotron-embed-1b-v2`, `nvidia/llama-nemotron-embed-vl-1b-v2`, `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1`, `nvidia/llama-3.2-nv-embedqa-1b-v1`, `nvidia/nv-embedqa-e5-v5`, `nvidia/nv-embedqa-mistral-7b-v2`, `nvidia/nv-embed-v1`, `nvidia/embed-qa-4`, `nvidia/nv-embedcode-7b-v1`, `baai/bge-m3`, `snowflake/arctic-embed-l` | Text/multimodal **embedders** | `/v1/embeddings` (+ `input_type`) |
| `nvidia/nvclip` | CLIP image+text embedder | `/v1/embeddings` (image as data-URI in `input`) |
| `nvidia/nemotron-parse`, `nvidia/nemoretriever-parse` | Document **OCR/layout parser** (900M VLM, ViT-H encoder + mBART decoder) | Dedicated NIM inference endpoint (`/v1/infer`-style); returns text + bounding boxes + semantic classes, not a chat turn |
| `nvidia/nemotron-4-340b-reward` | RLHF **reward model** — returns 5 scalar HelpSteer2 scores (helpfulness, correctness, coherence, complexity, verbosity), not prose | Chat-shaped request, but the response is scores. Do not treat as an assistant |
| `nvidia/ai-synthetic-video-detector` | Binary **classifier** for AI-generated video | Dedicated infer endpoint |
| `nvidia/neva-22b`, `nvidia/vila`, `adept/fuyu-8b`, `microsoft/kosmos-2`, `google/deplot` | Legacy VLMs | `https://ai.api.nvidia.com/v1/vlm/{org}/{model}` — **different host and path** |
| `meta/llama2-70b`, `mistralai/mixtral-8x22b-v0.1` | Base/legacy, weak instruction following | Avoid entirely |
| `nvidia/ising-calibration-1.5-31b` | Quantum-processor calibration VLM (reads qubit calibration plots) | Irrelevant to us — ignore |

### 1.5 Cost / quota reality on the free tier

* Free tier: roughly **40 requests/minute per model**, account-level; increases (~200 RPM) can be requested.
* NVIDIA Developer Program grants ~**1,000 inference credits**, extendable to ~5,000 via the forum request form. Credit burn scales with **active** parameters × tokens, which is exactly why every PRIMARY below is a low-active-parameter MoE rather than a big dense model.
* 40 RPM per model is a real constraint for a multi-agent app → **spread load across roles/models** (routing on Lightning, embeds on the 1B embedder, chat on Super) instead of funnelling everything through one model id.

**RECOMMENDATION:** Build a single `nvidia_client.py` service module exposing `chat()`, `embed()` and `vision()` helpers against `https://integrate.api.nvidia.com/v1`, with the model id injected from a `settings.NVIDIA_MODELS` dict (role → primary/fallback). Force `truncate="END"` and an explicit `input_type` on every embedding call. Treat parse/CLIP/rerank/detector as separate, clearly-named services so nobody accidentally sends them a chat payload. Assume 40 RPM/model and implement a token-bucket + automatic fallback-model retry on HTTP 429.

---

## 2. Role 1 — General reasoning + business Q&A (in-app AI assistant)

This is the "ask the system anything about the school" surface: occupancy, lesson planning, customer history, policy questions, drafting replies. Needs good instruction following, tool calling (it will query our Django ORM through function calls), reasonable context, and **sub-2s time-to-first-token** because it streams into HTMX.

Candidates and the verified facts:

* **`nvidia/nemotron-3-super-120b-a12b`** — 120B total but only **12B active per token** (hybrid Mamba-2 + attention + Latent MoE), native **1M-token** context, Multi-Token Prediction giving built-in speculative decoding, **>5× throughput** vs the previous Nemotron Super, 85.6% on PinchBench (agentic eval), AA Intelligence Index 26. Licensed under the **NVIDIA Nemotron Open Model License** — open weights, commercial use permitted. This is the sweet spot: frontier-class agentic quality at a 12B-active latency/credit profile.
* **`openai/gpt-oss-120b`** — MoE, **128K** context, **Apache 2.0** (the cleanest license in the whole catalog, zero copyleft/patent risk), and a `reasoning_effort` knob (`low`/`medium`/`high`) that lets us dial latency vs quality per request. Superb fallback and the safest choice if legal ever objects to NVIDIA/Meta-derived licenses.
* **`meta/muse-glimmer-30b`** — 30B dense, **Apache 2.0**, agentic-first, 131K+ context, natively multimodal (text+image). Beats Gemma-4-31B on agentic benchmarks. Cheapest "good enough" daily driver if credits get tight.
* Rejected: `nvidia/nemotron-3-ultra-550b-a55b` (550B/55B-active — 4–5× the credit burn and latency for a business-Q&A workload we do not need), `thinkingmachines/inkling` (975B/41B-active, excellent but overkill), `nvidia/llama-3.1-nemotron-ultra-253b-v1` and `nvidia/nemotron-4-340b-instruct` (dense-ish, older, slow), `mistralai/mistral-large*`, `meta/llama-3.3-70b-instruct` (70B dense = worst latency-per-quality in this list now).

Note on context: the hosted API caps Nemotron 3 models at **128K input/output** even though self-hosted supports 1M. 128K is far more than our assistant needs.

**RECOMMENDATION:** **PRIMARY `nvidia/nemotron-3-super-120b-a12b`**, run with reasoning **disabled** for interactive chat (enable it only for the "deep analysis" button) — 12B active params keep it fast and cheap, 128K hosted context easily holds a season of booking data, tool calling is strong, and the NVIDIA Open Model License is commercially usable. **FALLBACK `openai/gpt-oss-120b`** with `reasoning_effort="low"` — Apache 2.0, 128K, and a drop-in swap on 429/5xx. Keep `meta/muse-glimmer-30b` configured as a third-tier "budget mode".

---

## 3. Role 2 — Fast/cheap routing + short tasks

Intent classification, "which tool should handle this", short summaries, title generation, form field extraction, notification copy. Must be *cheap* and *fast*; quality bar is modest but JSON reliability matters.

* **`nvidia/nemotron-3.5-lightning-30b-a3b`** — 30B total / **3B active**, hybrid Mamba-2 + MoE, pretrained on 20T+ tokens, up to **1M context**, explicitly positioned by NVIDIA for *"fast, accurate specialized task execution for long-running agents"* and always-on agent loops. Model card states it is **ready for commercial use**. English + coding, plus ES/FR/DE/IT/JA. Released 2026-08-11 on build.nvidia.com — the newest and most on-purpose model in the catalog for this exact role.
* **`nvidia/nemotron-3-nano-30b-a3b`** — 31.6B total / **3.2B active**, up to **3.3× higher inference throughput** than GPT-OSS-20B and Qwen3-30B-A3B-Thinking while scoring higher, reasoning toggled by a chat-template flag, 128K hosted context. Rock-solid fallback.
* **`meta/llama-3.2-3b-instruct`** / **`nvidia/nemotron-mini-4b-instruct`** / **`nvidia/nvidia-nemotron-nano-9b-v2`** — genuinely tiny, useful only for pure classification (yes/no, one-of-N label). Keep `llama-3.2-3b-instruct` as the emergency tier.
* Rejected for routing: anything ≥8B dense (`meta/llama-3.1-8b-instruct`, `mistralai/mistral-7b-instruct-v0.3`, `ibm/granite-3.0-8b-instruct`, `zyphra/zamba2-7b-instruct`) — a 3B-active MoE beats them on both speed and accuracy today.

**RECOMMENDATION:** **PRIMARY `nvidia/nemotron-3.5-lightning-30b-a3b`** with reasoning off, `max_tokens ≤ 256`, `response_format={"type":"json_object"}` for all routing decisions. **FALLBACK `nvidia/nemotron-3-nano-30b-a3b`**. Third tier for trivial binary labels: `meta/llama-3.2-3b-instruct`. Using a *different* model id here than Role 1 also buys us a second independent 40-RPM bucket.

---

## 4. Role 3 — Coding + code review (AI Development Terminal)

Needs: repo-scale context, diff/patch generation, tool-calling for a terminal loop, and low latency because the developer is watching.

* **`poolside/laguna-xs-2.1`** — 33B total / **3B active** MoE, purpose-built for *agentic coding and long-horizon software engineering*. Verified scores: **70.9% SWE-bench Verified**, 63.1% SWE-bench Multilingual, 47.6% SWE-Bench Pro, 37.5% Terminal-Bench 2.0. Licensed **OpenMDW-1.1** — fully permissive, explicitly designed for open model weights/artifacts, commercially clean. A 3B-active model hitting ~71% SWE-bench Verified is the best quality-per-millisecond in the entire catalog for our terminal.
* **`nvidia/nemotron-3-super-120b-a12b`** — the escalation path for whole-repo review: 1M native context (128K hosted), Multi-Token Prediction gives **up to 3× speedup on structured generation like code**, strong agentic scores. Use for "review this entire app" rather than "fix this function".
* Heavyweights available if we ever need them: **`thinkingmachines/inkling`** (975B/41B-active, **77.6% SWE-bench Verified**, 63.8 Terminal-Bench 2.1, switchable reasoning, multimodal) — best raw coding score in our list but expensive and slow; **`moonshotai/kimi-k2.6`** (~1T/32B-active, 256K context, 66.7% Terminal-Bench 2.0, proven 4,000+ tool calls over a 13-hour session — the best *stability* under long agent loops); **`z-ai/glm-5.2`** (744B/40B-active, **1M context**, built for long-horizon coding over huge contexts); **`minimaxai/minimax-m3`** (1M context, 81.1 MCP-Mark Verified — best pure tool-invocation score); **`deepseek-ai/deepseek-v4-flash-0731`** (284B/13B-active, 1M context, TTFT ~1.48s, ~123 tok/s — the best *latency* of the frontier-class group).
* **`mistralai/codestral-22b-instruct-v0.1`** remains the right pick for fill-in-the-middle **inline autocomplete** (a different job from chat review).
* Explicitly avoid as obsolete: `bigcode/starcoder2-15b`, `meta/codellama-70b`, `deepseek-ai/deepseek-coder-6.7b-instruct`, `google/codegemma-*`, `ibm/granite-*-code-instruct`. They are 2023–2024 models and are outclassed by Laguna XS at a fraction of the latency.

**RECOMMENDATION:** **PRIMARY `poolside/laguna-xs-2.1`** for all interactive terminal work — edits, patches, explanations, per-file review (3B active = fast, OpenMDW-1.1 = clean, 70.9% SWE-bench Verified = credible). **FALLBACK / escalation `nvidia/nemotron-3-super-120b-a12b`** for multi-file or whole-repo review where 128K context and MTP-accelerated code generation pay off. Add `mistralai/codestral-22b-instruct-v0.1` as a separate "autocomplete" role, and expose `thinkingmachines/inkling` behind a manual "hard mode" toggle only — never as an automatic fallback, it will burn credits.

---

## 5. Role 4 — Vision / image understanding (equipment damage photos)

Job: a staff member photographs a dinged surfboard, a torn wetsuit seam, a frayed leash; the model describes the damage, classifies severity, and reads any label/serial text in the frame.

* **`nvidia/nemotron-nano-12b-v2-vl`** — 12B hybrid Transformer-Mamba VLM. Trained on NVIDIA-curated synthetic data specifically optimized for **OCR, chart reasoning and document comprehension**; achieves leading results on **OCRBench v2** and ≈74 average across MMMU / MathVista / AI2D / OCRBench / ChartQA / DocVQA / Video-MME. Context 16K→**128K**, dynamic tiling and token reduction for high-res images, Mamba layers give materially higher throughput / lower latency than a comparable pure-transformer VLM. Ships BF16/FP8/FP4. This is the best balance of *reads text in the photo* + *describes the physical damage* + *fast*.
* **`meta/muse-glimmer-30b`** — Apache 2.0, dense transformer + ViT-G/14 perception encoder, up to 4,096 visual tokens per image, 131K+ context, interleaved text+images, explicitly intended for "multimodal reasoning over screenshots, charts, documents and images" and LLM-as-a-judge. Excellent fallback and the most license-clean vision option.
* **`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`** — 30B/3B-active omni model with native **text, image, video and audio**, positioned as a multimodal perception sub-agent. Pick this if we later want video (a clip of a board flexing) or voice notes from instructors.
* **`nvidia/cosmos-reason2-8b`** — Qwen3-VL-based, purpose-built for **physical-world reasoning**: space, time, fundamental physics, with explicit chain-of-thought traces; commercially usable. Worth a bake-off specifically for "is this ding structural or cosmetic?" style judgments, where physical common sense beats generic VQA.
* Avoid: `meta/llama-3.2-90b-vision-instruct` (90B dense — slow and credit-hungry for marginal gain), `microsoft/phi-3-vision-128k-instruct`, `nvidia/neva-22b`, `nvidia/vila`, `adept/fuyu-8b`, `microsoft/kosmos-2` (all legacy **and** on the separate `ai.api.nvidia.com/v1/vlm/...` host — extra integration cost for worse results).

Images go in as standard OpenAI-style content parts (`{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}`). Downscale to ≤1600px on the Django side before upload — it cuts latency and credits substantially with no accuracy loss for damage assessment.

**RECOMMENDATION:** **PRIMARY `nvidia/nemotron-nano-12b-v2-vl`** — leading OCR quality means one model handles both "describe the damage" and "read the serial number on the board", at 12B with Mamba-accelerated throughput. **FALLBACK `meta/muse-glimmer-30b`** (Apache 2.0, safest license, strong multimodal reasoning). Run a one-week A/B against `nvidia/cosmos-reason2-8b` on real damage photos before locking the severity-classification prompt; if it wins on physical judgment, use it as a second opinion for high-value equipment only.

---

## 6. Role 5 — Document understanding / OCR-ish parsing (manuals, invoices)

Two genuinely different jobs, and they need two different models:

**(a) Structured extraction from a page image / scanned PDF → `nvidia/nemotron-parse`**
900M-parameter document VLM (600M ViT-H vision encoder + 250M mBART-based decoder). It emits **plain text, markdown, bounding boxes and semantic class tokens** (`<x_..>, <y_..>`, `<class_..>`) interleaved — i.e. layout-aware structured output, not a chat answer. NVIDIA's own RAG guidance says to prefer it for **scanned documents and complex layouts** because it beats traditional OCR on structural variability. Important context: **`nvidia/nemoretriever-parse` is the old name of the same product line** (renamed to Nemotron Parse in Nov 2025) — our list contains both ids; use `nvidia/nemotron-parse`, treat `nvidia/nemoretriever-parse` purely as a fallback id if the new one 404s. Also note **Nemotron Parse is currently English-first** (handwriting and more languages are on the roadmap).

**(b) Answering questions about a document → `nvidia/nemotron-nano-12b-v2-vl`**
For "what's the VAT total on this invoice?" or "which section of the manual covers fin box repair?", a general VLM with strong DocVQA/ChartQA/OCRBench scores and 128K context is the right tool, and it handles **Turkish** invoices where Nemotron Parse's English-first training is a risk.

Rejected: `google/deplot` (chart→table only, and on the legacy `ai.api.nvidia.com/v1/vlm` host), `microsoft/phi-3-vision-128k-instruct` (superseded).

Recommended pipeline for the manuals/invoices module:

```
PDF page → render to PNG (PyMuPDF, pure-Python, works fine on Windows 11)
  → nvidia/nemotron-parse  → markdown + bboxes
  → chunk (500–800 tokens, heading-aware)
  → nvidia/llama-nemotron-embed-1b-v2 (input_type="passage")
  → pgvector in PostgreSQL
```

**RECOMMENDATION:** **PRIMARY `nvidia/nemotron-parse`** for the ingestion pipeline (layout-aware markdown + bounding boxes is exactly what a RAG chunker wants), calling it on its **dedicated inference endpoint — never `chat.completions`**. **FALLBACK `nvidia/nemotron-nano-12b-v2-vl`** for (i) Turkish-language documents, and (ii) interactive "ask this document a question". Keep `nvidia/nemoretriever-parse` in config only as a legacy alias.

---

## 7. Role 6 — Analytics & math/statistics reasoning

Revenue/occupancy forecasting explanations, instructor-utilisation stats, pricing sensitivity, "why did November drop 18%". Accuracy on arithmetic and multi-step quantitative reasoning matters more than latency; these run behind a "Generate report" button, not in a chat stream.

Verified head-to-head on AIME 2025 (no tools):

| Model | AIME25 | Notes |
|---|---|---|
| `openai/gpt-oss-120b` (high effort) | **92.5** | Apache 2.0, 128K, `reasoning_effort` low/med/high |
| `nvidia/nemotron-3-super-120b-a12b` | 90.2 | Higher overall AA Intelligence Index (26 vs 24), 1M native context |
| `thinkingmachines/inkling` | 97.1 (AIME 2026) | 975B/41B-active — best but heavy |

`openai/gpt-oss-120b` wins here for a practical reason beyond the score: **`reasoning_effort` is an explicit cost/latency dial**. We run `high` for the monthly report, `medium` for ad-hoc queries, `low` for sanity checks — from one model id, one prompt template.

Rejected: `writer/palmyra-fin-70b-32k` (finance-domain 70B dense — domain tuning is for SEC filings/financial NLP, not surf-school KPIs, and dense 70B is slow), `nvidia/nemotron-3-ultra-550b-a55b` (cost), all math-weak small models.

**Critical engineering note:** do **not** let the LLM compute the numbers. Compute aggregates in PostgreSQL / pandas, hand the model the resulting table, and ask it to *explain, compare and flag anomalies*. Every one of these models will still occasionally miscount rows. Where genuine calculation is needed, use tool calling into a Python function.

**RECOMMENDATION:** **PRIMARY `openai/gpt-oss-120b`** with `reasoning_effort="high"` for scheduled/batch analytics and `"low"` for interactive — top AIME score in our list, Apache 2.0, and a built-in cost dial. **FALLBACK `nvidia/nemotron-3-super-120b-a12b`** (higher composite intelligence index, and its 128K hosted context matters when we paste a full year of daily aggregates). Always feed pre-computed numbers; never ask the model to do the arithmetic.

---

## 8. Role 7 — Text embeddings for RAG

**The deciding factor is Turkish.** Our corpus is Turkish + English (lesson notes, WhatsApp-style customer messages, Turkish invoices, English equipment manuals). Verified language coverage:

| Model | Dim | Max tokens | Languages evaluated | Turkish? | License |
|---|---|---|---|---|---|
| **`nvidia/llama-nemotron-embed-1b-v2`** | Matryoshka (truncatable) | **8,192** | 26 (EN, AR, BN, ZH, CS, DA, NL, FI, FR, DE, HE, HI, HU, ID, IT, JA, KO, NO, FA, PL, PT, RU, ES, SV, TH, **TR**) | **YES** | NVIDIA Open Model License + Llama 3.2 Community License |
| `baai/bge-m3` | 1024 | 8,192 | **100+** | **YES** | MIT (cleanest) |
| `nvidia/nemotron-3-embed-1b` | 2,048 | **32,768** | 34 evaluated — **Turkish not among them** | No (unverified) | OpenMDW-1.1 |
| `nvidia/nv-embedcode-7b-v1` | — | — | code-specialised | n/a | NVIDIA |

`nvidia/nemotron-3-embed-1b` is the strongest model on paper (the 8B sibling is **#1 on the RTEB multilingual leaderboard** as of 2026-07-15; the 1B is SOTA for its size; 32K context; permissive OpenMDW-1.1) — but its published evaluation across 34 languages **does not include Turkish**. That is a hard disqualifier for a Turkish-language product until we benchmark it ourselves.

`baai/bge-m3` is the safety net: 100+ languages, 8,192 tokens, MIT, and it is symmetric (no `input_type` gymnastics), so it is also the easiest to swap in.

Additional specialised embedders worth wiring up:
* **`nvidia/nv-embedcode-7b-v1`** — code-specific embeddings for the AI terminal's repo search (a text embedder is measurably worse at code retrieval).
* **`nvidia/llama-nemotron-embed-vl-1b-v2`** or **`nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1`** — embed *page images* directly, so scanned manuals become searchable without an OCR step.
* **`nvidia/nvclip`** — image↔text embeddings for "find photos of cracked fins" over the damage-photo archive.

Ignore as legacy: `nvidia/embed-qa-4`, `nvidia/nv-embed-v1`, `nvidia/nv-embedqa-mistral-7b-v2` (7B embedder = needless cost), `snowflake/arctic-embed-l` (English-only, 512 tokens).

Reminder from §1.2: index with `input_type="passage"`, search with `input_type="query"`, always `truncate="END"`, via `extra_body`. Store the model id alongside every vector — switching embedders means a full re-index.

**RECOMMENDATION:** **PRIMARY `nvidia/llama-nemotron-embed-1b-v2`** — the only top-tier NVIDIA embedder with **verified Turkish**, plus 8K context and Matryoshka dimensions (truncate to 512 dims for pgvector index size, keep 1024+ for reranking-quality scoring). **FALLBACK `baai/bge-m3`** — MIT, 100+ languages, drop-in. Benchmark `nvidia/nemotron-3-embed-1b` on our own Turkish eval set before considering it; adopt only if it wins on real data. Add `nvidia/nv-embedcode-7b-v1` for the code index and `nvidia/nvclip` for the photo index as separate, purpose-scoped stores.

---

## 9. Role 8 — Reranking

**Verified finding: our 102-model list contains no reranker.** NVIDIA does publish rerank NIMs — `nvidia/llama-3.2-nv-rerankqa-1b-v2` (multilingual/cross-lingual QA reranking, 8,192-token documents, 3.5× smaller than the older `nv-rerankqa-mistral-4b-v3`) and `nvidia/llama-nemotron-rerank-1b-v2` — but **neither id is in our account's list**. They also live on a different host and path (`https://ai.api.nvidia.com/v1/retrieval/{model}/reranking`, or `/v1/ranking` on some NIMs), not on `integrate.api.nvidia.com/v1`.

So reranking must be synthesised. Options, in order:

1. **LLM-as-reranker (recommended).** Send the query plus the top-20 candidate chunks (id + first ~300 chars) to a cheap fast model and ask for a JSON array of `{id, score}` sorted descending. With `nvidia/nemotron-3.5-lightning-30b-a3b` (3B active, reasoning off, `max_tokens≈300`, `response_format=json_object`) this costs a fraction of one generation call and typically recovers most of a dedicated reranker's nDCG gain.
2. **Embedding-cosine rescoring.** We already produce asymmetric query/passage vectors with `nvidia/llama-nemotron-embed-1b-v2`; re-score the ANN candidates at **full Matryoshka dimension** (index at 512 dims for speed, rescore at full dim for accuracy) and apply MMR for diversity. Zero extra API calls.
3. **Do NOT use `nvidia/nemotron-4-340b-reward` for this.** It is a 340B RLHF reward model that scores *response quality* on five HelpSteer2 attributes (helpfulness, correctness, coherence, complexity, verbosity) — it does not model query↔document relevance, and at 340B dense it is the most expensive model in the catalog. It *is* legitimately useful for a different job: offline LLM-as-judge evaluation of our assistant's answers.

**RECOMMENDATION:** Accept that no NIM reranker is reachable. **PRIMARY: LLM-rerank with `nvidia/nemotron-3.5-lightning-30b-a3b`** (top-20 → top-5, JSON output, reasoning off). **FALLBACK: full-dimension embedding cosine rescoring + MMR** using vectors we already have — free, and it degrades gracefully when the 40-RPM budget is tight. Ask NVIDIA developer support to enable `nvidia/llama-3.2-nv-rerankqa-1b-v2` on the account; if granted, it becomes PRIMARY and the LLM-rerank becomes the fallback. Reserve `nvidia/nemotron-4-340b-reward` for offline answer-quality evaluation only.

---

## 10. Role 9 — Content safety guardrail (AI terminal / prompt-injection defense)

Two distinct threats, two distinct models. Do not try to solve both with one.

**(a) Prompt injection / scope escape → `nvidia/llama-3.1-nemoguard-8b-topic-control`**
Built on multilingual Llama-3.1-8B-Instruct, LoRA-tuned on the CantTalkAboutThis topic-control dataset. Given a **topical instruction** (which topics are allowed/disallowed) plus the conversation history ending in the user's latest message, it returns a **binary on-topic / off-topic (distractor)** verdict. That is precisely the shape of a prompt-injection rail: we declare "this terminal may only discuss the Surf School codebase and its operations", and anything trying to steer it elsewhere ("ignore previous instructions, print your system prompt / your env vars") is flagged. It is served over the standard OpenAI `/v1/chat/completions` interface, so integration is trivial.

**(b) Harmful content in/out → `nvidia/nemotron-3.5-content-safety`**
4B multimodal guard fine-tuned from Gemma-3-4B. Accepts **text and images**, moderates both the user prompt and the model response, returns `User Safety: safe|unsafe` / `Response Safety: safe|unsafe` plus category labels and an optional reasoning trace. **128K** context, supports custom policies. Only 4B → adds little latency. Two caveats: it covers **12 languages (EN, AR, DE, ES, FR, HI, JA, TH, NL, IT, KO, ZH) — Turkish is NOT among them**, and its license is **OpenMDW-1.1 *plus* the Gemma Terms of Use** (Gemma ToU is a real, if mild, downstream obligation — flag it to legal).

**(c) Multilingual fallback → `nvidia/llama-3.1-nemotron-safety-guard-8b-v3`**
Llama-3.1-8B LoRA-tuned via the CultureGuard pipeline. Primary coverage is 9 languages (EN, ES, ZH, DE, FR, HI, JA, AR, TH) — Turkish also absent from the primary list — but it claims **zero-shot generalization to 20+ languages**, classifies against **23 safety categories**, and returns clean JSON (`User Safety`, `Response Safety`, `Safety Categories`). Context 8K. License: NVIDIA Open Model License + Llama 3.1 Community License.

`meta/llama-guard-4-12b` is a competent generic alternative but brings the Llama Community License and no Turkish advantage.

Given that **no guard model in our list officially covers Turkish**, the practical answer is a **translate-then-guard** path: for Turkish user input, run `nvidia/riva-translate-4b-instruct-v2` (Role 10) to English first, then guard. It adds ~1 cheap 4B call and closes the biggest hole.

**RECOMMENDATION:** Two-stage rail. **On input: `nvidia/llama-3.1-nemoguard-8b-topic-control`** as PRIMARY prompt-injection/scope guard for the AI Development Terminal (binary on/off-topic against an explicit topical policy). **On input and output: `nvidia/nemotron-3.5-content-safety`** as PRIMARY harmful-content classifier (4B, multimodal so it also screens uploaded damage photos, 128K). **FALLBACK `nvidia/llama-3.1-nemotron-safety-guard-8b-v3`** when we need broader zero-shot language coverage or the 23-category taxonomy. For Turkish input, translate to English via `riva-translate-4b-instruct-v2` before guarding. Non-negotiable non-model controls remain: the terminal runs with least-privilege DB credentials, no shell escape, an allowlisted command set, and all model output is treated as untrusted data — the guardrail is defence-in-depth, not the perimeter. Have legal confirm the Gemma Terms of Use attached to `nemotron-3.5-content-safety`.

---

## 11. Role 10 — Turkish ↔ English translation

* **`nvidia/riva-translate-4b-instruct-v2`** — NMT NIM covering **English + 36 non-English languages (37 total)**, and **Turkish is explicitly supported**: the language-pair codes `en-tr` and `tr-en` are documented. 4B parameters → very low latency and very low credit cost. Context **8,192** tokens. License: **NVIDIA Open Model License Agreement + Apache 2.0**.
* **API shape gotcha (important):** it uses the chat template, but the **system message content must be the language-pair code itself**, not an instruction:

```python
client.chat.completions.create(
    model="nvidia/riva-translate-4b-instruct-v2",
    messages=[
        {"role": "system", "content": "tr-en"},          # NOT "You are a translator"
        {"role": "user",   "content": "Ders yarın sabah 9'da başlıyor."},
    ],
    max_tokens=256,
)
```

Sending a normal system prompt will produce garbage. Chunk anything over ~6K tokens.

* **`nvidia/riva-translate-4b-instruct-v1.1`** (and the base `nvidia/riva-translate-4b-instruct`) — same family, previous revisions; keep v1.1 as the version-pinned fallback in case v2 is rate-limited or regressed.
* **Where a general LLM is better:** a dedicated NMT model is faithful but *literal*. For marketing copy, tone-matched customer replies, and idiomatic surf jargon ("takla attı", "set geliyor"), a general model produces better output. Use `nvidia/nemotron-3-super-120b-a12b` (Role 1's PRIMARY — no extra model to onboard) or `google/gemma-3-12b-it` for a cheaper localisation pass.

**RECOMMENDATION:** **PRIMARY `nvidia/riva-translate-4b-instruct-v2`** for all mechanical translation — UI strings, lesson notes, customer messages, and the translate-then-guard path in Role 9. It is the only purpose-built NMT model in our list, Turkish is verified in both directions, and at 4B it is the cheapest and fastest option available. **FALLBACK `nvidia/riva-translate-4b-instruct-v1.1`.** Route *marketing and tone-sensitive* text to `nvidia/nemotron-3-super-120b-a12b` instead, with an explicit style brief. Wrap the pair-code system message in a helper function so no caller can accidentally send a prose system prompt.

---

## 12. Proposed Django configuration

```python
# settings.py
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_VLM_LEGACY_BASE_URL = "https://ai.api.nvidia.com/v1/vlm"   # only if we ever need legacy VLMs

NVIDIA_MODELS = {
    # role:            (primary,                                    fallback)
    "assistant":       ("nvidia/nemotron-3-super-120b-a12b",        "openai/gpt-oss-120b"),
    "router":          ("nvidia/nemotron-3.5-lightning-30b-a3b",    "nvidia/nemotron-3-nano-30b-a3b"),
    "code":            ("poolside/laguna-xs-2.1",                   "nvidia/nemotron-3-super-120b-a12b"),
    "code_autocomplete": ("mistralai/codestral-22b-instruct-v0.1",  "poolside/laguna-xs-2.1"),
    "vision":          ("nvidia/nemotron-nano-12b-v2-vl",           "meta/muse-glimmer-30b"),
    "doc_parse":       ("nvidia/nemotron-parse",                    "nvidia/nemoretriever-parse"),   # NON-CHAT
    "doc_qa":          ("nvidia/nemotron-nano-12b-v2-vl",           "meta/muse-glimmer-30b"),
    "analytics":       ("openai/gpt-oss-120b",                      "nvidia/nemotron-3-super-120b-a12b"),
    "embed_text":      ("nvidia/llama-nemotron-embed-1b-v2",        "baai/bge-m3"),                  # /v1/embeddings
    "embed_code":      ("nvidia/nv-embedcode-7b-v1",                "nvidia/llama-nemotron-embed-1b-v2"),
    "embed_image":     ("nvidia/nvclip",                            "nvidia/llama-nemotron-embed-vl-1b-v2"),
    "rerank":          ("nvidia/nemotron-3.5-lightning-30b-a3b",    None),   # LLM-rerank; no NIM reranker available
    "guard_topic":     ("nvidia/llama-3.1-nemoguard-8b-topic-control", None),
    "guard_safety":    ("nvidia/nemotron-3.5-content-safety",       "nvidia/llama-3.1-nemotron-safety-guard-8b-v3"),
    "translate":       ("nvidia/riva-translate-4b-instruct-v2",     "nvidia/riva-translate-4b-instruct-v1.1"),
    "eval_judge":      ("nvidia/nemotron-4-340b-reward",            None),   # offline only, returns SCORES
}
```

Windows 11 / no-Docker notes:
* Nothing here needs a local GPU or a container — every model is hosted. `pip install openai httpx` is the whole dependency surface.
* Long-running calls (analytics with `reasoning_effort=high`, document ingestion) must not block Gunicorn/Waitress workers. Use Celery+Redis if available; otherwise Django 5 async views with `httpx.AsyncClient` are sufficient for our volume.
* Streaming to HTMX: expose an SSE view that proxies the OpenAI SDK's `stream=True` iterator; on Windows use `Daphne`/`Uvicorn` (ASGI) rather than plain WSGI, since WSGI buffers SSE.
* Set `timeout=120` on the client and a hard `max_tokens` everywhere — an unbounded reasoning model can otherwise eat the whole credit balance in one request.

**RECOMMENDATION:** Adopt the `NVIDIA_MODELS` dict above verbatim as the single source of truth, wrap it in one `services/ai/` package with three transport classes (`ChatService`, `EmbeddingService`, `ParseService`), and make every call site pass a **role name**, never a model id. That way the whole selection in this document can be re-tuned in one file when NVIDIA ships the next Nemotron.

---

## 13. Open items to verify with real traffic

1. Benchmark `nvidia/nemotron-3-embed-1b` vs `nvidia/llama-nemotron-embed-1b-v2` on a Turkish eval set — the former is the stronger model but has no published Turkish evaluation.
2. Confirm `nvidia/nemotron-parse` accuracy on **Turkish** invoices (it is documented as English-first); if weak, route Turkish docs to `nvidia/nemotron-nano-12b-v2-vl`.
3. Request enablement of `nvidia/llama-3.2-nv-rerankqa-1b-v2` (or `nvidia/llama-nemotron-rerank-1b-v2`) on the account — it would replace the LLM-rerank workaround in Role 8.
4. Request a rate-limit increase from 40 → 200 RPM before launch; measure per-role RPM in staging first.
5. Legal review of the license stack actually in use: NVIDIA Open Model License (Nemotron 3, Riva Translate), Apache 2.0 (gpt-oss, muse-glimmer), OpenMDW-1.1 (laguna-xs-2.1, nemotron-3.5-content-safety), MIT (bge-m3), plus Llama 3.1/3.2 Community License and Gemma Terms of Use on the derived guard models.
6. Measure real TTFT for Role 1 PRIMARY under our 40-RPM ceiling; if it exceeds ~2s consistently, demote to `nvidia/nemotron-3.5-lightning-30b-a3b` for chat and keep Super for the "deep analysis" path only.

---

## Sources

- [NVIDIA Debuts Nemotron 3 Family of Open Models](https://nvidianews.nvidia.com/news/nvidia-debuts-nemotron-3-family-of-open-models)
- [Introducing Nemotron 3 Super — NVIDIA Technical Blog](https://developer.nvidia.com/blog/introducing-nemotron-3-super-an-open-hybrid-mamba-transformer-moe-for-agentic-reasoning/)
- [NVIDIA Nemotron 3.5 Lightning — NVIDIA Technical Blog](https://developer.nvidia.com/blog/nvidia-nemotron-3-5-lightning-delivers-fast-accurate-specialized-task-execution-for-long-running-agents/)
- [nemotron-3.5-lightning-30b-a3b model card](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard)
- [Nemotron 3 Nano technical report (arXiv 2512.20848)](https://arxiv.org/html/2512.20848v1)
- [Nemotron 3 Super vs gpt-oss-120b — Artificial Analysis](https://artificialanalysis.ai/models/comparisons/nvidia-nemotron-3-super-120b-a12b-vs-gpt-oss-120b)
- [Use the API (OpenAI) — NeMo Retriever Embedding NIM](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/2.2.0/use-the-api-openai.html)
- [API Reference — NeMo Retriever Embedding NIM](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/reference.html)
- [NVIDIA NIM provider — LiteLLM docs](https://docs.litellm.ai/docs/providers/nvidia_nim)
- [NVIDIAEmbeddings integration — LangChain docs](https://docs.langchain.com/oss/python/integrations/embeddings/nvidia_ai_endpoints)
- [nvidia/Nemotron-3-Embed-1B-BF16 — Hugging Face](https://huggingface.co/nvidia/Nemotron-3-Embed-1B-BF16)
- [Nemotron 3 Embed ranks #1 on RTEB — Hugging Face blog](https://huggingface.co/blog/nvidia/nemotron-3-embed-wins-rteb)
- [nvidia/llama-nemotron-embed-1b-v2 — build.nvidia.com](https://build.nvidia.com/nvidia/llama-nemotron-embed-1b-v2/modelcard)
- [BAAI/bge-m3 — Hugging Face](https://huggingface.co/BAAI/bge-m3)
- [llama-3.2-nv-rerankqa-1b-v2 — build.nvidia.com](https://build.nvidia.com/nvidia/llama-3_2-nv-rerankqa-1b-v2)
- [Introducing Laguna XS 2.1 — Poolside](https://poolside.ai/blog/introducing-laguna-xs-2-1)
- [poolside/Laguna-XS-2.1 — Hugging Face](https://huggingface.co/poolside/Laguna-XS-2.1)
- [NVIDIA Nemotron Nano V2 VL (arXiv 2511.03929)](https://arxiv.org/html/2511.03929v2)
- [Turn Complex Documents into Usable Data with Nemotron Parse 1.1](https://developer.nvidia.com/blog/turn-complex-documents-into-usable-data-with-vlm-nvidia-nemotron-parse-1-1/)
- [nemoretriever-parse — NeMo Retriever docs](https://docs.nvidia.com/nemo/retriever/26.1.1/extraction/nemoretriever-parse)
- [nvidia/Cosmos-Reason2-8B — Hugging Face](https://huggingface.co/nvidia/Cosmos-Reason2-8B)
- [Introducing Muse Glimmer — Meta AI Research](https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model)
- [Inkling model card — Thinking Machines Lab](https://thinkingmachines.ai/model-card/inkling/)
- [DeepSeek V4 Flash 0731 — Artificial Analysis](https://artificialanalysis.ai/models/deepseek-v4-flash)
- [GLM 5.2 vs Kimi K2.7 vs MiniMax M3 comparison](https://www.blog.brightcoding.dev/2026/07/06/glm-52-vs-kimi-k27-vs-minimax-m3-the-ultimate-open-weight-ai-showdown-of-2026)
- [Introducing gpt-oss — OpenAI](https://openai.com/index/introducing-gpt-oss/)
- [GPT-OSS-120B — NVIDIA NGC](https://catalog.ngc.nvidia.com/orgs/nim/openai/containers/gpt-oss-120b/-)
- [nvidia/Riva-Translate-4B-Instruct-v2 — Hugging Face](https://huggingface.co/nvidia/Riva-Translate-4B-Instruct-v2)
- [nvidia/Nemotron-3.5-Content-Safety — Hugging Face](https://huggingface.co/nvidia/Nemotron-3.5-Content-Safety)
- [Llama 3.1 Nemotron Safety Guard 8B NIM docs](https://docs.nvidia.com/nim/llama-3-1-nemotron-safety-guard-8b/latest/index.html)
- [nvidia/llama-3.1-nemoguard-8b-topic-control — Hugging Face](https://huggingface.co/nvidia/llama-3.1-nemoguard-8b-topic-control)
- [Restrict Topics with NemoGuard TopicControl NIM — NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/getting-started/tutorials/nemoguard-topiccontrol-deployment.html)
- [nemotron-4-340b-reward — build.nvidia.com](https://build.nvidia.com/nvidia/nemotron-4-340b-reward/modelcard)
- [API Reference — NVIDIA NIM for NV-CLIP](https://docs.nvidia.com/nim/nvclip/latest/api-reference.html)
- [neva-22b infer reference — docs.api.nvidia.com](https://docs.api.nvidia.com/nim/reference/nvidia-neva-22b-infer)
- [NVIDIA Build free tier, credits and limits](https://yangmao.ai/en/providers/nvidia-build/free-tier/)
