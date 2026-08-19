"""Capture the UI screenshots that the introduction decks embed.

Walks the running development server with a headless Chrome (Playwright),
signs in as the admin, and saves one 16:9 PNG per screen into
``assets/screenshots/<lang>/``. The deck generator
(``scripts/generate_presentation.py``) embeds these files on its
``"screens"`` slides, so refresh them whenever the UI changes:

    1. Seed demo data:      .venv\\Scripts\\python scripts\\e2e_scenario.py
    2. Pull live sea data:  .venv\\Scripts\\python manage.py refresh_conditions
    3. Start the server:    .venv\\Scripts\\python manage.py runserver 127.0.0.1:8010
    4. Capture:             python scripts\\capture_screenshots.py
    5. Rebuild the decks:   python scripts\\generate_presentation.py

Requirements: ``pip install playwright`` plus an installed Google Chrome —
the script drives the system Chrome (``channel="chrome"``), so no browser
download is needed. Playwright is capture-time tooling only; it is not a
runtime dependency of the application.

The screenshots show the real seeded demo data, never mock-ups — the same
rule the deck applies to numbers: nothing on a slide that was not actually
observed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Capture credentials come from the environment so that no password -- not even
# a demo one -- is committed. The demo scenario seeds whatever you set here.
#
#     $env:CAPTURE_USERNAME = "admin"
#     $env:CAPTURE_PASSWORD = "<the password you set on the demo admin>"
#
# Capture with DEBUG off, or Django renders its yellow DEBUG badge into every
# screenshot:
#
#     $env:DJANGO_DEBUG = "False"
#
# and point the backup root somewhere neutral, or the Backup screen prints the
# absolute path of your own machine into the image:
#
#     $env:BACKUP_ROOT = "backups"
USERNAME = os.environ.get("CAPTURE_USERNAME", "admin")
PASSWORD = os.environ.get("CAPTURE_PASSWORD", "")

# name -> path after the /<lang> prefix
PAGES = [
    ("dashboard", "/"),
    ("students", "/students/"),
    ("instructors", "/instructors/"),
    ("bookings", "/bookings/"),
    ("lessons", "/lessons/"),
    ("equipment", "/equipment/"),
    ("rentals", "/rentals/"),
    ("surf_conditions", "/surf-conditions/"),
    ("safety", "/safety/"),
    ("finance", "/finance/"),
    ("pos", "/pos/"),
    ("analytics", "/analytics/"),
    ("reports", "/reports/"),
    ("ai", "/ai/"),
    ("audit", "/audit/"),
    ("backups", "/backups/"),
]


def shoot(base: str, out_root: Path, lang: str) -> int:
    from playwright.sync_api import sync_playwright

    out_dir = out_root / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            device_scale_factor=1.25,
            locale="tr-TR" if lang == "tr" else "en-US",
        )
        page = context.new_page()

        page.goto(f"{base}/{lang}/accounts/login/", wait_until="load")
        time.sleep(1)
        page.screenshot(path=str(out_dir / "login.png"))
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("load")
        time.sleep(0.5)

        for name, path in PAGES:
            url = f"{base}/{lang}{path}"
            try:
                # "load" rather than "networkidle": the analytics screen keeps
                # a request open and never reaches network-idle.
                page.goto(url, wait_until="load", timeout=60000)
                time.sleep(2.0)  # charts, HTMX swaps and web fonts
                page.screenshot(path=str(out_dir / f"{name}.png"))
                print(f"[{lang}] {name:16s} ok    {page.title()}")
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures += 1
                print(f"[{lang}] {name:16s} FAIL  {exc}")

        context.close()
        browser.close()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default=str(BASE_DIR / "assets" / "screenshots"))
    parser.add_argument("--lang", choices=["tr", "en", "all"], default="all")
    options = parser.parse_args()

    languages = ["tr", "en"] if options.lang == "all" else [options.lang]
    failures = 0
    for lang in languages:
        failures += shoot(options.base, Path(options.out), lang)
    print("done ->", options.out)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
