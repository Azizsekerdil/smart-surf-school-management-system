# Privacy

This document describes what personal data the **software** handles, what it
does not, and what a deployment still has to build. It is not a privacy notice
for any particular surf school — if you deploy this, you are the controller and
you have to write your own.

Nothing here is legal advice.

---

## 1. Is there any real personal data in this repository?

**No.** Every person-shaped value in the source, the demo seed, the tests, the
screenshots and the presentation is synthetic:

- e-mail addresses use IETF-reserved or clearly fictional domains
  (`example.com`, `example.test`, `surfschool.test`);
- phone numbers are generated from a counter, are not dialable, and are masked
  in anything published;
- names are invented;
- no national identity number (TCKN), IBAN, payment card or address of a real
  person appears anywhere;
- no database, dump, backup, log or media upload is committed — `.gitignore`
  excludes them and none has ever been added.

The one real e-mail address in the repository is the author's own, used as the
contact address that met.no's terms require in an API `User-Agent`.

---

## 2. What the product stores when you use it

### Ordinary personal data

Customers and students: name, e-mail, phone, birth date, preferred language,
booking source, marketing consent flag and timestamp, internal notes.
Instructors: the same, plus certifications, availability, time off, performance
reviews and commission.

Operational records: bookings, lessons and attendance, camp participation with
room and flight details, rentals, invoices, payments, refunds, point-of-sale
transactions.

Technical: login history (username, timestamp, user agent, IP), an audit log of
who changed what, and notification records.

### Special-category data — GDPR Art. 9 / KVKK Art. 6

The product is **designed** to hold this, because a surf school needs it to keep
people safe in the water:

- **Health-adjacent notes**: a free-text field described as "visible to the
  instructor: allergies, fears, kit sizes, languages";
- **Dietary requirements** on camp participants;
- **Medical documents** and **waivers** as document categories;
- **Water competence** (can swim, distance) and physical attributes (weight,
  height) used to size boards and wetsuits;
- **Emergency contacts**.

### Children's data

Explicitly modelled. Minors are recognised (`MINOR_AGE = 18`), a minor customer
**must** have an emergency contact — enforced in model validation — and camp
rosters count minors on site. Junior weeks and family lessons mean a large share
of camp-participant and lesson-attendance rows are records about children.

This is why the row-level access rules exist, and why the tests that cover them
are named and marked so nobody deletes them casually. See
`apps/accounts/tests/test_object_scoping.py`.

---

## 3. What the product does **not** do

- **No tracking of people.** There is no GPS, no route recording, no location
  history. Coordinates in the system belong to *surf spots*, not persons.
- **No biometric data**, no face recognition, no photo analysis.
- **No profiling or automated decision-making with legal effect.** Analytics
  aggregate business metrics; the Surf Score describes the ocean, not a person.
- **No third-party analytics, telemetry, advertising or tracking pixel.** The
  application makes no outbound call except to the weather provider you
  configure and to an AI provider you configure. With `AI_ROUTING_MODE=local_only`
  and `SURF_PROVIDER=manual`, it makes none at all.
- **No CDN.** Every front-end asset is vendored, so a browser session leaks
  nothing to a third party.
- **No data is sent to the author.** There is no phone-home of any kind.

---

## 4. Where personal data can leave the machine

There is exactly one path, and it is opt-in.

**AI providers.** If you configure a cloud provider (`NVIDIA_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_COMPAT_API_KEY`), the assistant's tool results are
sent to it as part of the conversation. Those results can contain **customer
names, e-mail addresses and phone numbers**, unmasked. In the default `auto`
routing mode, requests that use tools prefer the cloud provider.

**There is no PII masking or pseudonymisation in the AI layer today.** The
secret-redaction filter protects credentials in log lines; it does not touch
prompt payloads and does not match personal data.

If you process personal data of people in the EU or Türkiye, treat this as a
transfer requiring its own legal basis, and consider it carefully before
enabling. The safe configuration is:

```
AI_ROUTING_MODE=local_only
```

with LM Studio, which keeps everything on the machine. Read
[AI_TRANSPARENCY.md](AI_TRANSPARENCY.md) before choosing otherwise.

---

## 5. What is implemented, and what a deployment must still build

| Control | Status |
|---|---|
| Role-based access control | **Implemented** — 15 roles, 203 capabilities |
| Row-level access for customers/students | **Implemented** — narrowed before the query runs; foreign rows 404 |
| Operational overviews closed to external accounts | **Implemented** — camp rosters, participant lists, daily run sheets, camp finances |
| Audit log of who changed what | **Implemented** — tamper-evident, with a hash chain for terminal actions |
| Login history and brute-force lockout | **Implemented** |
| Secrets kept out of logs and backups | **Implemented** |
| Marketing consent record | **Implemented** (`marketing_consent`, `marketing_consent_at`) |
| Consent record for **health** data | **Not implemented** |
| Legal-basis register | **Not implemented** |
| Retention policy and automatic deletion | **Not implemented** |
| Data-subject access / portability export | **Not implemented** (the reporting module can export, but there is no per-person DSAR flow) |
| Right-to-erasure workflow | **Not implemented** (deletion is a manual admin action; some rows are protected because money hangs off them) |
| Field-level encryption at rest | **Not implemented** — and no longer advertised |
| Processing register / DPIA | **Not implemented** |
| Sub-processor register | **Not implemented** |
| Breach-notification runbook | **Not implemented** |

**No KVKK or GDPR compliance is claimed anywhere in this project, and that
restraint is deliberate.** The table above is why. Closing those gaps is a
deployment project, not a configuration switch.

---

## 6. Practical advice for a deployment

1. Run `python manage.py flush` before entering real data — the demo seed is
   synthetic and should not sit alongside real records.
2. Keep `AI_ROUTING_MODE=local_only` unless you have written down a legal basis
   for the transfer.
3. Restrict who holds `customers.export`, `analytics.export` and
   `backups.restore`; all three move personal data in bulk.
4. Backups contain everything. Encrypt the backup volume, and keep it off any
   web root.
5. Decide a retention period per record type and delete on schedule — the
   product will not do it for you.
6. Write your own privacy notice and consent texts, and have them reviewed.
7. For children: record who holds parental responsibility, and how consent was
   obtained. The product stores an emergency contact; it does not store a
   consent artefact.

---

## 7. Reporting a privacy problem

Use the private channel in [SECURITY.md](SECURITY.md). Do not include real
personal data in the report.
