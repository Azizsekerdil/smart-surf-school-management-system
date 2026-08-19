# Open-Source Architectural References & License Audit

**Project:** Smart Surf School Management System
**Target stack:** Python 3.11 · Django 5.2 LTS · Django REST Framework · HTMX · Alpine.js · Tailwind CSS · SQLite (dev) / PostgreSQL (prod) · Celery+Redis optional · **Windows 11 native, no Docker**
**Research date:** 2026-08-15
**Verification method:** every license below was read from the repository's own `LICENSE` file (decoded via `GET /repos/{owner}/{repo}/license`) or the GitHub repo API `license.spdx_id` field, cross-checked against PyPI classifiers where a package exists. Star counts and `pushed_at` dates are live values from the GitHub API on 2026-08-15.

---

## 0. TL;DR — The Legal Rulebook For This Project

Two buckets. There is no third bucket, and "it's just a small snippet" is not a defence.

| Bucket | Licenses | What we may do |
|---|---|---|
| **PERMISSIVE** | MIT, BSD-2-Clause, BSD-3-Clause, 0BSD, Apache-2.0, Unlicense | Copy code into our proprietary codebase. Must keep the original copyright notice + license text in `THIRD_PARTY_NOTICES.md`. Apache-2.0 additionally requires a `NOTICE` propagation and gives us an explicit patent grant. |
| **COPYLEFT / RESTRICTED** | GPL-2.0, GPL-3.0, LGPL-3.0, AGPL-3.0, SSPL, **no license at all** | **IDEAS ONLY.** Read it, learn the data model, take notes, then close the tab and write our own code from a blank file. Never copy a function, a model class, a template, a migration, or a SQL query. |

**The "no license" trap:** a public GitHub repo with no `LICENSE` file is **not** open source. Default copyright applies — all rights reserved. It is *more* restrictive than AGPL. Three of the "sports/school management" repos people commonly recommend fall into this trap (see §4 and §9).

**The AGPL trap for us specifically:** the Surf School system is a *web application*. AGPL-3.0 §13 triggers on network interaction, not on distribution. If AGPL code ends up in our server, every customer who loads a booking page is entitled to our complete corresponding source. This is the single biggest license risk in this project, and it is exactly the licence used by `wger`, `Snipe-IT`, `EspoCRM`, `SuiteCRM` and `django-crm` — four of which are the top hits when you search for "open source gym / asset / CRM system".

> **RECOMMENDATION:** Adopt a written policy before the first commit: *permissive-only for code, any-license for ideas.* Create `D:\Surf_School\THIRD_PARTY_NOTICES.md` at project start and append to it the moment any third-party code or dependency is added. Add a CI check (or a pre-commit hook) that fails if a new entry appears in `requirements.txt` without a corresponding notice entry. Treat "unlicensed repo" as strictly forbidden, harder than AGPL.

---

## 1. InvenTree — FLAGGED FOR EXPLICIT LICENSE VERIFICATION

This was called out for verification because InvenTree is frequently mis-reported online as GPL (people confuse it with other inventory tools). **It is not GPL.**

**Verified evidence:**

- `GET https://api.github.com/repos/inventree/InvenTree` → `license.spdx_id = "MIT"`, `license.name = "MIT License"`, `license.key = "mit"`
- `GET https://api.github.com/repos/inventree/InvenTree/license` → file `LICENSE`, spdx `MIT`
- Raw file `https://raw.githubusercontent.com/inventree/InvenTree/master/LICENSE`, first lines verbatim:
  ```
  MIT License

  Copyright (c) 2017 - InvenTree Developers
  ```
- Companion repos audited too — the whole ecosystem is consistently MIT:
  - `inventree/inventree-app` (Flutter mobile client, Dart) → **MIT**
  - `inventree/inventree-python` (API client library) → **MIT**

| Field | Value |
|---|---|
| Repo | https://github.com/inventree/InvenTree |
| Stars | ~7,390 |
| Forks | ~1,510 |
| Last push | 2026-08-14 (daily activity) |
| Latest release | **1.5.0**, published 2026-08-11 |
| Language | Python (Django + DRF) |
| **SPDX** | **MIT** |
| Archived | No |

**Actual architecture (verified from the repo tree, `src/backend/InvenTree/`):**
Django apps are split by *domain noun*, not by technical layer:
`build`, `common`, `company`, `data_exporter`, `generic`, `importer`, `machine`, `order`, `part`, `plugin`, `plugins`, `report`, `stock`, `users`, `web`.
Backend is Django + Django REST Framework, background work runs on **Django-Q** (not Celery), auth via **django-allauth**, and the frontend is a **separate React/Mantine SPA** (TanStack Query, Zustand, Lingui, React Router) served from the `web` app.

**Ideas to borrow:**

1. **The `part` vs `stock` split.** InvenTree separates the *catalogue definition* of a thing from *individual physical instances* of that thing. This maps perfectly onto surf gear: a `BoardModel` ("7'2 Softtop Funboard") versus a `BoardUnit` (serial `SB-0417`, condition `dinged`, currently rented to booking #881). Almost every naive surf-school schema gets this wrong by putting a `quantity` integer on the board model, which then makes it impossible to answer "which specific board has the cracked fin box?"
2. **Stock *tracking entries* as an append-only ledger.** Every movement (check-out, check-in, damage, retirement) is an immutable row, and current state is derived. Gives us free audit trails and "who had this wetsuit last?" queries.
3. **Barcode/QR as a first-class abstraction, not a field.** InvenTree resolves a scanned code through a plugin-style lookup that can return *any* model type. One scanner UI handles a board, a rack location, a student card, or a booking slip.
4. **The plugin registry pattern** — a stable internal API with entry points, so payment providers or SMS gateways can be added without touching core.
5. **Domain-named Django apps.** Adopt directly: `bookings/`, `lessons/`, `equipment/`, `students/`, `instructors/`, `billing/`, `reports/`, `common/`.
6. **A dedicated `report` app** for PDF/label generation driven by user-editable templates.

**What NOT to borrow:** the React/Mantine SPA layer. We committed to HTMX + Alpine, and InvenTree's frontend is a full SPA with its own build toolchain — copying it would silently import a second frontend architecture. Also note InvenTree's own choice of **Django-Q over Celery**, which is a meaningful signal for our Windows-native constraint (see §8).

> **RECOMMENDATION:** **InvenTree is our single most valuable reference and it is MIT — code reuse is legally permitted.** Use it as the canonical model for the equipment/inventory domain. In practice, still prefer *reimplementing* its schema rather than vendoring its apps, because InvenTree carries a lot of manufacturing-domain weight (BOMs, builds, purchase orders, supplier parts) that a surf school will never use. Copy the *patterns* — Part/StockItem split, stock-tracking ledger, barcode resolver, `report` app — and copy actual code only for narrow, self-contained utilities. If any InvenTree source file is copied verbatim, reproduce the MIT notice (`Copyright (c) 2017 - InvenTree Developers`) in `THIRD_PARTY_NOTICES.md`.

---

## 2. Booking & Scheduling

The core domain. A surf school booking is harder than a dentist appointment: it is a *group* activity, constrained simultaneously by instructor availability, board/wetsuit availability, tide/weather windows, and a max student-to-instructor ratio.

### 2.1 django-appointment — Apache-2.0 ✅ PERMISSIVE

| Field | Value |
|---|---|
| Repo | https://github.com/adamspd/django-appointment |
| Stars | ~277 · Forks ~84 |
| Last push | 2026-08-12 · Latest release **v3.10.1** (2026-07-27) |
| PyPI | `django-appointment` 3.10.1, `requires_python >=3.8` |
| **SPDX** | **Apache-2.0** (confirmed both by GitHub API and by the PyPI classifier `License :: OSI Approved :: Apache Software License`) |
| Copy code? | **YES** — with attribution + NOTICE handling |

Verified module layout: `models.py`, `services.py`, `views.py`, `views_admin.py`, `tasks.py`, `utils/`, `email_sender/`, `forms.py`, `decorators.py`, `management/`, `locale/`.

**Borrow:** (a) the **`services.py` layer** — booking logic lives in services, not in views or model methods, which is exactly the discipline a booking engine needs; (b) the **slot-generation algorithm** — converting a staff member's working hours minus existing appointments into discrete bookable slots is the fiddliest code in this whole project and it is here, already tested, under a permissive license; (c) the `email_sender/` package structure for confirmation/reminder mails; (d) built-in i18n via `locale/` — relevant for a surf school serving international tourists.

**Limitation:** it models **1 staff ↔ 1 client** appointments. Group lessons, equipment coupling, and instructor-ratio caps are ours to build.

**Apache-2.0 obligations:** retain copyright + license text, state significant changes in modified files, propagate any `NOTICE` file. In exchange we get an explicit patent grant that MIT/BSD do not offer.

### 2.2 django-scheduler — BSD-3-Clause ✅ PERMISSIVE

| Field | Value |
|---|---|
| Repo | https://github.com/llazzaro/django-scheduler |
| Stars | ~1,335 · Forks ~402 |
| Last push | 2026-02-23 (maintained, but slower cadence) |
| PyPI | `django-scheduler` 0.12.0, `requires_python >=3.10` |
| **SPDX** | **BSD-3-Clause** |
| Copy code? | **YES** — with attribution, no endorsement use |

The mature Django calendaring app: `Calendar` / `Event` / `Occurrence` / `Rule`, with **RRULE recurrence and the occurrence-override pattern** — a recurring series generates virtual occurrences, and cancelling or moving *one* instance persists a single override row rather than exploding the series into thousands of database rows.

**Borrow:** the recurrence + occurrence-override model. Recurring weekly kids' courses and instructor shift patterns need exactly this. Getting it wrong (materialising every occurrence) is a classic scaling mistake.

**Caveat:** it ships legacy jQuery/fullcalendar-era templates. Take the `models.py`/recurrence logic, discard the frontend.

### 2.3 Cal.com — MIT ✅ PERMISSIVE (⚠ license changed, and so did the repo name)

| Field | Value |
|---|---|
| Repo | https://github.com/calcom/cal.diy — **the `calcom/cal.com` path now redirects here** |
| Stars | ~47,600 · Forks ~14,780 |
| Last push | 2026-08-08 |
| **SPDX** | **MIT** — verified from the LICENSE file: `MIT License / Copyright (c) 2020-present Cal.com, Inc.` |
| Language | TypeScript (Next.js/Prisma) — **not** copy-pasteable into Django |

⚠ **This is a change from what most documentation and blog posts still say.** Cal.com was long known as AGPL-3.0 with a proprietary `/ee` enterprise directory. The current LICENSE file at the repo root is plain MIT. Because this is a relicensing event, treat it with care: MIT applies to what is in the repo now; do not assume it retroactively covers vendored forks you may have of older AGPL snapshots.

**Borrow (ideas — it's TypeScript anyway):** the **event-type abstraction** (a bookable *template*: duration, buffer before/after, min notice, max bookings per day, location type) is the cleanest model of "what can be booked" in open source, and maps directly to surf lesson products (Beginner Group 2h, Private 1h, 5-Day Course). Also: booking-questions as configurable per-event-type fields, and buffer-time handling — a surf school needs 30 min between sessions for gear turnaround.

### 2.4 Easy!Appointments — GPL-3.0 ⛔ IDEAS ONLY

| Field | Value |
|---|---|
| Repo | https://github.com/alextselegidis/easyappointments |
| Stars | ~4,321 · Last push 2026-08-14 (very active) |
| **SPDX** | **GPL-3.0** |
| Language | PHP |
| Copy code? | **NO** |

Mature, battle-tested booking product. **Ideas only** — and being PHP, the temptation to copy is low anyway. Worth an hour of reading for its *service ↔ provider ↔ working plan ↔ exception* model, especially "working plan exceptions" (a specific date where an instructor's hours differ), which is a real surf-school need.

### 2.5 Also checked (and rejected)

- `foad-heidari/dj-booking` — small, low activity, thin feature set. Not worth the dependency.
- `mdave/pyappointment` — hard dependency on the proprietary Cronofy SaaS. Rejected on lock-in grounds.
- `saroarjahan/Django-appointment-and-booking-system` — tutorial-grade project, not a reference architecture.

> **RECOMMENDATION for booking & scheduling:**
> **Build the booking engine ourselves, seeded from two permissive sources.** Take the **slot-generation + `services.py` layering from `django-appointment` (Apache-2.0)** and the **RRULE recurrence + occurrence-override model from `django-scheduler` (BSD-3-Clause)**. Take the **event-type abstraction as an idea from Cal.com**. Do **not** add `django-appointment` as a runtime dependency — its 1-staff↔1-client assumption is baked into its models and we will fight it within two sprints; instead treat it as a reference implementation and copy the specific algorithms we need (which its Apache-2.0 license explicitly permits) into our own `bookings/services.py`. Add `jazzband/django-recurrence` (**BSD-3-Clause**, ~544★, pushed 2026-07-22, PyPI 1.14) as the actual runtime dependency for storing/parsing RRULEs — it is small, focused, and does not impose a domain model on us. Skip Easy!Appointments except as reading material.

---

## 3. Inventory, Equipment Rental, QR / Barcode

### 3.1 The rental gap — an honest finding

There is **no credible open-source equipment-rental system worth referencing.** A GitHub search for rental + Django, sorted by stars, tops out at **36 stars** (`s1s1ty/CarRentalSystem`, no license, last push 2022) and is otherwise a field of unlicensed student projects and property-rental CRUD apps. The named candidates from the brief in this category do not exist in usable form.

This is genuinely good news framed correctly: **rental is not a missing library, it is a small amount of domain logic on top of a good inventory model.** A rental is a `StockItem` + a time interval + a booking FK + a condition-on-return. InvenTree gives us the hard 80%.

### 3.2 InvenTree — MIT ✅

See §1. This is the answer for equipment. ~7,390★, released 1.5.0 on 2026-08-11, MIT.

### 3.3 Snipe-IT — AGPL-3.0 ⛔ IDEAS ONLY

| Field | Value |
|---|---|
| Repo | https://github.com/grokability/snipe-it (the `snipe/snipe-it` path redirects here) |
| Stars | ~14,818 · Last push 2026-08-15 |
| **SPDX** | **AGPL-3.0** |
| Language | PHP |
| Copy code? | **ABSOLUTELY NOT** — AGPL §13 network clause |

The most popular open-source asset-management system, and the most dangerous entry in this document, because it is the top search result for "open source asset checkout system" and it does *exactly* what a surf school needs at the gear counter.

**Ideas only, and they are good ideas:** the **check-out / check-in state machine** with an asset assignable to a *user*, a *location*, or another *asset*; **maintenance records** attached to an asset (ding repairs, fin replacements, wetsuit seam repairs); **asset status labels** (Deployable / Deployed / Undeployable / Archived) — which maps cleanly to surf gear (Available / Rented / In Repair / Retired); and **depreciation tracking**, which matters more than it sounds for boards and wetsuits with a 2–3 year life.

### 3.4 QR / Barcode libraries

| Library | Repo | Stars | Last push | SPDX | Verdict |
|---|---|---|---|---|---|
| **segno** | https://github.com/heuer/segno | ~795 | 2026-07-23 | **BSD-3-Clause** (clean on both GitHub and PyPI; PyPI 1.6.6) | ✅ **Preferred** |
| qrcode | https://github.com/lincolnloop/python-qrcode | ~4,929 | 2026-03-25 | BSD-2-Clause style (GitHub reports `NOASSERTION`; LICENSE reads `Copyright (c) 2011, Lincoln Loop`, standard BSD, no non-endorsement clause) | ✅ OK, but see caveat |
| python-barcode | https://github.com/WhyNotHugo/python-barcode | ~656 | 2026-07-27 | **MIT** | ✅ For linear barcodes (EAN/Code128) |

⚠ **Caveat on `qrcode`:** its PyPI metadata carries *two* license classifiers — `License :: OSI Approved :: BSD License` **and** `License :: Other/Proprietary License`. The repository LICENSE file itself is unambiguously BSD, so this is almost certainly stale packaging metadata rather than a real dual-license, but it is exactly the kind of ambiguity that costs a day during a legal review. `segno` has no such ambiguity, is pure-Python with zero dependencies (no Pillow required for SVG output), and produces smaller SVGs.

> **RECOMMENDATION for inventory & rental:**
> **Model the equipment domain on InvenTree's Part/StockItem split (MIT, copy freely), and layer Snipe-IT's check-out/check-in state machine and status labels on top as reimplemented ideas (AGPL — read, never copy).** Concretely: `equipment/models.py` gets `GearModel` (catalogue: type, size, brand, suitable-for-level) and `GearUnit` (physical: serial, QR payload, condition, status, purchase date, current location), plus an append-only `GearMovement` ledger for every check-out/check-in/damage/retire event. Do not search further for a rental library — none exists at reference quality. For codes, **use `segno` (BSD-3-Clause)** for QR generation and `python-barcode` (MIT) only if linear barcodes are ever needed; avoid `qrcode` purely to dodge its contradictory PyPI classifier. Generate one QR per `GearUnit` at creation time and print via the InvenTree-style `report` app pattern.

---

## 4. Point of Sale

### 4.1 OnlineRetailPOS — MIT ✅ PERMISSIVE (but stale and small)

| Field | Value |
|---|---|
| Repo | https://github.com/virajkothari7/OnlineRetailPOS |
| Stars | ~47 · Forks ~27 |
| Last push | **2025-01-01** (~19 months stale) |
| **SPDX** | **MIT** |
| Copy code? | Legally yes — practically, borrow only the UX idea |

Named in the brief, and it does exist and is genuinely MIT. But at 47 stars and 19 months without a commit, it is a personal project, not a reference architecture. Its one distinctive idea is worth stealing: it is designed for a **Windows touch tablet/PC running Django locally, with a second display for a customer-facing view**. That is uncannily close to a surf school's beach-hut counter — and, notably, it is a Windows-native deployment story, matching our constraint.

### 4.2 Open Source Point of Sale — MIT ✅ PERMISSIVE

| Field | Value |
|---|---|
| Repo | https://github.com/opensourcepos/opensourcepos |
| Stars | ~4,346 · Last push 2026-08-14 (very active) |
| **SPDX** | **MIT** — GitHub reports `NOASSERTION` because the LICENSE file carries four stacked copyright lines, but the text itself opens `MIT License / Copyright (c) 2013-2025 jekkos ...` |
| Language | PHP (CodeIgniter) |

Mature, actively developed, permissively licensed. PHP, so no direct code reuse, but a far better *design* reference than OnlineRetailPOS: register/shift sessions, cash drawer reconciliation, split tender, receipts, returns, customer store-credit.

### 4.3 django-oscar — BSD-3-Clause ✅ / Saleor — BSD-3-Clause ✅

| Project | Repo | Stars | Last push | SPDX |
|---|---|---|---|---|
| django-oscar | https://github.com/django-oscar/django-oscar | ~6,616 | 2026-08-12 | **BSD-3-Clause** |
| Saleor | https://github.com/saleor/saleor | ~23,224 | 2026-08-14 | **BSD-3-Clause** |

Both permissive, both Python/Django, both far larger than we need. **Oscar's real gift is its `Order` state machine and its `Line`/`LinePrice` split**, plus the "fork the app to customise" pattern. **Saleor's gift is its checkout→payment→fulfilment separation** and how it models discounts/vouchers — a surf school will absolutely need vouchers ("5-lesson pack", "hotel partner rate", "group discount").

> **RECOMMENDATION for POS:**
> **Do not adopt a POS framework.** django-oscar and Saleor are e-commerce platforms whose weight (catalogue, cart, shipping, tax engines, GraphQL in Saleor's case) would dominate the project for a use case that is really "take payment for a lesson and rent some gear at a counter." Instead: build a thin `billing/` app modelling `Order` → `OrderLine` → `Payment`, borrowing **Oscar's order state machine and line-price split (BSD-3-Clause, copy permitted)** and **Saleor's voucher/discount model as an idea**. Steal the **dual-display touch layout idea from OnlineRetailPOS (MIT)** for the beach-counter UI, and read **opensourcepos (MIT)** for register-shift and cash-reconciliation semantics before designing the till. For card payments use **`dj-stripe` (MIT, ~1,787★, pushed 2026-08-09)** rather than hand-rolling webhook handling.

---

## 5. CRM

Every serious open-source CRM is copyleft. This category is **ideas-only across the board.**

| Project | Repo | Stars | Last push | SPDX | Verdict |
|---|---|---|---|---|---|
| SuiteCRM | https://github.com/SuiteCRM/SuiteCRM | ~5,662 | 2026-07-31 | **AGPL-3.0** | ⛔ Ideas only |
| EspoCRM | https://github.com/espocrm/espocrm | ~3,230 | 2026-08-14 | **AGPL-3.0** | ⛔ Ideas only |
| django-crm | https://github.com/DjangoCRM/django-crm | ~610 | 2026-08-13 | **AGPL-3.0** | ⛔ Ideas only — *and it's Django, so the copy temptation is high* |
| ERPNext | https://github.com/frappe/erpnext | ~38,102 | 2026-08-15 | **GPL-3.0** | ⛔ Ideas only |
| Odoo | https://github.com/odoo/odoo | ~53,728 | 2026-08-15 | **LGPL-3.0** (LICENSE text; GitHub reports `NOASSERTION`) | ⛔ Ideas only |

⚠ **`DjangoCRM/django-crm` is the sharpest hazard in this table.** It is Python, it is Django, it is actively maintained, and its models will look immediately copy-pasteable to a developer under deadline. It is **AGPL-3.0**. Copying even one model class into our codebase would, on a strict reading, oblige us to publish the entire Surf School source to every user who hits the site.

⚠ **Odoo nuance:** the community edition is LGPL-3.0 — which for a *dynamically linked library* would be relatively permissive, but Odoo modules are not libraries we link against, they are application code we would be deriving from. Odoo Enterprise is separately proprietary. Treat the whole thing as ideas-only.

**Ideas actually worth having:** the *lead → opportunity → customer* lifecycle; activity/interaction timelines on a contact record (every call, email, lesson, and no-show on one scrollable feed); and segmentation for marketing (e.g. "everyone who took a beginner lesson last summer and hasn't returned").

> **RECOMMENDATION for CRM:**
> **Build nothing that calls itself a CRM. Build a `students/` app with a customer timeline.** A surf school's CRM needs are ~5% of SuiteCRM: a `Student` profile (level, medical notes, waiver signed date, emergency contact, photo consent), an append-only `StudentActivity` timeline, and simple tag-based segmentation for a seasonal mailing. Read EspoCRM's entity/relationship design for inspiration if a richer model is ever needed. **Formally blacklist `DjangoCRM/django-crm` in the project's contributing guide** with a one-line reason ("AGPL — Django code, do not copy"), because it is the one AGPL project in this document that a hurried developer could plausibly paste from without noticing.

---

## 6. RBAC, Permissions, Multi-Tenancy

Surf schools have sharply defined roles: Owner, Manager, Instructor, Front-desk, and Student/Parent. Instructors must see only their own lessons; front-desk must take payments but not change pricing.

| Library | Repo | Stars | Last push | SPDX | Verdict |
|---|---|---|---|---|---|
| **django-rules** | https://github.com/dfunckt/django-rules | ~1,978 | 2025-10-11 | **MIT** | ✅ **Primary pick** |
| django-guardian | https://github.com/django-guardian/django-guardian | ~3,910 | 2026-07-30 | **BSD-2-Clause** + CC-BY-4.0 (icons only) | ✅ Fallback |
| django-organizations | https://github.com/bennylope/django-organizations | ~1,363 | 2026-08-09 | **BSD-2-Clause** | ✅ For multi-school |
| django-tenants | https://github.com/django-tenants/django-tenants | ~1,882 | 2026-08-10 | **MIT** | ✅ Only if true isolation is required |
| django-role-permissions | https://github.com/vintasoftware/django-role-permissions | ~755 | **2023-06-09** | MIT | ⚠ Stale ~3 years — avoid |

**License notes:** GitHub reports `NOASSERTION` for **django-guardian**; the LICENSE file is a standard BSD grant (`Copyright (c) 2010-2025 The django-guardian Contributors`) with **no** non-endorsement clause → BSD-2-Clause, plus a clearly separated CC-BY-4.0 section covering bundled Font Awesome Free 6.7.2 SVG icons. Permissive either way; if we ever vendor those icons, the CC-BY attribution applies to them specifically.

**Why django-rules over django-guardian:** guardian stores per-object permissions as *rows in the database*, which is powerful but means every gear item and every booking accrues permission rows and every check is a query. django-rules evaluates *predicates in Python* with zero database overhead — `is_lesson_instructor & is_lesson_not_finalised`. Surf school rules are relational-logical ("you may edit a lesson if you teach it and it hasn't been invoiced"), not per-object ACL grants. django-rules is also the better fit for HTMX, since the same predicate can gate both the view and the template fragment.

⚠ `django-rules` last saw a push on 2025-10-11 — ~10 months quiet. This is acceptable for a small, stable, feature-complete library with no framework coupling, but note it as a watch item.

> **RECOMMENDATION for RBAC:**
> **Use Django's built-in Groups for coarse roles (Owner / Manager / Instructor / Frontdesk / Student) + `django-rules` (MIT) for object-level predicates.** Do not build a custom permission framework, and do not reach for `django-guardian` unless a genuine per-object ACL requirement appears (it is BSD-2 and a safe fallback if so). Skip `django-role-permissions` — 3 years stale. **Defer multi-tenancy:** if the product later serves multiple surf schools, add `django-organizations` (BSD-2-Clause) for shared-schema membership first; only escalate to `django-tenants` (MIT, PostgreSQL schema-per-tenant) if a customer contractually demands data isolation — note it is PostgreSQL-only and would break the SQLite dev workflow.

---

## 7. Admin UI, Dashboards, and the HTMX Frontend

This is where our HTMX + Alpine + Tailwind choice pays off, and where the reference quality is highest.

### 7.1 django-htmx-patterns — Unlicense (PUBLIC DOMAIN) ✅✅ BEST-CASE LICENSE

| Field | Value |
|---|---|
| Repo | https://github.com/spookylukey/django-htmx-patterns |
| Stars | ~1,062 |
| Last push | 2026-01-11 |
| **SPDX** | **Unlicense** — LICENSE.rst states: *"All software or snippets of computer code contained in this repo is released into the public domain, as per the 'Unlicense' licence"* |
| Copy code? | **YES — freely, no attribution legally required** |

The single most copy-friendly resource in this entire document. A curated pattern repository with full working example code for exactly the problems we will hit: inline editing, modal dialogs, form validation round-trips, pagination/infinite scroll, dependent/cascading selects, and partial re-rendering.

### 7.2 django-unfold — MIT ✅ (and it already uses our exact stack)

| Field | Value |
|---|---|
| Repo | https://github.com/unfoldadmin/django-unfold |
| Stars | ~3,627 · Forks ~363 |
| Last push | 2026-08-14 · PyPI 0.104.1 (`requires_python >=3.12,<4.0`) |
| **SPDX** | **MIT** |

A modern Django admin theme built on **Tailwind CSS with HTMX and Alpine.js for interactivity** — i.e. it is architecturally identical to our chosen frontend stack. Dark mode, responsive, custom action buttons, configurable dashboards, extensible filters. The maintainers offer paid professional services, but **the package itself is unambiguously MIT** with no open-core split.

⚠ **Version constraint:** PyPI declares `requires_python >=3.12,<4.0`. **Our stack specifies Python 3.11.** Current `django-unfold` releases will not install on 3.11. This is a real conflict that must be resolved at pin time — either pin an older Unfold release that supports 3.11, or move the project to Python 3.12+.

### 7.3 Other frontend/admin references

| Project | Repo | Stars | Last push | SPDX | Note |
|---|---|---|---|---|---|
| htmx | https://github.com/bigskysoftware/htmx | ~48,965 | 2026-08-12 | **0BSD** | Most permissive license in existence — zero conditions, not even attribution |
| Alpine.js | https://github.com/alpinejs/alpine | ~31,854 | 2026-08-14 | **MIT** | |
| Tailwind CSS | https://github.com/tailwindlabs/tailwindcss | ~97,231 | 2026-08-14 | **MIT** | |
| django-htmx | https://github.com/adamchainz/django-htmx | ~2,003 | 2026-08-13 | **MIT** | PyPI 1.29.0. Request helpers, `HttpResponseClientRedirect`. Essential glue. |
| django-template-partials | https://github.com/carltongibson/django-template-partials | ~655 | 2025-11-20 | **MIT** | ⭐ Define named fragments *inside* a template so HTMX can re-render just that block — avoids a `partials/` directory explosion |
| django-components | https://github.com/django-components/django-components | ~1,514 | 2026-08-10 | **MIT** | Heavier component model; alternative to the above |
| django-tailwind | https://github.com/timonweb/django-tailwind | ~1,752 | 2026-06-12 | **MIT** | Manages the Tailwind build from `manage.py` — useful on Windows where npm workflows are fiddly |
| django-jazzmin | https://github.com/farridav/django-jazzmin | ~1,877 | 2026-06-25 | **MIT** | AdminLTE/Bootstrap skin. Works, but Bootstrap conflicts with our Tailwind choice |
| neapolitan | https://github.com/carltongibson/neapolitan | ~705 | 2026-03-15 | **MIT** | ⭐ CRUD class-based views — scaffolds list/detail/create/update/delete fast |
| django-tables2 | https://github.com/jieter/django-tables2 | ~2,012 | 2026-07-20 | BSD-2-Clause style (GitHub `NOASSERTION`; "same terms as the original django-tables", Copyright (c) 2011 Bradley Ayers) | ✅ Sortable/paginated tables |
| django-crispy-forms | https://github.com/django-crispy-forms/django-crispy-forms | ~5,160 | 2026-07-29 | **MIT** | Form rendering; needs a Tailwind template pack |
| django-filter | https://github.com/carltongibson/django-filter | ~4,685 | 2026-07-15 | **BSD-3-Clause** (3 clauses incl. non-endorsement; GitHub `NOASSERTION`; PyPI 26.1) | ✅ Pairs beautifully with HTMX filter forms |
| falco-cli | https://github.com/falcopackages/falco-cli | ~391 | 2026-06-15 | **MIT** (LICENSE: `Copyright (c) 2024 Tobi DEGNON`) | Opinionated modern-Django CRUD/HTMX scaffolding + guides |
| matorral | https://github.com/matorral-project/matorral | ~88 | 2026-08-07 | **AGPL-3.0** | ⛔ Ideas only — a real Django+HTMX app, useful to *read* for structure |

**Charts:** there is no compelling Django-specific charting library worth a dependency. Both `django-plotly-dash` and `django-chartjs` failed to surface as maintained, widely-adopted options in current searching. Modern practice is to render a JSON endpoint from DRF and draw client-side.

> **RECOMMENDATION for UI:**
> **Adopt this exact frontend set, all permissive:** `htmx` (0BSD) + `alpinejs` (MIT) + `tailwindcss` (MIT) + **`django-htmx`** (MIT) + **`django-template-partials`** (MIT) + **`django-filter`** (BSD-3) + **`django-tables2`** (BSD-2-style) + **`neapolitan`** (MIT) for CRUD scaffolding.
> **Before writing a single interactive view, work through `spookylukey/django-htmx-patterns` — it is public domain (Unlicense), so its example code may be copied verbatim with zero legal obligation.** That is the highest-leverage, lowest-risk asset in this entire audit.
> For the staff back-office, **use `django-unfold` (MIT)** — it is the only admin theme built on our exact HTMX/Alpine/Tailwind stack, which means one design language across admin and custom pages. **Resolve the Python version conflict first:** current Unfold requires Python ≥3.12 while the stack says 3.11. Recommend moving the project to **Python 3.12** (still fully supported by Django 5.2 LTS) rather than pinning a stale Unfold. Reject `django-jazzmin` — its Bootstrap/AdminLTE base would force two CSS frameworks into one project. For charts, expose DRF JSON endpoints and render with a client-side library chosen at build time; do not take a Django charting dependency.

---

## 8. Background Tasks — A Windows-Specific Decision

The brief lists "Celery+Redis optional." Given **Windows 11 native with no Docker**, this deserves its own recommendation, and there is a strong signal from our primary reference: **InvenTree, an MIT Django app of comparable domain complexity, uses Django-Q rather than Celery.**

| Option | Repo | Stars | Last push | SPDX | Windows-native fit |
|---|---|---|---|---|---|
| **django-q2** | https://github.com/django-q2/django-q2 | ~624 | 2026-08-14 | **MIT** | ✅ Works with the DB as broker — **no Redis needed** |
| huey | https://github.com/coleifer/huey | ~6,007 | 2026-08-05 | **MIT** | ✅ SQLite/Redis brokers, very light |
| django-tasks | https://github.com/RealOrangeOne/django-tasks | ~810 | 2026-05-22 | **BSD-3-Clause** | ✅ Reference implementation of Django's own Tasks framework direction |
| Celery | https://github.com/celery/celery | ~28,784 | 2026-08-14 | **BSD-3-Clause** (LICENSE: *"Celery is licensed under The BSD License (3 Clause...)"*; GitHub `NOASSERTION`; PyPI 5.6.3) | ⚠ Requires a broker; Windows support has long been non-first-class |

All four are permissive — this is purely an operational decision, not a legal one.

> **RECOMMENDATION for background tasks:**
> **Start with `django-q2` (MIT), using the PostgreSQL/SQLite database as the broker — no Redis, no Docker, no extra service to babysit on Windows 11.** This mirrors InvenTree's own choice for the same class of application, and it removes an entire moving part from a deployment that has no container runtime. Reminder emails, invoice PDFs, and end-of-day reports do not need Celery's throughput. Keep Celery as a documented escalation path (it is BSD-3, so switching later costs engineering time, not legal review) and revisit only if job volume genuinely demands it. Watch `RealOrangeOne/django-tasks` (BSD-3) as the likely future-standard once Django's built-in Tasks framework matures.

---

## 9. Explicitly Rejected — And Why

These matter as much as the picks, because each is a plausible-looking search result that would waste a sprint or create legal exposure.

| Project | Repo | Stars | SPDX | Why rejected |
|---|---|---|---|---|
| **sportsms** | https://github.com/MaliusMartin/sportsms | **6** | **NONE — no LICENSE file** | Named in the brief; the real repo exists but has 6 stars, 3 forks, last push 2025-01-19, is 46 MB (committed binaries/media), and GitHub reports its primary language as **JavaScript** despite the Django claim. **No license = all rights reserved.** Cannot copy a single line. Nothing here we can't design better in an afternoon. |
| **wger** | https://github.com/wger-project/wger | ~6,658 | **AGPL-3.0** | The best-known Django fitness/gym app (~6,660★, pushed 2026-08-14) and the obvious hit for "Django gym management." **AGPL — network copyleft.** Ideas only: its gym-member management and workout-log models are worth reading, nothing more. |
| Various Django gym systems | `mithun-t/...`, `Pradip-p/GYMfits`, `Pawan243/...` | 6–22 | Mostly none | Tutorial-grade, 6–22 stars, mostly unlicensed. No architectural value. |
| TareqMonwer/Django-School-Management | https://github.com/TareqMonwer/Django-School-Management | ~591 | **NONE** | 591 stars makes this look authoritative in search results. **No license file — all rights reserved.** Do not copy. |
| mwinamijr/django-scms | https://github.com/mwinamijr/django-scms | ~59 | **NONE** | Same trap, smaller. |
| adigunsherif/Django-School-Management-System | https://github.com/adigunsherif/Django-School-Management-System | ~393 | **MIT** | Legally fine to copy, but it is an academic-school system (grades, exams, semesters) with almost no overlap with a surf school's booking/gear/weather domain. |
| django-money | https://github.com/django-money/django-money | ~1,775 | BSD-3-style | **ARCHIVED as of 2026.** Do not add to a new project. Use `Decimal` with an explicit currency field, or find a maintained successor at pin time. |
| django-role-permissions | https://github.com/vintasoftware/django-role-permissions | ~755 | MIT | Last push 2023-06-09 — ~3 years stale. |
| ERPNext / Odoo / SuiteCRM / EspoCRM | — | 3k–54k | GPL-3.0 / LGPL-3.0 / AGPL-3.0 | Copyleft. Ideas only. Also 100× our required scope. |

> **RECOMMENDATION:** Record this rejection table in the project wiki. Its purpose is to stop a future contributor from "discovering" wger or sportsms in month four and quietly pasting from them. **The unlicensed repos (sportsms, TareqMonwer, django-scms) are the highest-risk entries here precisely because they look harmless** — no scary AGPL banner, just an absence most developers never check.

---

## 10. Large-Scale Django Architecture References (all permissive)

For "how do you structure a Django project that will still be maintainable at 40k lines" — read these rather than any of the small booking apps.

| Project | Repo | Stars | Last push | SPDX | What to borrow |
|---|---|---|---|---|---|
| **NetBox** | https://github.com/netbox-community/netbox | ~21,315 | 2026-08-15 | **Apache-2.0** | ⭐ The best-in-class Django+DRF architecture reference. Change-logging on every object, custom fields, tags, saved filters, a plugin framework, and consistent generic CRUD views. Domain is networking but the *skeleton* is exactly what an asset-heavy business app needs. |
| Saleor | https://github.com/saleor/saleor | ~23,224 | 2026-08-14 | **BSD-3-Clause** | Checkout → payment → fulfilment separation; discounts/vouchers |
| django-oscar | https://github.com/django-oscar/django-oscar | ~6,616 | 2026-08-12 | **BSD-3-Clause** | Order state machine; "fork the app to customise" pattern |
| Wagtail | https://github.com/wagtail/wagtail | ~20,445 | 2026-08-14 | **BSD-3-Clause** | The best admin UX in the Django ecosystem; excellent permission/workflow model |
| Django itself | https://github.com/django/django | ~88,428 | 2026-08-14 | **BSD-3-Clause** | — |

**Supporting libraries, all verified permissive:**
`djangorestframework` **BSD-3-Clause** (LICENSE.md, Encode OSS Ltd; GitHub `NOASSERTION`; PyPI 3.18.0) · `django-allauth` **MIT** (~10,368★) · `django-simple-history` **BSD-3-Clause** (~2,461★, now under `django-commons/`) · `django-import-export` **BSD-2-Clause** (~3,332★, now at `django-import-export/django-import-export`, PyPI 4.4.1) · `django-model-utils` **BSD-3-Clause** · `django-debug-toolbar` **BSD-3-Clause** (now under `django-commons/`) · `django-axes` **MIT** · `django-two-factor-auth` **MIT** · `django-waffle` **BSD-3-Clause** (now at `django-waffle/django-waffle`) · `django-oauth-toolkit` **BSD-2-Clause style** (now at `django-oauth/django-oauth-toolkit`) · `dj-stripe` **MIT** · `django-typer` **MIT**.

> ⚠ **Note on repo moves:** several long-standing Jazzband packages have migrated to new orgs (`django-commons`, `django-waffle`, `django-oauth`, `django-import-export`), and `wsvincent/djangox` is now `wsvincent/lithium`. Old URLs redirect today, but **pin by PyPI package name, never by git URL.**

> **RECOMMENDATION for overall architecture:**
> **Use NetBox (Apache-2.0) as the primary structural template for the whole project** — it is the closest permissively-licensed analogue to what we are building (an asset-and-relationship-heavy Django+DRF business application with a server-rendered UI), and Apache-2.0 permits direct code reuse with attribution. Specifically adopt its **change-logging pattern** (every mutation journaled — indispensable for "who cancelled this booking?"), its **tagging model**, and its **consistent generic CRUD view base classes**. Layer InvenTree's domain modelling (§1) on top for equipment. Read Wagtail for admin UX standards.

---

## 11. Starter Kits

| Project | Repo | Stars | Last push | SPDX | Verdict |
|---|---|---|---|---|---|
| **cookiecutter-django** | https://github.com/cookiecutter/cookiecutter-django | ~13,592 | 2026-08-15 | **BSD-3-Clause** | ✅ The reference. Read it; don't necessarily run it. |
| lithium (ex-djangox) | https://github.com/wsvincent/lithium | ~2,463 | 2026-04-09 | MIT text (multi-copyright header → GitHub `NOASSERTION`) | ✅ Minimal, readable |
| falco-cli | https://github.com/falcopackages/falco-cli | ~391 | 2026-06-15 | **MIT** | ✅ Modern HTMX-first Django DX + guides |
| SaaS Pegasus | — | — | — | **Commercial/proprietary** | ⛔ Paid licence — not open source, excluded |

**The cookiecutter-django caveat for us:** its generated project is heavily **Docker-oriented** (compose files for local and production, Traefik, containerised Postgres/Redis/Celery). We are on **Windows 11 native with no Docker**, so a generated project would arrive with a large amount of infrastructure we cannot run and would have to strip out — often the slowest possible way to start.

> **RECOMMENDATION for starter kits:**
> **Do not generate the project from cookiecutter-django. Start from `django-admin startproject` and lift patterns from cookiecutter-django by hand.** Specifically borrow its **split settings layout** (`config/settings/{base,local,production}.py`), its **environment-variable configuration convention** (`django-environ`), its **`requirements/{base,local,production}.txt` split**, and its **custom user model from commit #1** (adding one later is genuinely painful). This gives us cookiecutter's hard-won production wisdom without importing a Docker-shaped deployment we cannot execute. Skim `falco-cli` (MIT) for HTMX-era conventions. Both are permissive, so lifting config files verbatim is legally fine — record the BSD-3 notice for cookiecutter-django if any file is copied wholesale.

---

## 12. Stack Compatibility Flags Found During This Research

Two conflicts surfaced that affect which references are actually usable. Flagging rather than silently assuming.

1. **Python 3.11 vs `django-unfold`.** Current `django-unfold` (0.104.1) declares `requires_python >=3.12,<4.0`. It will not install on Python 3.11. Since Unfold is the one admin theme matching our HTMX/Alpine/Tailwind stack, this is a real fork in the road.
2. **"Django 5" vs current releases.** As of 2026-08-15 the Django download page lists **5.2 LTS** (latest 5.2.17, mainstream support ended 2025-12-03, **extended support to April 2028**), **6.0** (mainstream to 2026-08-04, extended to April 2027), and **6.1** (current, mainstream to April 2027). PyPI's default `Django` is **6.1**, which requires **Python ≥3.12**. So "pip install Django" today does *not* give you Django 5.

> **RECOMMENDATION:** **Move the project to Python 3.12 and pin `Django>=5.2,<6.0` (5.2 LTS).** Python 3.12 is fully supported by Django 5.2 LTS, unblocks `django-unfold` and other modern packages already dropping 3.11, and costs nothing at this stage — whereas discovering the 3.11 ceiling after the admin UI is built is expensive. Keep Django on **5.2 LTS**, not 6.x: extended support runs to **April 2028**, which is the longest runway available and matches the "Django 5" decision already made. Pin explicitly in `requirements/base.txt` so `pip install` never silently pulls Django 6.1. Revisit at the next LTS.

---

## 13. Final Shopping List

**Copy code from (PERMISSIVE — attribution required):**

| Source | SPDX | For |
|---|---|---|
| spookylukey/django-htmx-patterns | **Unlicense** (public domain) | HTMX interaction patterns — copy freely |
| InvenTree | **MIT** | Part/StockItem split, stock ledger, barcode resolver, report app |
| NetBox | **Apache-2.0** | Overall project skeleton, change logging, generic CRUD views |
| django-appointment | **Apache-2.0** | Slot generation, `services.py` layering, email sender |
| django-scheduler | **BSD-3-Clause** | RRULE recurrence + occurrence overrides |
| django-oscar / Saleor | **BSD-3-Clause** | Order state machine, line prices, vouchers |
| cookiecutter-django | **BSD-3-Clause** | Split settings, env config, requirements layout |
| htmx / Alpine / Tailwind / django-htmx / django-template-partials / neapolitan / django-filter / django-tables2 / django-rules / django-q2 / dj-stripe / segno / django-unfold | 0BSD / MIT / BSD | Runtime dependencies |

**Read for ideas only (COPYLEFT / UNLICENSED — never copy):**
Snipe-IT (AGPL-3.0) · wger (AGPL-3.0) · **django-crm (AGPL-3.0 — highest paste risk, it's Django)** · EspoCRM (AGPL-3.0) · SuiteCRM (AGPL-3.0) · matorral (AGPL-3.0) · Easy!Appointments (GPL-3.0) · ERPNext (GPL-3.0) · Odoo (LGPL-3.0) · **sportsms / TareqMonwer / django-scms (NO LICENSE — most restrictive of all)**

> **FINAL RECOMMENDATION:**
> The legal position is better than expected: **InvenTree is MIT, not GPL** — so our single most valuable domain reference is fully reusable, and the flagged risk did not materialise. Build on **NetBox's skeleton (Apache-2.0) + InvenTree's equipment domain (MIT) + a hand-rolled booking engine seeded from django-appointment (Apache-2.0) and django-scheduler (BSD-3)**, with the frontend driven by the public-domain **django-htmx-patterns**. Take **zero runtime dependencies on copyleft projects**. The three concrete actions before the first commit: (1) create `THIRD_PARTY_NOTICES.md`; (2) commit the §9 rejection table to the wiki so AGPL and unlicensed repos are pre-blacklisted by name; (3) settle the **Python 3.12 + Django 5.2 LTS** pin, since the current 3.11 assumption already blocks a recommended package.

---

*All star counts, push dates, and SPDX identifiers verified against the live GitHub API and PyPI on 2026-08-15. Every license classified above was read from the project's own LICENSE file where GitHub reported `NOASSERTION`.*
