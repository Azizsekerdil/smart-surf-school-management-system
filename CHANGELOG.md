# Changelog

All notable changes to this project are recorded here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/);
versioning follows [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-08-19 — first public release

This is the first version published outside a private repository. The entries
below are the changes that had to be made before it could be.

### Security — fixed

- **Row-level authorisation for customer and student accounts.** Both external
  roles hold `finance.view`, `rentals.view`, `lessons.view` and
  `surf_camps.view` so a self-service surface can exist at all. The object-level
  rule that was supposed to narrow those to the caller's own rows existed as a
  class but was **referenced nowhere** — a customer could read every invoice,
  payment, rental, lesson register and camp participant in the school, including
  records about children. New `apps/accounts/scoping.py` narrows every affected
  queryset before it runs; a foreign row returns 404, not 403. Applied to the
  REST API and the HTML views alike, and to the custom actions that build their
  own querysets.
- **Fail-closed scoping.** `apps/core/mixins.OwnerScopedQuerysetMixin` returned
  the *unfiltered* queryset when no ownership path was declared. It now returns
  nothing. A missing declaration hides data instead of publishing it.
- **Broken ownership lookup.** The lessons screens declared
  `attendances__student__user`, which is not a valid path — `Student` reaches a
  login through its `Customer`. The first external user to open the timetable
  would have hit a `FieldError`. Fixed, and a test now walks every declared
  lookup and fails if one does not resolve.
- **Operational overviews closed to external accounts.** Camp participant lists,
  daily rosters, camp finances, the booking calendar, the daily run sheet, the
  hire counter's customer search and the stock board have no "own rows"
  projection and are now refused outright for customers and students.
- **`finance.revenue`.** Taking money at a counter (`finance.view`) and reading
  the school's takings are now separate privileges. Reception and rental staff
  keep the first and lose the second, which makes the long-standing claim "a
  rental clerk cannot see revenue" true for the first time.
- **First-run credential contract.** `manage.py bootstrap_admin` creates a
  documented `admin` / `admin` account that must change its password before
  anything opens, refuses to authenticate from anywhere but the local device,
  dies permanently on first change, cannot be restored by a password reset, is
  stored as an **Argon2id** hash and is rate-limited. The `must_change_password`
  flag already existed but nothing read it; new middleware enforces it on every
  request.
- **Authentication backend bypass.** Django's bare `ModelBackend` was listed
  after the project's own backend, so it re-authorised sign-ins the project's
  backend deliberately refused. Removed.
- **WSGI/ASGI no longer default to development settings.** They defaulted to
  `config.settings.dev` while the deployment guide told operators to set the
  variable in `.env` — which cannot work, because `.env` is read from inside the
  settings module, after Django has chosen one. Following the documented
  procedure therefore produced `DEBUG=True`, `ALLOWED_HOSTS=["*"]`, the insecure
  fallback secret key and disabled brute-force protection, bound to `0.0.0.0`.
  The entrypoints now refuse to start without an explicit setting.
- **JWT revocation.** `rest_framework_simplejwt.token_blacklist` is installed
  and `BLACKLIST_AFTER_ROTATION` is on. Rotation without blacklisting left a
  stolen refresh token usable for its full 7-day life, undetectably.
- **`git config` is no longer auto-approved** in the AI terminal. Several git
  configuration keys hold values git executes through a shell during ordinary
  commands, which undercut the module's "no shell, ever" guarantee.
- **Argon2id** is now the first password hasher (was PBKDF2).

### Fixed

- **`apps/backups/` and `templates/backups/` were not in version control.** The
  `.gitignore` rule `backups/` matched any directory of that name at any depth,
  so a whole Django app listed in `INSTALLED_APPS` was silently excluded and a
  clone of the repository could not start. The rule is now anchored
  (`/backups/`), as are `/media/` and `/logs/`, and the app is included.
- `.claude/` is now git-ignored.

### Removed

- **`FIELD_ENCRYPTION_KEY`.** It advertised "encryption of provider API keys
  stored in the database". No such code ever existed, and the design is the
  opposite — keys are read from the environment and never written to the
  database. Removed rather than left advertising a control that does not exist.
- Internal planning, readiness and vendor-probe documents, and a master-prompt
  document for a different product, are out of the public repository's scope.
- The previously generated slide decks, which carry the two corrected
  access-control claims and screenshots captured with a `DEBUG` banner and a
  local filesystem path visible.

### Documentation

- README rewritten: what the product is **not**, its maturity, mechanically
  measured counts, the first-run credential, demo-data honesty, environment
  variables, the local-only AI option, and the financial/legal/health limits.
- The bilingual-interface claim is corrected. Help and Training **content** is
  bilingual; the interface chrome is English pending a compiled Turkish
  catalogue. The previous wording — "menus, notifications, validation messages
  and help content are fully translated" — was not true.
- Coverage percentage is no longer quoted, because it was not measured for this
  release.
- Added `SECURITY.md`, `PRIVACY.md`, `AI_TRANSPARENCY.md`,
  `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `docs/known-limitations.md`, `NOTICE`, `sbom.spdx.json`, `sbom.cdx.json`,
  a Dependabot configuration and a CI workflow.
- `LICENSE` now contains the unmodified MIT text; the appended notices moved to
  `NOTICE`, so licence detection reports MIT instead of NOASSERTION.
  `package.json` declares `"license": "MIT"`.

### Tests

- `apps/accounts/tests/test_object_scoping.py` — 42 negative authorisation
  tests, including ones named and marked specifically for records about minors,
  plus structural tests that fail the build if a new endpoint omits an ownership
  policy or declares a lookup that does not resolve.
- `apps/accounts/tests/test_bootstrap_admin.py` — the first-run credential
  contract, clause by clause.
- Suite: **2 130 tests, 2 128 passing, 2 skipped.** The two skips need a live AI
  provider, which the test settings deliberately forbid. 90 of them are marked
  `security`.

## [1.0.0] — 2026-08-18

Initial internal release. Not published.
