"""The single-use ``admin`` / ``admin`` first-run credential.

A fresh install has to be reachable by *somebody*, and asking an operator to
run ``createsuperuser`` from a terminal before they can see the product is a
real barrier. This module makes the documented default safe instead of
pretending it is secret. The password is printed in the README, on the console
and on the sign-in screen — it is a **bootstrap token, not a secret** — and the
rules below are what stop it from being a back door.

The contract, all of which is enforced in code and asserted in
``apps/accounts/tests/test_bootstrap_admin.py``:

1. **Nothing is reachable until the password is changed.** While
   ``must_change_password`` is set, :class:`apps.accounts.middleware.
   ForcePasswordChangeMiddleware` redirects every HTML request to the
   change-password screen and answers every API request with 403. That covers
   the dashboard, customer and student records, finance, exports, backups and
   the AI settings without listing them.
2. **Bootstrap sign-in is local-device only.** While the account still holds
   the default, authentication is refused unless the request arrives from the
   loopback interface. A bootstrap install exposed on ``0.0.0.0`` cannot be
   taken over from the network.
3. **The default dies on first change.** Changing the password clears the
   bootstrap flag permanently, and
   :class:`apps.accounts.validators.RejectBootstrapPasswordValidator` refuses
   to set it back to ``admin`` afterwards.
4. **A password reset cannot restore it.** Reset goes through the same
   validator and the same "clear the flag" path, so the default never returns.
5. **The stored form is a hash**, never the plaintext: Argon2id first in
   ``PASSWORD_HASHERS`` (see ``config/settings/base.py``).
6. **Failed attempts are rate limited** by django-axes (per IP *and* per
   username, with a cool-off), and every bootstrap event is written to the
   audit log **without** the credential.

The command that creates the account is ``manage.py bootstrap_admin``. It
refuses to run once any user exists, so it cannot be used to reset a live
system back to a known password.
"""

from __future__ import annotations

import ipaddress

from django.utils.translation import gettext_lazy as _

#: Documented first-run username.
BOOTSTRAP_USERNAME = "admin"
#: Documented first-run password. Public by design — see the module docstring.
BOOTSTRAP_PASSWORD = "admin"  # noqa: S105 - documented non-secret, see the surrounding comment  # nosec B105

#: Shown on the console, in the README and on the sign-in screen.
BOOTSTRAP_WARNING = _(
    "First run: sign in as admin / admin from this device. "
    "You must choose a new password before anything else opens, "
    "and admin / admin stops working the moment you do."
)


def is_local_request(request) -> bool:
    """True when the request came from the machine the server runs on.

    Only ``REMOTE_ADDR`` is consulted. ``X-Forwarded-For`` is deliberately
    ignored: it is attacker-controlled unless a trusted proxy rewrites it, and
    treating it as authoritative here would hand the bootstrap account to
    anyone who can set a header.
    """
    if request is None:
        return False
    remote = (request.META or {}).get("REMOTE_ADDR", "")
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote.strip())
    except ValueError:
        return False
    return address.is_loopback
