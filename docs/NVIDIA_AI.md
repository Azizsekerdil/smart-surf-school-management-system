# NVIDIA NIM Integration

Everything in this document was **measured against a live account**, not taken
from documentation. The raw probe output is in
[research/VERIFIED_API_PROBES.md](research/VERIFIED_API_PROBES.md). Where the two
disagree, the measurements win — and in several places they did.

---

## 1. Setup

1. Create an account at <https://build.nvidia.com>.
2. Generate an API key (profile → API Keys). It looks like `nvapi-…`.
3. Put it in `.env`:

   ```ini
   NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
   ```

4. Restart the server, then open **AI → AI Control Center** and press
   **Test NVIDIA**.

> The key is read from the environment only. It is never stored in the database,
> never written to a log, and never rendered in the UI — the Control Center shows
> "configured" or "not configured" and nothing more.

If no key is set, the NVIDIA provider reports itself disabled and the application
carries on with local AI (or with no AI at all).

---

## 2. Three findings that changed the implementation

### 2.1 Reasoning must be switched off explicitly

Nemotron 3 models are reasoning models. Called normally, a trivial prompt was
still running after 90 seconds, and the chain-of-thought appeared inside the
answer:

```
content = 'The user says: "Reply with exactly: OK". I should...'
```

Three approaches were tested:

| Attempt | Result |
|---|---|
| default | timed out; thinking leaked into `content` |
| system message `/no_think` | 3 563 ms; thinking still produced |
| **`chat_template_kwargs={"thinking": false}`** | **535 ms; `content="OK"`, `reasoning_content=""`** |

`apps/ai/providers/nvidia.py` therefore sends that flag on every interactive
call. A caller that genuinely wants deliberate reasoning passes `thinking=True`.

### 2.2 Reasoning arrives in its own field

When reasoning is on, it comes back as `reasoning_content`, separate from
`content`. The provider parses them separately and the UI shows reasoning in a
collapsed `<details>` block — never concatenated into the answer.

### 2.3 The model catalogue over-reports

`GET /v1/models` returned **102 models**, but several answered:

```
404 {"detail":"Function '…': Not found for account '…'"}
```

Model availability is therefore **probed**, not assumed. The Control Center's
**Probe models** button runs a real one-token completion against every configured
model and records the outcome. This is how you discover what your account can
actually use.

Models dropped from the design for exactly this reason:
`mistralai/codestral-22b-instruct-v0.1`, `ibm/granite-34b-code-instruct` and
`moonshotai/kimi-k2.6` — all 404. Shipping them would have produced a feature
that silently never worked.

---

## 3. Measured results

### Chat models

| Model | Result | Latency | Verdict |
|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | OK | **731 ms** | **Assistant primary.** Tool-calling verified working |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | OK | **535 ms** | **Fast/routing primary.** Quickest model tested |
| `z-ai/glm-5.2` | OK | 4 473 ms | Non-reasoning alternate |
| `nvidia/nvidia-nemotron-nano-9b-v2` | OK | 2 267 ms | Cheap alternate |
| `nvidia/nemotron-nano-12b-v2-vl` | OK | 24 748 ms (cold) | **Vision primary**, background only |
| `nvidia/riva-translate-4b-instruct-v2` | OK | **389 ms** | **Translation.** Purpose-built and fast |
| `openai/gpt-oss-120b` | OK at `reasoning_effort=low` | 17 242 ms | **Batch analytics only** — never interactive |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | OK | 21 396 ms | Too slow |
| `poolside/laguna-xs-2.1` | **503** | — | `ResourceExhausted` — shared capacity, fallback only |
| `meta/llama-3.3-70b-instruct` | timeout | — | Unreliable |
| `google/gemma-4-31b-it` | timeout | — | Unreliable |

### Embedding models

| Model | Result | Dimensions |
|---|---|---|
| `nvidia/llama-nemotron-embed-1b-v2` | OK (311 ms) | **2048** — primary |
| `nvidia/nemotron-3-embed-1b` | OK (464 ms) | 2048 |
| `nvidia/nv-embedqa-e5-v5` | OK (320 ms) | 1024 — fallback |
| `baai/bge-m3` | **500** | — |
| `snowflake/arctic-embed-l` | **404** | — |

---

## 4. The role map

Call sites ask for a role, never a model id. `apps/ai/models_catalog.py`:

| Role | Primary | Fallback |
|---|---|---|
| `assistant` | `nvidia/nemotron-3-super-120b-a12b` | `z-ai/glm-5.2` |
| `fast` | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nvidia-nemotron-nano-9b-v2` |
| `code` | `nvidia/nemotron-3-super-120b-a12b` | `poolside/laguna-xs-2.1` |
| `vision` | `nvidia/nemotron-nano-12b-v2-vl` | — |
| `analytics` | `openai/gpt-oss-120b` (low effort) | `nvidia/nemotron-3-super-120b-a12b` |
| `translate` | `nvidia/riva-translate-4b-instruct-v2` | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| `embedding` | `nvidia/llama-nemotron-embed-1b-v2` (2048) | `nvidia/nv-embedqa-e5-v5` (1024) |
| `guard` | `nvidia/llama-3.1-nemoguard-8b-topic-control` | `nvidia/nemotron-3.5-lightning-30b-a3b` |

Override any of these per-installation in **AI Control Center → NVIDIA →
Settings**, which writes `AIProviderConfig.model_overrides` — no code change and
no restart.

---

## 5. Embeddings

The endpoint is `/v1/embeddings`, never `/v1/chat/completions`. NVIDIA's
retrieval embedders are asymmetric and need `input_type`:

* `"passage"` when indexing a document,
* `"query"` when searching.

Getting this wrong does not raise an error — it silently degrades recall, which
is worse. `apps/ai/rag.py` passes the right value on each path.

`truncate: "END"` is always sent, so a long document chunk cannot fail an entire
batch with a 400.

**Dimensions matter.** A 2048-dimension index cannot be searched with a
1024-dimension query vector. Every `RagChunk` stores its embedding model and
width, and `rag.search()` only compares chunks that match the query embedding's
model. When the index contains more than one model, the Control Center shows a
"re-index required" warning rather than returning quietly wrong results.

---

## 6. Non-chat models

Several ids in the catalogue are not chat models. Calling
`chat/completions` on them fails or returns nonsense:

| Model | What it actually is | Correct endpoint |
|---|---|---|
| `nvidia/*-embed-*`, `baai/bge-m3`, `snowflake/arctic-embed-l` | Embedders | `/v1/embeddings` |
| `nvidia/nvclip` | CLIP image+text embedder | `/v1/embeddings` |
| `nvidia/nemotron-parse`, `nvidia/nemoretriever-parse` | Document OCR / layout parsers | dedicated inference endpoint |
| `nvidia/nemotron-4-340b-reward` | Reward model — returns scores, not prose | scored response |
| `nvidia/ai-synthetic-video-detector` | Classifier | dedicated endpoint |
| `nvidia/neva-22b`, `nvidia/vila`, `adept/fuyu-8b`, `microsoft/kosmos-2`, `google/deplot` | Legacy VLMs | `https://ai.api.nvidia.com/v1/vlm/{org}/{model}` — different host |

---

## 7. Rate limits and cost

The free developer tier allows roughly **40 requests per minute per model**,
account-wide, with a finite credit allowance.

Two design consequences:

1. **Load is spread across model ids.** Routing uses Lightning, chat uses Super,
   embeddings use the 1B embedder — so each has its own bucket rather than all
   contending for one.
2. **Every request is metered.** `AIUsageRecord` captures tokens, latency, cost
   estimate, success and whether a fallback was used. **AI → AI Usage & Costs**
   shows it by provider, model, operation and user.

Costs shown in the UI are **estimates** and labelled as such: NVIDIA's developer
tier bills in credits rather than dollars, so the per-million-token rates in
`models_catalog.py` give a sense of scale, not an invoice.

A per-provider monthly budget can be set in the Control Center; once reached,
cloud requests are refused and routing falls back to local models.

---

## 8. Testing the integration

**In the application:** AI Control Center → *Test NVIDIA*, then *Probe models*.

**From the command line:**

```powershell
.\.venv\Scripts\python.exe scripts\test_nvidia_ai.py
```

That script exercises connection, authentication, model listing, chat completion,
streaming, embeddings, timeout handling and error handling, and prints a table of
results. It reads the key from the environment and never prints it.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Authentication failed` | Missing or wrong key | Check `NVIDIA_API_KEY` in `.env`; it must start with `nvapi-` |
| `404 Not found for account` | The model is not enabled for you | Run *Probe models* and pick one that passes |
| `503 ResourceExhausted` | Shared capacity for that model is full | The router falls back automatically; try again later |
| `429` | Rate limit | Spread load, or request a limit increase from NVIDIA |
| Answers contain the model's thinking | `thinking: false` not applied | Only happens if you call the API directly; the bundled provider always sets it |
| Very slow replies | You are on a reasoning-heavy model | Use the `fast` role, or check which model the Control Center reports |
| RAG returns nothing after switching provider | The index was built with a different embedding model | **Re-index all** on the knowledge screen |
