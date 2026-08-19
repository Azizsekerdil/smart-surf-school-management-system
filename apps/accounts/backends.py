"""Authentication backends."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from .bootstrap import is_local_request

UserModel = get_user_model()
logger = logging.getLogger("apps.accounts")


class EmailOrUsernameModelBackend(ModelBackend):
    """Allow signing in with either the username or the e-mail address.

    Reception staff know customers by e-mail; instructors prefer short
    usernames. Supporting both removes a common support burden.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get("email")
        if identifier is None or password is None:
            return None

        try:
            user = UserModel.objects.get(
                Q(username__iexact=identifier) | Q(email__iexact=identifier.strip())
            )
        except UserModel.DoesNotExist:
            # Run the default hasher anyway so timing does not reveal whether
            # the account exists.
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            user = (
                UserModel.objects.filter(
                    Q(username__iexact=identifier) | Q(email__iexact=identifier.strip())
                )
                .order_by("id")
                .first()
            )
            if user is None:
                return None

        if not (user.check_password(password) and self.user_can_authenticate(user)):
            return None

        if getattr(user, "is_bootstrap_account", False) and not is_local_request(request):
            # The documented first-run credential is published, so it must not
            # be usable across a network. Refused here rather than in the login
            # view so token endpoints, the Django admin and any future entry
            # point inherit the rule.
            logger.warning(
                "Refused a remote sign-in with the first-run bootstrap account."
            )
            return None

        return user
