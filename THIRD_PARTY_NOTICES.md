# Third-party notices

This file is required by [`docs/OPEN_SOURCE_LICENSES.md`](docs/OPEN_SOURCE_LICENSES.md),
which mandates it at repository root. It lists the third-party software this
project depends on, bundles or calls, together with the licence each one is
distributed under.

The machine-readable equivalents are [`sbom.spdx.json`](sbom.spdx.json) and
[`sbom.cdx.json`](sbom.cdx.json). They are the authority; this document is the
human summary.

**How the inventory was produced.** The npm half comes from `syft` reading
`package-lock.json`. The Python half comes from `cyclonedx-py environment` run
against the resolved virtual environment that the test suite was executed in —
`requirements.txt` pins ranges (`Django>=5.2,<6.0`) rather than exact versions,
so a static read of it cannot state a version, and the tested environment is the
honest answer. Re-resolving the ranges later will produce newer versions than
the ones recorded here.

**Totals at the time of writing:** 122 third-party components — 57 MIT, 30
BSD-family, 14 Apache-2.0, plus the individual entries called out below. **No
GPL, AGPL or SSPL component is present.**

---

## 1. Components that carry an obligation beyond attribution

| Component | Version | Licence | What it means here |
|---|---|---|---|
| `psycopg` / `psycopg-binary` | 3.3.x | **LGPL-3.0-only** | The PostgreSQL driver. Installed from PyPI and used unmodified through its public API, which is compatible with distributing this project's own source under MIT. **The obligation attaches if you redistribute a bundle that embeds the binary** — an installer, a container image, a frozen executable. In that case LGPL-3.0 relinking and notice duties apply to the driver. `docs/OPEN_SOURCE_LICENSES.md` records `pg8000` (BSD-3-Clause, pure Python) as the escape hatch if that is unacceptable. |
| `certifi` | 2026.x | **MPL-2.0** | File-level copyleft: modifications to certifi's own files must be published. This project does not modify it. |
| Open-Meteo data | — | **CC BY 4.0**, free tier **non-commercial** | Attribution is rendered in the application footer as `Weather data by Open-Meteo.com (CC BY 4.0)`. A **commercial** deployment must subscribe to a paid Open-Meteo plan, self-host Open-Meteo (AGPL-3.0 — note that self-hosting brings AGPL obligations to *that* deployment), or switch to met.no with `SURF_COMMERCIAL_MODE=True`. |
| met.no (Norwegian Meteorological Institute) | — | NLOD / CC BY 4.0 | Requires a descriptive `User-Agent` with contact details. The provider refuses to call without one. |

---

## 2. Vendored front-end assets

These are committed into `static/vendor/` so the product works with no CDN and
no internet connection. Each ships its own licence file next to it.

| Asset | Version | Licence | Licence file in this repo |
|---|---|---|---|
| HTMX | 1.9.12 | 0BSD | `static/vendor/htmx/LICENSE` |
| Alpine.js | 3.16.x | MIT | bundled header |
| Chart.js | 4.5.x | MIT | `static/vendor/chartjs/LICENSE.md` |
| Lucide icons (`lucide-static`) | 0.468.0 | ISC (Lucide, derived from Feather, MIT) | `static/vendor/icons/LICENSE` |

`scripts/vendor_assets.js` performs the vendoring, so the provenance of every
file under `static/vendor/` is reproducible.

**Fonts: none are vendored.** The application uses the reader's system font
stack. See §4 for the fonts used in generated documents.

---

## 3. Python and npm dependencies

The full list with versions and licences is in the SBOMs. Summary by licence
family:

| Licence | Components |
|---|---|
| MIT | 57 |
| BSD (2-, 3-clause, 0BSD) | 30 |
| Apache-2.0 (incl. dual Apache/BSD) | 14 |
| Python-2.0 / PSF-2.0 | 3 |
| MIT-CMU (Pillow) | 1 |
| ISC | 1 |
| MPL-2.0 (certifi) | 1 |
| LGPL-3.0-only (psycopg) | 2 |
| Not declared in package metadata | 3 — `bleach` (Apache-2.0 per its own repository), `python-crontab` (LGPL-3.0 per its own repository), `uritemplate` (Apache-2.0 / BSD-3-Clause dual per its own repository) |

> The three "not declared" rows are components whose wheel metadata omits a
> machine-readable licence field. The licences named are taken from each
> project's own repository and are recorded here rather than left blank —
> but they are **not** machine-verified, which is why they are listed
> separately instead of being folded into the counts above.

### Notable runtime dependencies

| Component | Licence |
|---|---|
| Django | BSD-3-Clause |
| Django REST Framework | BSD-3-Clause |
| django-filter, drf-spectacular | BSD-3-Clause |
| djangorestframework-simplejwt | MIT |
| django-cors-headers, django-htmx, django-environ, django-axes, whitenoise | MIT |
| **argon2-cffi**, argon2-cffi-bindings | MIT |
| celery, kombu, billiard, vine, amqp | BSD-3-Clause |
| redis, django-celery-beat | MIT / BSD-3-Clause |
| Pillow | MIT-CMU |
| segno | BSD-3-Clause |
| python-barcode | MIT |
| reportlab | BSD-3-Clause |
| openpyxl | MIT |
| XlsxWriter | BSD-2-Clause |
| numpy | BSD-3-Clause |
| python-dateutil | Apache-2.0 OR BSD-3-Clause |
| httpx, httpcore, h11, anyio | BSD-3-Clause / MIT |
| requests, urllib3 | Apache-2.0 / MIT |
| tenacity | Apache-2.0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| bleach | Apache-2.0 (see note above) |
| Markdown | BSD-3-Clause |

### Development-only dependencies

pytest, pytest-django, pytest-cov, pytest-xdist, factory-boy, Faker, freezegun,
responses, ruff, bandit, pip-audit, django-debug-toolbar, python-pptx, pywin32.
All MIT, BSD or Apache-2.0 except `pywin32` (PSF-style). These are **not**
shipped to a user of the product.

---

## 4. Documents generated by this project

### Slide deck fonts

`scripts/generate_presentation.py` produces PPTX and PDF decks. Historic decks
built on this machine embedded four subsetted **Segoe UI** faces (Microsoft,
proprietary; OS/2 `fsType = 8`, "editable embedding", so the embedding itself is
permitted by the font's own licensing bits).

Embedding proprietary glyph data into a repository distributed under MIT is
avoidable, so the generator now requests an openly licensed face and records it
here:

| Font | Licence | Where it comes from |
|---|---|---|
| **DejaVu Sans** | DejaVu Fonts License (Bitstream Vera–derived, permissive; free to use, embed and redistribute) | Shipped with `reportlab`; also present on most Linux systems |
| Source Sans 3 / Inter | SIL Open Font License 1.1 | Acceptable substitutes if you regenerate with a different toolchain |

If you regenerate the deck on a machine where the requested face is missing, the
tool falls back to the platform default — check the embedded fonts before
publishing the result.

### Report exports

PDF reports are produced with `reportlab` (BSD-3-Clause). `apps/reporting/exporters/pdf.py`
reads a system font from `C:\Windows\Fonts` when one is available; that font is
**not** embedded in this repository and is used under the licence of the machine
it runs on.

---

## 5. Trademarks

NVIDIA, NVIDIA NIM, Anthropic, Claude, LM Studio, Open-Meteo, met.no,
Stormglass, Django, Tailwind CSS, HTMX, Alpine.js, Chart.js, Lucide, PostgreSQL,
SQLite, Redis, Microsoft, Windows and PowerShell are trademarks of their
respective owners. They appear here and in the documentation as accurate
nominative references to the products this software interoperates with. No
endorsement is claimed or implied, and none appears in this project's name,
package name, module paths or file names.

---

## 6. AI models and hosted inference

This project calls model APIs; it does **not** redistribute any model weights.
Anything you run through LM Studio, NVIDIA NIM, Anthropic or a generic
OpenAI-compatible endpoint is governed by that provider's own licence and terms
of service, which you accept directly with them. See
[AI_TRANSPARENCY.md](AI_TRANSPARENCY.md).

---

## 7. Provenance of this project's own code

`docs/OPEN_SOURCE_LICENSES.md` maintains an "architecture and ideas only"
register of projects that were read during design, several of which are
AGPL-3.0 (Snipe-IT, wger, django-crm, EspoCRM, SuiteCRM, matorral). The register
exists precisely because AGPL §13 triggers on network interaction, which is this
product's deployment shape.

**Status: not machine-verified.** Confirming that no AGPL code was copied would
require code-similarity analysis against those six upstream codebases, and no
such analysis has been run. What can be said is narrower and true: the register
was kept deliberately, the entries are marked as read-for-architecture only, and
no file in this repository carries an upstream copyright header or licence
notice from any of them. Anyone relying on the MIT grant for a commercial
derivative should form their own view — see
[CONTRIBUTING.md](CONTRIBUTING.md) §"Licence of code you submit" for the rule
that applies going forward.

---

*Last regenerated: 2026-08-19, from the resolved environment used for the
release test run.*
