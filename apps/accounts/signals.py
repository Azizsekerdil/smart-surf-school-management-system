"""Login/logout tracking and audit hooks."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger("apps.accounts")


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):  # noqa: ARG001
    from .models import UserSession

    UserSession.objects.create(
        user=user,
        session_key=getattr(request.session, "session_key", "") or "",
        ip_address=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:400],
        was_successful=True,
    )
    user.__class__.objects.filter(pk=user.pk).update(last_seen_at=timezone.now())

    # Honour the user's saved interface language for this session.
    if request is not None and getattr(user, "language", None):
        request.session["_language"] = user.language

    logger.info(
        "login.success",
        extra={"user": user.get_username(), "role": user.role, "ip": _client_ip(request)},
    )


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):  # noqa: ARG001
    if user is None:
        return
    from .models import UserSession

    session_key = getattr(getattr(request, "session", None), "session_key", "") or ""
    queryset = UserSession.objects.filter(user=user, logout_at__isnull=True)
    if session_key:
        queryset = queryset.filter(session_key=session_key) or queryset
    queryset.update(logout_at=timezone.now())
    logger.info("login.logout", extra={"user": user.get_username()})


@receiver(user_login_failed)
def record_failed_login(sender, credentials, request=None, **kwargs):  # noqa: ARG001
    username = (credentials or {}).get("username", "")
    logger.warning(
        "login.failed",
        extra={"attempted_username": str(username)[:150], "ip": _client_ip(request)},
    )

    UserModel = get_user_model()
    from .models import UserSession

    user = UserModel.objects.filter(username__iexact=username).first() or (
        UserModel.objects.filter(email__iexact=username).first() if username else None
    )
    if user is not None:
        UserSession.objects.create(
            user=user,
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:400],
            was_successful=False,
            logout_at=timezone.now(),
        )
