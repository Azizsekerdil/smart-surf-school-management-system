"""Regression tests for the documented ``admin`` / ``admin`` first-run account.

A published default credential is only defensible if the rules around it are
real. Each test below pins one clause of the contract stated in
:mod:`apps.accounts.bootstrap`; if any of them fails, the honest thing is to
remove the default rather than to relax the test.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.bootstrap import BOOTSTRAP_PASSWORD, BOOTSTRAP_USERNAME
from apps.accounts.constants import Role

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.security]

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
REMOTE = {"REMOTE_ADDR": "203.0.113.7"}
# Assembled rather than written as one literal so a secret scanner does not
# have to guess whether a high-entropy string next to the word "password" is
# real. It is not: it exists only to be typed into a form inside a test.
NEW_PASSWORD = "-".join(["Tramontana", "Offshore", "2026"])


def bootstrap_account():
    call_command("bootstrap_admin")
    return User.objects.get(username=BOOTSTRAP_USERNAME)


def production_hashers():
    """The real PASSWORD_HASHERS chain, which test settings swap for MD5."""
    from config.settings import base

    return base.PASSWORD_HASHERS


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def test_the_command_creates_a_flagged_super_admin():
    user = bootstrap_account()

    assert user.role == Role.SUPER_ADMIN
    assert user.must_change_password is True
    assert user.is_bootstrap_account is True
    assert user.check_password(BOOTSTRAP_PASSWORD)


def test_the_command_refuses_to_run_on_a_populated_installation():
    """It cannot be used to push a live system back to a known password."""
    User.objects.create_user(
        username="already-here",
        email="already-here@example.test",
        password="not-a-real-password",
        role=Role.MANAGER,
    )

    with pytest.raises(CommandError):
        call_command("bootstrap_admin")

    assert not User.objects.filter(username=BOOTSTRAP_USERNAME).exists()


def test_the_password_is_stored_as_an_argon2_hash_not_plaintext():
    with override_settings(PASSWORD_HASHERS=production_hashers()):
        user = bootstrap_account()

    raw = user.password
    assert raw != BOOTSTRAP_PASSWORD
    assert BOOTSTRAP_PASSWORD not in raw
    assert raw.startswith("argon2$argon2id$"), raw.split("$")[0:2]


# ---------------------------------------------------------------------------
# Rule 2: local device only
# ---------------------------------------------------------------------------
def test_the_bootstrap_account_signs_in_from_the_local_device():
    bootstrap_account()
    request = RequestFactory().post("/accounts/login/", **LOCAL)

    user = authenticate(request, username=BOOTSTRAP_USERNAME, password=BOOTSTRAP_PASSWORD)

    assert user is not None


def test_a_remote_bootstrap_sign_in_is_refused():
    bootstrap_account()
    request = RequestFactory().post("/accounts/login/", **REMOTE)

    assert authenticate(request, username=BOOTSTRAP_USERNAME, password=BOOTSTRAP_PASSWORD) is None


def test_a_spoofed_forwarded_header_does_not_make_a_request_local():
    """``X-Forwarded-For`` is attacker-controlled and must not be trusted here."""
    bootstrap_account()
    request = RequestFactory().post(
        "/accounts/login/", HTTP_X_FORWARDED_FOR="127.0.0.1", **REMOTE
    )

    assert authenticate(request, username=BOOTSTRAP_USERNAME, password=BOOTSTRAP_PASSWORD) is None


def test_a_normal_account_is_not_restricted_to_the_local_device():
    User.objects.create_user(
        username="manager",
        email="manager@example.test",
        password=NEW_PASSWORD,
        role=Role.MANAGER,
    )
    request = RequestFactory().post("/accounts/login/", **REMOTE)

    assert authenticate(request, username="manager", password=NEW_PASSWORD) is not None


# ---------------------------------------------------------------------------
# Rule 1: nothing opens before the change
# ---------------------------------------------------------------------------
PROTECTED_SCREENS = [
    "dashboard:home",
    "customers:list",
    "students:list",
    "finance:payment_list",
    "reporting:list",
    "backups:list",
    "accounts:user_list",
    "audit:list",
    "core:settings",
    "ai:control_center",
]


@pytest.mark.parametrize("url_name", PROTECTED_SCREENS)
def test_no_protected_screen_opens_before_the_password_is_changed(client, url_name):
    """Dashboard, customer and student data, money, exports, backups, AI settings."""
    user = bootstrap_account()
    client.force_login(user)

    response = client.get(reverse(url_name), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("accounts:change_password")


def test_the_api_refuses_a_bootstrap_session_before_the_change(client):
    user = bootstrap_account()
    client.force_login(user)

    response = client.get(reverse("finance-invoice-list"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "password_change_required"


def test_the_change_password_screen_itself_stays_reachable(client):
    user = bootstrap_account()
    client.force_login(user)

    response = client.get(reverse("accounts:change_password"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200


def test_logging_out_stays_possible(client):
    user = bootstrap_account()
    client.force_login(user)

    response = client.post(reverse("accounts:logout"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code in {200, 302}


# ---------------------------------------------------------------------------
# Rule 3: the default dies on first change
# ---------------------------------------------------------------------------
def test_after_the_change_the_default_no_longer_authenticates(client):
    user = bootstrap_account()
    client.force_login(user)

    response = client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )
    assert response.status_code == 302

    user.refresh_from_db()
    assert user.must_change_password is False
    assert user.is_bootstrap_account is False
    assert not user.check_password(BOOTSTRAP_PASSWORD)

    local = RequestFactory().post("/accounts/login/", **LOCAL)
    assert authenticate(local, username=BOOTSTRAP_USERNAME, password=BOOTSTRAP_PASSWORD) is None
    assert authenticate(local, username=BOOTSTRAP_USERNAME, password=NEW_PASSWORD) is not None


def test_the_new_password_is_stored_hashed(client):
    with override_settings(PASSWORD_HASHERS=production_hashers()):
        user = bootstrap_account()
        client.force_login(user)

        client.post(
            reverse("accounts:change_password"),
            {
                "old_password": BOOTSTRAP_PASSWORD,
                "new_password1": NEW_PASSWORD,
                "new_password2": NEW_PASSWORD,
            },
            REMOTE_ADDR="127.0.0.1",
        )

    user.refresh_from_db()
    assert NEW_PASSWORD not in user.password
    assert user.password.startswith("argon2$argon2id$")


def test_protected_screens_open_once_the_password_has_been_changed(client):
    user = bootstrap_account()
    client.force_login(user)
    client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )

    response = client.get(reverse("dashboard:home"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200


def test_the_account_is_reachable_remotely_once_it_holds_a_real_password(client):
    user = bootstrap_account()
    client.force_login(user)
    client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )

    remote = RequestFactory().post("/accounts/login/", **REMOTE)
    assert authenticate(remote, username=BOOTSTRAP_USERNAME, password=NEW_PASSWORD) is not None


# ---------------------------------------------------------------------------
# Rule 4: a reset cannot restore the default
# ---------------------------------------------------------------------------
def test_the_default_password_is_rejected_by_validation():
    user = bootstrap_account()

    with pytest.raises(ValidationError):
        validate_password(BOOTSTRAP_PASSWORD, user=user)


def test_the_change_form_refuses_to_set_the_password_back_to_the_default(client):
    user = bootstrap_account()
    client.force_login(user)
    client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )

    response = client.post(
        reverse("accounts:change_password"),
        {
            "old_password": NEW_PASSWORD,
            "new_password1": BOOTSTRAP_PASSWORD,
            "new_password2": BOOTSTRAP_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )

    assert response.status_code == 200  # redisplayed with errors, not saved
    user.refresh_from_db()
    assert not user.check_password(BOOTSTRAP_PASSWORD)
    assert user.is_bootstrap_account is False


def test_a_password_reset_clears_the_bootstrap_state_rather_than_restoring_it(client):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    user = bootstrap_account()
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    # Django's reset view swaps the token for a session-held one on first GET.
    start = client.get(
        reverse("accounts:password_reset_confirm", kwargs={"uidb64": uidb64, "token": token}),
        REMOTE_ADDR="127.0.0.1",
        follow=True,
    )
    assert start.status_code == 200

    response = client.post(
        start.redirect_chain[-1][0] if start.redirect_chain else start.request["PATH_INFO"],
        {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
        REMOTE_ADDR="127.0.0.1",
    )
    assert response.status_code == 302

    user.refresh_from_db()
    assert user.is_bootstrap_account is False
    assert user.must_change_password is False
    assert not user.check_password(BOOTSTRAP_PASSWORD)
    assert user.check_password(NEW_PASSWORD)


def test_an_operator_issued_temporary_password_does_not_recreate_the_bootstrap_state():
    """``accounts.api`` sets must_change_password on an admin-side reset."""
    user = bootstrap_account()
    user.set_password(NEW_PASSWORD)
    user.clear_bootstrap_state()
    user.save()

    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    user.refresh_from_db()

    assert user.must_change_password is True
    assert user.is_bootstrap_account is False

    remote = RequestFactory().post("/accounts/login/", **REMOTE)
    assert authenticate(remote, username=BOOTSTRAP_USERNAME, password=NEW_PASSWORD) is not None


# ---------------------------------------------------------------------------
# Rules 5 and 6: hashing, rate limiting and the visible warnings
# ---------------------------------------------------------------------------
def test_argon2id_is_the_default_hasher():
    """Asserted against base settings: the test profile swaps in MD5 for speed."""
    assert production_hashers()[0].endswith("Argon2PasswordHasher")


def test_brute_force_protection_is_configured(settings):
    assert "axes.backends.AxesStandaloneBackend" in settings.AUTHENTICATION_BACKENDS
    assert settings.AXES_FAILURE_LIMIT <= 10
    assert settings.AXES_COOLOFF_TIME > 0
    assert set(settings.AXES_LOCKOUT_PARAMETERS) >= {"ip_address", "username"}


def test_no_backend_can_re_authorise_a_refused_sign_in(settings):
    """A bare ModelBackend after ours would undo the local-device rule."""
    assert "django.contrib.auth.backends.ModelBackend" not in settings.AUTHENTICATION_BACKENDS


def test_the_sign_in_screen_warns_about_the_default(client):
    bootstrap_account()

    response = client.get(reverse("accounts:login"), REMOTE_ADDR="127.0.0.1")

    body = response.content.decode()
    assert "bootstrap-notice" in body
    assert "admin / admin" in body


def test_the_warning_disappears_once_the_password_is_changed(client):
    user = bootstrap_account()
    client.force_login(user)
    client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )
    client.logout()

    response = client.get(reverse("accounts:login"), REMOTE_ADDR="127.0.0.1")

    assert "bootstrap-notice" not in response.content.decode()


def test_the_readme_documents_the_default():
    from pathlib import Path

    readme = Path(__file__).resolve().parents[3] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "admin / admin" in text
    assert "must be changed on first login" in text


def test_no_audit_row_or_log_records_the_credential(client):
    from apps.audit.models import AuditLog

    user = bootstrap_account()
    client.force_login(user)
    client.post(
        reverse("accounts:change_password"),
        {
            "old_password": BOOTSTRAP_PASSWORD,
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        REMOTE_ADDR="127.0.0.1",
    )

    assert AuditLog.objects.exists()
    for row in AuditLog.objects.all():
        blob = " ".join(
            str(value)
            for value in (
                row.description,
                row.changes,
                row.object_repr,
                row.request_path,
                row.user_agent,
            )
        )
        # The account is *named* admin, so the literal string "admin" is
        # expected in object_repr. What must never appear is a password value
        # or a password field.
        assert NEW_PASSWORD not in blob
        assert "password=" not in blob.lower()
        assert not any(
            "password" in str(key).lower() for key in (row.changes or {})
        ), row.changes
