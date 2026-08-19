# Known limitations, and the roadmap

One page, honestly. If something is broken, untested or not built, it is listed
here rather than left for you to discover.

Legend: **NOT BUILT** · **UNTESTED** · **PARTIAL** · **BY DESIGN**

---

## 1. Deployment and scale

| | Status | Detail |
|---|---|---|
| PostgreSQL | **UNTESTED** | It is the documented production target and the code is written for it, but the 2 130-test suite has only ever run against SQLite. |
| Load / performance testing | **NOT BUILT** | No benchmark, no profile, no concurrency test. Sizing guidance would be a guess. |
| Multi-tenant / multi-branch | **BY DESIGN — absent** | No tenant, branch or organisation model exists. One installation serves one school. Two branches on one instance would see each other's customers. |
| Production deployment | **NOT BUILT** | No installation has run a real season. `docs/DEPLOYMENT.md` is a plan, not a proven runbook. |
| Containerisation | **NOT BUILT** | No Dockerfile, no compose file. Note that a container image embedding `psycopg-binary` picks up LGPL-3.0 redistribution duties — see THIRD_PARTY_NOTICES.md §1. |
| Horizontal scaling | **NOT BUILT** | Sessions are database-backed and the design assumes one application process. |

## 2. Testing

| | Status | Detail |
|---|---|---|
| Unit and integration tests | **Built** | 2 130 collected, 2 128 passing, 2 skipped; 90 marked `security`. |
| Coverage measurement | **NOT MEASURED for this release** | `pytest-cov` is installed and `scripts/test.ps1 -Coverage` runs it, but no figure was produced for 1.1.0, so none is quoted anywhere. |
| Browser / end-to-end UI tests | **NOT BUILT** | No Playwright or Selenium suite. `scripts/smoke_test.py` checks that 37 signed-in screens and 2 public screens return 200; it does not click anything. |
| AI provider integration tests | **BY DESIGN — absent** | Tests never touch the network; provider settings point at an unroutable address. The two skipped tests are the AI-dependent ones. Providers are verified by hand. |
| Acceptance scenario | **PARTIAL** | `scripts/e2e_scenario.py` has 20 steps. Steps 18 and 19 self-report SKIPPED when LM Studio or NVIDIA are unreachable, so "20/20" holds only with both providers live. |

## 3. Privacy and compliance

Full detail in [PRIVACY.md](../PRIVACY.md). Summary of what is **not** built:

- consent record for health data (only marketing consent exists);
- legal-basis register, processing register, DPIA;
- retention policy and automatic deletion;
- data-subject access / portability / erasure workflow;
- field-level encryption at rest;
- sub-processor register and breach-notification runbook.

**No KVKK, GDPR, PCI-DSS or ISO compliance is claimed.** The product models
special-category data and children's records; closing the gaps above is a
deployment project.

## 4. AI

Full detail in [AI_TRANSPARENCY.md](../AI_TRANSPARENCY.md).

| | Status | Detail |
|---|---|---|
| PII masking before a cloud call | **NOT BUILT** | This is the most significant known gap. Tool results can carry customer names, e-mail addresses and phone numbers, and the default `auto` routing sends tool-bearing requests to the cloud. Mitigation today: `AI_ROUTING_MODE=local_only`. |
| Per-request human approval for a cloud call | **NOT BUILT** | |
| Region pinning / recorded retention assumptions | **NOT BUILT** | Governed by your contract with the provider. |
| Providers | **PARTIAL** | LM Studio, NVIDIA NIM, Anthropic and a generic OpenAI-compatible endpoint. No OpenAI, Gemini, Azure, Ollama or vLLM adapter. |
| Model quality | **BY DESIGN — yours** | Local answer quality depends on the model you load. |

## 5. Interface and languages

| | Status | Detail |
|---|---|---|
| Turkish interface chrome | **NOT BUILT** | 337 templates use `{% trans %}` but no compiled catalogue is shipped, so strings fall back to English. Turkish *formatting* (dates, numbers, currency) does apply. Build one with `manage.py i18n_extract` / `i18n_compile`. |
| Help & Training content in Turkish | **Built** | Genuinely bilingual — the seeds carry `title_tr` / `body_tr`. |
| Mobile application | **BY DESIGN — absent** | Responsive web only. |
| Accessibility audit | **NOT BUILT** | No WCAG audit, no screen-reader testing. |
| Content-Security-Policy header | **NOT BUILT** | |

## 6. Integrations that do not exist

Card payments (Stripe, iyzico, PayPal); e-Fatura / e-Arşiv or any fiscal
integration; fiscal printers; SMS and WhatsApp gateways; accounting-package
export; calendar sync (Google, Outlook, CalDAV); Zapier or webhooks out.

## 7. Presentation and screenshots

**Status for 1.1.0: the slide deck is not part of this release.**

The previously generated decks (four PDFs, four PPTX files, two HTML decks, all
built from one source) are excluded from the public repository because they
carry two access-control claims that were false when they were written, a
bilingual claim that is still false, screenshots taken with Django's `DEBUG`
banner visible, a local filesystem path (`D:\...\backups`) baked into one
screenshot, real consumer equipment brands in the demo seed, and four embedded
subsets of a proprietary font.

The code claims have since been fixed and the bilingual claim corrected in the
README, but **regenerating the deck requires capturing fresh screenshots against
a running server**, which was not possible in the release environment. Until
that is done:

- `scripts/presentation_content.py` and `scripts/generate_presentation.py` ship,
  so anyone can regenerate a deck from this source;
- no PDF, PPTX or HTML deck is published;
- `assets/screenshots/` ships as the raw capture output and is **not** presented
  as a marketing artefact.

Regenerating properly means: reseed with the corrected demo data (neutral
equipment brands, obviously fictional student names, masked undialable phone
numbers), capture with `DJANGO_DEBUG=False` and a neutral backup path, rebuild
the deck with an openly licensed font, and re-verify the output for text, images
and metadata.

## 8. Backup and restore

| | Status | Detail |
|---|---|---|
| Backup, checksum, verify, restore | **Built** | SHA-256 per archive, safety backup before restore, zip-slip prevented. |
| Decompression / size limit on restore | **NOT BUILT** | A hostile archive on the backup drive could fill the disk. Reachable only with `backups.restore`, a privileged capability. |
| Off-site / cloud backup | **NOT BUILT** | Local filesystem only. |
| Backup encryption | **NOT BUILT** | Encrypt the volume. |

## 9. Provenance

`docs/OPEN_SOURCE_LICENSES.md` keeps a register of projects read for
architecture during design, several AGPL-3.0. No code-similarity analysis has
been run against them. See THIRD_PARTY_NOTICES.md §7 for the exact status —
"not machine-verified" rather than "clean".

---

## Roadmap — in the order it would matter

1. **Mask personal identifiers in AI tool results** when the resolved provider is
   not local; make `local_only` the default when a person-returning tool is in
   scope.
2. **Run the suite against PostgreSQL** in CI and fix whatever that surfaces.
3. **Compile the Turkish catalogue**, recapture the Turkish screenshots, and
   only then restate the bilingual claim.
4. **Regenerate the presentation** from the corrected seed and publish it.
5. **Measure coverage** and publish the figure with the report that backs it.
6. **Retention and erasure**: a per-model retention policy and a data-subject
   request flow.
7. **Decompression limits** on restore.
8. **Browser end-to-end tests** for the booking and check-out flows.
9. **Content-Security-Policy** and an accessibility pass.
10. **Off-site encrypted backup.**

Nothing above is promised or scheduled. This is a single-maintainer project.
