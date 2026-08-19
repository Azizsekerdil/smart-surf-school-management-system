#!/usr/bin/env python
"""Render every main screen and report what breaks.

Faster and more informative than hitting a running server: failures come back as
Python tracebacks rather than a 500 page. Run it after any change that touches
templates, URLs or view context.

    .\\.venv\\Scripts\\python.exe scripts\\smoke_test.py
    .\\.venv\\Scripts\\python.exe scripts\\smoke_test.py --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import NoReverseMatch, reverse  # noqa: E402

GREEN, RED, YELLOW, GREY, BOLD, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[1m", "\033[0m"
)

#: Every screen a signed-in Super Admin should be able to open.
SCREENS: list[tuple[str, str, dict]] = [
    ("Dashboard", "dashboard:home", {}),
    ("Global search", "dashboard:search", {}),
    # --- people ---------------------------------------------------------
    ("Customers", "customers:list", {}),
    ("Students", "students:list", {}),
    ("Instructors", "instructors:list", {}),
    ("CRM", "crm:dashboard", {}),
    ("Users", "accounts:user_list", {}),
    ("Role matrix", "accounts:role_matrix", {}),
    ("Profile", "accounts:profile", {}),
    # --- operations -----------------------------------------------------
    ("Locations", "locations:list", {}),
    ("Lessons", "lessons:list", {}),
    ("Booking calendar", "bookings:calendar", {}),
    ("Booking list", "bookings:list", {}),
    ("Surf camps", "surf_camps:list", {}),
    # --- equipment ------------------------------------------------------
    ("Equipment", "equipment:list", {}),
    ("Rentals", "rentals:list", {}),
    ("Maintenance", "maintenance:list", {}),
    # --- surf & safety --------------------------------------------------
    ("Surf conditions", "surf_conditions:dashboard", {}),
    ("Safety", "safety:dashboard", {}),
    # --- business -------------------------------------------------------
    ("Finance", "finance:dashboard", {}),
    ("POS terminal", "pos:terminal", {}),
    ("Analytics", "analytics:dashboard", {}),
    ("Reports", "reporting:list", {}),
    # --- platform -------------------------------------------------------
    ("Notifications", "notifications:list", {}),
    ("Backups", "backups:list", {}),
    ("Audit log", "audit:list", {}),
    ("Settings", "core:settings", {}),
    # --- AI -------------------------------------------------------------
    ("AI assistant", "ai:chat", {}),
    ("AI control center", "ai:control_center", {}),
    ("AI usage", "ai:usage", {}),
    ("AI knowledge", "ai:knowledge_list", {}),
    ("AI terminal", "ai_terminal:console", {}),
    ("AI terminal policy", "ai_terminal:policy", {}),
    ("AI proposals", "ai_terminal:proposal_list", {}),
    # --- guidance -------------------------------------------------------
    ("Help center", "help_center:home", {}),
    ("Training center", "training:home", {}),
    ("Onboarding", "onboarding:start", {}),
]

#: Screens reachable without signing in.
PUBLIC = [
    ("Login", "accounts:login", {}),
    ("Password reset", "accounts:password_reset", {}),
]

API_PATHS = [
    "/api/health/",
    "/api/v1/users/me/",
    "/api/schema/",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Print full tracebacks.")
    options = parser.parse_args()

    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).order_by("pk").first()
    if admin is None:
        print(f"{RED}No superuser exists. Run: manage.py createsuperuser{RESET}")
        return 2

    passed: list[str] = []
    failed: list[tuple[str, str, str]] = []
    skipped: list[str] = []

    def check(label: str, url: str, client: Client, expect=(200,)) -> None:
        try:
            response = client.get(url, follow=False)
        except Exception as exc:  # noqa: BLE001 - that is the point of the script
            failed.append((label, url, f"{type(exc).__name__}: {exc}"))
            print(f"  [{RED}FAIL{RESET}] {label:<24} {GREY}{url}{RESET}")
            if options.verbose:
                traceback.print_exc()
            return

        if response.status_code in expect:
            passed.append(label)
            print(f"  [{GREEN}  OK{RESET}] {label:<24} {GREY}{url}  {response.status_code}{RESET}")
        elif response.status_code in (301, 302):
            passed.append(label)
            target = response.headers.get("Location", "")
            print(f"  [{YELLOW}  ->{RESET} ] {label:<24} {GREY}{url}  {response.status_code} -> {target}{RESET}")
        else:
            failed.append((label, url, f"HTTP {response.status_code}"))
            print(f"  [{RED}FAIL{RESET}] {label:<24} {GREY}{url}  {response.status_code}{RESET}")

    print(f"{BOLD}Public pages{RESET}")
    anonymous = Client()
    for label, name, kwargs in PUBLIC:
        try:
            url = reverse(name, kwargs=kwargs)
        except NoReverseMatch as exc:
            skipped.append(f"{label}: {exc}")
            print(f"  [{YELLOW}SKIP{RESET}] {label:<24} no such URL")
            continue
        check(label, url, anonymous)

    print(f"\n{BOLD}Application screens (as {admin.username}){RESET}")
    client = Client()
    client.force_login(admin)
    for label, name, kwargs in SCREENS:
        try:
            url = reverse(name, kwargs=kwargs)
        except NoReverseMatch as exc:
            skipped.append(f"{label}: {exc}")
            print(f"  [{YELLOW}SKIP{RESET}] {label:<24} no such URL ({exc})")
            continue
        check(label, url, client)

    print(f"\n{BOLD}API{RESET}")
    for path in API_PATHS:
        check(path, path, client)

    print(f"\n{BOLD}Admin{RESET}")
    check("Django admin", "/en/admin/", client)

    print(f"\n{BOLD}{'=' * 66}{RESET}")
    print(f"  {GREEN}{len(passed)} ok{RESET}   {RED if failed else GREY}{len(failed)} failed{RESET}   {GREY}{len(skipped)} skipped{RESET}")
    if failed:
        print(f"\n  {BOLD}Failures{RESET}")
        for label, url, reason in failed:
            print(f"    {RED}x{RESET} {label} ({url}) — {reason}")
    if skipped:
        print(f"\n  {BOLD}Skipped{RESET}")
        for item in skipped:
            print(f"    {YELLOW}-{RESET} {item}")
    print(f"{BOLD}{'=' * 66}{RESET}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
