# Python Dependency Set — Smart Surf School Management System

**Target platform:** Windows 11 (native, no Docker, no C compiler guaranteed, no GTK/MSYS2)
**Python:** 3.11 (CPython, 64-bit)
**Research date:** 2026-08-15 — all versions and licenses verified against PyPI / upstream sources on this date.

**Hard constraint driving every choice below:** every package must install from a *prebuilt wheel* via `pip install` alone. No source builds, no `vcvarsall.bat`, no MSYS2, no GTK, no Node.js requirement at runtime.

---

## 0. Executive summary — the constraint you cannot negotiate

**Python 3.11 forces Django 5.2 LTS.** This is not a preference, it is a wheel-level fact:

| Django | Requires-Python | Works on 3.11? |
|---|---|---|
| 5.2.17 (LTS) | >=3.10 | **YES** (3.10–3.14) |
| 6.0.8 | >=3.12 | NO |
| 6.1 | >=3.12 | NO |

Django 6.1 was released 2026-08-05 and its PyPI `Requires-Python` is `>=3.12`. On Python 3.11, `pip install django` will silently resolve *backwards* to the newest 5.2.x. Pin it explicitly so this is intentional rather than accidental.

This is a happy accident: Django 5.2 is the current **LTS** with extended support to **April 2028**, which is exactly what a production line-of-business app wants. The stack below is therefore built entirely on the 5.2 LTS line.

**Second-order consequence:** anything that has already dropped Django 5.2 or Python 3.11 is out. Everything recommended below was checked against both.

---

## 1. Core framework — Django, DRF, filtering, OpenAPI

### Verified facts

| Package | Latest | License | Requires-Python | Notes |
|---|---|---|---|---|
| Django | 6.1 (2026-08-05) | BSD-3-Clause | **>=3.12** | Unusable on 3.11 |
| Django | **5.2.17** (2026-08-04) | BSD-3-Clause | >=3.10 | **LTS, ext. support to Apr 2028** |
| djangorestframework | 3.18.0 (2026-08-07) | BSD-3-Clause | >=3.10 | Supports Django 5.2/6.0/6.1 |
| djangorestframework | **3.17.2** (2026-08-05) | BSD-3-Clause | >=3.10 | Last version predating spectacular's cap |
| django-filter | **26.1** (2026-07-11) | BSD-3-Clause | >=3.10 | CalVer; supports Django 5.2/6.0/6.1 |
| drf-spectacular | **0.30.0** (2026-07-06) | BSD-3-Clause | >=3.8 | Documents DRF **3.12–3.17** |

### The DRF version trap

DRF **3.18.0 shipped 2026-08-07**. drf-spectacular **0.30.0 shipped 2026-07-06** — a month *earlier*. Its documented support matrix stops at **DRF 3.17**.

Checked the actual constraint in drf-spectacular's `pyproject.toml`: the dependency is `djangorestframework>=3.10.3` with **no upper bound**. So pip *will* happily install spectacular alongside DRF 3.18 — it just won't error. That is worse than a hard pin: you get an untested combination with no warning, and schema generation is exactly the kind of deep-introspection code that breaks silently on a DRF internals change.

For a production system, take the combination the maintainer actually tested.

> **RECOMMENDATION:** Pin **Django 5.2.17 LTS** (forced by Python 3.11, and the right call anyway — support to April 2028). Pin **DRF 3.17.2, not 3.18.0**, because drf-spectacular 0.30.0 predates 3.18 and only documents support through 3.17; there is no upper-bound pin to protect you, so pin it yourself. Add **django-filter 26.1** and **drf-spectacular 0.30.0**. All four are BSD-3-Clause — zero licensing friction. Re-evaluate the DRF 3.18 upgrade only after a drf-spectacular release explicitly names it.

```txt
Django==5.2.17                    # BSD-3-Clause
djangorestframework==3.17.2       # BSD-3-Clause  (NOT 3.18 — see above)
django-filter==26.1               # BSD-3-Clause
drf-spectacular==0.30.0           # BSD-3-Clause
```

---

## 2. Authentication — allauth vs built-in, session vs JWT

### Verified facts

| Package | Latest | License | Requires-Python | Base deps |
|---|---|---|---|---|
| django-allauth | 65.19.1 | MIT | >=3.10 | `Django>=4.2.16`, `asgiref>=3.8.1` — **pure Python** |
| djangorestframework-simplejwt | 5.5.1 (2025-07-21) | MIT | >=3.9 | `django>=4.2`, `djangorestframework>=3.14`, `pyjwt>=1.7.1` |

**Critical detail on allauth:** the *base* install pulls only Django and asgiref — no C extensions, no `cryptography`. The compiled dependencies live entirely in **extras**, which you opt into:

| Extra | Pulls in | Windows risk |
|---|---|---|
| (base) | — | **None** |
| `socialaccount` | `oauthlib`, `requests`, `pyjwt[crypto]` → `cryptography` | Low — cryptography ships abi3 win_amd64 wheels |
| `mfa` | `qrcode`, `fido2` | Low — both pure Python |
| `saml` | `python3-saml` → **`xmlsec`/`lxml` native libs** | **HIGH — avoid on Windows** |
| `openid` / `steam` | `python3-openid` | Low |

`python3-saml` is the one that will ruin your day on Windows: it binds to libxmlsec1. A surf school does not need SAML.

**simplejwt caveat:** 5.5.1 is from July 2025 and its classifiers list Django only up to **5.2** — it has not been validated against Django 6.x. On the 5.2 LTS line this is a non-issue, and it is a further argument for staying on 5.2.

### Architecture

Two different clients, two different auth mechanisms — do not force one to serve both:

- **Server-rendered HTMX pages** (staff dashboard, booking screens): Django **session auth**. HTMX sends cookies natively; sessions are revocable server-side and immune to the "where do I store the token" problem. Combine with `SessionAuthentication` in DRF so the browsable API and HTMX partials just work.
- **Mobile app / third-party integrations** (future): **simplejwt** with short-lived access tokens plus rotating refresh tokens and the blacklist app enabled.

> **RECOMMENDATION:** Use **django-allauth 65.19.1 (base install, no extras)** on top of Django's built-in `User`/auth — you get email-as-login, email verification, and password reset flows that you would otherwise write and get subtly wrong. It is MIT and pure Python, so it costs nothing on Windows. Do **NOT** install the `saml` extra (native `xmlsec`). Add `socialaccount` only if you actually want "Sign in with Google" — `cryptography` has abi3 Windows wheels so it is safe, just unnecessary weight. For the API, layer **simplejwt 5.5.1** with `ROTATE_REFRESH_TOKENS=True` and the blacklist app, while keeping `SessionAuthentication` as the first authentication class so HTMX views need no token plumbing.

```txt
django-allauth==65.19.1                  # MIT  (base install only — NOT [saml])
djangorestframework-simplejwt==5.5.1     # MIT
```

---

## 3. Environment configuration

### Verified facts

| Package | Latest | Release date | License | Requires-Python | Compiled deps |
|---|---|---|---|---|---|
| django-environ | **0.14.0** | 2026-06-18 | MIT | >=3.9,<4 | None |
| python-decouple | 3.8 | **2023-03-01** | MIT | (unspecified) | None |
| pydantic-settings | 2.15.0 | 2026-08-07 | MIT | >=3.10 | **pydantic-core (Rust)** |

- **python-decouple 3.8** has not shipped since **March 2023** — over three years stale, no declared `Requires-Python`. Rejected on maintenance grounds alone.
- **pydantic-settings 2.15.0** is excellent and actively developed, but drags in `pydantic>=2.7` → `pydantic-core`, a compiled Rust extension. It *does* publish Windows wheels, so it would install — but it is a heavyweight validation framework bolted onto config reading, and it adds a Rust toolchain to your risk surface for no gain here.
- **django-environ 0.14.0** (June 2026, actively maintained, supports Django through 6.0) is purpose-built: `env.db()` parses `DATABASE_URL` straight into Django's `DATABASES` dict, `env.cache()` does the same for Redis. That URL-parsing is exactly the SQLite-dev/Postgres-prod switch this project needs, with zero custom code.

> **RECOMMENDATION:** **django-environ 0.14.0**. MIT, pure Python, actively maintained, and its `env.db()` / `env.cache()` helpers give you the dev-SQLite → prod-PostgreSQL switch as a one-line settings change driven purely by `DATABASE_URL`. Reject python-decouple (unmaintained since 2023) and pydantic-settings (pulls the compiled pydantic-core for no benefit at this scale).

```txt
django-environ==0.14.0            # MIT
```

---

## 4. PostgreSQL driver

### Verified facts

- **psycopg 3.3.4**, `Requires-Python >=3.10`
- **License: `LGPL-3.0-only`** ⚠️ **COPYLEFT — FLAGGED, see §16**
- **`psycopg-binary` 3.3.4** (released 2026-05-01) ships **`psycopg_binary-3.3.4-cp311-cp311-win_amd64.whl` (3.6 MB)** — **CONFIRMED present on PyPI.** Wheels cover CPython 3.10–3.14 on Windows, macOS and Linux.

So `pip install "psycopg[binary]==3.3.4"` on Windows 11 + Python 3.11 pulls a prebuilt wheel with libpq statically bundled. **No PostgreSQL client install, no compiler, no `pg_config` on PATH.** This is the single smoothest part of the whole stack.

Do not use `psycopg[c]` (compiles from source) and do not use legacy `psycopg2` (needs `pg_config`); `psycopg2-binary` exists but is explicitly discouraged for production by its own maintainers and is the older 2.x API.

Note the driver is only needed in production. Dev runs on SQLite from stdlib, so keep this in a separate `requirements-prod.txt` if you want the dev install to stay minimal — but installing it in dev is harmless and lets you test against Postgres locally.

> **RECOMMENDATION:** **`psycopg[binary]==3.3.4`** (psycopg 3.x). The `cp311 / win_amd64` wheel is confirmed published, so installation is compiler-free on this exact platform. Add `psycopg[pool]` only if you deploy without an external pooler. **License caveat: psycopg is LGPL-3.0-only, not BSD/MIT.** For a normal deployment — pip-installed into a venv, never statically linked, never redistributed as a modified copy — LGPL imposes no obligation on your application's own source. See §16 for the full analysis before you ship a bundled installer.

```txt
psycopg[binary]==3.3.4            # LGPL-3.0-only  ⚠️ see §16
```

---

## 5. Background tasks — Celery on Windows is a dead end

### Verified fact — Celery's own FAQ

The official Celery documentation, answering "Does Celery support Windows?", states:

> "No. Since Celery 4.x, Windows is no longer supported due to lack of resources."

It adds that it "may still work and we are happy to accept patches." That is not a foundation for a production system. In practice Celery 5 on Windows requires `--pool=solo` (single task at a time, no concurrency, defeating the point) or `--pool=threads`, plus `FORKED_BY_MULTIPROCESSING=1` hacks, and the prefork pool — the default and the only well-tested one — cannot work because Windows has no `fork()`.

### The alternatives, verified

| Package | Latest | License | Windows verdict |
|---|---|---|---|
| celery | 5.x | BSD-3-Clause | **Officially unsupported since 4.x** |
| django-q2 | 1.11.0 (2026-08-14) | MIT | **Cluster cannot run — no fork()** |
| huey | **3.3.4** (2026-08-05) | **MIT** (Charles Leifer) | **Thread worker works** |

**django-q2** looked promising (MIT, Django 5.2+, ORM broker so no Redis needed) but its own install docs are explicit: the cluster relies on OS forking, which Windows lacks, and Windows users "should however be able to develop and test without the cluster by setting the `sync` option to `True`" — i.e. tasks run inline, synchronously. That is a dev workaround, not a worker. Its PyPI classifiers list only MacOS and POSIX. Rejected.

**huey** is the answer, and it is uniquely well-suited to the "degrade gracefully without Redis" requirement:

1. **Worker model:** huey's consumer defaults to `-k thread` (OS threads). Only `-k process` is unavailable on Windows (docs confirm: Windows only supports `spawn`, and huey's state is not picklable). The **default worker type works on Windows** — no flags, no hacks. This is the decisive difference from Celery.
2. **Storage backends without Redis:** huey ships `RedisHuey`, **`SqliteHuey`**, `PostgresHuey`, `FileHuey`, plus priority/expiry variants. `SqliteHuey` needs nothing but stdlib `sqlite3`.
3. **Immediate mode:** when `settings.DEBUG = True`, huey's Django integration runs tasks **synchronously inline** by default, explicitly "to avoid running both Redis and an additional consumer process while developing or testing." Zero infrastructure in dev.

### The graceful-degradation ladder

This gives you a three-rung fallback selected in `settings.py`, with **identical task code** at every rung:

| Rung | Condition | Backend | Consumer |
|---|---|---|---|
| 1 | `DEBUG=True` (dev) | `immediate=True` | none needed |
| 2 | Redis absent (prod-lite / single box) | `SqliteHuey` | `manage.py run_huey -k thread` |
| 3 | Redis present (scaled) | `RedisHuey` | `manage.py run_huey -k thread -w 4` |

```python
# settings.py — degrade gracefully, no code changes in tasks
REDIS_URL = env("REDIS_URL", default=None)
if DEBUG:
    HUEY = {"huey_class": "huey.SqliteHuey", "immediate": True,
            "filename": BASE_DIR / "huey.sqlite3"}
elif REDIS_URL:
    HUEY = {"huey_class": "huey.RedisHuey", "url": REDIS_URL,
            "consumer": {"workers": 4, "worker_type": "thread"}}
else:
    HUEY = {"huey_class": "huey.SqliteHuey",
            "filename": BASE_DIR / "huey.sqlite3",
            "consumer": {"workers": 2, "worker_type": "thread"}}
```

`redis` (redis-py **8.1.0, MIT**) stays an optional install — the app must never import it unconditionally.

> **RECOMMENDATION:** **Reject Celery outright** — its own FAQ has declared Windows unsupported since 4.x, and the prefork pool cannot exist without `fork()`. **Reject django-q2** — its cluster has the same fork dependency and its docs offer only synchronous `sync=True` on Windows. **Use huey 3.3.4 (MIT)** with the **thread** worker (the default, and the one that works on Windows) and the three-rung ladder above: `immediate=True` in dev, **`SqliteHuey`** when Redis is absent, `RedisHuey` when it is present. Task code is byte-identical across all three, so Redis becomes a pure performance upgrade rather than a hard dependency. Install `redis` only in the prod extras.

```txt
huey==3.3.4                       # MIT
redis==8.1.0                      # MIT   (OPTIONAL — prod only, app must degrade without it)
```

---

## 6. PDF generation — WeasyPrint is disqualified on Windows

### WeasyPrint — VERIFIED, and the answer is no

WeasyPrint **69.0** (BSD, `Requires-Python >=3.10`) declares itself `Operating System :: OS Independent` on PyPI, which is misleading. Its official installation guide is explicit about Windows:

> "When Python is installed, you have to install Pango and its dependencies. The easiest way to install these libraries is to use MSYS2."

The documented Windows procedure is: install MSYS2 → run `pacman -S mingw-w64-x86_64-pango` → *then* pip install WeasyPrint. It binds to Pango/libgobject/cairo through `cffi` at **runtime**, so `pip install weasyprint` appears to succeed and then fails at import with `OSError: cannot load library 'libgobject-2.0-0'`.

**This violates the "pip install alone" requirement. WeasyPrint is disqualified.** (It is a genuinely excellent renderer — on Linux/Docker it would be the top pick. That option is off the table here.)

### The candidates, verified

| Package | Latest | License | Windows install | Verdict |
|---|---|---|---|---|
| WeasyPrint | 69.0 | BSD | **Needs MSYS2 + Pango** | **DISQUALIFIED** |
| **ReportLab** | **5.0.0** (2026-06-18) | **BSD** | **`py3-none-any` wheel** | **WINNER** |
| xhtml2pdf | 0.2.17 (2025-02-24) | Apache-2.0 | pip-only, but see below | Rejected |
| fpdf2 | 2.8.8 | **LGPL-3.0-only** ⚠️ | pip-only | Rejected |

### ReportLab 5.0.0 — the situation changed in your favour

Historically ReportLab was a partial no on Windows because of its `_rl_accel` and `_renderPM` C extensions. **That is no longer true.** Verified on PyPI: ReportLab **5.0.0** publishes exactly two artifacts —

- `reportlab-5.0.0.tar.gz`
- **`reportlab-5.0.0-py3-none-any.whl` (2.0 MB, uploaded 2026-06-18)**

A `py3-none-any` wheel is **pure Python by definition**. There are no `cp311`/`win_amd64` builds because none are needed. ReportLab's 5.0 release notes confirm the direction: the `_renderPM` and `_rl_renderPM` C extensions are discontinued, replaced years ago by optional PyCairo, and acceleration is now an opt-in `accel` extra. Do **not** install the `accel`, `pycairo`, `bidi`, or `shaping` extras — the bare install is what stays compiler-free.

**License: BSD** — metadata reads *"BSD license (see license.txt for details), Copyright (c) 2000-2025, ReportLab Inc."*, classified `OSI Approved :: BSD License`. **Confirmed BSD for the open-source toolkit** (ReportLab Plus is the separate paid product; you do not need it).

Capability check against your requirements: ReportLab's Platypus layer provides `Table` with `TableStyle` (banded rows, spans, repeating headers across pages) and `Image` flowables — so tables are first-class and charts drop in as PNGs (see §8). Page templates give you headers/footers and "Page N of M".

### Why not the others

- **xhtml2pdf 0.2.17** — fatal and specific: it pins **`reportlab<5,>=4.0.4`**, so it is **hard-incompatible with ReportLab 5.0.0** and would drag you back to the 4.x line. It is also 18 months stale (Feb 2025) and pulls a heavy chain — `pyHanko`, `pyhanko-certvalidator`, `svglib`, `python-bidi` (Rust-backed), `arabic-reshaper`. Far more surface area for one convenience.
- **fpdf2 2.8.8** — **LGPL-3.0-only** ⚠️. Pure Python and pip-friendly, but copyleft, and its table/layout engine is weaker than Platypus for multi-page reports with repeating headers.

> **RECOMMENDATION:** **ReportLab 5.0.0 (BSD)**, bare install with no extras. WeasyPrint is verified to require MSYS2 + Pango on Windows and is disqualified. ReportLab 5.0.0 now ships a **pure-Python `py3-none-any` wheel** — the historical C-extension objection is gone — so `pip install reportlab==5.0.0` works on a bare Windows 11 box with no compiler. Use **Platypus** (`SimpleDocTemplate` + `Table`/`TableStyle` + `Image`) for reports with banded tables, repeating headers and embedded chart PNGs. Avoid **xhtml2pdf** (pins `reportlab<5`, directly conflicting with this choice, and 18 months stale) and **fpdf2** (LGPL-3.0-only, weaker tables).

```txt
reportlab==5.0.0                  # BSD   (bare install — do NOT add [accel]/[pycairo])
```

---

## 7. Excel export

### Verified facts

| Package | Latest | Release | License | Deps | Read | Write | Charts |
|---|---|---|---|---|---|---|---|
| XlsxWriter | **3.2.9** | current | **BSD-2-Clause** | **none** | No | Yes | **Yes, extensive** |
| openpyxl | **3.1.5** | 2024-06-28 | MIT | `et-xmlfile` | Yes | Yes | Basic |

These are complements, not competitors:

- **XlsxWriter** is write-only, has **zero dependencies** (stdlib only — nothing can break on Windows), and has by far the richer feature set for *generating* files: native charts, conditional formatting, data validation, autofilters, frozen panes, sparklines.
- **openpyxl** is the only one of the two that can **read** `.xlsx`, which you need for importing customer or booking spreadsheets. Its chart support is comparatively basic. Note 3.1.5 dates from June 2024 — stable and unproblematic, but not actively evolving.

> **RECOMMENDATION:** Install **both**, with a clear division of labour. **XlsxWriter 3.2.9 (BSD-2-Clause, zero dependencies)** for all *generated* exports — revenue reports, lesson rosters, instructor schedules — because its native chart and conditional-formatting support means the Excel file contains real, editable Excel charts rather than pasted images. **openpyxl 3.1.5 (MIT)** solely for *reading* uploaded spreadsheets on import. Do not use openpyxl for generation; do not attempt to read with XlsxWriter.

```txt
XlsxWriter==3.2.9                 # BSD-2-Clause  (generation + native charts)
openpyxl==3.1.5                   # MIT           (reading imports only)
```

---

## 8. Charts — server-side for PDF, client-side for the browser

Two genuinely different problems; use a different tool for each.

### Server-side (charts embedded in PDF/Excel)

**matplotlib 3.11.1** (2026-07-18):
- License: **matplotlib/PSF-based license — OSI-approved, BSD-compatible, permissive.** Not copyleft.
- **`matplotlib-3.11.1-cp311-cp311-win_amd64.whl` (9.3 MB) — CONFIRMED on PyPI.**
- `Requires-Python >=3.11` — 3.11 is exactly the floor, so you are fine, but note there is no headroom below.

Render with the **`Agg`** backend (`matplotlib.use("Agg")` before pyplot import) — headless, no GUI toolkit, no Tk. Save to `io.BytesIO()` as PNG at `dpi=150` and hand it straight to ReportLab's `Image` flowable. Never let matplotlib touch a GUI backend inside a web process.

Its ~40 MB footprint (it pulls NumPy, also wheeled for cp311/win_amd64) is real but justified when you need publication-quality static images.

### Client-side (dashboard in the browser)

| Library | Latest | License | Verdict |
|---|---|---|---|
| **Chart.js** | **4.5.1** | **MIT** | **RECOMMENDED** |
| ApexCharts | current | **Custom dual-license** ⚠️ | **AVOID** |

**ApexCharts is no longer MIT.** Verified against the repository LICENSE file: it is now a **custom tiered license**:
- Community (free): personal/educational/non-profit, or commercial use by organisations under **$2M USD annual revenue**
- Commercial (paid): required at **$2M+ annual revenue**
- **OEM/Redistribution (paid): required when "embedding ApexCharts into a product or platform used by other people"**

That third clause is the killer for this project. A **surf school management system deployed for schools** is plausibly redistribution/OEM — and even if you stay under the revenue threshold today, you would be building a licence tripwire into the product that fires on commercial success. The license also forbids "use in competing charting products."

**Chart.js 4.5.1 is plain MIT**, no thresholds, no OEM clause, ~200 KB minified, and vendors cleanly as a single `chart.umd.js` file with zero build step and no CDN.

> **RECOMMENDATION:** Use **both, for different targets.** **matplotlib 3.11.1** with the **`Agg`** backend for server-side PNGs embedded into ReportLab PDFs — the `cp311/win_amd64` wheel is confirmed, and it is permissively licensed. **Chart.js 4.5.1 (MIT)**, vendored locally as `chart.umd.js`, for interactive browser dashboards. **Avoid ApexCharts** — verified to have moved to a custom dual license with a **$2M revenue threshold and a paid OEM tier for embedding in a product used by other people**, which is precisely this project's likely distribution model. Do not let a chart library dictate your business model.

```txt
matplotlib==3.11.1                # matplotlib (PSF-based, BSD-compatible)
# Chart.js 4.5.1 (MIT) — vendored JS, see §12
```

---

## 9. QR codes and barcodes

### Verified facts

| Package | Latest | Release | License | Deps |
|---|---|---|---|---|
| qrcode | **8.2** | 2025-05-01 | BSD | `colorama` (Windows only); `pillow` / `pypng` optional |
| python-barcode | **0.16.1** | 2025-08-27 | MIT | `Pillow` optional (SVG needs nothing) |
| Pillow | **12.3.0** | 2026-07-01 | **MIT-CMU** | — |

**Pillow Windows wheels CONFIRMED:** `pillow-12.3.0-cp311-cp311-win_amd64.whl` (7.2 MB) is published, alongside cp310/cp312 and win32 variants. Pillow is the one package here that genuinely *is* a large C extension, and it is also the one with the most reliable wheel coverage in the entire Python ecosystem. No compiler needed.

Both `qrcode` and `python-barcode` are pure Python and treat Pillow as optional — they emit SVG with no dependencies at all. Since you need PNGs for PDF embedding, install Pillow explicitly (ReportLab and matplotlib effectively want it present anyway).

Practical fit: QR codes for digital lesson passes / student check-in scanned at the beach; Code128 barcodes for equipment asset tags (surfboards, wetsuits) if you use a laser scanner.

> **RECOMMENDATION:** **qrcode 8.2 (BSD)** + **python-barcode 0.16.1 (MIT)** + **Pillow 12.3.0 (MIT-CMU)**. All three install from wheels on Windows 11 / Python 3.11 with the cp311 win_amd64 Pillow wheel confirmed on PyPI. Generate PNG for PDF/Excel embedding and SVG for crisp on-screen rendering. Use QR for student check-in passes and Code128 for equipment asset tags.

```txt
qrcode[pil]==8.2                  # BSD
python-barcode==0.16.1            # MIT
Pillow==12.3.0                    # MIT-CMU
```

---

## 10. Tailwind CSS on Windows without npm pain

### Verified facts

| Option | Latest | License | Node needed? | Offline? |
|---|---|---|---|---|
| Tailwind standalone CLI | **v4.3.3** (2026-07-16) | MIT | **No** | **Yes, once vendored** |
| django-tailwind | 4.5.0 (2026-06-12) | MIT | No (standalone mode) | **No — downloads at runtime** |
| pytailwindcss | 0.3.1 (2026-06-12) | MIT | No | **No — downloads at runtime** |

**Standalone binary confirmed:** the Tailwind CSS **v4.3.3** GitHub release publishes **`tailwindcss-windows-x64.exe` (~112.5 MB)** as a release asset. Self-contained, no Node, no npm, no `node_modules`.

**The offline trap in the Python wrappers:** `django-tailwind 4.5.0` depends on `pytailwindcss>=0.3.0`, whose documented behaviour is that "the binary will be downloaded automatically on the first run of the `tailwindcss` command." There is a `tailwindcss_install` pre-download command and a `TAILWINDCSS_VERSION` env var, but **no documented env var to point at an already-downloaded binary.** For a project that "must run offline", a first-run GitHub download is a hard failure — on a locked-down beach-office machine it will simply hang or 403.

### Tailwind v4 vs v3 — what actually changed

This matters because most tutorials you will find are still v3:

| | v3 | v4 |
|---|---|---|
| Config | `tailwind.config.js` (JS) | **CSS-first: `@theme { }` inside your CSS** |
| Import | `@tailwind base/components/utilities` | **`@import "tailwindcss";`** |
| Content paths | `content: [...]` in config | **Automatic source detection** (`@source` to override) |
| CLI in npm pkg | Yes | **Removed** — hence standalone CLI is now the *primary* Node-free route |
| PostCSS | Required | Not required with CLI |
| Engine | JS | Rust (Oxide) — much faster |

v4's removal of the CLI from the main npm package is precisely why the standalone binary is now first-class rather than a curiosity.

### The robust offline setup

Commit the binary to the repo (or an artifacts store) and drive it with a two-line script — no Python wrapper, no download, no `node_modules`:

```
D:\Surf_School\
  tools\tailwindcss.exe            <- vendored v4.3.3 windows-x64, committed
  theme\src\input.css              <- @import "tailwindcss"; @theme { ... }
  static\css\site.css              <- build output, ALSO committed
```

```bat
:: build_css.bat
tools\tailwindcss.exe -i theme\src\input.css -o static\css\site.css --minify
:: dev: append --watch
```

Committing the **built** `site.css` too is the belt-and-braces move: a fresh clone runs `manage.py runserver` and renders correctly having never executed the binary at all. That is what "must run offline" really demands.

The 112 MB binary is the honest cost. If your Git host objects, keep it out of Git and place it via your provisioning step — but the *built CSS* stays committed either way.

> **RECOMMENDATION:** **Vendor the standalone Tailwind CSS v4.3.3 `tailwindcss-windows-x64.exe` directly in the repo** under `tools\`, driven by a `build_css.bat` one-liner. **Reject django-tailwind/pytailwindcss** — despite being MIT and Node-free, both download the binary from GitHub on first run with no documented way to point them at a pre-placed one, which breaks the offline requirement. **Also commit the built `static/css/site.css`**, so a fresh clone renders correctly without ever running the binary. Write the theme in **v4 CSS-first style** (`@import "tailwindcss";` + `@theme { }`) and ignore v3-era `tailwind.config.js` tutorials.

*No requirements.txt line — this is a vendored binary, deliberately not a Python dependency.*

---

## 11. HTMX + Alpine.js — vendored, no CDN

### Verified facts

| Library | Latest stable | License | Note |
|---|---|---|---|
| **htmx** | **2.0.10** (npm `latest` tag) | **0BSD** (BSD Zero Clause) | 2.x declared feature-complete |
| **Alpine.js** | **3.16.1** | **MIT** | Actively released |

**Do not ship htmx 4.x.** The most recent GitHub *release* is **`v4.0.0-beta6` (2026-07-23)** — a **prerelease**. htmx 4.0 swaps XHR for the Fetch API, which is a behavioural break. The npm `latest` tag still resolves to **2.0.10**, and the 2.x line is declared feature-complete and maintained indefinitely, so there is no upgrade pressure. Beta code does not go into a production booking system.

**htmx is 0BSD** — the "Zero Clause BSD" licence, i.e. public-domain-equivalent with no attribution requirement. The most permissive licence in the entire stack.

Vendor both as plain files, no build step:

```
static\vendor\htmx\2.0.10\htmx.min.js
static\vendor\alpinejs\3.16.1\alpine.min.js
```

Load order matters: **htmx first, Alpine second**, and Alpine must be `defer`. Add `htmx.process(el)` on `htmx:afterSwap` if you swap in Alpine-bearing fragments, or Alpine will not initialise the new nodes.

Add **django-htmx 1.29.0 (MIT, 2026-08-05, supports Django 5.2/6.0/6.1)** on the Python side — it gives you `request.htmx` for branching on partial vs full renders, which keeps views clean.

> **RECOMMENDATION:** Vendor **htmx 2.0.10 (0BSD)** and **Alpine.js 3.16.1 (MIT)** as local static files under `static/vendor/<lib>/<version>/`, version-pinned in the path so upgrades are explicit and cache-busting is free. **Explicitly avoid htmx 4.0.0-beta6** — the newest GitHub release is a prerelease that swaps XHR for Fetch; the 2.x line is feature-complete and maintained indefinitely. Load htmx before Alpine, with Alpine deferred. Add **django-htmx 1.29.0 (MIT)** for `request.htmx`.

```txt
django-htmx==1.29.0               # MIT
# htmx 2.0.10 (0BSD) + Alpine.js 3.16.1 (MIT) — vendored static files
```

---

## 12. Vendored front-end assets (complete manifest)

No CDN references anywhere in the templates. Full offline operation.

| Asset | Version | License | Vendored path |
|---|---|---|---|
| htmx | 2.0.10 | 0BSD | `static/vendor/htmx/2.0.10/htmx.min.js` |
| Alpine.js | 3.16.1 | MIT | `static/vendor/alpinejs/3.16.1/alpine.min.js` |
| Chart.js | 4.5.1 | MIT | `static/vendor/chartjs/4.5.1/chart.umd.js` |
| Tailwind output CSS | built w/ v4.3.3 | MIT | `static/css/site.css` |
| Tailwind CLI binary | v4.3.3 | MIT | `tools/tailwindcss.exe` |

All five are permissively licensed (0BSD/MIT) — no attribution page is strictly required, though including one is good practice.

> **RECOMMENDATION:** Commit all five assets to the repository with the version embedded in the path. Add a CI/lint check that greps templates for `cdn.`, `unpkg`, `jsdelivr` and fails the build — offline-correctness silently rots the moment someone pastes a CDN `<script>` tag during a hurried fix.

---

## 13. Testing

### Verified facts

| Package | Latest | Release | License | Requires-Python |
|---|---|---|---|---|
| pytest | **9.1.1** | current | MIT | >=3.10 |
| pytest-django | **4.14.0** | 2026-08-10 | BSD-3-Clause | >=3.10 |
| pytest-cov | **7.1.0** | 2026-03-21 | MIT | >=3.9 |
| factory-boy | **3.3.3** | 2025-02-03 | MIT | >=3.8 |
| Faker | **40.36.0** | current | MIT | >=3.10 |

Two compatibility notes worth pinning for:

- **pytest-django 4.14.0 supports Django 5.2 and 6.0** and states that for older Django you must use earlier releases. Django 5.2 is at the *bottom* of its support window — correct for us, but it means a future pytest-django release could drop 5.2 while you are still on the LTS. Pinning protects you.
- **pytest-cov 7.1.0** requires `coverage[toml]>=7.10.6` and `pytest>=7` — compatible with pytest 9.1.1.
- **factory-boy 3.3.3** (Feb 2025) depends on `Faker>=0.7.0` with no upper bound, so Faker 40.x resolves fine. Pin Faker anyway: it is on a fast CalVer-ish cadence and locale data shifts between releases, which makes unpinned test data non-reproducible.

All five are pure Python. Zero Windows risk.

> **RECOMMENDATION:** **pytest 9.1.1 + pytest-django 4.14.0 + pytest-cov 7.1.0 + factory-boy 3.3.3 + Faker 40.36.0**, all pure Python and Windows-clean. Pin **Faker** strictly — unpinned, its rapid release cadence makes generated test data irreproducible across machines. Use `--reuse-db` in `pytest.ini` for fast local runs on SQLite, and set `FAKER_SEED` in `conftest.py` for deterministic factories.

```txt
pytest==9.1.1                     # MIT
pytest-django==4.14.0             # BSD-3-Clause
pytest-cov==7.1.0                 # MIT
factory-boy==3.3.3                # MIT
Faker==40.36.0                    # MIT
```

---

## 14. Security and linting

### Verified facts

| Package | Latest | Release | License | Requires-Python |
|---|---|---|---|---|
| ruff | **0.16.3** | current | MIT | >=3.7 |
| bandit | **1.9.4** | 2026-02-25 | Apache-2.0 | >=3.10 |
| pip-audit | **2.10.1** | 2026-06-10 | Apache-2.0 | >=3.10 |

- **ruff 0.16.3** — written in Rust but distributed as prebuilt platform wheels; nothing compiles on install. Replaces flake8 + isort + pyupgrade + black in one tool. Enable the `DJ` (flake8-django) and `S` (bandit-equivalent) rule sets.
- **bandit 1.9.4** — pulls `PyYAML`, `stevedore`, `rich`, and `colorama` on Windows; all pure Python or wheeled. Note ruff's `S` rules cover much of bandit already; keep bandit as the dedicated security gate since its Django-specific checks and SARIF output are useful in CI.
- **pip-audit 2.10.1** — Apache-2.0, from PyPA. Queries the PyPI advisory database. **Requires network access**, so it belongs in CI, not in an offline runtime path.

> **RECOMMENDATION:** **ruff 0.16.3 (MIT)** as the single lint + format tool with the `DJ` and `S` rule sets enabled — it needs no compiler despite being Rust, since Astral ships platform wheels. **bandit 1.9.4 (Apache-2.0)** as a dedicated pre-commit security gate. **pip-audit 2.10.1 (Apache-2.0)** in **CI only** — it needs network access to query the advisory DB, so it must never sit on an offline startup path. All three are dev-only; keep them out of `requirements.txt` and in `requirements-dev.txt`.

```txt
ruff==0.16.3                      # MIT
bandit==1.9.4                     # Apache-2.0
pip-audit==2.10.1                 # Apache-2.0   (CI only — needs network)
```

---

## 15. i18n — the GNU gettext question on Windows

### VERIFIED: yes, `compilemessages` requires the GNU gettext binaries

This is the one genuine Windows gap in the whole stack. Django's `makemessages` and `compilemessages` are **thin wrappers that shell out to the GNU gettext executables** (`xgettext`, `msgfmt`, `msgmerge`). Windows does not ship them. Without them you get:

```
CommandError: Can't find msgfmt. Make sure you have GNU gettext tools 0.15
or newer installed.
```

Note this is a **build-time** dependency only. **Django's *runtime* translation machinery is pure Python** — it reads compiled `.mo` files via the stdlib `gettext` module. So the binaries are needed only when you *change* translations, never when you *run* the app.

That distinction is the whole basis of the fallback plan.

### Options, in order of preference

**A. Install the gettext binaries (best, if you can).** Any of:
- `choco install gettext` (Chocolatey — adds to PATH automatically)
- `winget install gettext`
- GnuWin32 installer from gnuwin32.sourceforge.net (manual PATH edit)
- MSYS2 `pacman -S mingw-w64-x86_64-gettext`

These are **standalone `.exe` tools on PATH — not a compiler toolchain and not GTK.** Installing them does not violate the "no C compiler / no GTK" constraint; it is just a couple of small binaries. You can even vendor `msgfmt.exe`/`xgettext.exe` into `tools\gettext\` and prepend that to PATH in your build script — same offline pattern as the Tailwind binary.

**B. Pure-Python fallback: `python-gettext`.**
- **python-gettext 5.0**, **BSD**, `Requires-Python >=3.7`, **no dependencies**.
- Confirmed from its description: *"This implementation of Gettext for Python includes a Msgfmt class which can be used to generate compiled mo files from Gettext po files and includes support for the newer msgctxt keyword."*
- Caveat: **last released 2023-03-30.** It replaces `msgfmt` (`.po` → `.mo`) only — it does **not** replace `xgettext`, so it cannot *extract* strings, only compile them.

Wire it in as a management command that overrides `compilemessages`:

```python
# core/management/commands/compilemessages.py
import pathlib
from django.core.management.base import BaseCommand
from pythongettext.msgfmt import Msgfmt

class Command(BaseCommand):
    help = "Compile .po -> .mo in pure Python (no GNU gettext required)."

    def handle(self, *args, **opts):
        for po in pathlib.Path("locale").rglob("*.po"):
            mo = po.with_suffix(".mo")
            with open(mo, "wb") as fh:
                fh.write(Msgfmt(str(po)).get().read())
            self.stdout.write(f"compiled {po} -> {mo}")
```

**C. Commit the `.mo` files.** Normally `.mo` is a build artifact you would gitignore. Here, committing it means a fresh clone runs translated with **no gettext tooling of any kind** — the same reasoning as committing the built Tailwind CSS. Given the surf school likely needs at least English plus one local language, and translations change rarely, this is cheap insurance.

> **RECOMMENDATION:** **Layer all three.** Primary: install GNU gettext via **`choco install gettext`** (or vendor `msgfmt.exe`/`xgettext.exe` into `tools\gettext\` and prepend to PATH) — these are small standalone executables, not a compiler or GTK, so they do not breach the platform constraint, and they are the only route to `makemessages` string *extraction*. Fallback for machines without them: **python-gettext 5.0 (BSD, pure Python, zero deps)** wired into a custom `compilemessages` command as shown, which covers `.po` → `.mo` compilation but **not** extraction. Safety net: **commit the compiled `.mo` files to the repo** so a fresh clone runs fully translated with no gettext tooling present at all. Remember the runtime is pure Python — this dependency only ever bites when translations *change*.

```txt
python-gettext==5.0               # BSD   (fallback compilemessages — dev/build only)
```

---

## 16. License summary — copyleft flags

Full audit of every recommended package. The task asked specifically for GPL/AGPL flags; I have included **LGPL** too, since it is copyleft and two packages carry it.

### ⚠️ Copyleft — attention required

| Package | License | Class | Assessment |
|---|---|---|---|
| **psycopg[binary] 3.3.4** | **LGPL-3.0-only** | Weak copyleft | **RECOMMENDED — acceptable, see below** |
| **fpdf2 2.8.8** | **LGPL-3.0-only** | Weak copyleft | **NOT RECOMMENDED** (rejected in §6 anyway) |

**No GPL-3.0 or AGPL packages appear anywhere in the recommended set.** That is the important headline: AGPL in particular would be a genuine problem for a hosted SaaS surf-school product, and there is none.

**On psycopg's LGPL-3.0-only:** LGPL is *weak* copyleft. Its obligations attach to the **library**, not to your application, provided you:
- **Do** dynamically link / import it normally (a pip install into a venv is exactly this),
- **Do not** modify psycopg's own source and redistribute it without publishing those changes,
- **Do** allow an end user who receives your distributed software to substitute their own psycopg build.

For a **web application deployed on your own servers**, nothing is distributed to end users at all, so LGPL obligations are effectively inert. If you later ship an **on-premise bundled installer** (PyInstaller one-file, MSI) to individual surf schools, that *is* distribution — you would then need to keep psycopg as a replaceable dynamic component and include its licence text. **Flagging it now so it is a known decision, not a discovery.**

*(If LGPL is unacceptable to your legal position under any circumstances, the escape hatch is `pg8000` — a pure-Python, BSD-licensed PostgreSQL driver. It is slower and less battle-tested with Django. I do not recommend switching pre-emptively.)*

### Permissive — no obligations beyond attribution

| License | Packages |
|---|---|
| **0BSD** | htmx (no attribution required at all) |
| **BSD-2/3-Clause / BSD** | Django, DRF, django-filter, drf-spectacular, ReportLab, XlsxWriter, qrcode, pytest-django, python-gettext |
| **MIT / MIT-CMU** | django-allauth, simplejwt, django-environ, huey, redis, openpyxl, Pillow, python-barcode, django-htmx, pytest, pytest-cov, factory-boy, Faker, ruff, whitenoise, Chart.js, Alpine.js, Tailwind CSS |
| **Apache-2.0** | bandit, pip-audit (dev-only) |
| **PSF-based** | matplotlib (BSD-compatible) |
| **ZPL-2.1** | waitress (OSI-approved permissive, not copyleft) |

### ⚠️ Non-OSS licence avoided

**ApexCharts** — custom tiered license: free under $2M revenue, **paid commercial at $2M+**, **paid OEM tier for embedding in a product used by other people**, and prohibited "in competing charting products." Rejected in §8 in favour of MIT Chart.js. Flagged here because it is widely and incorrectly documented online as MIT.

> **RECOMMENDATION:** The stack is **clean of GPL and AGPL entirely**. Accept **psycopg's LGPL-3.0-only** — for a server-deployed web app it creates no practical obligation, and it is the best-supported PostgreSQL driver with confirmed cp311 Windows wheels. Revisit that decision only if you later distribute a bundled on-premise installer. Avoid **fpdf2** (LGPL, and unnecessary given ReportLab) and **ApexCharts** (non-OSS despite widespread claims to the contrary).

---

## 17. Final requirements files

### `requirements.txt` (production runtime)

```txt
# ============================================================
# Smart Surf School Management System - runtime dependencies
# Python 3.11 / Windows 11 native - all wheels, no compiler
# Verified 2026-08-15
# ============================================================

# --- Core framework (Django 5.2 LTS: Django 6.x requires Python >=3.12) ---
Django==5.2.17                    # BSD-3-Clause
djangorestframework==3.17.2       # BSD-3-Clause  (NOT 3.18 - drf-spectacular caps at 3.17)
django-filter==26.1               # BSD-3-Clause
drf-spectacular==0.30.0           # BSD-3-Clause

# --- Auth ---
django-allauth==65.19.1           # MIT  (base only - do NOT add [saml]: native xmlsec)
djangorestframework-simplejwt==5.5.1  # MIT

# --- Config ---
django-environ==0.14.0            # MIT

# --- Front-end integration ---
django-htmx==1.29.0               # MIT

# --- Background tasks (Celery is unsupported on Windows) ---
huey==3.3.4                       # MIT  (SqliteHuey fallback + thread worker)

# --- Documents / reporting ---
reportlab==5.0.0                  # BSD      (pure-python py3-none-any wheel)
XlsxWriter==3.2.9                 # BSD-2-Clause  (generation + native charts)
openpyxl==3.1.5                   # MIT      (reading imports)
matplotlib==3.11.1                # PSF-based/BSD-compatible  (Agg backend only)
Pillow==12.3.0                    # MIT-CMU  (cp311 win_amd64 wheel confirmed)

# --- Codes ---
qrcode[pil]==8.2                  # BSD
python-barcode==0.16.1            # MIT

# --- Static file serving ---
whitenoise==6.12.0                # MIT
```

### `requirements-prod.txt` (deployment extras)

```txt
-r requirements.txt

psycopg[binary]==3.3.4            # LGPL-3.0-only  (cp311 win_amd64 wheel CONFIRMED)
redis==8.1.0                      # MIT  (OPTIONAL - huey falls back to SqliteHuey)
waitress==3.0.2                   # ZPL-2.1  (gunicorn does NOT run on Windows)
```

### `requirements-dev.txt`

```txt
-r requirements.txt

pytest==9.1.1                     # MIT
pytest-django==4.14.0             # BSD-3-Clause
pytest-cov==7.1.0                 # MIT
factory-boy==3.3.3                # MIT
Faker==40.36.0                    # MIT
ruff==0.16.3                      # MIT
bandit==1.9.4                     # Apache-2.0
pip-audit==2.10.1                 # Apache-2.0  (CI only - requires network)
python-gettext==5.0               # BSD  (fallback compilemessages)
django-debug-toolbar              # BSD-3-Clause  (optional)
```

### Not pip-installed — vendored binaries and assets

| Item | Version | License | Location |
|---|---|---|---|
| Tailwind CSS standalone CLI | v4.3.3 | MIT | `tools/tailwindcss.exe` |
| GNU gettext (optional) | 0.21+ | GPL — **tool only, not linked** | `tools/gettext/` or `choco install gettext` |
| htmx | 2.0.10 | 0BSD | `static/vendor/htmx/2.0.10/` |
| Alpine.js | 3.16.1 | MIT | `static/vendor/alpinejs/3.16.1/` |
| Chart.js | 4.5.1 | MIT | `static/vendor/chartjs/4.5.1/` |

*Note: GNU gettext's own binaries are GPL, but they are **developer tools invoked as separate processes**, never linked into or distributed with your application. This creates no licensing obligation on your code — the same reason using GCC does not GPL your program.*

### Windows deployment note

**`gunicorn` does not run on Windows** (it depends on the `fcntl` module, POSIX-only). Use **waitress 3.0.2** (ZPL-2.1, pure Python, explicitly documented as running "on CPython on Unix and Windows under Python 3.9+", Development Status: Mature) behind IIS/nginx as a reverse proxy, or run it directly for small deployments.

> **RECOMMENDATION:** Split into three files as above. Verify the whole set installs clean on a fresh box before committing with:
> ```
> py -3.11 -m venv .venv
> .venv\Scripts\activate
> python -m pip install --upgrade pip
> pip install --only-binary=:all: -r requirements-dev.txt -r requirements-prod.txt
> ```
> **The `--only-binary=:all:` flag is the real test** — it makes pip *fail loudly* rather than silently attempting a source build. If that command succeeds on a machine with no Visual Studio Build Tools, the entire "no C compiler" requirement is proven rather than assumed. Put this exact command in CI.

---

## 18. Risk register

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | `pip install django` grabs 6.x on a 3.12+ box, breaking parity | Medium | Pin `==5.2.17`; add `requires-python = ">=3.11,<3.12"` to `pyproject.toml` |
| 2 | DRF auto-upgrades to 3.18, breaking spectacular schema gen | **High if unpinned** | Pinned to 3.17.2; spectacular has **no** upper bound to protect you |
| 3 | Someone adds WeasyPrint for "nicer" PDFs | Medium | Documented as disqualified; add to a `FORBIDDEN_DEPS` CI check |
| 4 | Someone adds `xhtml2pdf` | Medium | It pins `reportlab<5` — would silently downgrade ReportLab. CI-block it |
| 5 | Redis assumed present; app crashes without it | Medium | huey three-rung ladder (§5); test the no-Redis path in CI |
| 6 | CDN `<script>` tag pasted into a template | **High over time** | CI grep for `cdn.`/`unpkg`/`jsdelivr` in templates |
| 7 | `compilemessages` fails on a dev box without gettext | High | Committed `.mo` files + python-gettext fallback command |
| 8 | Tailwind binary not present on a fresh clone | Medium | Built `site.css` is committed — app renders regardless |
| 9 | simplejwt unmaintained / no Django 6 support | Low (now) | Reassess before any Django 6 migration; 5.2 LTS runs to Apr 2028 |
| 10 | psycopg LGPL becomes an issue on on-prem distribution | Low | Documented §16; `pg8000` (BSD) is the escape hatch |

---

## Appendix — sources

- [Django on PyPI](https://pypi.org/project/Django/) · [Django Downloads / supported versions](https://www.djangoproject.com/download/) · [Django install FAQ (Python compatibility)](https://docs.djangoproject.com/en/5.2/faq/install/)
- [djangorestframework](https://pypi.org/project/djangorestframework/) · [django-filter](https://pypi.org/project/django-filter/) · [drf-spectacular](https://pypi.org/project/drf-spectacular/)
- [django-allauth](https://pypi.org/project/django-allauth/) · [djangorestframework-simplejwt](https://pypi.org/project/djangorestframework-simplejwt/)
- [django-environ](https://pypi.org/project/django-environ/) · [python-decouple](https://pypi.org/project/python-decouple/) · [pydantic-settings](https://pypi.org/project/pydantic-settings/)
- [psycopg](https://pypi.org/project/psycopg/) · [psycopg-binary files](https://pypi.org/project/psycopg-binary/#files)
- [Celery FAQ — Windows support](https://docs.celeryq.dev/en/stable/faq.html) · [django-q2 install docs](https://django-q2.readthedocs.io/en/master/install.html) · [huey consumer docs](https://huey.readthedocs.io/en/latest/consumer.html) · [huey Django integration](https://huey.readthedocs.io/en/latest/contrib.html) · [huey LICENSE (MIT)](https://github.com/coleifer/huey/blob/master/LICENSE)
- [WeasyPrint first steps — Windows/MSYS2/Pango](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) · [ReportLab files (py3-none-any wheel)](https://pypi.org/project/reportlab/#files) · [ReportLab 5.0 release notes](https://docs.reportlab.com/releases/notes/whats-new-50/) · [xhtml2pdf](https://pypi.org/project/xhtml2pdf/) · [fpdf2](https://pypi.org/project/fpdf2/)
- [XlsxWriter](https://pypi.org/project/XlsxWriter/) · [openpyxl](https://pypi.org/project/openpyxl/)
- [matplotlib files](https://pypi.org/project/matplotlib/#files) · [ApexCharts LICENSE](https://github.com/apexcharts/apexcharts.js/blob/main/LICENSE) · [Chart.js versions (jsDelivr)](https://data.jsdelivr.com/v1/packages/npm/chart.js)
- [qrcode](https://pypi.org/project/qrcode/) · [python-barcode](https://pypi.org/project/python-barcode/) · [Pillow files](https://pypi.org/project/pillow/#files)
- [Tailwind standalone CLI announcement](https://tailwindcss.com/blog/standalone-cli) · [Tailwind latest release (v4.3.3 + windows-x64.exe)](https://github.com/tailwindlabs/tailwindcss/releases) · [django-tailwind](https://pypi.org/project/django-tailwind/) · [pytailwindcss](https://pypi.org/project/pytailwindcss/)
- [htmx versions (jsDelivr)](https://data.jsdelivr.com/v1/packages/npm/htmx.org) · [htmx releases](https://github.com/bigskysoftware/htmx/releases) · [Alpine.js versions (jsDelivr)](https://data.jsdelivr.com/v1/packages/npm/alpinejs) · [django-htmx](https://pypi.org/project/django-htmx/)
- [pytest](https://pypi.org/project/pytest/) · [pytest-django](https://pypi.org/project/pytest-django/) · [pytest-cov](https://pypi.org/project/pytest-cov/) · [factory-boy](https://pypi.org/project/factory-boy/) · [Faker](https://pypi.org/project/faker/)
- [ruff](https://pypi.org/project/ruff/) · [bandit](https://pypi.org/project/bandit/) · [pip-audit](https://pypi.org/project/pip-audit/)
- [python-gettext](https://pypi.org/project/python-gettext) · [Django ticket #25677 (compilemessages/msgfmt)](https://code.djangoproject.com/ticket/25677)
- [whitenoise](https://pypi.org/project/whitenoise/) · [waitress](https://pypi.org/project/waitress/)
