# Introduction decks

Ten generated files — two languages × two variants × three formats.
**Do not edit them by hand.** Everything here is produced from one source.

| File | Language | For |
|---|---|---|
| `Surf_School_Tanitim.pptx` | Turkish | Presenting on a screen |
| `Surf_School_Tanitim.pdf` | Turkish | Sending by e-mail |
| `Surf_School_Tanitim.html` | Turkish | Phone and tablet — swipe through, no app needed |
| `Surf_School_Tanitim_Baski.pptx` | Turkish | Printing |
| `Surf_School_Tanitim_Baski.pdf` | Turkish | Printing |
| `Surf_School_Intro_EN.pptx` | English | Presenting on a screen |
| `Surf_School_Intro_EN.pdf` | English | Sending by e-mail |
| `Surf_School_Intro_EN.html` | English | Phone and tablet |
| `Surf_School_Intro_EN_Print.pptx` | English | Printing |
| `Surf_School_Intro_EN_Print.pdf` | English | Printing |

28 slides each — including eight slides of real UI screenshots taken from the
running application (dashboard, booking calendar, students, equipment, rentals,
surf conditions, finance, analytics, AI assistant, backups, audit log, sign-in).

---

## Why there is a print variant

The screen deck is dark, because that is what carries a projector and a laptop
display. Printed, the same slide is a solid block of ink: slow, expensive, and
grey rather than black on most office printers. The print variant is therefore
**rebuilt on white with darker text** — not the same file exported differently.

## The HTML deck

Self-contained: every slide is embedded as a base64 PNG, so the file works from
a USB stick, an e-mail attachment or a phone with no connection. Swipe, use the
arrow keys, or tap the dots. `Ctrl+P` prints one slide per page.

Around 2 MB each, which is the price of embedding the images rather than
depending on a folder that will eventually be separated from the file.

---

## Regenerating

```bash
python scripts/generate_presentation.py
```

```bash
python scripts/generate_presentation.py --lang tr
python scripts/generate_presentation.py --skip-pdf      # PPTX only
```

Content lives in [`scripts/presentation_content.py`](../../scripts/presentation_content.py)
and the layout in [`scripts/generate_presentation.py`](../../scripts/generate_presentation.py).
Edit the content file, run the command, and all ten files are rebuilt together —
which is the point: eight hand-maintained copies of the same wording drift apart
within a week.

### The screenshots

The `"screens"` slides embed PNGs from `assets/screenshots/<lang>/`, captured
from the **running application with seeded demo data** — never mock-ups. To
refresh them after a UI change:

```bash
python scripts/capture_screenshots.py
```

(Requires `pip install playwright` plus an installed Chrome, a dev server on
`127.0.0.1:8010`, demo data from `scripts/e2e_scenario.py` and a sea-state
reading from `manage.py refresh_conditions` — the script's docstring has the
full sequence.) Then regenerate the decks. The generator fails loudly if a
referenced screenshot is missing.

Note: the application's Turkish translation catalog is not compiled yet, so the
TR screenshots currently show the English UI with Turkish locale formatting
(dates, currency). Recapture once the TR catalog ships.

### Requirements

`python-pptx` builds the PPTX files. The PDF and the slide images come from
**PowerPoint via COM automation** — the only converter on the build machine.
Without PowerPoint the PPTX files still build and the script says exactly what
it could not produce.

---

## A rule for the content

**Every number on a slide is one that was actually measured**, and the deck says
where it came from. There is no "100% secure", no "world first", no "guaranteed
compliant" — those cannot be checked, and putting them next to the real figures
would make the real figures look like marketing too.

The deck also carries a *Known limits* slide. If an introduction only lists
strengths, the buyer discovers the weaknesses in production instead.

Figures as of 2026-08-18:

```
28 apps · 86 models · 75 REST resources · 15 roles · 202 capabilities
2,039 tests passing · 72.5% coverage · 40/40 screens · 20/20 acceptance steps
```

AI latencies come from live calls recorded in
[`docs/research/VERIFIED_API_PROBES.md`](../research/VERIFIED_API_PROBES.md).

When any of these change, update `presentation_content.py` and regenerate —
otherwise the deck quietly starts lying.
