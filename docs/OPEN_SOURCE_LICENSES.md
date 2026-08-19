# Open-Source Attribution & License Compliance

**Project:** Smart Surf School Management System
**Document owner:** Engineering (reviewed by Legal before any commercial release)
**Last updated:** 2026-08-15
**Basis:** consolidated from the six Phase-1 research files in `docs/research/`
(`OSS_REFERENCES.md`, `SURF_WEATHER_APIS.md`, `NVIDIA_MODEL_SELECTION.md`,
`PYTHON_LIBS_WINDOWS.md`, `SURF_DOMAIN_MODEL.md`, `AI_TERMINAL_SECURITY.md`).

> **Accuracy rule for this document.** A license is stated here **only** where a Phase-1
> research file recorded it against a verified source (repository `LICENSE` file, GitHub
> `license.spdx_id`, PyPI classifier, or the provider's own terms page). Anything not
> verified in that research is marked **`UNVERIFIED — must confirm before release`**.
> Do not "fill in" an unverified row from memory; verify it and cite the source.

---

## 1. Purpose and Policy Statement

This document is the single register of third-party material used by, referenced by, or
studied for the Smart Surf School Management System. It exists so that (a) every
attribution obligation we carry is discharged, and (b) no copyleft-encumbered code can
enter a proprietary, network-served product by accident.

### 1.1 The policy — two buckets, no third bucket

| Bucket | Licenses | What we may do |
|---|---|---|
| **PERMISSIVE** | MIT, MIT-CMU, BSD-2-Clause, BSD-3-Clause, 0BSD, Apache-2.0, ISC, PSF / PSF-based, Unlicense, ZPL-2.1 | **Code may be copied** into our proprietary codebase. We must retain the original copyright notice and license text in `THIRD_PARTY_NOTICES.md`. Apache-2.0 additionally requires propagating any `NOTICE` file and stating significant changes in modified files; in exchange it grants an explicit patent licence. |
| **COPYLEFT / RESTRICTED** | GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, AGPL-3.0, SSPL, **and any repository with no LICENSE file** | **ARCHITECTURAL IDEAS ONLY.** Read it, learn the data model, take notes, close the tab, write our own code from a blank file. Never copy a function, a model class, a template, a migration, a SQL query, or a config file. "It was only a small snippet" is not a defence. |

### 1.2 Three rules that follow from the policy

1. **The AGPL trap is the primary legal risk in this project.** This product is a *web
   application*. AGPL-3.0 §13 triggers on **network interaction**, not on distribution. If
   AGPL code reached our server, every customer who loaded a booking page would be entitled
   to our complete corresponding source. The most popular open-source systems in every one of
   our problem domains (asset checkout, gym management, CRM) are AGPL-3.0. See §4.
2. **"No license" is stricter than AGPL, not looser.** A public GitHub repository with no
   `LICENSE` file is *not* open source — default copyright applies, all rights reserved.
   Three widely-recommended "school / sports management" Django repos fall into this trap and
   are named and blacklisted in §4.3.
3. **Weak copyleft (LGPL) is accepted only for dynamically-linked, unmodified libraries.**
   Exactly one runtime dependency carries it (`psycopg`, §2.2). The obligation is inert for a
   server-deployed web app and becomes live only if we ship a bundled on-premise installer.

### 1.3 Standing obligations

- `THIRD_PARTY_NOTICES.md` must exist at the repository root and must be appended to the
  moment any third-party code or dependency is added.
- A CI check must fail the build if a new entry appears in `requirements.txt` without a
  corresponding notice entry.
- The §4.3 blacklist must be reproduced in `CONTRIBUTING.md` so a future contributor cannot
  "discover" an AGPL or unlicensed repository in month four and quietly paste from it.
- Attribution required by an external data provider (§5) must be rendered in the application
  footer, driven by the provider that actually served the data — not hand-maintained.

---

## 2. Runtime Python Dependencies

Source: `docs/research/PYTHON_LIBS_WINDOWS.md` (all versions and licenses verified against
PyPI / upstream on 2026-08-15). Platform constraint: Windows 11 native, Python 3.11, every
package must install from a prebuilt wheel — no compiler, no MSYS2, no GTK.

### 2.1 Core runtime (`requirements.txt`)

| Package | Version | License | Purpose |
|---|---|---|---|
| Django | 5.2.17 (LTS) | BSD-3-Clause | Web framework. Pinned to 5.2 LTS: Django 6.x requires Python ≥3.12; extended support to April 2028. |
| djangorestframework | 3.17.2 | BSD-3-Clause | REST API layer. **Not 3.18** — drf-spectacular 0.30.0 documents support only through DRF 3.17 and carries no upper-bound pin. |
| django-filter | 26.1 | BSD-3-Clause | Queryset filtering; pairs with HTMX filter forms. |
| drf-spectacular | 0.30.0 | BSD-3-Clause | OpenAPI 3 schema generation. |
| django-allauth | 65.19.1 | MIT | Email-as-login, verification, password reset. **Base install only — never the `[saml]` extra** (native `xmlsec`/`lxml`, breaks the Windows no-compiler constraint). |
| djangorestframework-simplejwt | 5.5.1 | MIT | JWT auth for future mobile / third-party API clients. |
| django-environ | 0.14.0 | MIT | `.env` configuration; `env.db()` gives the SQLite-dev → PostgreSQL-prod switch. |
| django-htmx | 1.29.0 | MIT | `request.htmx` helpers, `HttpResponseClientRedirect`. |
| huey | 3.3.4 | MIT | Background tasks. Thread worker (the default) works on Windows; `SqliteHuey` removes the Redis dependency. Chosen because Celery is officially unsupported on Windows and django-q2's cluster requires `fork()`. |
| reportlab | 5.0.0 | BSD | PDF generation (Platypus tables, repeating headers, embedded chart PNGs). Bare install only — **do not add `[accel]`, `[pycairo]`, `[bidi]`, `[shaping]`**, which reintroduce compiled extensions. |
| XlsxWriter | 3.2.9 | BSD-2-Clause | Excel **generation** (native charts, conditional formatting). Zero dependencies. |
| openpyxl | 3.1.5 | MIT | Excel **reading** for spreadsheet imports only. |
| matplotlib | 3.11.1 | matplotlib license (PSF-based, OSI-approved, BSD-compatible — not copyleft) | Server-side chart PNGs for PDF/Excel embedding. `Agg` backend only, never a GUI backend inside a web process. |
| Pillow | 12.3.0 | MIT-CMU | Image handling; also used to re-encode untrusted uploads (`AI_TERMINAL_SECURITY.md` §8.5). |
| qrcode[pil] | 8.2 | BSD | QR codes for student check-in passes and equipment tags. |
| python-barcode | 0.16.1 | MIT | Code128 barcodes for equipment asset tags. |
| whitenoise | 6.12.0 | MIT | Static file serving. |

### 2.2 Production-only extras (`requirements-prod.txt`)

| Package | Version | License | Purpose |
|---|---|---|---|
| psycopg[binary] | 3.3.4 | **LGPL-3.0-only** ⚠ weak copyleft — see §2.5 | PostgreSQL driver. `cp311 / win_amd64` wheel confirmed on PyPI; libpq statically bundled, no `pg_config` needed. |
| redis | 8.1.0 | MIT | Optional huey broker. The application must never import it unconditionally — `SqliteHuey` is the no-Redis fallback. |
| waitress | 3.0.2 | ZPL-2.1 (OSI-approved permissive, not copyleft) | WSGI server. `gunicorn` cannot run on Windows (POSIX-only `fcntl`). |

### 2.3 Runtime packages introduced by the AI terminal and AI client work

Sourced from `AI_TERMINAL_SECURITY.md` §8.1 / §8.6 and `NVIDIA_MODEL_SELECTION.md` §12.
These are runtime, not dev-only, and must be added to `requirements.txt` when those features land.

| Package | Version | License | Purpose |
|---|---|---|---|
| psutil | 7.2.2 | BSD-3-Clause | Orphan-process reconciliation sweep at terminal-worker startup. |
| django-axes | 8.3.1 | MIT | Login brute-force protection (5 failures → 30 min cooloff). |
| django-ratelimit | 4.1.0 | Apache-2.0 | Per-user rate limits on terminal `propose` / `approve` endpoints. Pin it — upstream publishes no Django 5.2 test matrix. |
| openai (Python SDK) | not pinned in research | **UNVERIFIED — must confirm before release** | The whole client surface for NVIDIA NIM (`integrate.api.nvidia.com/v1` is OpenAI-compatible). |
| httpx | not pinned in research | **UNVERIFIED — must confirm before release** | Async HTTP for weather providers and long-running AI calls. |
| pydantic | not pinned in research | **UNVERIFIED — must confirm before release** | `CommandProposal` schema validation at the AI-terminal trust boundary. |
| django-csp | not pinned in research | **UNVERIFIED — must confirm before release** | CSP on Django 5.2 (built-in CSP arrives only in Django 6.0). |
| python-magic | not pinned in research | **UNVERIFIED — must confirm before release** | Content-sniffing upload validation. Note: binds to `libmagic`; the Windows wheel story must be verified against the no-compiler constraint before adoption. |
| py-svg-hush | not pinned in research | **UNVERIFIED — must confirm before release** | Only if SVG uploads are ever permitted. Current policy rejects SVG outright. |

### 2.4 Dev / CI only — not shipped, but recorded

| Package | Version | License | Purpose |
|---|---|---|---|
| pytest | 9.1.1 | MIT | Test runner. |
| pytest-django | 4.14.0 | BSD-3-Clause | Django integration for pytest. |
| pytest-cov | 7.1.0 | MIT | Coverage. |
| factory-boy | 3.3.3 | MIT | Test factories. |
| Faker | 40.36.0 | MIT | Test data. Pin strictly — locale data shifts between releases. |
| ruff | 0.16.3 | MIT | Lint + format (`DJ` and `S` rule sets enabled). Rust, but ships platform wheels. |
| bandit | 1.9.4 | Apache-2.0 | Security static analysis gate (`S602/S604/S605` — the `shell=True` ban). |
| pip-audit | 2.10.1 | Apache-2.0 | Dependency CVE audit. **CI only** — requires network, must never sit on an offline startup path. |
| python-gettext | 5.0 | BSD | Pure-Python `compilemessages` fallback for boxes without GNU gettext. Compiles `.po` → `.mo` only; does **not** replace `xgettext`. |
| django-debug-toolbar | (optional) | BSD-3-Clause | Local debugging. |

### 2.5 Copyleft flag — `psycopg` (LGPL-3.0-only)

LGPL is *weak* copyleft: obligations attach to the library, not to our application, provided we

- dynamically link / import it normally (a `pip install` into a venv is exactly this),
- do not modify psycopg's own source and redistribute it without publishing those changes, and
- allow anyone who receives our distributed software to substitute their own psycopg build.

For a web application deployed on our own servers, **nothing is distributed to end users at
all**, so the obligations are effectively inert. If we later ship an **on-premise bundled
installer** (PyInstaller one-file, MSI) to individual surf schools, that *is* distribution:
psycopg must then remain a replaceable dynamic component and its licence text must be
included. Escape hatch if LGPL ever becomes unacceptable: `pg8000` (pure-Python, BSD) —
slower and less battle-tested with Django; do not switch pre-emptively.

**No GPL-3.0, AGPL-3.0 or SSPL package appears anywhere in the recommended dependency set.**

### 2.6 Dependencies explicitly rejected on licence or platform grounds

| Package | License / issue | Why rejected |
|---|---|---|
| **ApexCharts** | **Custom tiered licence — NOT open source** (widely and incorrectly documented online as MIT) | Free only under $2M annual revenue; paid commercial at $2M+; **paid OEM tier required for "embedding into a product or platform used by other people"** — precisely this product's distribution model. Also forbids use in competing charting products. **Replaced by Chart.js (MIT).** |
| fpdf2 2.8.8 | LGPL-3.0-only | Copyleft, and weaker tables than ReportLab Platypus. |
| WeasyPrint 69.0 | BSD (licence fine) | Requires MSYS2 + Pango on Windows; fails at import, not at install. Violates the pip-install-only constraint. |
| xhtml2pdf 0.2.17 | Apache-2.0 (licence fine) | Pins `reportlab<5` — would silently downgrade our chosen renderer. Add to a `FORBIDDEN_DEPS` CI check. |
| celery | BSD-3-Clause (licence fine) | Officially unsupported on Windows since 4.x (its own FAQ). |
| django-q2 | MIT (licence fine) | Cluster requires `fork()`; on Windows only `sync=True` inline execution is available. |
| `qrcode` (as a QR generator for equipment tags) | BSD in the repo LICENSE, but PyPI carries **both** `BSD License` **and** `Other/Proprietary License` classifiers | Contradictory packaging metadata is a legal-review time sink. `segno` (BSD-3-Clause, zero deps) is the ambiguity-free alternative if this ever becomes contentious. **Note:** `qrcode[pil] 8.2` is currently in the runtime set (§2.1) — this ambiguity is an open item in §7. |
| django-money | BSD-3-style | **Archived as of 2026.** Do not add to a new project. |
| django-role-permissions | MIT | ~3 years stale (last push 2023-06-09). |

---

## 3. Vendored Front-End Assets

No CDN references anywhere in templates — full offline operation is a hard requirement.
Source: `PYTHON_LIBS_WINDOWS.md` §10–§12.

| Asset | Version | License | Purpose |
|---|---|---|---|
| htmx | 2.0.10 | **0BSD** (Zero-Clause BSD — public-domain-equivalent, no attribution required) | Server-driven interactivity. **Do not ship 4.0.0-beta** — it swaps XHR for Fetch and is a prerelease; the 2.x line is feature-complete and maintained. Vendored at `static/vendor/htmx/2.0.10/htmx.min.js`. |
| Alpine.js | 3.16.1 | MIT | Local component state. Load **after** htmx, with `defer`. Vendored at `static/vendor/alpinejs/3.16.1/alpine.min.js`. |
| Alpine.js CSP build (`@alpinejs/csp`) | matches Alpine 3.16.1 | MIT (same project) | Required to avoid `unsafe-eval` in the Content-Security-Policy (`AI_TERMINAL_SECURITY.md` §8.7). Decide before writing components — retrofitting is expensive. |
| Chart.js | 4.5.1 | MIT | Browser dashboard charts. ~200 KB minified, single `chart.umd.js`, no build step. Vendored at `static/vendor/chartjs/4.5.1/chart.umd.js`. |
| Tailwind CSS — built output | built with CLI v4.3.3 | MIT | `static/css/site.css`. **Committed** so a fresh clone renders correctly having never run the binary. |
| Tailwind CSS — standalone CLI binary | v4.3.3 (`tailwindcss-windows-x64.exe`, ~112.5 MB) | MIT | `tools/tailwindcss.exe`. Node-free build. `django-tailwind` / `pytailwindcss` are **rejected**: both download the binary from GitHub on first run with no documented way to point at a pre-placed one, which breaks the offline requirement. |
| GNU gettext binaries (optional build tool) | 0.21+ | **GPL — tool only, never linked or distributed with the product** | `msgfmt` / `xgettext` for `makemessages` / `compilemessages`. Invoked as separate processes; this creates no licensing obligation on our code, for the same reason that compiling with GCC does not GPL a program. Placed at `tools/gettext/` or installed via `choco install gettext`. |

**Fonts and icons:** the Phase-1 research manifest vendors **no** web font and **no** icon set.
Any font or icon pack added later must be recorded here with its licence *before* it is
committed — icon sets are a common source of CC-BY obligations (e.g. django-guardian bundles
Font Awesome Free 6.7.2 under CC-BY-4.0; we do not adopt django-guardian, but the pattern is
the one to watch).

**⚠ Discrepancy with the current repository state.** `package.json` in the working tree
currently pins `htmx.org ^1.9.12`, `alpinejs ^3.14.9`, `chart.js ^4.4.7`,
`tailwindcss ^3.4.17`, and adds **`lucide-static ^0.468.0`**, which is not covered by any
Phase-1 research file. Licence for `lucide-static`: **UNVERIFIED — must confirm before
release**. The version drift and the unaudited icon package are open items in §7.

---

## 4. Reference Projects Studied

Source: `docs/research/OSS_REFERENCES.md`. All SPDX identifiers below were read from the
project's own `LICENSE` file where GitHub reported `NOASSERTION`, and cross-checked against
PyPI classifiers where a package exists (verified 2026-08-15).

### 4.1 Permissively licensed references

| Project | Repository | License | What we studied | Code copied? |
|---|---|---|---|---|
| django-htmx-patterns | https://github.com/spookylukey/django-htmx-patterns | **Unlicense** (public domain) | Inline editing, modal dialogs, form-validation round-trips, pagination/infinite scroll, dependent selects, partial re-rendering | **Yes — public domain; may be copied verbatim with zero legal obligation.** Highest-leverage, lowest-risk asset in the audit. |
| InvenTree | https://github.com/inventree/InvenTree | **MIT** (verified: `MIT License / Copyright (c) 2017 - InvenTree Developers`) | Part vs StockItem split (→ `GearModel` / `GearUnit`), append-only stock-tracking ledger, barcode-as-abstraction resolver, plugin registry, domain-named Django apps, dedicated `report` app | **No — architecture/ideas only.** MIT *would* permit code reuse; we reimplement because InvenTree carries manufacturing weight (BOMs, builds, POs) a surf school never needs. If any file is ever copied verbatim, reproduce the MIT notice in `THIRD_PARTY_NOTICES.md`. |
| NetBox | https://github.com/netbox-community/netbox | **Apache-2.0** | Overall project skeleton, change-logging on every object, custom fields, tags, saved filters, generic CRUD view base classes | **No — architecture/ideas only.** Apache-2.0 would permit reuse with attribution + NOTICE propagation; if any code is lifted, that obligation activates. |
| django-appointment | https://github.com/adamspd/django-appointment | **Apache-2.0** (GitHub API + PyPI classifier) | `services.py` layering, **slot-generation algorithm**, `email_sender/` structure, `locale/` i18n layout | **Yes — narrow algorithm reuse into `bookings/services.py`.** Apache-2.0 obligations apply: retain copyright + licence text, state significant changes in modified files, propagate any `NOTICE`. **Not** added as a runtime dependency — its 1-staff↔1-client model is baked into its schema. |
| django-scheduler | https://github.com/llazzaro/django-scheduler | **BSD-3-Clause** | RRULE recurrence + **occurrence-override pattern** (a cancelled instance persists one override row instead of materialising the series) | **Yes — recurrence model / `models.py` logic only.** Attribution + no-endorsement clause apply. Discard its legacy jQuery/fullcalendar templates. |
| django-oscar | https://github.com/django-oscar/django-oscar | **BSD-3-Clause** | `Order` state machine, `Line` / `LinePrice` split, "fork the app to customise" pattern | **Yes — limited: order state machine and line-price split.** Attribution required. We do **not** adopt Oscar as a framework. |
| Saleor | https://github.com/saleor/saleor | **BSD-3-Clause** | Checkout → payment → fulfilment separation; discount/voucher modelling ("5-lesson pack", "hotel partner rate") | **No — architecture/ideas only.** |
| cookiecutter-django | https://github.com/cookiecutter/cookiecutter-django | **BSD-3-Clause** | Split settings (`config/settings/{base,local,production}.py`), `django-environ` convention, `requirements/` split, custom user model from commit #1 | **Yes — config files may be lifted verbatim.** Record the BSD-3 notice if any file is copied wholesale. We do **not** generate the project from it — its output is heavily Docker-oriented and we run Windows-native. |
| Cal.com | https://github.com/calcom/cal.diy (`calcom/cal.com` redirects here) | **MIT** — ⚠ relicensed; most blog posts still say AGPL-3.0. MIT applies to the repo **as it is now**; it does not retroactively cover older AGPL snapshots or forks. | Event-type abstraction (duration, buffers, min notice, max bookings/day, location type), booking-questions per event type, gear-turnaround buffer time | **No — architecture/ideas only** (TypeScript / Next.js / Prisma; not portable to Django anyway). |
| Wagtail | https://github.com/wagtail/wagtail | **BSD-3-Clause** | Admin UX standards; permission/workflow model | **No — architecture/ideas only.** |
| django-unfold | https://github.com/unfoldadmin/django-unfold | **MIT** (no open-core split) | Admin theme built on **our exact stack** — Tailwind + HTMX + Alpine | **No code copied — candidate runtime dependency.** ⚠ **Blocked:** PyPI declares `requires_python >=3.12,<4.0`; our stack is Python 3.11. Resolve before adoption (see §7). |
| Open Source Point of Sale | https://github.com/opensourcepos/opensourcepos | **MIT** (GitHub reports `NOASSERTION` only because the LICENSE stacks four copyright lines; text opens `MIT License / Copyright (c) 2013-2025 jekkos`) | Register/shift sessions, cash-drawer reconciliation, split tender, receipts, returns, store credit | **No — architecture/ideas only** (PHP / CodeIgniter). |
| OnlineRetailPOS | https://github.com/virajkothari7/OnlineRetailPOS | **MIT** | One idea only: Windows touch-tablet POS with a second customer-facing display — uncannily close to a beach-hut counter | **No — UX idea only.** 47 stars, ~19 months stale; a personal project, not a reference architecture. |
| falco-cli | https://github.com/falcopackages/falco-cli | **MIT** (`Copyright (c) 2024 Tobi DEGNON`) | Modern HTMX-first Django conventions and scaffolding guides | **No — architecture/ideas only.** |
| lithium (ex-djangox) | https://github.com/wsvincent/lithium | MIT text (multi-copyright header → GitHub `NOASSERTION`) | Minimal readable Django starter layout | **No — architecture/ideas only.** |
| Django-School-Management-System | https://github.com/adigunsherif/Django-School-Management-System | **MIT** | Academic-school domain (grades, exams, semesters) | **No — evaluated and rejected.** Legally copyable, but almost no overlap with a surf school's booking/gear/weather domain. |

### 4.2 Copyleft references — IDEAS ONLY, never code

| Project | Repository | License | What we studied | Code copied? |
|---|---|---|---|---|
| Snipe-IT | https://github.com/grokability/snipe-it | **AGPL-3.0** | Check-out / check-in state machine (asset assignable to user, location, or another asset); maintenance records; status labels Deployable / Deployed / Undeployable / Archived → Available / Rented / In Repair / Retired; depreciation tracking | **No — architecture/ideas only.** The most dangerous entry in this register: it is the top search result for "open source asset checkout" and does exactly what a surf school needs at the gear counter. |
| wger | https://github.com/wger-project/wger | **AGPL-3.0** | Gym-member management and workout-log models | **No — architecture/ideas only.** |
| django-crm | https://github.com/DjangoCRM/django-crm | **AGPL-3.0** | Lead → opportunity → customer lifecycle | **No — architecture/ideas only. HIGHEST PASTE RISK IN THIS DOCUMENT** — it is Python, it is Django, it is actively maintained, and its models look immediately copy-pasteable. Formally blacklisted in `CONTRIBUTING.md`. |
| EspoCRM | https://github.com/espocrm/espocrm | **AGPL-3.0** | Entity/relationship design; contact activity timelines | **No — architecture/ideas only.** |
| SuiteCRM | https://github.com/SuiteCRM/SuiteCRM | **AGPL-3.0** | CRM lifecycle and segmentation concepts | **No — architecture/ideas only.** |
| matorral | https://github.com/matorral-project/matorral | **AGPL-3.0** | A real Django + HTMX application, useful to *read* for structure | **No — architecture/ideas only.** |
| Easy!Appointments | https://github.com/alextselegidis/easyappointments | **GPL-3.0** | Service ↔ provider ↔ working plan ↔ **working-plan exceptions** (a specific date where an instructor's hours differ) | **No — architecture/ideas only** (PHP). |
| ERPNext | https://github.com/frappe/erpnext | **GPL-3.0** | ERP scope reference | **No — architecture/ideas only.** |
| Odoo (Community) | https://github.com/odoo/odoo | **LGPL-3.0** (LICENSE text; GitHub reports `NOASSERTION`) | Business-suite modelling | **No — architecture/ideas only.** Odoo modules are application code we would derive from, not a library we link against, so the LGPL "dynamic linking" relief does not apply. Odoo Enterprise is separately proprietary. |
| Open-Meteo server | https://github.com/open-meteo/open-meteo | **AGPL-3.0** | Only relevant as a self-hosting option for the weather API (§5.1) | **No code copied.** Running it *unmodified* as a private backend creates no AGPL obligation; modifying it and exposing it over a network does. |

### 4.3 BLACKLIST — no LICENSE file, all rights reserved

These are **more restrictive than AGPL** and are the highest-risk entries precisely because
they look harmless — no scary licence banner, just an absence most developers never check.

| Project | Repository | License | Why blacklisted | Code copied? |
|---|---|---|---|---|
| sportsms | https://github.com/MaliusMartin/sportsms | **NONE — no LICENSE file** | Named in the original brief. 6 stars, 3 forks, 46 MB of committed binaries, GitHub reports its primary language as JavaScript despite the Django claim. | **No — copying any line would be copyright infringement.** |
| Django-School-Management | https://github.com/TareqMonwer/Django-School-Management | **NONE — no LICENSE file** | 591 stars makes it look authoritative in search results. It is not licensed. | **No.** |
| django-scms | https://github.com/mwinamijr/django-scms | **NONE — no LICENSE file** | Same trap, smaller. | **No.** |
| Various Django gym systems (`mithun-t/…`, `Pradip-p/GYMfits`, `Pawan243/…`) | — | **Mostly NONE** | Tutorial-grade, 6–22 stars, no architectural value. | **No.** |

### 4.4 Libraries evaluated as references (licences recorded for completeness)

`segno` BSD-3-Clause · `python-barcode` MIT · `django-rules` MIT (primary RBAC pick;
predicate-based, no per-object ACL rows) · `django-guardian` BSD-2-Clause + **CC-BY-4.0 for
bundled Font Awesome Free 6.7.2 icons** (attribution applies to the icons specifically if
ever vendored) · `django-organizations` BSD-2-Clause · `django-tenants` MIT ·
`django-template-partials` MIT · `django-components` MIT · `neapolitan` MIT ·
`django-tables2` BSD-2-Clause style (`NOASSERTION`; "same terms as the original
django-tables", `Copyright (c) 2011 Bradley Ayers`) · `django-crispy-forms` MIT ·
`django-jazzmin` MIT (rejected — Bootstrap/AdminLTE conflicts with Tailwind) ·
`django-tasks` BSD-3-Clause · `dj-stripe` MIT · `django-allauth` MIT ·
`django-simple-history` BSD-3-Clause · `django-import-export` BSD-2-Clause ·
`django-model-utils` BSD-3-Clause · `django-debug-toolbar` BSD-3-Clause · `django-axes` MIT ·
`django-two-factor-auth` MIT · `django-waffle` BSD-3-Clause · `django-oauth-toolkit`
BSD-2-Clause style · `django-typer` MIT · `djangorestframework` BSD-3-Clause (LICENSE.md,
Encode OSS Ltd) · Django itself BSD-3-Clause.

**SaaS Pegasus** — commercial/proprietary, paid licence, **not open source, excluded.**

> **Pin by PyPI package name, never by git URL.** Several long-standing Jazzband packages have
> migrated orgs (`django-commons`, `django-waffle`, `django-oauth`, `django-import-export`),
> and `wsvincent/djangox` is now `wsvincent/lithium`. Old URLs redirect *today*.

---

## 5. External Data Services

Source: `docs/research/SURF_WEATHER_APIS.md` (all endpoints called live 2026-08-15 against
the Alaçatı / Çeşme reference spot, `lat 38.28, lon 26.37`).

### 5.1 Open-Meteo — default provider ⚠ read the two-licence distinction

Endpoints in use: Forecast `https://api.open-meteo.com/v1/forecast` and Marine
`https://marine-api.open-meteo.com/v1/marine`. Also available keyless: Air Quality,
Geocoding, Elevation, Historical (ERA5).

**There are two separate licences here and conflating them is the standard mistake.**

| | Licence | What it means for us |
|---|---|---|
| **(a) The data** | **CC BY 4.0** | **Commercial use is permitted**, with attribution. |
| **(b) The free hosted API service** | **NON-COMMERCIAL ONLY** — the terms state plainly: *"You may only use the free API services for non-commercial purposes."* | A surf school management system that is sold or subscribed to **is commercial**. The free host may not be used in that mode. |

**Required attribution — exact HTML from Open-Meteo's licence page, to be rendered in the
base template footer:**

```html
<a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>
```

Upstream model sources and their licences (none is non-commercial-only): DWD `CC-BY`,
ECMWF `CC-BY`, MeteoSwiss `CC-BY`, NOAA NCEP standard NOAA licence, Météo-France custom,
CMC custom.

Free-tier limits: <10,000 calls/day, 5,000/hour, 600/minute (~300,000/month). At our design
volume — one school, 5 spots, 30-minute prefetch — usage is ~480 calls/day, about 5% of the
cap. **The licence, not the rate limit, is the binding constraint.**

Three legitimate commercial routes: (1) Open-Meteo **API Standard** plan (1M calls/month,
commercial permitted; exact EUR price not published — must be obtained); (2) **self-host**
the server, which is **AGPLv3** — running it unmodified as a private backend creates no
obligation, but there is no Docker on our Windows target so this means a Linux VM/VPS;
(3) stay free during development and internal single-school use and gate the switch behind
config.

**Encode the boundary in code:** `settings.SURF_WEATHER_COMMERCIAL_MODE = True` must force
the provider to refuse the free host and require `OPEN_METEO_API_KEY`, switching the base URL
to `customer-api.open-meteo.com` / `customer-marine-api.open-meteo.com`. The licence line
must not be crossable by accident when the product starts charging.

**Tide caveat to surface in the UI:** Open-Meteo has no tide-extremes endpoint. We derive
high/low from the hourly `sea_level_height_msl` series (±30 min accuracy). Open-Meteo's own
documented caveat — *"Accuracy at coastal areas is limited. This is not suitable for coastal
navigation."* — means the UI must label this **"modelled tide"**, never station data.

### 5.2 MET Norway (Yr) — the commercial-clean land-weather fallback

`https://api.met.no/weatherapi/locationforecast/2.0/compact`

- **Licence: CC BY 4.0. Commercial use is permitted.** Attribution obligation: give
  appropriate credit, link to the licence, and indicate whether changes were made.
  *(MET Norway does not mandate a fixed string in the research; our footer wording must be
  agreed and then recorded here.)*
- **No API key — but a valid identifying `User-Agent` is mandatory.** Missing or banned UAs
  (`okhttp`, `Dalvik`, `fhttp`, `Java`) get **403**. Format: `AppName/version contact`.
  Must come from Django settings; never ship a blank or default one.
- **Local caching is a term of service, not a suggestion.** We must cache and send
  `If-Modified-Since` with the exact prior `Last-Modified` value. Our cache entries therefore
  store `Last-Modified` / `ETag` alongside the payload from day one.
- Rate limit: >20 requests/second per application requires a special agreement.
- Marine (`oceanforecast/2.0`) is **Nordic waters only** and `tidalwater/1.1` is **Norway
  only** — useless at our reference spot. Land weather only.

### 5.3 Optional / paid providers

| Service | Auth | Licence & commercial terms | Required attribution | Status |
|---|---|---|---|---|
| **Stormglass.io** | API key (`Authorization: <key>` header, not Bearer) | Free tier **10 requests/day, non-commercial**. Commercial use begins at the **Medium plan, €49/mo, 5,000 req/day**; Large €129/mo. | Not recorded in research — confirm before enabling | Optional, tide-only provider. Best-in-class station-based `tide/extremes`. |
| **WorldTides v3** | API key on every request | Credit model, ~$0.001/credit; 100 free credits on signup. **⚠ Terms state each API request is licensed for a *single user* unless otherwise agreed** — a real constraint for a cached multi-tenant deployment. | **Required verbatim:** *"Tidal data retrieved from www.worldtides.info. Copyright © 2014-2026 Brainware LLC."* | Optional, tide-only. **Do not enable in any paid multi-tenant deployment until the multi-user/caching position is confirmed in writing** (email info@worldtides.info; record the answer here). |
| **Visual Crossing** | API key | Free tier 1,000 **records**/day and — uniquely — **free for commercial use**. Paid from ~$35/mo, $0.0001/record. Marine ("Maritime Elements") gated to the Corporate plan. | Not recorded in research — confirm before enabling | Optional land-weather fallback. Note the billing-unit trap: a 7-day hourly forecast ≈ 168 records. |
| **NOAA CO-OPS** | None (they request `application=YourAppName`) | **US Government work — public domain. Free for commercial use.** No attribution required; courtesy credit is good practice. | None required | **Not implemented** — US stations only, no coverage at Alaçatı. Implement immediately as `noaa-coops` if the product expands to US customers; `interval=hilo` gives station-grade tide extremes free. |
| **NDBC** | None | Public domain, commercial use fine. Fixed-width text files, not JSON. | None required | **Not implemented** — US buoys only. Value is as an *observation* source for a future forecast-vs-actual accuracy feature, not as a forecast provider. |

### 5.4 Explicitly excluded services and why

| Service | Reason for exclusion |
|---|---|
| **Surfline** | **No public API.** The `services.surfline.com/kbyg/...` endpoint is an internal one discovered from page source. Their Terms of Use govern surfline.com and subdomains including forecasts; reverse-engineering an internal endpoint to power a commercial competing product is against the letter and the spirit of those terms. Also zero stability contract. **Not in v1, not behind a flag, not "just for dev."** The only correct path is a commercial licence negotiated with Surfline. Leave it out of the provider registry entirely rather than shipping a tempting stub. |
| **Windy Point Forecast** | Map & Point Forecast terms grant a **strictly personal, non-commercial licence**; all commercial use requires the Professional plan. Offers nothing Open-Meteo does not already provide keylessly. |
| **OpenWeatherMap** | Licence is not the issue — it has **no marine, wave, swell or tide data at all**, which is structurally disqualifying for a surf product. |
| **ODB Open Tide API (NTU Taiwan, TPXO10)** | `UNVERIFIED / PARTIALLY FAILED` — three live attempts returned HTTP 422 or an empty body. Worse, **TPXO atlas products are free for academic use only; commercial use requires a licence from Oregon State University**, and consuming them through a third-party API does not launder that obligation. |
| **Local harmonic tide computation (pyTMD / pytide / pytides)** | The code is permissive; **the constituent atlases are not.** TPXO requires an OSU commercial licence; FES2014 requires an AVISO agreement (free for research, commercial by agreement). NOAA station constituents are public domain but US-only. |

---

## 6. AI Models

Source: `docs/research/NVIDIA_MODEL_SELECTION.md` for hosted NVIDIA NIM models; local model
ids read from `config/settings/base.py` and `.env.example`.

### 6.1 Governing principle

> **We call hosted inference APIs and run third-party model weights locally through LM Studio.
> We do not redistribute model weights, we do not fine-tune or publish derived models, and no
> model weights are bundled into the product.** Each model's weights and outputs are governed
> by that model's **own** licence and by the hosting provider's terms of service — those terms
> are *not* covered by, and do not flow from, the open-source policy in §1. Before any
> commercial release, Legal must review the licence stack actually in use, because several of
> these carry downstream obligations (Llama Community Licences, Gemma Terms of Use) that are
> mild but real.

### 6.2 NVIDIA NIM — hosted models (`https://integrate.api.nvidia.com/v1`)

| Role | Model id | Licence (as recorded in research) | Notes |
|---|---|---|---|
| Assistant (primary) | `nvidia/nemotron-3-super-120b-a12b` | **NVIDIA Nemotron Open Model License** — open weights, commercial use permitted | 120B total / 12B active; 128K hosted context. |
| Assistant (fallback), analytics (primary) | `openai/gpt-oss-120b` | **Apache-2.0** | The cleanest licence in the catalogue — zero copyleft or patent risk. Safe harbour if Legal objects to NVIDIA/Meta-derived terms. |
| Vision (fallback), budget assistant | `meta/muse-glimmer-30b` | **Apache-2.0** | Most licence-clean vision option. |
| Coding (primary) | `poolside/laguna-xs-2.1` | **OpenMDW-1.1** — permissive, designed for open model weights/artifacts, commercially clean | 33B/3B-active. |
| Router (primary), LLM-rerank | `nvidia/nemotron-3.5-lightning-30b-a3b` | Model card states **"ready for commercial use"**; SPDX identifier not recorded → **UNVERIFIED — must confirm before release** | |
| Router (fallback) | `nvidia/nemotron-3-nano-30b-a3b` | **UNVERIFIED — must confirm before release** | |
| Vision (primary), doc QA | `nvidia/nemotron-nano-12b-v2-vl` | **UNVERIFIED — must confirm before release** | |
| Document parse (primary) | `nvidia/nemotron-parse` | **UNVERIFIED — must confirm before release** | Non-chat: dedicated inference endpoint. `nvidia/nemoretriever-parse` is the legacy alias. |
| Text embeddings (primary) | `nvidia/llama-nemotron-embed-1b-v2` | **NVIDIA Open Model License + Llama 3.2 Community License** | The Llama Community Licence is a real downstream obligation — include it in the Legal review. Only top-tier NVIDIA embedder with verified Turkish. |
| Text embeddings (fallback) | `baai/bge-m3` | **MIT** — cleanest of the embedders | 100+ languages, symmetric, drop-in. |
| Embeddings (benchmark candidate) | `nvidia/nemotron-3-embed-1b` | **OpenMDW-1.1** | Not adopted — published evaluation covers 34 languages and **does not include Turkish**. |
| Code embeddings | `nvidia/nv-embedcode-7b-v1` | Recorded only as "NVIDIA" — specific agreement not identified → **UNVERIFIED — must confirm before release** | |
| Safety — content | `nvidia/nemotron-3.5-content-safety` | **OpenMDW-1.1 *plus* the Gemma Terms of Use** ⚠ | Fine-tuned from Gemma-3-4B. The Gemma ToU is a real, if mild, downstream obligation — **explicitly flagged for Legal in the research.** Also: covers 12 languages, **Turkish is not among them.** |
| Safety — topic control | `nvidia/llama-3.1-nemoguard-8b-topic-control` | Built on Llama-3.1-8B-Instruct; licence not stated in research → **UNVERIFIED — must confirm before release** (a Llama Community Licence obligation is likely) | Prompt-injection / scope guard for the AI terminal. |
| Safety — multilingual fallback | `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` | **NVIDIA Open Model License + Llama 3.1 Community License** | |
| Translation (primary) | `nvidia/riva-translate-4b-instruct-v2` | **NVIDIA Open Model License Agreement + Apache-2.0** | Turkish verified in both directions (`en-tr`, `tr-en`). |
| Translation (fallback) | `nvidia/riva-translate-4b-instruct-v1.1` | **UNVERIFIED — must confirm before release** (same family; not separately stated) | |
| Code autocomplete | `mistralai/codestral-22b-instruct-v0.1` | **UNVERIFIED — must confirm before release** | ⚠ Mistral's Codestral terms are known to differ from a plain OSS licence. **Do not enable this role until verified.** |
| Offline eval judge | `nvidia/nemotron-4-340b-reward` | **UNVERIFIED — must confirm before release** | Returns HelpSteer2 scores, not prose. Offline evaluation only. |
| Evaluated, not adopted | `thinkingmachines/inkling`, `moonshotai/kimi-k2.6`, `z-ai/glm-5.2`, `minimaxai/minimax-m3`, `deepseek-ai/deepseek-v4-flash-0731`, `nvidia/cosmos-reason2-8b`, `nvidia/nvclip`, `nvidia/llama-nemotron-embed-vl-1b-v2`, `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1` | **UNVERIFIED — must confirm before release** (`cosmos-reason2-8b` is described in research as "commercially usable" but no SPDX was recorded) | Verify before any is promoted into an active role. |

**Service terms, not model terms:** access is through the NVIDIA API Catalog (build.nvidia.com)
under an `nvapi-...` key, subject to NVIDIA's own terms of service. Free tier is ~40
requests/minute per model, with ~1,000 developer inference credits (extendable to ~5,000).
The API key is an environment variable and must never be committed.

### 6.3 Local models served through LM Studio

These ids come from `config/settings/base.py` / `.env.example`, **not** from any Phase-1
research file, so **no licence below has been verified**. They run locally against an
OpenAI-compatible server at `http://localhost:1234/v1`.

| Role | Model id | Licence | Status |
|---|---|---|---|
| General | `google/gemma-4-12b-qat` | **UNVERIFIED — must confirm before release.** Gemma-family weights are distributed under the **Gemma Terms of Use**, which the research already flags as a real downstream obligation elsewhere (§6.2). | Default local provider — verify first. |
| Vision | `qwen/qwen3-vl-8b` | **UNVERIFIED — must confirm before release** | |
| Math | `qwen2.5-math-7b-instruct` | **UNVERIFIED — must confirm before release** | |
| Light vision | `moondream-2b-2025-04-14` | **UNVERIFIED — must confirm before release** | |
| Embeddings | `text-embedding-nomic-embed-text-v1.5` | **UNVERIFIED — must confirm before release** | |
| Runtime | **LM Studio** (the desktop application itself) | **UNVERIFIED — must confirm before release.** LM Studio is a closed-source application with its own EULA; its terms for commercial/business use must be checked before it is relied on in a paid deployment. | |

Also configured but outside the open-source scope: **Anthropic Claude**
(`claude-sonnet-5`, `https://api.anthropic.com/v1`) — a hosted commercial API governed by
Anthropic's commercial terms, and a generic OpenAI-compatible provider slot whose terms
depend entirely on whatever endpoint is configured into it.

### 6.4 Obligations that follow

1. **No weights are redistributed.** Every model above is either called over HTTP or loaded
   locally by LM Studio from the operator's own machine. Nothing ships in our artefacts.
2. **Model output is untrusted data**, per `AI_TERMINAL_SECURITY.md` — it is never a security
   authority, never executed, and never rendered unescaped. That is a security control, but it
   also limits the licence surface: we do not incorporate model output into distributed code
   without human review.
3. **Before commercial launch, Legal must sign off the licence stack actually in use:**
   NVIDIA Open Model License, Apache-2.0, OpenMDW-1.1, MIT, **Llama 3.1 / 3.2 Community
   Licenses**, and the **Gemma Terms of Use**.

---

## 7. Compliance Checklist

Tick these before any commercial release. Items marked ⛔ are release blockers.

### 7.1 Register and notices

- [ ] `THIRD_PARTY_NOTICES.md` exists at the repository root and carries the full licence text
      for every permissive package whose code we copied (§4.1) and for every runtime
      dependency (§2).
- [ ] Apache-2.0 obligations discharged for **django-appointment** (and NetBox if code is ever
      lifted): copyright + licence retained, significant changes stated in modified files, any
      `NOTICE` propagated.
- [ ] BSD attribution recorded for **django-scheduler**, **django-oscar**, **cookiecutter-django**.
- [ ] CI check fails the build when a new `requirements.txt` entry has no corresponding
      `THIRD_PARTY_NOTICES.md` entry.
- [ ] The §4.2 / §4.3 blacklist is reproduced in `CONTRIBUTING.md`, naming **django-crm
      (AGPL-3.0, Django, highest paste risk)**, Snipe-IT, wger, and the three unlicensed repos.

### 7.2 Dependency hygiene

- [ ] ⛔ **Reconcile `requirements.txt` with the audited set in §2.** The working tree
      currently contains packages that no Phase-1 research file audited —
      `django-cors-headers`, `celery`, `django-celery-beat`, `numpy`, `python-dateutil`,
      `httpx`, `requests`, `tenacity`, `cryptography`, `bleach`, `Markdown` — and omits
      audited choices (`huey`, `django-allauth`, `matplotlib`, `whitenoise` version,
      `waitress`). Every package that ships must appear in §2 with a verified licence.
- [ ] ⛔ Resolve the **`qrcode` PyPI classifier ambiguity** (`BSD License` **and**
      `Other/Proprietary License`) — either accept it with a written note or switch to
      `segno` (BSD-3-Clause, zero deps).
- [ ] `FORBIDDEN_DEPS` CI check blocks **WeasyPrint**, **xhtml2pdf**, **fpdf2**, **ApexCharts**.
- [ ] `pip-audit` runs in CI (network-dependent — CI only, never on an offline startup path).
- [ ] Everything pinned by **PyPI package name**, never by git URL.
- [ ] ⛔ Decide the **`django-unfold` / Python 3.11** conflict: Unfold requires Python ≥3.12.
      Either move the project to Python 3.12+ (also recommended by the security research, which
      wants 3.13 for `os.path.isreserved()` and Django 6.x built-in CSP) or drop Unfold.

### 7.3 Front-end assets

- [ ] ⛔ **Reconcile `package.json` with the vendored manifest in §3** — the tree pins htmx
      1.9.12 / Alpine 3.14.9 / Chart.js 4.4.7 / Tailwind 3.4.17 against an audited manifest of
      htmx 2.0.10 / Alpine 3.16.1 / Chart.js 4.5.1 / Tailwind 4.3.3.
- [ ] ⛔ Verify and record the licence for **`lucide-static`** (currently in `package.json`,
      covered by no research file) or remove it.
- [ ] CI grep fails the build on `cdn.`, `unpkg`, `jsdelivr` in templates — offline
      correctness rots the moment someone pastes a CDN `<script>` tag during a hurried fix.
- [ ] Built `static/css/site.css` is committed so a fresh clone renders without running the
      Tailwind binary.
- [ ] Any font or icon set added later is entered in §3 **before** it is committed.

### 7.4 External data services

- [ ] ⛔ **Open-Meteo attribution is in the base template footer** —
      `<a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>` — driven by the
      `attribution_html` of the providers that actually contributed data.
- [ ] ⛔ **`SURF_WEATHER_COMMERCIAL_MODE` is implemented and enforced**: when true, the
      Open-Meteo provider refuses the free host and requires `OPEN_METEO_API_KEY`
      (`customer-api.open-meteo.com`). The free tier is **non-commercial only**.
- [ ] Commercial route chosen and paid for before the first paying customer: Open-Meteo API
      Standard plan **or** self-hosted AGPLv3 server on a Linux VPS. Obtain the Standard plan
      price (not published).
- [ ] MET Norway: identifying `User-Agent` set from settings (never blank — instant 403);
      `If-Modified-Since` / `Last-Modified` honoured (a **term of service**, not an
      optimisation); CC BY 4.0 credit rendered.
- [ ] WorldTides, if enabled: verbatim copyright string rendered, **and** the **single-user
      clause** clarified in writing with Brainware LLC before any cached multi-tenant use.
      Record their answer in `docs/research/SURF_WEATHER_APIS.md`.
- [ ] Surfline, Windy, OpenWeatherMap, ODB/TPXO, FES2014 remain **unimplemented and absent
      from the provider registry** — no stubs.
- [ ] Modelled tide is labelled "modelled tide" in the UI, never presented as station data.

### 7.5 AI models

- [ ] ⛔ Legal review of the model licence stack in active use: NVIDIA Open Model License,
      Apache-2.0, OpenMDW-1.1, MIT, **Llama 3.1 / 3.2 Community Licenses**, **Gemma Terms of
      Use**.
- [ ] ⛔ Every model marked **UNVERIFIED** in §6 is either verified and updated here, or
      disabled in configuration. In particular: **`mistralai/codestral-22b-instruct-v0.1`**
      (Codestral terms differ from a plain OSS licence) and the five local LM Studio models.
- [ ] ⛔ **LM Studio's own EULA** checked for commercial/business use.
- [ ] Gemma Terms of Use confirmed for **`nvidia/nemotron-3.5-content-safety`** and for the
      local `google/gemma-4-12b-qat`.
- [ ] No model weights are bundled, redistributed, or published in any artefact we ship.
- [ ] API keys (`NVIDIA_API_KEY`, `ANTHROPIC_API_KEY`, `STORMGLASS_API_KEY`,
      `OPEN_METEO_API_KEY`) live only in the environment and are never committed.

### 7.6 Copyleft containment

- [ ] Zero GPL / AGPL / SSPL code in the codebase and zero runtime dependencies on copyleft
      projects (GNU gettext binaries are developer tools invoked as separate processes and are
      **not** linked or distributed — this is the same reason compiling with GCC does not GPL a
      program).
- [ ] **psycopg LGPL-3.0-only decision recorded** (§2.5). If an on-premise bundled installer is
      ever shipped, psycopg must remain a replaceable dynamic component and its licence text
      must be included; re-open the decision at that point.
- [ ] No code, template, migration, model class, or SQL query originates from any project
      listed in §4.2 or §4.3.

---

*Maintained alongside `THIRD_PARTY_NOTICES.md`. When a dependency, vendored asset, data
service, or model changes, update this file in the same commit. Every "UNVERIFIED" marker is
a task, not a disclaimer.*
