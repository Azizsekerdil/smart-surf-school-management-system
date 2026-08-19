# AI transparency

What the AI in this product does, what it is allowed to decide, what leaves your
machine, and how to turn all of it off.

---

## 1. The short version

- **The product works completely without AI.** No non-AI feature depends on it.
- **A fully local option exists and is free**: LM Studio with
  `AI_ROUTING_MODE=local_only`. Nothing leaves the machine.
- **The AI never has the final say** on safety, maintenance, pricing, medical or
  legal questions. Where it contributes, a named human signs off.
- **The AI cannot write SQL and cannot invent data.** It reaches the database
  only through 13 typed, capability-gated Python functions.
- **The one real risk is data leaving your machine** when you enable a cloud
  provider. Section 5 states exactly what goes and what does not.

---

## 2. Providers

| Provider | Where it runs | Enabled when | Cost |
|---|---|---|---|
| **LM Studio** (OpenAI-compatible) | Your machine | Always available | Free |
| NVIDIA NIM | NVIDIA's cloud (US) | `NVIDIA_API_KEY` is set | Your account |
| Anthropic Claude | Anthropic's cloud (US) | `ANTHROPIC_API_KEY` is set | Your account |
| Generic OpenAI-compatible | Wherever you point it | `OPENAI_COMPAT_BASE_URL` is set | Yours |

With no key, a provider reports **`NOT_CONFIGURED`** and makes **no network
call at all**. Local AI and every non-AI feature keep working.

**Keys are never written to the database.** They are read from the environment
only, stripped from AI-terminal child processes, excluded from backups (which
record variable *names*, never values), and redacted from every log line. The
interface shows the provider name, its status and the **last four characters** of
the key — never the key. "Test connection" runs only when you press it, and the
key is not logged when it does.

---

## 3. Routing — and the default you should probably change

`AI_ROUTING_MODE` takes three values:

| Mode | Behaviour |
|---|---|
| `local_only` | Everything runs on LM Studio. **Nothing leaves the machine.** |
| `cloud_only` | Everything goes to the configured cloud provider. |
| `auto` *(default)* | Simple requests go local; "hard" requests prefer the cloud with local as fallback. |

**A request that uses tools is always classified "hard".** Tool use is exactly
the case where the model is handed customer records. So on a default
installation *with a cloud key present*, asking the assistant about a customer
sends that customer's data to a US-hosted API.

That is a real property of the default configuration and it is stated here
rather than buried. If you handle personal data of people in the EU or Türkiye,
set `AI_ROUTING_MODE=local_only`.

---

## 4. What the AI can do

Thirteen typed tools, each gated on the calling user's capabilities. The model
chooses which to call; it cannot call one the user could not perform themselves,
and it cannot construct a query of its own.

Read-side tools return, among other things: customer search results, outstanding
payments, active rentals, lesson and camp schedules, instructor performance,
equipment status, surf conditions and open safety items.

Structural guarantees:

- **No SQL.** There is no text-to-SQL feature. Repository-wide, there is no
  `RawSQL`, `.raw()`, `.extra()`, `eval`, `exec` or `pickle` outside tests.
- **Mutating tools are off by default** (`include_mutating=False`).
- **Arguments are filtered to the declared schema** before a tool runs, so a
  model cannot smuggle an extra parameter.
- **Retrieved documents are framed as untrusted content** in the prompt, and the
  system prompt carries explicit anti-prompt-injection rules. Those are soft
  controls; the hard control is that the tool layer is capability-gated.

---

## 5. What is sent to a cloud provider, and what is not

**Sent** (when a cloud provider is selected for a request):

- your question;
- the system prompt;
- the results of any tool the model called — **including customer names, e-mail
  addresses and phone numbers when the tool returns them**;
- retrieved snippets from your own documents when RAG is used.

**Never sent:**

- your API keys for other providers;
- passwords or password hashes;
- the database, backups or log files;
- anything at all when the mode is `local_only` or no key is configured.

**Not implemented — say so plainly:** there is **no PII masking or
pseudonymisation** in the AI layer. The secret-redaction filter that protects
credentials in log lines does not apply to prompt payloads and does not match
personal data. There is no per-request human approval for a cloud call, no
region pinning, no recorded retention or training-use assumption for NVIDIA or
Anthropic, and no data-processing agreement in this repository.

**Retention and training by the provider** are governed by *your* contract with
that provider, not by this software. Read their terms.

---

## 6. What the AI is not allowed to decide

This is enforced in code, not merely stated:

| Decision | Rule |
|---|---|
| **Safety warnings** | An AI-suggested warning is **invisible** until a named staff member signs it off. The suggestion is labelled as an AI recommendation. |
| **Maintenance risk** | The score is computed statistically from service history and usage. `apps/maintenance/services.py` imports nothing from `apps.ai`. |
| **Surf Score** | A deterministic weighted calculation from measured wave, wind, period, tide, weather and water temperature, using published thresholds. Not a model output. |
| **Money** | The AI does not price, invoice, refund or take payment. |
| **Medical or legal questions** | Out of scope. The product gives no medical or legal advice. |
| **Running commands or changing code** | The AI terminal *proposes*; a human approves. Writing a file needs both an approval and a specific capability. There is no shell. |

---

## 7. Costs and limits

- Every AI request is recorded with its provider, model, token counts and
  estimated cost.
- `AIProviderConfig.monthly_budget_usd` sets a ceiling; requests are blocked once
  it is reached.
- The AI usage screen shows spend per provider and per period.
- Local models cost nothing and are not counted against a budget.

---

## 8. Auditing

Every AI query is written to the audit log with the user, the provider, the
model and which tools were called. The prompt and the response are **not**
stored in the audit row. Terminal actions additionally carry a SHA-256 hash
chain.

---

## 9. How to switch it off completely

```dotenv
AI_ROUTING_MODE=local_only
NVIDIA_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_COMPAT_API_KEY=
AI_TERMINAL_ENABLED=False
```

Then simply do not run LM Studio. Every AI surface degrades to "not configured";
nothing else changes.

---

## 10. Known limitations of the AI layer

- No PII masking (§5) — the most important one.
- Model answers can be wrong. Tool grounding reduces invention but does not
  eliminate it. Check anything that matters.
- Local model quality depends on which model you load.
- Automated tests never touch the network, so provider integrations are verified
  by hand and the AI-dependent acceptance steps report SKIPPED when no provider
  is reachable.
- Latency figures quoted in the presentation are single-run measurements from
  one machine, not benchmarks.
