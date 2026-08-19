# Architecture

**Smart Surf School Management System**
*Akıllı Sörf Okulu Yönetim Sistemi*

---

## 1. What this system is

A single application that runs the daily operations of a surf school: the people
(customers, students, instructors), the schedule (lessons, bookings, camps), the
gear (inventory, rentals, maintenance), the ocean (conditions, safety), the money
(finance, point of sale), and the reporting and AI layers that sit on top.

It is designed for a real business with a handful of staff on a beach, not for a
data centre. That single fact drives most of the decisions below: **it must
install and run on one Windows machine, keep working when the internet is down,
and never lose a booking or a payment.**

---

## 2. Technology decisions

### 2.1 Backend: Python 3.11 + Django 5.2 LTS + Django REST Framework

Django was specified, and it is the right call: this application is overwhelmingly
CRUD-with-business-rules over a relational schema, with a hard requirement for
authentication, per-role permissions, an admin surface, migrations, i18n and an
audit trail. Django provides all of that as a coherent whole.

Django 5.2 is the current LTS, supported to April 2028. DRF provides the REST API
that the brief requires and that any future mobile or SPA client would consume.

### 2.2 Frontend: Django templates + HTMX + Alpine.js + Tailwind CSS

The brief offered React + TypeScript as an example and explicitly allowed
"a modern Django frontend architecture" with a written justification. This is it.

**The decision.** Server-rendered Django templates, with HTMX for partial page
updates and Alpine.js for local component state, styled with Tailwind CSS. The
full DRF API exists alongside it and is not a second-class citizen.

**Why, given this project's actual constraints:**

| Consideration | Django + HTMX | React SPA |
|---|---|---|
| Surface area for ~28 modules and ~120 screens | One template per screen | Component + route + API client + state slice per screen |
| Permissions | One capability check, server-side, and the markup is never sent | Duplicated: server enforces, client must also hide |
| i18n (TR/EN) | Django's `gettext` covers Python and templates in one catalogue | A second, separate i18n stack for the client |
| Offline-first | Works — the server is local | Needs a build step, a dev server, and a bundle |
| Deployment on one Windows machine | `runserver` / one WSGI process | Two processes, or a build pipeline |
| Auth | Session cookie, CSRF, done | Token lifecycle, refresh, storage decisions |

The deciding argument is not developer preference — it is **how much of the
specified scope actually ends up working**. The brief lists 28 functional areas
and an 18-step acceptance test. Every hour spent on a client-side state layer is
an hour not spent making booking-conflict detection correct. HTMX gives the
interactivity that this application genuinely needs (live conflict checking while
a booking is typed, an inline POS cart, a calendar that pages without reloading,
a streaming AI chat) at a fraction of the cost.

**What we give up:** a fully client-side offline mode, and the ecosystem of React
component libraries. Neither is required here.

**How the door is kept open:** every module exposes a complete DRF viewset with
capability enforcement, so a React or mobile client can be added later against a
stable, documented API (`/api/docs/`) without touching the domain layer.

**No CDN.** HTMX, Alpine, Chart.js and the Lucide icon set are vendored into
`static/vendor/` by `scripts/vendor_assets.js`. The application renders correctly
with no internet connection.

### 2.3 Database: SQLite in development, PostgreSQL in production

PostgreSQL is the production target and is configured through a single
`DATABASE_URL`. It is not installed on the development machine, so development
and tests run on SQLite — which the brief explicitly permits.

To keep that honest, the ORM code is written portably: no `ArrayField`, no
PostgreSQL-only lookups, no `distinct("field")`, no raw SQL. Aggregates use
`Coalesce(..., Value(Decimal("0.00")))` so an empty table returns zero rather than
`None` on both engines. SQLite is configured with WAL journalling, a busy timeout
and enforced foreign keys so it behaves as closely to Postgres as it can.

### 2.4 Background work: Celery when available, otherwise inline

Redis is not installed. Rather than making the whole system depend on
infrastructure that is not there, `CELERY_TASK_ALWAYS_EAGER` defaults to true when
Redis is unreachable, so `.delay()` executes inline and every scheduled feature
still happens. Where a task genuinely needs to run on a timetable — condition
refresh, backups, reminders — there is also a management command, so Windows Task
Scheduler can drive it with no broker at all.

The cache follows the same rule: Redis if it answers a ping at startup, local
memory otherwise. Neither choice changes any calling code.

---

## 3. Application structure

```
D:\Surf_School\
    config/                 project configuration
        settings/           base · dev · prod · test
        urls.py             i18n-prefixed HTML routes + /api/
        api_urls.py         DRF router with per-app auto-discovery
        celery.py           optional; beat schedule
    apps/                   one package per business capability
    templates/              server-rendered HTML
    static/                 vendored JS/CSS/icons, compiled Tailwind
    assets/css/input.css    Tailwind source
    locale/                 tr/ and en/ message catalogues
    scripts/                PowerShell developer scripts
    docs/                   this documentation
    tests/                  cross-module integration tests
```

### 3.1 Layering inside an app

```
models.py       schema, field-level validation, derived properties
selectors.py    read queries
services.py     business rules and transactions   <-- decisions live here
views.py        HTTP orchestration only
api.py          DRF serializers + viewsets + ROUTES
forms.py        input validation and widgets
tasks.py        background entry points
```

A view never contains a multi-step business rule. "Can this student join this
lesson?" is answered by `apps.bookings.services.check_booking_conflicts`, which is
called identically from the HTML view, the REST API and the AI tool layer — so
the three can never disagree.

### 3.2 Dependency direction

```
core  ←  everything          (core imports nothing from apps.*)
accounts, audit  ←  every business app
business apps  →  each other only through string FKs and lazy apps.get_model()
ai, ai_terminal  →  read the domain through the same services as the UI
dashboard, analytics, reporting  →  read-only consumers of every module
```

Cross-app foreign keys are declared as strings (`"customers.Customer"`) and
cross-app *behaviour* goes through `django.apps.apps.get_model()`. This is not
ceremony: it is what lets `bookings` ask `safety` about restrictions without
`safety` having to exist, which in turn is what makes the modules independently
buildable and testable.

---

## 4. Access control

Two mechanisms, one source of truth.

**Capabilities.** A capability is a string, `"<module>.<action>"`, e.g.
`bookings.add`, `finance.refund`, `ai_terminal.approve`. The matrix in
`apps/accounts/constants.py` maps each of the 15 roles to its capability set. That
one table drives:

* the navigation menu (`apps/core/context_processors.py`)
* HTML views (`CapabilityRequiredMixin`)
* the REST API (`HasCapability`)
* the AI tool layer (a tool runs with the requesting user's permissions)
* the `{% can %}` template tag

Because the UI and the API read the same matrix, a screen can never offer an
action the API would reject.

**Django permissions.** `manage.py bootstrap_roles` projects the matrix onto
Django groups and model permissions, so the admin and any third-party package
that reads `user.groups` see the same thing.

Individual exceptions are expressed per user as `extra_capabilities` (grants) and
`denied_capabilities` (revocations). Denials always win.

---

## 5. The AI layer

### 5.1 Provider abstraction

```
apps/ai/providers/
    base.py            BaseAIProvider, ChatMessage, ChatResponse, EmbeddingResponse
    openai_compat.py   shared HTTP transport for OpenAI-compatible endpoints
    lmstudio.py        local, free, offline
    nvidia.py          NVIDIA NIM
    anthropic.py       Claude (different wire format — translated here)
    registry.py        discovery and health reporting
```

Call sites ask for a **role** (`assistant`, `fast`, `vision`, `analytics`,
`translate`, `embedding`, `guard`), never a model id. `models_catalog.py` resolves
a role to a concrete model plus an ordered fallback chain.

**A failed provider is a value, not an exception.** Every method returns
`ok=False` with a human-readable message. The application is fully usable with
every AI backend offline.

### 5.2 Model selection is based on measurement, not documentation

`docs/research/VERIFIED_API_PROBES.md` records live calls made from this machine.
The results changed the design in ways the documentation did not predict:

* NVIDIA Nemotron models are reasoning models. Without
  `chat_template_kwargs={"thinking": false}` a trivial prompt exceeded 90 seconds
  and chain-of-thought leaked into the answer. With it: **535 ms and a clean
  reply.** That flag is now the default on every interactive call.
* Reasoning arrives in a separate `reasoning_content` field. It is stored and
  displayed collapsed, never concatenated into the answer.
* `/v1/models` lists 102 models but several return `404 Not found for account`.
  The Control Center therefore **probes** models instead of trusting the list.
* `openai/gpt-oss-120b` takes 17 s even at low reasoning effort, so it is reserved
  for background analytics and never put on an interactive path.
* Embedding width differs per model (2048 vs 1024 vs 768). Every RAG chunk stores
  its model and dimension, and search refuses to compare across them.

### 5.3 Routing

`local_only` · `cloud_only` · `auto`. In `auto`, cheap and short work goes to the
local model first and hard reasoning goes to the cloud first, with fallback in
both directions. Every call — whatever the path — is recorded in `AIUsageRecord`,
so the cost dashboard cannot understate usage.

### 5.4 Grounding: tools, not prompts

The assistant cannot state a number it did not obtain from a tool call. Tools
(`apps/ai/tools.py`) run real queries, are **capability-checked against the
requesting user**, and return an explicit `"__no_data__"` marker when there is
genuinely nothing — which the model is instructed to relay rather than fill in.

This is what makes "the AI must not invent data" enforceable rather than
aspirational: there is no path by which a figure reaches the user without a query
having produced it.

### 5.5 The AI is never the final safety authority

Every AI-produced suggestion is rendered inside `.ai-surface` with an
"AI Recommendation" chip. In the data model, an AI-suggested safety warning is not
authoritative until a named staff member acknowledges it. Surf scores are computed
by deterministic code from published thresholds — the AI may narrate them, never
produce them.

---

## 6. The AI Development Terminal

The highest-risk feature in the brief, so its boundary is explicit.

**The model never executes anything.** It proposes. `apps/ai_terminal/security.py`
decides, and a human approves.

1. **No shell.** Commands run as an argument vector with `shell=False`. Parsing
   rejects every shell metacharacter, so chaining, piping and redirection are
   structurally impossible rather than filtered.
2. **Executable allowlist.** `git`, `python`, `pytest`, `ruff`, `bandit`, `pip`,
   `coverage`. Anything else is refused.
3. **Sub-command rules.** `git status` is safe; `git push`, `git reset` and
   `git rebase` are blocked outright. `manage.py test` is safe; `manage.py flush`
   is blocked; `manage.py migrate` needs approval. `python -c` is arbitrary code
   execution and is never permitted.
4. **Workspace jail.** Path arguments are resolved and must sit inside the
   workspace. Windows-specific escapes are handled explicitly: UNC paths,
   drive-relative paths (`C:foo`), 8.3 short names, reserved device names
   (`CON`, `NUL`, `COM1`), and NTFS alternate data streams. `.env`, `.git/config`
   and the database file are protected even from reads.
5. **Approval gate.** Anything not classified safe stores a proposal and does
   nothing. An approver may edit the command first — and the edit is re-validated
   from scratch, because approval is not a policy bypass.
6. **Execution hygiene.** Clean environment with credentials stripped, stdin at
   `DEVNULL` so nothing can prompt, `CREATE_NEW_PROCESS_GROUP` plus `taskkill /T`
   so a timeout kills the whole tree, and a hard output cap.
7. **Everything is audited**, including refusals.

Code changes follow the same shape: propose → diff → human approves → optional git
checkpoint branch → apply → revert available.

---

## 7. Data integrity

* **Money is always `Decimal(12, 2)`** via `money_field()`. There is no float
  anywhere in a monetary path.
* **Multi-row operations are wrapped in `transaction.atomic()`** — creating a
  booking writes the booking *and* the attendance, or neither.
* **Soft delete by default.** `BaseModel` hides deleted rows from `.objects` and
  exposes them on `.all_objects`, so a mis-click never destroys history.
* **The audit log is append-only.** `AuditLog.save()` refuses to update an
  existing row.
* **Stock is a ledger.** `pos.StockMovement` rows are append-only and the
  quantity is derived from them; a voided sale writes a compensating movement
  rather than deleting one.

---

## 8. Security posture

| Threat | Control |
|---|---|
| SQL injection | ORM only; no raw SQL, no string interpolation |
| XSS | Django autoescaping; user content never `mark_safe`d; Markdown sanitised with bleach |
| CSRF | Django middleware; HTMX sends the token from the cookie on every request |
| Brute force | `django-axes`, lockout by IP + username |
| Secrets in logs | `SecretRedactionFilter` on every handler, with patterns for `nvapi-`, `sk-ant-`, `sk-`, `gh*_`, bearer tokens, JWTs and DSN passwords |
| Secrets in the database | API keys come from the environment; `AIProviderConfig` deliberately has no key field |
| File upload abuse | Extension + declared MIME + magic-byte agreement, size caps, filename sanitisation covering Windows device names |
| Path traversal | `Path.resolve()` + `is_relative_to()`, plus the Windows-specific cases listed in §6 |
| Command injection | See §6 — no shell exists to inject into |
| Prompt injection | Retrieved content is framed as untrusted data; the model cannot execute; tools are capability-checked; safety decisions require human sign-off |
| Privilege escalation via AI | Tools run with the requesting user's capabilities, not the system's |

---

## 9. Internationalisation

Full Turkish and English. Source strings are English; Turkish lives in the
catalogue. URLs are language-prefixed (`/tr/…`, `/en/…`) and the switcher is in
the top bar.

GNU gettext is **not installed on Windows**, which would normally make
`makemessages` and `compilemessages` impossible. Rather than adding a system
prerequisite, the project ships pure-Python replacements:

* `manage.py i18n_extract` — walks the tree, parses Python with `ast` (so it finds
  every `gettext` variant and never picks up a string from a comment) and scans
  templates for `{% translate %}` / `{% blocktranslate %}`. It merges into the
  existing catalogue and keeps vanished strings as obsolete entries rather than
  discarding translations.
* `manage.py i18n_compile` — writes the binary `.mo` format directly.

---

## 10. Observability

* **Structured logging** with per-area handlers (`surf_school.log`,
  `security.log`, `ai.log`) and JSON formatting for the security and AI streams.
* **`/api/health/`** probes the database, cache, Celery, LM Studio, NVIDIA,
  Anthropic and the surf-data provider, reporting per-component latency. It
  returns a minimal payload to anonymous callers and full detail to
  authenticated staff.
* **Audit trail** for every money, permission, backup and AI-initiated action.
* **AI usage and cost dashboard** covering tokens, latency, failures and
  fallbacks by provider, model, operation and user.

---

## 11. Deliberate trade-offs

| Choice | Alternative | Why |
|---|---|---|
| HTMX over a React SPA | React + TypeScript | Delivers far more of the specified scope working; API stays open for a future SPA |
| Embeddings in `JSONField`, scored with numpy | pgvector / a vector database | Corpus is hundreds of chunks; brute force is milliseconds and adds no infrastructure |
| ReportLab | WeasyPrint | WeasyPrint needs GTK on Windows; ReportLab installs from a wheel |
| `segno` | `qrcode` | `qrcode` publishes contradictory licence classifiers |
| Capability strings | Django permissions alone | Django permissions cannot express "may approve an AI command" or "may restore a backup" |
| Soft delete everywhere | Hard delete | Operational data has history; an accidental delete must be recoverable |
| Eager Celery by default | Requiring Redis | The system must work on a laptop on a beach |
