"""Template context: the unread badge on the topbar bell.

This runs on **every** rendered page, including the 500 error page, so it obeys
three rules:

1. exactly one ``COUNT(*)`` query, served by the
   ``(recipient, is_read, -created_at)`` index;
2. the result is memoised on the request object, because a page that renders
   the bell in the topbar *and* in a mobile menu must not pay twice;
3. any failure — anonymous visitor, unmigrated database, dead connection while
   rendering the error page — degrades to ``0`` instead of raising.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError

from .models import Notification

logger = logging.getLogger(__name__)

#: Attribute used to memoise the count for the duration of one request.
_REQUEST_CACHE_ATTR = "_surf_unread_notification_count"


def unread_notifications(request) -> dict:
    """Return ``{"unread_notification_count": int}`` for the current user."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"unread_notification_count": 0}

    count = getattr(request, _REQUEST_CACHE_ATTR, None)
    if count is None:
        try:
            count = Notification.objects.filter(recipient=user, is_read=False).count()
        except DatabaseError:
            # The table may not exist yet (fresh checkout, mid-migration).
            logger.debug("Unread notification count unavailable", exc_info=True)
            count = 0
        try:
            setattr(request, _REQUEST_CACHE_ATTR, count)
        except AttributeError:  # pragma: no cover - exotic request objects
            pass

    return {"unread_notification_count": count}


def invalidate_unread_cache(request) -> None:
    """Drop the memoised count after a view changed the read state."""
    if request is not None and hasattr(request, _REQUEST_CACHE_ATTR):
        try:
            delattr(request, _REQUEST_CACHE_ATTR)
        except AttributeError:  # pragma: no cover
            pass
