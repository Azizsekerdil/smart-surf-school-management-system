# AI Architecture

How the AI layer is built, why it is built that way, and what stops it from
being dangerous or from making things up.

---

## 1. Design goals

| Goal | How it is met |
|---|---|
| Never invent data | The model can only obtain a figure by calling a tool that runs a real query |
| Never leak data | Tools run with the requesting user's capabilities |
| Never be the safety authority | AI safety output is a labelled recommendation requiring staff sign-off |
| Never be a hard dependency | Every AI path degrades to a clear message; the rest of the app is unaffected |
| Never lock in a vendor | Providers are pluggable; call sites name a role, not a model |
| Always be accountable | Every request is metered by provider, model, operation and user |

---

## 2. Layers

```
        UI  (chat · control center · usage · terminal)
        │
        services.py        prompting, the tool loop, usage accounting
        │
   ┌────┴────┬─────────────┐
   │         │             │
 tools.py  rag.py      router.py        capability-checked DB access ·
   │         │             │            retrieval · provider selection
   └────┬────┴─────────────┘
        │
   providers/       lmstudio · nvidia · anthropic · openai_compat
        │
   models_catalog.py     role → model + fallback chain
```

---

## 3. Providers

```python
class BaseAIProvider(ABC):
    name: str
    is_cloud: bool
    supports_vision / supports_tools / supports_streaming / supports_embeddings: bool

    def chat(messages, *, role, model=None, tools=None, ...) -> ChatResponse
    def stream_chat(messages, ...) -> Iterator[str]
    def embed(texts, *, input_type="passage") -> EmbeddingResponse
    def health_check() -> HealthResult
```

Four implementations ship:

| Provider | Transport | Notes |
|---|---|---|
| `lmstudio` | OpenAI-compatible | Local, free, offline. Always "configured" |
| `nvidia` | OpenAI-compatible | Handles the reasoning flag and `input_type` quirks |
| `anthropic` | Anthropic Messages | Different wire format, translated in both directions |
| `openai_compat` | OpenAI-compatible | Ollama, vLLM, LocalAI, anything else |

**A failure is a value, not an exception.** Every method returns `ok=False` with a
message a human can act on ("LM Studio is not running", "That model is not
enabled for this account"). Nothing in the application has to wrap an AI call in
`try/except`, and nothing crashes when every provider is down.

Adding a provider is one class plus one line in `providers/registry.py`.

---

## 4. Roles, not model names

No call site ever names a model. It asks for a role:

```python
router.chat(messages, role=AIRole.ASSISTANT.value)
```

`models_catalog.py` resolves the role per provider into a primary model and an
ordered fallback chain. Retuning the entire model line-up is a one-file change.

Roles: `assistant`, `fast`, `code`, `vision`, `analytics`, `math`, `translate`,
`embedding`, `guard`.

Each entry also records **measured** latency, whether the model is safe on an
interactive path, and any request extras it needs. Those measurements come from
live probes — see [NVIDIA_AI.md](NVIDIA_AI.md) §3 — and they removed three models
from the design that documentation would have kept.

---

## 5. Routing

```
local_only   never leaves the machine
cloud_only   always uses a cloud provider
auto         cost-aware (default)
```

In `auto`, complexity is estimated with a cheap heuristic — never a model call,
because routing must not itself cost a round-trip — and combined with the role:

* short questions, classification, translation, maths and embeddings → **local first**
* vision → **local first** (a local VLM is usually enough)
* hard reasoning, long context, tool use → **cloud first**

Fallback runs in both directions. If the primary fails, the next entry in the
chain is tried, up to three attempts, and the answer is flagged `used_fallback`
so the UI can show which provider actually replied.

The router is also the single choke point where usage is recorded, so no call can
escape the cost dashboard.

---

## 6. Grounding: tools, not prompt stuffing

This is the mechanism that makes "the AI must not invent data" enforceable.

```python
@register(
    "get_revenue_summary",
    "Total revenue for a period, split by source…",
    {...json schema...},
    capability="finance.view",
)
def get_revenue_summary(user, start_date=None, end_date=None) -> dict:
    ...
    if total == 0:
        return _empty(_("No revenue was recorded between … and …."))
```

Three properties matter:

1. **Capability-checked.** `tools_for_user()` only offers the model tools the
   requesting user may run, and `execute_tool()` re-checks before running. A
   rental clerk asking about payroll gets the same refusal the finance screen
   would give them — the assistant is not a privilege-escalation path.
2. **An explicit "no data" marker.** An empty result returns
   `{"status": "__no_data__", "message": …}`, and the system prompt instructs the
   model to relay that rather than fill the gap with a plausible example.
   "There were no bookings last week" is a correct answer.
3. **Schema-filtered arguments.** Arguments are filtered against the declared
   JSON schema before reaching Python, so the model cannot pass arbitrary keyword
   arguments into a function.

Tools cover lessons, bookings, revenue, outstanding payments, equipment usage,
maintenance risk, active rentals, instructor performance, customers, students,
surf conditions and safety.

---

## 7. RAG

Chunking splits on paragraph boundaries near a 1500-character target with a
200-character overlap, so a procedure step or a table row is not cut in half.

Storage is a `JSONField` scored with numpy rather than a vector database. For a
surf school's corpus — manuals, policies, safety procedures, help articles;
hundreds of chunks, not millions — a brute-force dot product is single-digit
milliseconds and adds no infrastructure. If the corpus ever outgrows that, only
`rag.search()` changes.

**Dimension safety is explicit.** NVIDIA's embedder returns 2048 dimensions, the
local `nomic` model 768, `nv-embedqa-e5-v5` 1024. Vectors from different models
are not comparable, and mixing them produces confidently wrong retrieval rather
than an error. Every chunk stores its embedding model and width; search only
compares chunks matching the query embedding's model; and when the index contains
more than one model the Control Center shows a re-index warning.

Retrieved passages enter the prompt inside an explicit *untrusted content* frame
(§9) and come back to the UI as citations, so an answer can always be traced to
its evidence.

---

## 8. The conversation loop

```
user message
  → RAG retrieval (optional)
  → system prompt (rules + user's capabilities + retrieved context)
  → router.chat(..., tools=schemas_for_user(user))
  → model requests tools?
       yes → execute each (capability-checked) → feed results back → repeat
       no  → done
  → persist AIMessage (content, reasoning, provider, model, tokens, latency,
                       tools used, citations)
```

The loop is bounded at four iterations. On the last one the model is told to
answer from what it already has and to say plainly what remains unknown.

Reasoning is stored in its own field and rendered collapsed — never concatenated
into the answer.

---

## 9. Prompt injection

The model reads customer notes, uploaded documents and database rows, any of
which can contain hostile text. Defences:

* **Framing.** Retrieved content is preceded by an explicit instruction that it
  is reference material and that any instruction inside it must be ignored and
  reported.
* **The system prompt states the precedence** — ground rules override anything
  found in data.
* **No execution path.** Nothing the model emits is executed. Terminal commands
  go through the policy engine; code changes go through human review; tool
  arguments are schema-filtered.
* **Capability checks are server-side**, so a model persuaded to call a
  forbidden tool still gets a refusal.

The structural point: **a fully hijacked model still cannot cause an effect.** It
can only produce a proposal that policy code evaluates and a person approves.

---

## 10. Cost and usage

`AIUsageRecord` captures, for every single request: provider, model, role,
operation, cloud flag, prompt/completion tokens, estimated cost, latency, success
and whether a fallback was used.

**AI → AI Usage & Costs** shows this by provider, model, operation and user, with
period-over-period comparison, a daily tokens-and-cost chart, and local-vs-cloud
share.

Costs are labelled **estimates**: NVIDIA's developer tier bills in credits, not
dollars, so the per-million-token rates give a sense of scale rather than an
invoice. Anthropic's rates are real list prices.

A per-provider monthly budget can be set; once reached, cloud requests are refused
and routing falls back to local models.

---

## 11. Where AI is used, and where it deliberately is not

| Feature | AI? |
|---|---|
| Assistant chat | Yes — grounded in tools |
| Knowledge base search | Yes — embeddings |
| Dashboard narrative | Yes — narrates numbers computed elsewhere |
| Damage photo assessment | Yes — vision, advisory only |
| TR ↔ EN translation | Yes |
| AI development terminal | Yes — proposes only |
| **Surf Score** | **No** — deterministic, from published thresholds |
| **Maintenance risk** | **No** — statistical, from service history |
| **Booking conflict detection** | **No** — explicit business rules |
| **Any money calculation** | **No** — Decimal arithmetic |
| **Any safety decision** | **No** — a named staff member decides |

The last five are the important ones. Where a wrong answer would cost money,
double-book a lesson or put a student in unsafe water, the answer is computed by
code that can be read, tested and reasoned about. The AI may explain those
numbers; it never produces them.

---

## 12. Testing

No test touches the network. `config/settings/test.py` points every provider at
an unroutable address, so an accidental live call fails immediately rather than
silently passing and costing money.

Provider tests use recorded responses; the router is tested for fallback ordering
and mode handling; tools are tested for capability enforcement and for returning
the "no data" marker rather than an empty success.
