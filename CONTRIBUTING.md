# Contributing

Thanks for looking. This file is required by
[`docs/OPEN_SOURCE_LICENSES.md`](docs/OPEN_SOURCE_LICENSES.md), which mandates
that it reproduce the licence rules below.

## Before you start

This is a single-maintainer project. Open an issue before writing anything
substantial — it may already be planned, deliberately out of scope, or blocked
by something not visible from outside.

Small, focused pull requests are much easier to accept than large ones.

## Licence of code you submit

By opening a pull request you agree that your contribution is licensed under the
**MIT License**, the same licence as the rest of the project
([LICENSE](LICENSE)).

### Licence compatibility — the hard rule

**Do not paste, adapt or translate code from a project under a copyleft
licence.** Concretely, code derived from any of the following may **never** enter
this repository:

| Licence | Why it is refused |
|---|---|
| **AGPL-3.0** (any version) | §13 triggers on *network interaction*, which is exactly this product's deployment shape. A single derived file would oblige every operator of every deployment to publish their whole modified source. |
| **GPL-2.0 / GPL-3.0** | Incompatible with distributing this project under MIT. |
| **SSPL** | Not an open-source licence; service-provider obligations are unbounded. |
| **EUPL**, **CDDL**, **CPL/EPL** (as a source) | Reciprocal terms that MIT cannot absorb. |
| Anything with a "non-commercial", "no derivatives" or field-of-use restriction | Not redistributable under MIT. |

**Permissive licences are acceptable** where their notice requirements are
honoured: MIT, BSD-2/3-Clause, 0BSD, ISC, Apache-2.0, MIT-CMU, PSF-2.0,
Python-2.0. If you bring in code under one of these, add the upstream copyright
notice and record the component in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) **in the same pull request**.

**Weak copyleft** (LGPL, MPL-2.0) is acceptable only as an *unmodified,
separately installed dependency* — never as copied source. `psycopg`
(LGPL-3.0-only) and `certifi` (MPL-2.0) are already in that position; see
THIRD_PARTY_NOTICES.md §1 for the obligations that attach if you redistribute a
bundle.

### The AGPL reference register

`docs/OPEN_SOURCE_LICENSES.md` keeps a register of projects that were read
**for architecture and ideas only** during design, several of which are AGPL-3.0
(Snipe-IT, wger, django-crm, EspoCRM, SuiteCRM, matorral). Reading a project to
understand a problem is fine. Copying its code is not, and neither is
transliterating it.

If your contribution was informed by any of them, say so in the pull request. It
is not disqualifying; it just needs to be visible.

### AI-assisted contributions

Say so in the pull request if a model wrote a meaningful part of your patch, and
name the tool. You remain responsible for the licence position of what you
submit — an assistant can reproduce training data verbatim, and "the model wrote
it" is not a provenance argument. Review the output as if you had copied it from
a stranger's repository.

## Development setup

```powershell
git clone <your fork>
cd smart-surf-school-management-system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env       # then set DJANGO_SECRET_KEY
npm install; npm run build
python manage.py migrate
python manage.py bootstrap_roles
python manage.py bootstrap_admin  # admin / admin, local device only, must be changed
python manage.py runserver
```

## Before you open a pull request

```powershell
python -m pytest -q          # all tests must pass
python -m ruff check .       # must be clean
python -m bandit -q -r apps config -x "*/tests/*"
```

`ruff format` is available and is applied to new files, but it is **not** a CI
gate: the codebase predates the formatter and running it across the tree would
rewrite ~250 files. Do not reformat files you are not otherwise changing.

CI runs the same set; see [.github/workflows/ci.yml](.github/workflows/ci.yml).

### Rules that will get a pull request sent back

1. **A failing or deleted test.** Never delete or weaken a test to make a change
   pass. If a test is wrong, fix the test *and say why* in the pull request.
2. **A new API endpoint or list view under `finance`, `rentals`, `lessons`,
   `surf_camps` or `bookings` without an ownership policy.** A structural test
   in `apps/accounts/tests/test_object_scoping.py` enforces this. Declare
   `external_access` (and `owner_lookups` when it is `OWN`) in the class body.
3. **A claim in the README or the presentation that the code does not enforce.**
   Two such claims had to be corrected before this project could be published;
   we are not adding a third. If you want to state a guarantee, write the test
   that proves it and link to it.
4. **A real secret, key, token or personal record** in any file, including
   fixtures, screenshots and test data. `.env.example` uses empty values or
   literal placeholders only.
5. **A number in the documentation that was not measured.** Count it from the
   source in the same pull request.
6. **New Python dependencies without a licence check.** Add the component to
   THIRD_PARTY_NOTICES.md and regenerate the SBOMs.

## Code style

- `ruff` is the linter, and its configuration lives in `pyproject.toml`.
  `ruff check` must be clean. Format new files with `ruff format`; leave
  untouched files alone.
- Line length 100.
- Business rules go in `services.py`; queries go in `selectors.py`; views
  orchestrate and render. A view should not contain a money rule or a safety
  rule.
- User-facing strings are English source strings wrapped in `gettext`.
- Money is `Decimal`. Never `float`.
- Comments explain *why*, not *what*.

## Tests

- `pytest`, `pytest-django`, `factory-boy`. Fixtures live in each app's
  `tests/factories.py`.
- Mark security assertions with `@pytest.mark.security`.
- For an access-control change, write the **negative** test as well as the
  positive one: what the wrong role must *not* see. That is the class of bug
  that a positive-only suite cannot catch.
- Never let a test reach the network. The test settings point every integration
  at an unroutable address on purpose.

## Reporting security issues

Not here — see [SECURITY.md](SECURITY.md).

## Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
