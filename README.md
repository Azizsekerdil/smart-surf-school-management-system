<div align="center">

# 🌊 Smart Surf School Management System

### Akıllı Sörf Okulu Yönetim Sistemi

**Everything a surf school runs on, in one application.**
Lessons · Bookings · Camps · Equipment · Rentals · Safety · Finance · POS · Analytics · AI

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2%20LTS-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![HTMX](https://img.shields.io/badge/HTMX-1.9-3366CC)](https://htmx.org)
[![Tailwind](https://img.shields.io/badge/Tailwind-3.4-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[English](#english) · [Türkçe](#türkçe)

</div>

---

<a name="english"></a>

## English

### What it is

A management system for a working surf school. It handles the people
(customers, students, instructors), the schedule (lessons, bookings, surf camps),
the gear (inventory, rentals, maintenance), the ocean (conditions, safety),
and the business (finance, point of sale, analytics, reporting) — with a local
and cloud AI layer on top that is grounded in the school's own data.

It is built to run on **one Windows machine**, to keep working **without an
internet connection**, and to never lose a booking or a payment.

### What it is **not**

Read this before deciding whether it fits your school.

- **Not multi-tenant.** There is no branch, tenant or organisation model. One
  installation serves one school. Running two branches on one instance would
  show every branch's staff the other's customers.
- **Not a payment processor.** It records payments; it does not take card
  payments. There is no Stripe, iyzico or PayPal integration.
- **No fiscal or e-invoice integration.** Invoices are internal documents. There
  is no e-Fatura / e-Arşiv connector and no fiscal printer support.
- **Not certified for anything.** No KVKK, GDPR, PCI-DSS or ISO claim is made.
  The product models special-category data (medical notes, waivers, minors'
  records) but ships no legal-basis register, retention policy, data-subject
  request flow or erasure workflow. Those are a deployment's responsibility.
- **No mobile app.** It is a responsive web application.
- **No SMS or WhatsApp gateway.** Notifications are in-app and e-mail.
- **The AI is not an authority.** It does not decide safety, pricing, medical or
  legal questions — see [AI_TRANSPARENCY.md](AI_TRANSPARENCY.md).
- **Turkish UI chrome is not translated yet.** See *Languages* below; this is
  the one place where an earlier version of this README overstated things.

### Maturity

**Beta / reference implementation.** It is feature-complete against its own
specification and heavily tested, but it has never run a real season for a real
school. Concretely:

- Developed and tested on **SQLite**. PostgreSQL is the documented production
  target and the code is written for it, but the suite has not been run against
  a live PostgreSQL server.
- **No load testing** and no browser-automation (end-to-end UI) tests.
- **No production deployment exists.** Treat the deployment guide as a plan, not
  as a battle-tested runbook.
- AI providers are exercised by hand; automated tests never touch the network.

### Measured facts

Every number below was counted from *this* source tree and from the test run —
none is copied from an older document.

| | Count | How it was counted |
|---|---:|---|
| Django apps | 28 | `LOCAL_APPS` in `config/settings/base.py` |
| Concrete models | 86 | Django app registry |
| Model fields (incl. relations) | 1 861 | Django app registry |
| Database tables | 97 | distinct `db_table` |
| Migrations | 33 | `apps/*/migrations/0*.py` |
| REST resources | 75 | entries in the auto-discovered DRF router |
| HTML templates | 359 | `templates/**/*.html` |
| Roles | 15 | `Role` in `apps/accounts/constants.py` |
| Capabilities | 203 | `all_capabilities()` |
| Role → capability grants | 717 | `ROLE_CAPABILITIES` |
| Reports (PDF / Excel / CSV) | 20 | `@report(...)` decorators |
| AI tools | 13 | `@register(...)` in `apps/ai/tools.py` |
| Smoke-test screens | 37 signed-in + 2 public | `SCREENS` / `PUBLIC` in `scripts/smoke_test.py` |
| Acceptance steps | 20 | `@step(...)` in `scripts/e2e_scenario.py` |
| Python files / lines | 569 / 131 331 | file walk |
| Test files | 91 | `apps/**/test_*.py` |
| **Tests collected / passing** | **2 130 / 2 128 (+2 skipped)** | `pytest -q` on this tree |
| Tests marked `security` | 90 (45 declarations, some parametrised) | `pytest -m security` |

The two skips are deliberate: they need an AI provider on the network, which the
test settings forbid.

> Coverage percentage is **not** quoted here. Coverage was not measured for this
> release, so any figure would be one you could not check.

### Feature overview

| Area | What you get |
|---|---|
| **Users & roles** | 15 roles, a capability matrix driving menus, views, API and AI tools alike, per-user grants and denials, row-level ownership scoping for customer/student accounts, brute-force protection, full audit trail |
| **CRM** | Customers, surf-specific student profiles, skill assessments, documents, waivers, duplicate detection, merge |
| **Instructors** | Certifications with expiry tracking, weekly availability, time off, performance reviews, commission |
| **Lessons** | Lesson catalogue, capacity and instructor-ratio safety rules, roster check-in, equipment assignment, conditions snapshot |
| **Bookings** | Server-rendered calendar, conflict detection across instructor / student / equipment / level / safety, waitlist, cancellation with fees |
| **Surf camps** | Multi-day programmes, participants, accommodation, transfers, dietary needs, daily activity planner, financials |
| **Equipment** | QR-labelled inventory, board volume and wetsuit matching, condition tracking, utilisation, CSV import |
| **Rentals** | Hourly / daily / weekly pricing, deposits, late fees capped at 3× the hire, damage charges, check-out and check-in screens |
| **Maintenance** | Ding and damage taxonomy, service history, costs, and a **statistical** maintenance-risk forecast (no AI in the score) |
| **Surf conditions** | Wave, swell, wind, tide, temperature and UV from a pluggable provider, plus a **deterministic** 0–100 Surf Score per skill level |
| **Safety** | Incidents, lifeguard rostering, emergency contacts, evacuation plans, equipment checks, warnings, student restrictions |
| **Finance** | Invoices, payments, refunds, expenses, commissions, lesson packages, P&L |
| **Point of sale** | Touch-friendly till with barcode entry, stock ledger, receipts |
| **Analytics** | Mean, median, standard deviation, moving average, trend, correlation, seasonality and forecasting over every metric |
| **Reporting** | 20 reports exportable to PDF, Excel and CSV |
| **Backup** | Manual and scheduled backups with checksums, verification, and a restore flow that takes a safety backup first |
| **AI** | Local (LM Studio) and cloud (NVIDIA, Anthropic) providers, routing, RAG over your own documents, tool-grounded assistant, cost tracking |
| **AI terminal** | A sandboxed development console where an AI agent proposes commands and code changes for a human to approve |
| **Guidance** | Bilingual Help Center, interactive Training Center, first-run onboarding wizard |

### Access control — what is actually enforced

Two layers, both in code and both covered by tests:

1. **Capability check (view level).** Every screen and every API endpoint
   declares a capability such as `finance.view`. The same matrix drives the
   sidebar, so the UI never offers an action the API would refuse.
2. **Ownership scoping (row level).** Customers and students hold `finance.view`,
   `rentals.view`, `lessons.view` and `surf_camps.view` so a self-service portal
   can work at all. `apps/accounts/scoping.py` narrows every one of those
   surfaces to the rows that belong to the requesting person, **before** the
   query runs. Requesting somebody else's row returns 404, not 403.

Two consequences worth stating plainly, because both are guarantees this
project previously claimed without enforcing:

- **A customer or student sees only their own records.** Enforced on invoices,
  payments, package cards, rentals, bookings, waiting-list entries, lesson
  registers and camp places — including, deliberately and specifically, records
  about children. Camp rosters, participant lists, daily run sheets and camp
  finances are closed to external accounts outright, because an operational
  overview has no "own rows" projection.
- **A rental clerk cannot see revenue.** Taking money at a counter needs
  `finance.view`; reading the school's takings, margin and instructor commission
  needs `finance.revenue`, which reception and rental staff do not hold. The
  aggregate query is never run for them.

Both statements are asserted by
[`apps/accounts/tests/test_object_scoping.py`](apps/accounts/tests/test_object_scoping.py).
A structural test in the same file fails the build if a new endpoint under those
modules is added without declaring an ownership policy.

### Requirements

| | Minimum | Notes |
|---|---|---|
| OS | Windows 10/11 | Also runs on Linux/macOS |
| Python | 3.11+ | |
| Node.js | 18+ | Only to rebuild CSS; the compiled CSS is committed |
| Database | SQLite | PostgreSQL 14+ for production |
| Redis | — | Optional; without it Celery runs inline |
| LM Studio | — | Optional; enables free, private, offline AI |

### Install

```powershell
git clone https://github.com/<your-account>/smart-surf-school-management-system.git
cd smart-surf-school-management-system
.\scripts\setup.ps1 -WithDemoData
```

That creates the virtual environment, installs dependencies, generates a
`SECRET_KEY`, builds the front-end assets, applies migrations, synchronises the
role groups, compiles the translations and loads demo data.

Then create the first administrator and start the server:

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_admin
.\scripts\start.ps1
```

Open <http://127.0.0.1:8000/>.

#### First login — `admin` / `admin`, and it must be changed

`manage.py bootstrap_admin` creates a single-use first-run account:

> **admin / admin — must be changed on first login.**

This is a **documented bootstrap credential, not a secret**. It is safe only
because of the rules around it, all of which are enforced in code and pinned by
[`apps/accounts/tests/test_bootstrap_admin.py`](apps/accounts/tests/test_bootstrap_admin.py):

- **Nothing opens until you change it.** While the account carries
  `must_change_password`, every screen redirects to the change-password page and
  every API call returns `403 password_change_required`. That covers the
  dashboard, customer and student records, finance, exports, backups and the AI
  settings — the rule applies to all requests, not to a list of screens somebody
  has to remember to keep updated.
- **It only works from the machine the server runs on.** A sign-in with the
  bootstrap credential from any non-loopback address is refused, including
  through the API token endpoints. `X-Forwarded-For` is ignored, so the check
  cannot be spoofed with a header.
- **It dies the moment you change it.** The flag is cleared permanently, and a
  password validator refuses to set the password back to `admin` afterwards. A
  password reset chooses a new password and cannot restore the default.
- **It is stored as an Argon2id hash**, never in plaintext, and never appears in
  a log line, an audit row, a backup, an export or a screenshot.
- **Failed attempts are rate limited** per IP and per username, with a lockout
  (django-axes; 8 failures, 15-minute cool-off by default).
- `bootstrap_admin` **refuses to run once any user exists**, so it cannot be used
  to push a live system back to a known password.

If you would rather not have a default at all, skip the command and use
`manage.py createsuperuser` instead. Both paths are supported.

<details>
<summary>Manual installation</summary>

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# edit .env — at minimum set DJANGO_SECRET_KEY
npm install; npm run build
python manage.py migrate
python manage.py bootstrap_roles
python manage.py i18n_compile
python manage.py bootstrap_admin      # or: createsuperuser
python manage.py runserver
```
</details>

### Demo data — synthetic only

`-WithDemoData` (and `scripts/e2e_scenario.py`) seed **entirely synthetic**
records: invented names, `example.test` addresses, patterned phone numbers and
made-up equipment. No real person, customer, school, booking or payment appears
anywhere in this repository, its demo seed, its fixtures, its screenshots or its
presentation. Delete the demo data before you enter anything real:

```powershell
python manage.py flush
```

### Key screens

| Path | Screen |
|---|---|
| `/` | Dashboard |
| `/bookings/` | Booking calendar |
| `/equipment/` | Equipment inventory |
| `/surf-conditions/` | Conditions and Surf Score |
| `/finance/` | Finance dashboard (needs `finance.revenue`) |
| `/pos/` | Point of sale |
| `/analytics/` | Analytics |
| `/ai/` | AI assistant |
| `/ai/control-center/` | AI Control Center |
| `/ai-terminal/` | AI Development Terminal |
| `/backups/` | Backup & restore |
| `/api/docs/` | Interactive API documentation |
| `/api/health/` | Health check |

### Environment variables

Copy `.env.example` to `.env`. **Every credential in the template is empty or a
literal placeholder** — there is no real key, token, password or connection
string anywhere in this repository or its history.

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django signing key — required | *(empty)* |
| `DJANGO_DEBUG` | Debug mode. Never `True` on a shared machine | `True` in `.env.example` |
| `DJANGO_ALLOWED_HOSTS` | Hostnames the site answers on | localhost |
| `DATABASE_URL` | SQLite by default, PostgreSQL for production | sqlite |
| `SURF_PROVIDER` | `open-meteo` \| `stormglass` \| `manual` | `open-meteo` |
| `AI_ROUTING_MODE` | `auto` \| `local_only` \| `cloud_only` | `auto` |
| `LM_STUDIO_BASE_URL` | Local OpenAI-compatible server | `http://localhost:1234/v1` |
| `NVIDIA_API_KEY` | Optional cloud provider | *(empty → disabled)* |
| `ANTHROPIC_API_KEY` | Optional cloud provider | *(empty → disabled)* |
| `OPENAI_COMPAT_API_KEY` | Optional generic provider | *(empty → disabled)* |
| `AXES_FAILURE_LIMIT` | Failed logins before lockout | `8` |

`DJANGO_SETTINGS_MODULE` **cannot** be set in `.env` — `.env` is read from inside
the settings module, after Django has already chosen one. Set it in the process
environment. `config/wsgi.py` and `config/asgi.py` refuse to start without it
rather than silently falling back to the development profile.

### AI providers, and the local-only option

The application works with **no AI configured**. Everything below is additive,
and no non-AI feature depends on it.

**Local (free, private, offline) — recommended starting point**

1. Install [LM Studio](https://lmstudio.ai) and download a model.
2. Start its local server (Developer → Start Server, default port 1234).
3. That is all — the default `LM_STUDIO_BASE_URL` already points at it.

Set `AI_ROUTING_MODE=local_only` and **nothing leaves the machine**. A local
embedding model additionally makes the knowledge base fully offline.

**NVIDIA NIM / Anthropic Claude (optional, cloud)**

Put your own key in `.env` (`NVIDIA_API_KEY` / `ANTHROPIC_API_KEY`). With no key
the provider reports `NOT_CONFIGURED` and makes no call at all. The interface
shows the provider name, its status and the **last 4 characters** of the key,
never the key itself; "Test connection" runs only when you press it; and the key
is stripped from every log line, from child processes and from backups.

> No API key is ever written to the database. Keys are read from the environment
> only — see `apps/ai/models.py` and `apps/ai/providers/base.py`.

**Read [AI_TRANSPARENCY.md](AI_TRANSPARENCY.md) before enabling a cloud
provider.** In the default `auto` routing mode, tool-bearing requests prefer the
cloud, and tool results can contain customer names, e-mail addresses and phone
numbers. There is no PII masking in the AI layer today. If that matters to you —
and under KVKK/GDPR it very likely does — use `local_only`.

### Privacy and human approval

- The product stores special-category data by design: medical notes, waivers,
  allergy and dietary information, emergency contacts, and records about
  **minors**. See [PRIVACY.md](PRIVACY.md) for what is stored, what is not, and
  what a deployment still has to build.
- **AI never has the last word on safety.** An AI-suggested safety warning is
  invisible until a named staff member signs it off, and the maintenance-risk
  score is computed statistically with no AI involvement.
- **The AI terminal cannot act alone.** Commands are classified SAFE / REQUIRES
  APPROVAL / BLOCKED against an allowlist; file writes need both a human
  approval and a specific capability; there is no shell.

### Financial, legal and health limits

- Figures are bookkeeping records, **not accounting or tax advice**, and the
  product is not an accounting package. Nothing it produces is a fiscal
  document.
- Waivers, incident reports and consent records are **operational records, not
  legal instruments**. Have a lawyer review anything you rely on.
- Medical and allergy fields are **operational notes for instructors**, not
  clinical records. The product gives no medical advice and makes no medical
  determination. In an emergency, call the emergency services.
- The Surf Score is a deterministic suitability heuristic from published
  thresholds. It is **not** a safety certification and does not replace a
  lifeguard's or an instructor's judgement.

### Testing

```powershell
.\scripts\test.ps1              # tests
.\scripts\test.ps1 -Coverage    # + coverage report
.\scripts\test.ps1 -All         # + lint + bandit + pip-audit
```

or directly:

```powershell
python -m pytest -q               # 2 130 tests
python -m pytest -q -m security   # the access-control assertions only
```

CI runs the suite, `ruff`, `bandit` and `pip-audit` on every push and pull
request — see [.github/workflows/ci.yml](.github/workflows/ci.yml).

### Languages

The Help Center and Training Center **content** is genuinely bilingual
(Turkish and English) — the seeds carry `title_tr` / `body_tr` fields.

The **interface chrome is English**. 337 templates use `{% trans %}`, but no
compiled Turkish catalogue is shipped, so strings fall back to their English
source. Turkish *locale formatting* (dates, numbers, currency) does apply. An
earlier version of this README and the slide deck claimed "menus, notifications,
validation messages and help content are fully translated"; that was not true
and has been corrected rather than quietly dropped.

To supply a catalogue (GNU gettext is **not** required — the project ships pure
Python tooling):

```powershell
python manage.py i18n_extract     # scan the code for translatable strings
# translate locale/tr/LC_MESSAGES/django.po
python manage.py i18n_compile     # write the binary catalogues
```

### Screenshots and presentation

Screenshots live in `assets/screenshots/{en,tr}/` and are captured by
`scripts/capture_screenshots.py` from the synthetic demo seed. The slide deck is
generated from `scripts/presentation_content.py` by
`scripts/generate_presentation.py`.

See `docs/presentation/README.md` for how it is built, and
[docs/known-limitations.md](docs/known-limitations.md) for the current state of
the public deck.

### Backups

```powershell
.\scripts\backup.ps1                      # full backup now
.\scripts\backup.ps1 -Type daily -Verify  # for Task Scheduler
.\scripts\backup.ps1 -ApplyRetention
```

Restoring is deliberately high-friction: the backup is verified, a safety backup
of the current state is taken first, and you must type the backup code to
confirm. Backups record environment variable **names** only — never values.

### Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions and their justification |
| [DATABASE.md](docs/DATABASE.md) | Data model |
| [API.md](docs/API.md) | REST API |
| [AI_ARCHITECTURE.md](docs/AI_ARCHITECTURE.md) | Providers, routing, RAG, grounding |
| [AI_TRANSPARENCY.md](AI_TRANSPARENCY.md) | What the AI does, what leaves the machine, what it may not decide |
| [PRIVACY.md](PRIVACY.md) | What personal data the product handles |
| [SECURITY.md](SECURITY.md) | How to report a vulnerability |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model and controls |
| [docs/known-limitations.md](docs/known-limitations.md) | Known limitations and roadmap |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Third-party components and licences |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute, and the licence rules for code you submit |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community expectations |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) | Backup and disaster recovery |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment |
| [TESTING.md](docs/TESTING.md) | Test strategy |
| [USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md) | User guide (English) |
| [USER_GUIDE_TR.md](docs/USER_GUIDE_TR.md) | Kullanım kılavuzu (Türkçe) |
| [OPEN_SOURCE_LICENSES.md](docs/OPEN_SOURCE_LICENSES.md) | Licence compliance policy |

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `RuntimeError: DJANGO_SETTINGS_MODULE is not set` | Set it in the process environment before starting a WSGI/ASGI server. It cannot be set in `.env` |
| `ModuleNotFoundError: django` | Virtual environment not active — run `.\scripts\setup.ps1` |
| Pages render unstyled | CSS not built — run `npm run build` |
| Every page redirects to "Change password" | The account still carries the first-run flag. Change the password; that is the design |
| Bootstrap login refused | `admin` / `admin` only works from the machine the server runs on |
| "LM Studio is not running" | Start LM Studio's local server, or ignore it — the app works without AI |
| NVIDIA model returns 404 | That model is not enabled for your account. Use **Probe models** to find the ones that are |
| Surf conditions are empty | Coastal coordinates need `cell_selection=sea`; check the spot's latitude/longitude |
| `compilemessages` fails | Use `python manage.py i18n_compile` instead — GNU gettext is not needed |
| Turkish characters wrong in a CSV | Open it as UTF-8; exports include a BOM for Excel |

### Reporting a vulnerability

Please do **not** open a public issue for a security problem. See
[SECURITY.md](SECURITY.md).

### Known limitations and roadmap

[docs/known-limitations.md](docs/known-limitations.md) lists, in one place, what
does not work yet, what is untested, and what is planned.

---

<a name="türkçe"></a>

## Türkçe

### Nedir

Çalışan bir sörf okulunun operasyonunu tek uygulamada yöneten bir sistem:
insanlar (müşteriler, öğrenciler, eğitmenler), program (dersler, rezervasyonlar,
sörf kampları), ekipman (envanter, kiralama, bakım), deniz (koşullar, güvenlik)
ve işletme (finans, POS, analitik, raporlama) — üstünde de okulun kendi verisine
dayanan yerel ve bulut yapay zekâ katmanı.

**Tek bir Windows bilgisayarda** çalışacak, **internet olmadan da** kullanılabilecek
ve hiçbir rezervasyonu veya ödemeyi kaybetmeyecek şekilde tasarlandı.

### Ne değildir

- **Çok kiracılı (multi-tenant) değildir.** Şube/kiracı modeli yoktur; bir kurulum
  bir okula hizmet eder.
- **Ödeme altyapısı değildir.** Ödemeleri kaydeder, kart çekmez.
- **e-Fatura / e-Arşiv entegrasyonu yoktur.** Faturalar iç belgedir, mali belge değildir.
- **Hiçbir uyumluluk sertifikası iddia edilmez** (KVKK, GDPR, PCI-DSS, ISO).
- **Mobil uygulama yoktur**; duyarlı bir web uygulamasıdır.
- **SMS / WhatsApp geçidi yoktur.**
- **Arayüz Türkçe çevirisi henüz derlenmemiştir** — aşağıya bakın.

### Olgunluk

**Beta / referans uygulama.** Kendi şartnamesine göre tamamlanmış ve yoğun test
edilmiştir, ancak gerçek bir okulda gerçek bir sezon çalıştırılmamıştır.
Geliştirme ve testler **SQLite** üzerinde yapılmıştır; PostgreSQL hedeftir ama
canlı bir PostgreSQL sunucusuna karşı test edilmemiştir. Yük testi ve tarayıcı
otomasyon testi yoktur.

### Kurulum

```powershell
git clone https://github.com/<hesabiniz>/smart-surf-school-management-system.git
cd smart-surf-school-management-system
.\scripts\setup.ps1 -WithDemoData
.\.venv\Scripts\python.exe manage.py bootstrap_admin
.\scripts\start.ps1
```

Ardından <http://127.0.0.1:8000/> adresini açın.

#### İlk giriş — `admin` / `admin`, ilk girişte değiştirilmelidir

> **admin / admin — ilk girişte mutlaka değiştirilmelidir.**

Bu belgelenmiş bir ilk kurulum bilgisidir, **gizli bir parola değildir**. Güvenli
olmasının nedeni etrafındaki kurallardır; hepsi kodla zorunlu kılınmış ve
testlerle sabitlenmiştir:

- Parola değiştirilene kadar **hiçbir ekran, hiçbir API açılmaz**;
- yalnızca sunucunun çalıştığı **makineden** (loopback) giriş yapılabilir;
- değiştirildiği anda `admin / admin` **kalıcı olarak geçersiz** olur ve parola
  sıfırlama onu geri getiremez;
- parola **Argon2id ile özetlenerek** saklanır; hiçbir log, denetim kaydı, yedek
  veya ekran görüntüsünde görünmez;
- hatalı denemeler IP ve kullanıcı adı bazında **hız sınırlaması ve kilitleme**
  ile karşılanır.

Varsayılan bir hesap istemiyorsanız bu komutu atlayıp `createsuperuser`
kullanabilirsiniz.

### Demo veri — tamamı sentetiktir

Demo veri seti tamamen uydurma kayıtlardan oluşur (`example.test` adresleri,
desenli telefon numaraları, uydurma isimler). Depoda, demo veride, ekran
görüntülerinde veya sunumda **hiçbir gerçek kişi veya kayıt yoktur**. Gerçek veri
girmeden önce `python manage.py flush` çalıştırın.

### Erişim denetimi — gerçekten uygulanan iki katman

1. **Yetki denetimi (ekran düzeyi):** her ekran ve uç nokta bir yetki bildirir.
2. **Sahiplik daraltması (satır düzeyi):** müşteri ve öğrenci hesapları yalnızca
   kendi kayıtlarını görür. Bu, `apps/accounts/scoping.py` içinde sorgu
   çalışmadan **önce** uygulanır; başkasının kaydı istenirse 404 döner.

Bunun iki somut sonucu — ve her ikisi de testlerle sabitlenmiştir:

- **Müşteri/öğrenci yalnızca kendi kayıtlarını görür**; kamp katılımcı listeleri,
  günlük yoklama çizelgeleri ve kamp finansalları dış hesaplara tamamen kapalıdır
  (bu kayıtlar çoğu zaman **çocuklara** aittir).
- **Kiralama personeli ciro rakamlarını göremez**: tezgâhta tahsilat için
  `finance.view` yeterlidir, ciro/kâr/komisyon için `finance.revenue` gerekir ve
  bu yetki onlarda yoktur — sorgu hiç çalıştırılmaz.

### Öne çıkan özellikler

- **15 rol** ve 203 yetkiden oluşan matris
- **Çakışma kontrollü rezervasyon**: eğitmen, öğrenci, ekipman, seviye ve güvenlik
- **QR etiketli ekipman envanteri**, board hacmi ve wetsuit önerisi
- **Hesaplanan Surf Score**: yapay zekâ tahmini değil, yayımlanmış eşiklere
  dayanan **deterministik bir hesaptır**
- **Güvenlik modülü**: yapay zekâ hiçbir zaman nihai karar merci değildir
- **Finans ve POS**: tüm tutarlar `Decimal`, her ödeme denetim kaydına yazılır
- **20 rapor**, PDF / Excel / CSV
- **Yedekleme ve geri yükleme**: sağlama toplamı doğrulaması ve geri yükleme
  öncesi otomatik güvenlik yedeği
- **Yerel + bulut YZ**, kendi belgeleriniz üzerinde RAG, maliyet takibi

### Yapay zekâ kurulumu

Uygulama **yapay zekâ olmadan da eksiksiz çalışır**.

**Yerel (ücretsiz, gizli, çevrimdışı):** [LM Studio](https://lmstudio.ai) kurun,
bir model indirin ve yerel sunucuyu başlatın. `AI_ROUTING_MODE=local_only`
ayarlarsanız **hiçbir veri makineden çıkmaz**.

**Bulut (NVIDIA / Anthropic):** kendi anahtarınızı `.env` dosyasına ekleyin.
Anahtar yoksa sağlayıcı `NOT_CONFIGURED` durumunda kalır ve hiçbir çağrı yapmaz.
Arayüzde yalnızca sağlayıcı adı, durumu ve anahtarın **son 4 karakteri** görünür.

> **Bulut sağlayıcıyı açmadan önce [AI_TRANSPARENCY.md](AI_TRANSPARENCY.md)
> belgesini okuyun.** Varsayılan `auto` modda araç çağıran istekler buluta gider
> ve araç sonuçları müşteri adı, e-posta ve telefon içerebilir; YZ katmanında
> bugün maskeleme yoktur.

### Diller

Yardım Merkezi ve Eğitim Merkezi **içeriği** gerçekten iki dillidir. **Arayüz
metinleri İngilizcedir**: 337 şablon `{% trans %}` kullanır ancak derlenmiş bir
Türkçe katalog paketlenmemiştir, bu yüzden metinler İngilizce kaynağa düşer.
Türkçe **biçimlendirme** (tarih, sayı, para birimi) uygulanır. Önceki sürüm
"menüler, bildirimler, doğrulama mesajları ve yardım içeriği tamamen çevrilmiş"
diyordu; bu doğru değildi ve sessizce silinmek yerine düzeltildi.

### Sık karşılaşılan sorunlar

| Belirti | Çözüm |
|---|---|
| `DJANGO_SETTINGS_MODULE is not set` | Değişkeni işletim sistemi ortamında ayarlayın; `.env` içinde çalışmaz |
| Her sayfa "Parola değiştir" ekranına yönlendiriyor | Hesap hâlâ ilk kurulum bayrağını taşıyor; parolayı değiştirin |
| `admin/admin` girişi reddediliyor | Bu hesap yalnızca sunucunun çalıştığı makineden giriş yapabilir |
| Sayfalar stilsiz görünüyor | `npm run build` çalıştırın |
| `compilemessages` hata veriyor | Bunun yerine `python manage.py i18n_compile` kullanın |
| CSV'de Türkçe karakterler bozuk | Dosyayı UTF-8 olarak açın; dışa aktarımlar BOM içerir |

---

## Licence

MIT — see [LICENSE](LICENSE).

Third-party components and their licences: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Additional notices about external services and models: [NOTICE](NOTICE).
Machine-readable inventories: [sbom.spdx.json](sbom.spdx.json) and
[sbom.cdx.json](sbom.cdx.json).

Weather and marine data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
Open-Meteo's free hosted tier is for **non-commercial** use; a commercial
deployment must subscribe to a paid plan, self-host Open-Meteo, or switch the
provider to met.no with `SURF_COMMERCIAL_MODE=True`.
