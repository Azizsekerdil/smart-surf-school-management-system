# Local AI (LM Studio)

Local AI is the **default and recommended** configuration. It is free, keeps
customer data on the school's own machine, and keeps the assistant working when
the internet does not.

---

## 1. Why local first

| | Local (LM Studio) | Cloud (NVIDIA / Anthropic) |
|---|---|---|
| Cost | Free | Metered |
| Data | Never leaves the machine | Sent to a third party |
| Internet | Not required | Required |
| Latency | Depends on your hardware | Depends on the network |
| Quality on hard reasoning | Lower | Higher |

A surf school's assistant mostly answers "what is on today", "who is free at 11",
"which board suits a 78 kg beginner". A 12B local model handles that well. The
router sends the genuinely hard questions to the cloud only when a cloud provider
is configured — and works fine when none is.

---

## 2. Setup

1. Install [LM Studio](https://lmstudio.ai).
2. Download at least one instruct model. Verified working on this machine:

   | Model | Role |
   |---|---|
   | `google/gemma-4-12b-qat` | General assistant |
   | `qwen/qwen3-vl-8b` | Vision — equipment damage photos |
   | `qwen2.5-math-7b-instruct` | Statistics narration |
   | `moondream-2b-2025-04-14` | Lightweight vision |
   | `text-embedding-nomic-embed-text-v1.5` | **Embeddings — enables offline RAG** |

3. Open the **Developer** tab and press **Start Server** (default port `1234`).
4. That is all. `.env` already contains:

   ```ini
   LM_STUDIO_BASE_URL=http://localhost:1234/v1
   ```

5. Confirm it in the application: **AI → AI Control Center → Test Local AI**.

---

## 3. Get the embedding model

Without an embedding model the assistant still works, but the knowledge base
(RAG) does not — searching your own manuals and policies needs vectors.

In LM Studio, search for and download `nomic-embed-text-v1.5`. Once it is loaded,
the Control Center shows the RAG index as available and **Re-index all** on the
knowledge screen will build it.

This is the piece that makes the whole AI feature set work with **no cloud
account at all**.

---

## 4. Role mapping

`apps/ai/models_catalog.py` maps roles to local models:

| Role | Model |
|---|---|
| `assistant` | `google/gemma-4-12b-qat` |
| `fast` | `google/gemma-4-12b-qat` → `moondream-2b` |
| `vision` | `qwen/qwen3-vl-8b` → `moondream-2b` |
| `math`, `analytics` | `qwen2.5-math-7b-instruct` |
| `embedding` | `text-embedding-nomic-embed-text-v1.5` |

Override any of these in `.env` (`LM_STUDIO_MODEL_GENERAL`, `…_VISION`,
`…_MATH`, `…_EMBEDDING`) or in the Control Center — useful when you download a
model with a different name.

The model **id** must match what LM Studio reports. The Control Center lists the
ids it currently sees, so copy from there rather than guessing.

---

## 5. Routing modes

Set in the chat composer or in `.env` as `AI_ROUTING_MODE`:

| Mode | Behaviour |
|---|---|
| `local_only` | Never leaves the machine. Choose this if customer data must not be sent anywhere |
| `cloud_only` | Always uses a cloud provider |
| `auto` *(default)* | Short, simple, translation, maths and embedding work goes local first; hard reasoning goes cloud first. Falls back in both directions |

In `auto`, if the cloud is unreachable the answer still arrives — from the local
model, with a "used fallback" badge so the operator knows.

---

## 6. Hardware guidance

| RAM / VRAM | Realistic choice |
|---|---|
| 8 GB | A 3B model (`moondream`, a small Qwen). Usable for short answers |
| 16 GB | A 7–8B model. Good general assistant |
| 24 GB+ | A 12B quantised model (`gemma-4-12b-qat`). Comfortable |
| 32 GB+ | 12B plus a vision model plus embeddings loaded together |

Prefer quantised builds (`Q4_K_M`, `QAT`). The quality difference against the
full-precision weights is small for this workload; the memory difference is not.

Local generation is slower than a hosted API. The provider's timeout is 180 s,
deliberately generous — a slow free answer beats no answer.

---

## 7. Working offline

With LM Studio running and a cloud key absent or unreachable:

* the assistant answers from local models;
* the knowledge base works, if a local embedding model is loaded;
* every non-AI feature is entirely unaffected;
* the Control Center shows cloud providers as unavailable, and the chat shows
  which provider actually answered.

Surf conditions are the one genuinely network-dependent feature. The last fetched
conditions stay in the database and are shown with their timestamp and a "stale"
marker rather than disappearing.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "LM Studio is not running" | The local server is stopped | LM Studio → Developer → Start Server |
| Test passes but chat fails | No model loaded into memory | Load a model in LM Studio; downloading is not enough |
| "No model configured for role" | The configured id does not match LM Studio's | Copy the exact id from the Control Center's model list |
| Very slow replies | Model too large for the hardware | Use a smaller or more heavily quantised build |
| Knowledge base returns nothing | No embedding model | Download `nomic-embed-text-v1.5`, then **Re-index all** |
| Search worked, then stopped after changing models | The index was built with different-width vectors | **Re-index all** — the Control Center flags this explicitly |
| Port 1234 in use | Another process holds it | Change the port in LM Studio and update `LM_STUDIO_BASE_URL` |

---

## 9. Any OpenAI-compatible server

LM Studio is not special — it just speaks the OpenAI protocol. Ollama, vLLM,
LocalAI, text-generation-webui and llama.cpp's server all work the same way.
Either point `LM_STUDIO_BASE_URL` at them, or use the generic provider:

```ini
OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
OPENAI_COMPAT_MODEL=llama3.1:8b
OPENAI_COMPAT_API_KEY=not-needed
```

Both appear in the Control Center and participate in routing and cost tracking
like any other provider.
