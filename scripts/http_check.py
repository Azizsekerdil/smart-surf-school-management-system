#!/usr/bin/env python
"""Fetch key screens from a *running* server over real HTTP.

The Django test client instruments template rendering and deep-copies the
context on every render, which can exhaust the stack on pages that include a
partial inside a long loop. That is an artefact of the harness, not of the
application — so this script verifies the same pages the way a browser would.

    .\\.venv\\Scripts\\python.exe scripts\\http_check.py
    .\\.venv\\Scripts\\python.exe scripts\\http_check.py --base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import httpx

PATHS = [
    "/api/health/",
    "/tr/",
    "/tr/search/?q=test",
    "/tr/customers/",
    "/tr/students/",
    "/tr/instructors/",
    "/tr/lessons/",
    "/tr/bookings/",
    "/tr/surf-camps/",
    "/tr/equipment/",
    "/tr/rentals/",
    "/tr/maintenance/",
    "/tr/locations/",
    "/tr/surf-conditions/",
    "/tr/safety/",
    "/tr/finance/",
    "/tr/pos/",
    "/tr/analytics/",
    "/tr/reports/",
    "/tr/crm/",
    "/tr/notifications/",
    "/tr/backups/",
    "/tr/audit/",
    "/tr/settings/",
    "/tr/accounts/users/",
    "/tr/accounts/roles/",
    "/tr/accounts/profile/",
    "/tr/ai/",
    "/tr/ai/control-center/",
    "/tr/ai/usage/",
    "/tr/ai/knowledge/",
    "/tr/ai-terminal/",
    "/tr/ai-terminal/policy/",
    "/tr/help/",
    "/tr/training/",
    "/tr/onboarding/",
    "/en/",
    "/en/training/",
    "/en/admin/",
    "/api/docs/",
]

GREEN, RED, GREY, RESET = "\033[92m", "\033[91m", "\033[90m", "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    # No default: a password must be supplied explicitly or through the
    # environment, so this script never carries a credential.
    parser.add_argument(
        "--password",
        default=os.environ.get("HTTP_CHECK_PASSWORD", ""),
        help="Sign-in password. Defaults to $HTTP_CHECK_PASSWORD.",
    )
    options = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with httpx.Client(base_url=options.base, follow_redirects=True, timeout=60) as client:
        login_url = "/tr/accounts/login/"
        page = client.get(login_url)
        token = ""
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        if match:
            token = match.group(1)

        response = client.post(
            login_url,
            data={
                "username": options.username,
                "password": options.password,
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": options.base + login_url},
        )
        signed_in = "sessionid" in client.cookies
        print(f"Sign-in: {'OK' if signed_in else 'FAILED'} (HTTP {response.status_code})")
        if not signed_in:
            print(f"{RED}Could not sign in — check the credentials.{RESET}")
            return 2

        ok = bad = 0
        failures: list[tuple[str, str]] = []
        for path in PATHS:
            try:
                result = client.get(path)
            except httpx.HTTPError as exc:
                bad += 1
                failures.append((path, f"{type(exc).__name__}"))
                print(f"  [{RED}FAIL{RESET}] {path:<34} {type(exc).__name__}")
                continue

            size = len(result.content)
            if result.status_code == 200:
                ok += 1
                print(f"  [{GREEN}  OK{RESET}] {path:<34} {GREY}200  {size:>7,} bytes{RESET}")
            else:
                bad += 1
                failures.append((path, f"HTTP {result.status_code}"))
                print(f"  [{RED}FAIL{RESET}] {path:<34} {result.status_code}")

        print(f"\n  {GREEN}{ok} ok{RESET}   {RED if bad else GREY}{bad} failed{RESET}")
        for path, reason in failures:
            print(f"    x {path} - {reason}")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
