# Security policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting for this repository:
**Security → Advisories → Report a vulnerability**. That creates a private
thread visible only to the maintainers.

If private reporting is unavailable to you, open a public issue containing
**only** the sentence "I would like to report a security issue privately" and no
technical detail, and wait to be contacted.

### What to include

- What you did, what happened, and what you expected.
- The smallest reproduction you have — a request, a role, a URL.
- The version or commit you tested.
- Whether the finding is already public anywhere.

Please do not include real personal data in a report. If a reproduction needs a
record, use the synthetic demo seed.

### What to expect

This is a single-maintainer project with no paid support and no bug-bounty
programme. There is no guaranteed response time. In practice:

- acknowledgement when the report is read;
- an assessment of severity and whether it is in scope;
- a fix, a mitigation, or an honest statement that it will not be fixed;
- credit in [CHANGELOG.md](CHANGELOG.md) if you want it.

## Supported versions

Only the `main` branch is supported. There are no maintained release branches
and no backported security fixes.

## Scope

**In scope** — anything in this repository that runs as part of the product:

- authentication, the capability matrix, and the row-level ownership scoping in
  `apps/accounts/scoping.py`;
- the first-run bootstrap credential contract (`apps/accounts/bootstrap.py`);
- the AI terminal's command allowlist, workspace jail and approval gate;
- backup and restore, including archive extraction;
- file upload validation;
- template escaping and any `mark_safe` usage;
- the REST API, including the JWT lifecycle.

**Out of scope**

- Findings that require `DJANGO_DEBUG=True`. Development settings are
  deliberately permissive and are documented as such.
- Findings against `config/settings/dev.py`.
- Vulnerabilities in third-party dependencies — report those upstream. Tell us
  anyway if this project's usage makes an upstream issue exploitable here.
- Social engineering, physical access, and denial of service through resource
  exhaustion on a single-tenant self-hosted application.
- The documented `admin` / `admin` first-run credential **as such**. It is
  published on purpose. A way to *bypass* one of the rules that constrain it —
  reaching a screen before the password is changed, signing in with it from a
  remote address, restoring it after a change — is very much in scope.

## Known weaknesses, stated up front

These are documented rather than hidden. Do not report them as new findings;
do report a way to make one of them worse.

| Area | Status |
|---|---|
| **Personal data sent to cloud AI** | In the default `auto` routing mode, tool-bearing requests prefer a cloud provider, and tool results can carry customer names, e-mail addresses and phone numbers. There is **no PII masking in the AI layer**. Use `AI_ROUTING_MODE=local_only`. See [AI_TRANSPARENCY.md](AI_TRANSPARENCY.md). |
| **Single tenant** | There is no branch/tenant isolation. Two schools must not share one installation. |
| **No field-level encryption at rest** | Database contents are protected by filesystem and database permissions only. A `FIELD_ENCRYPTION_KEY` setting that advertised otherwise has been removed rather than left in place. |
| **Backup restore has no decompression limit** | A hostile archive placed on the backup drive could fill the disk during restore. Zip-slip *is* prevented. Reachable only by a holder of `backups.restore`, which is a privileged capability. |
| **PostgreSQL untested** | The suite runs on SQLite. |
| **No CSP** | No Content-Security-Policy header is set. |

## Security controls that exist

So that a reviewer knows what to test against:

- **Capability matrix** — 203 capabilities across 15 roles, driving menus, HTML
  views, the REST API and the AI tool layer from one source of truth.
- **Row-level ownership scoping** — external accounts (customer, student) are
  narrowed to their own rows before the query runs; a foreign row returns 404.
  A structural test fails the build if a new endpoint omits its policy.
- **First-run credential contract** — forced password change, loopback-only
  bootstrap sign-in, permanent retirement on first change, Argon2id hashing,
  rate limiting. Pinned by `apps/accounts/tests/test_bootstrap_admin.py`.
- **Brute force** — django-axes, first in the authentication backend chain, 8
  failures, 15-minute cool-off, keyed on IP *and* username.
- **JWT** — 2-hour access tokens, 7-day refresh, rotation **with** blacklisting
  so logout and password change actually revoke.
- **Secret redaction** — a logging filter strips `nvapi-`, `sk-ant-`, `sk-`,
  `gh[pousr]_`, `AKIA`, Bearer/Basic, JWTs and 16 sensitive key names (including
  Turkish `sifre`/`parola`) from every handler. Provider keys are stripped from
  AI-terminal child processes and excluded from backups (names only, never
  values).
- **No API key in the database** — keys come from the environment only.
- **AI terminal** — `shell=False` always, shell-metacharacter rejection, a
  7-executable allowlist, per-subcommand SAFE/APPROVAL/BLOCKED classification, a
  Windows-aware workspace jail (NUL bytes, alternate data streams, reserved
  device names, UNC, 8.3 names, reparse points), process-tree kill on timeout,
  and human approval as a stored gate. `git config` is **not** auto-approved.
- **Uploads** — extension, declared MIME and magic bytes must all agree; SVG is
  not an allowed image type; filenames are NFKC-normalised and stripped of
  directory components, control characters and device names.
- **Restore** — every archive member is resolved against the destination root
  and refused if it escapes.
- **No dangerous primitives** — no `eval`, `exec`, `pickle`, `yaml.load`,
  `os.system`, `RawSQL` or `.extra()` outside tests. The only raw cursor use is
  `SELECT 1` in the health probe. The AI reaches data only through 13 typed,
  capability-gated Python tools — it never writes SQL.
- **Human approval for AI safety output** — an AI-suggested safety warning is
  invisible until a named staff member signs it off.

## Deploying safely

- Set `DJANGO_SETTINGS_MODULE=config.settings.prod` **in the process
  environment**. It cannot be set in `.env`; the WSGI/ASGI entrypoints refuse to
  start without it rather than falling back to development settings.
- Set a real `DJANGO_SECRET_KEY` and explicit `DJANGO_ALLOWED_HOSTS`. The
  production profile refuses to start otherwise.
- Terminate TLS in front of the application and keep `SECURE_SSL_REDIRECT`,
  `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` on.
- Change the bootstrap password before exposing the machine to a network.
- Keep `backups/`, `media/`, `logs/` and the database file off any web root.
