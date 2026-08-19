"""Password validators specific to this product."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .bootstrap import BOOTSTRAP_PASSWORD, BOOTSTRAP_USERNAME


class RejectBootstrapPasswordValidator:
    """Refuse the documented first-run password, for anybody, forever.

    Django's ``CommonPasswordValidator`` already rejects ``admin`` today, but
    that list is a dependency that can change. The bootstrap contract promises
    that ``admin`` / ``admin`` is dead after the first change, and a promise
    that depends on somebody else's word list is not a promise. This validator
    states it directly.
    """

    def validate(self, password, user=None):
        if password is None:
            return
        if password.strip().lower() == BOOTSTRAP_PASSWORD:
            raise ValidationError(
                _(
                    "That is the documented first-run password. It cannot be "
                    "used as a real password."
                ),
                code="password_is_bootstrap_default",
            )
        username = (getattr(user, "get_username", lambda: "")() or "").strip().lower()
        if username == BOOTSTRAP_USERNAME and password.strip().lower() == BOOTSTRAP_USERNAME:
            raise ValidationError(
                _("The password must not repeat the username."),
                code="password_equals_username",
            )

    def get_help_text(self):
        return _("Your password cannot be the documented first-run password.")
