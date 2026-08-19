# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2026-08-18

First release. Everything below is new.

### Foundation
- Django 5.2 LTS project with split settings (`base` / `dev` / `prod` / `test`)
- 28 apps, each with the same internal layering (`models` → `services` → `views`/`api`)
- `core`: abstract base models with UUID public ids, timestamps, authorship and
  soft delete; a project-standard `money_field()`; generic `Note` and `Document`
  attachments; runtime `SystemSetting`
- Shared enumerations and surf-domain thresholds in `apps/core/enums.py`
- Structured logging with automatic secret redaction on every handler
- `/api/health/` probing database, cache, Celery, LM Studio, NVIDIA, Anthropic
  and the surf-data provider
- SQLite (WAL, foreign keys enforced) for development; PostgreSQL-ready through
  `DATABASE_URL`
- Graceful degradation when Redis and Celery are absent

### Access control
- 15 roles with a capability matrix that drives the menu, HTML views, the REST
  API and the AI tool layer from one source of truth
- Per-user capability grants and denials, with denials winning
- Sign-in with username or e-mail, brute-force protection, session history
- Append-only audit log covering money, permissions, bookings, equipment,
  backups, exports and every AI action

### Operations
- Customers with waivers, documents, duplicate detection and merge
- Students with surf level, medical notes, skill assessments, board-volume and
  wetsuit recommendations
- Instructors with certification expiry tracking, weekly availability, time off,
  performance reviews and commission
- 10 lesson types with capacity and instructor-ratio safety rules (stricter for
  under-18 groups), roster check-in and equipment assignment
- Bookings with server-rendered calendar, conflict detection across instructor,
  student, equipment, level and safety restrictions, waitlist promotion, and
  cancellation with time-based fees
- Surf camps with day-by-day programmes, participants, accommodation, transfers
  and financials

### Equipment
- QR-labelled inventory with volume, size and rider-weight matching
- Rentals with hourly/daily/weekly pricing, deposits, late fees (capped at 3× the
  hire), damage charges and check-in/check-out screens
- Maintenance with a ding/damage taxonomy, service history, costs, and a
  **statistical** maintenance-risk forecast computed from real service history

### Surf & safety
- Pluggable surf-data provider architecture; Open-Meteo by default (no API key),
  met.no as a commercially-clean fallback
- A 0–100 **Surf Score** per skill level, computed deterministically from
  published thresholds, with a per-factor breakdown so the number can be
  explained — and a hard safety gate that caps it when wave height or wind
  exceeds the level's limit
- Safety incidents, lifeguard rostering, emergency contacts, evacuation plans,
  equipment checks, weather warnings and student restrictions
- AI-suggested warnings are not authoritative until a named staff member
  acknowledges them

### Business
- Invoices, payments, refunds, expenses, instructor commissions, lesson packages
- Point of sale with barcode entry, an append-only stock ledger and receipts
- Statistics engine: mean, median, standard deviation, percentiles, moving and
  weighted averages, exponential smoothing, linear regression, trend,
  correlation, seasonality, outliers and forecasting — with explicit low-
  confidence warnings on thin data
- Analytics dashboards with period-over-period comparison
- 20 reports exportable to PDF (ReportLab), Excel (openpyxl) and CSV

### Backup
- Manual and scheduled backups with SHA-256 checksums and content verification
- SQLite captured through the `sqlite3` backup API, not a file copy
- PostgreSQL via `pg_dump` with the password passed through the environment
- Restore requires verification, a typed confirmation code, and takes an
  automatic safety backup first, rolling back on failure
- Retention policy that never deletes a manual backup or the latest good one

### Artificial intelligence
- Provider abstraction covering LM Studio (local), NVIDIA NIM, Anthropic and any
  OpenAI-compatible endpoint
- Role-based model selection with ordered fallback chains, driven by **measured**
  latency and availability rather than documentation
- Smart router with `local_only` / `cloud_only` / `auto` modes
- Tool-grounded assistant: the model can only state a figure obtained from a
  capability-checked database query, and reports "no data" rather than inventing
- RAG over the school's own documents, with per-chunk embedding-model and
  dimension tracking so incompatible vectors are never compared
- AI Control Center with live health checks and **model probing** — because the
  NVIDIA catalogue lists more models than an account can invoke
- Token and cost accounting per provider, model, operation and user, with
  optional monthly budgets

### AI Development Terminal
- Sandboxed console where the AI proposes and a human approves
- No shell: commands run as a validated argument vector with `shell=False`
- Executable allowlist, per-sub-command policy, and a workspace jail that handles
  Windows-specific escapes (UNC, drive-relative, 8.3 short names, reserved device
  names, alternate data streams)
- Approval gate with re-validation of edited commands
- Development agent that produces a plan and a reviewable diff, with an optional
  git checkpoint branch and one-click revert
- Every proposal, approval, rejection and execution audited — including refusals

### Interface
- Django templates + HTMX + Alpine.js + Tailwind CSS, all assets vendored (no CDN)
- Light and dark themes
- Full Turkish and English, with pure-Python `.po`/`.mo` tooling so GNU gettext is
  not a prerequisite on Windows
- Bilingual Help Center, interactive Training Center and a first-run onboarding
  wizard

### Tooling
- `scripts/setup.ps1`, `start.ps1`, `test.ps1`, `backup.ps1`
- `scripts/test_nvidia_ai.py` — standalone integration test for the NVIDIA API
- `manage.py bootstrap_roles`, `i18n_extract`, `i18n_compile`, `seed_demo_data`

### Notes on this release
- Development runs on SQLite because PostgreSQL is not installed on the build
  machine; the ORM is written portably and PostgreSQL is configured through a
  single environment variable
- Celery defaults to eager execution when Redis is unavailable; scheduled work is
  also exposed as management commands for Windows Task Scheduler
- Open-Meteo's data is CC BY 4.0 but its **free hosted tier is non-commercial** —
  see `docs/OPEN_SOURCE_LICENSES.md` before a commercial deployment

[1.0.0]: https://github.com/Azizsekerdil/smart-surf-school-management-system/releases/tag/v1.0.0
