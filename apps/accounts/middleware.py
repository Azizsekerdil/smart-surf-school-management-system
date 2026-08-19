"""Middleware that makes ``must_change_password`` mean something.

The flag existed on the user model and was set in two places, but nothing ever
read it: a user carrying it could browse the entire product. This middleware is
the enforcement half.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, resolve, reverse
from django.utils.translation import gettext as _

#: URL names that must stay reachable while a password change is outstanding —
#: otherwise the user could not complete it, or log out.
ALLOWED_URL_NAMES = frozenset(
    {
        "accounts:change_password",
        "accounts:logout",
        "accounts:login",
        "accounts:lockout",
        "accounts:password_reset",
        "accounts:password_reset_done",
        "accounts:password_reset_confirm",
        "accounts:password_reset_complete",
        "core:health",
        "set_language",
    }
)

#: Path prefixes that carry no data of their own.
ALLOWED_PREFIXES = ("/static/", "/media/")


class ForcePasswordChangeMiddleware:
    """Hold a user on the change-password screen until they change it.

    Applied to *every* authenticated request, not to a hand-maintained list of
    sensitive views. That is deliberate: an allowlist of four URLs cannot fall
    out of date the way a denylist of two hundred screens would, so a module
    added next year is protected without anybody remembering to protect it.

    API requests get 403 with a machine-readable reason rather than a redirect,
    so a token client is told why instead of receiving a login page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is None
            or not user.is_authenticated
            or not getattr(user, "must_change_password", False)
            or self._is_allowed(request)
        ):
            return self.get_response(request)

        if self._is_api(request):
            return JsonResponse(
                {
                    "error": {
                        "type": "password_change_required",
                        "message": _(
                            "This account must set a new password before the API "
                            "will answer."
                        ),
                        "detail": {},
                    }
                },
                status=403,
            )

        messages.warning(
            request,
            _("Choose a new password to finish setting up this account."),
        )
        return redirect("accounts:change_password")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _is_api(request) -> bool:
        return request.path.startswith("/api/")

    def _is_allowed(self, request) -> bool:
        path = request.path
        if any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            return True
        static_url = getattr(settings, "STATIC_URL", "") or ""
        if static_url and path.startswith(static_url):
            return True
        try:
            match = resolve(path)
        except Exception:  # noqa: BLE001 - an unmatched URL is not an exemption
            return False
        return match.view_name in ALLOWED_URL_NAMES

    @staticmethod
    def change_password_url() -> str:
        try:
            return reverse("accounts:change_password")
        except NoReverseMatch:  # pragma: no cover - the URL is always installed
            return "/"
