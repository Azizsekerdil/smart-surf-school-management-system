"""Read queries for the notification screens.

Kept out of the views so the list page, the topbar dropdown and the REST API
all filter identically — a notification that is invisible in one place must be
invisible in the others.
"""

from __future__ import annotations

from django.db.models import Count, Q

from .models import Notification, NotificationCategory

#: How many entries the topbar dropdown shows.
DROPDOWN_LIMIT = 10


def notifications_for(user):
    """Every notification addressed to *user*, newest first.

    There is deliberately no "see everyone's notifications" query: a manager
    has no business reading an instructor's inbox, so no capability unlocks it.
    """
    return Notification.objects.for_user(user).order_by("-created_at", "-id")


def filtered_notifications(user, *, unread_only: bool = False, category: str = "", level: str = "", search: str = ""):
    """The list screen's queryset, honouring the four filters it exposes."""
    queryset = notifications_for(user)
    if unread_only:
        queryset = queryset.filter(is_read=False)
    if category in NotificationCategory.values:
        queryset = queryset.filter(category=category)
    if level:
        queryset = queryset.filter(level=level)
    search = (search or "").strip()
    if search:
        queryset = queryset.filter(Q(title__icontains=search) | Q(body__icontains=search))
    return queryset


def recent_for(user, limit: int = DROPDOWN_LIMIT):
    """The newest *limit* notifications — what the bell menu shows."""
    return list(notifications_for(user)[:limit])


def unread_count(user) -> int:
    """One indexed COUNT. Used by the API and by the HTMX badge refresh."""
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(recipient=user, is_read=False).count()


def unread_counts_by_category(user) -> dict[str, int]:
    """``{category: unread}`` for the filter chips on the list screen."""
    rows = (
        Notification.objects.for_user(user)
        .filter(is_read=False)
        .values("category")
        .annotate(total=Count("id"))
    )
    return {row["category"]: row["total"] for row in rows}
