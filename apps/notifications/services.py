"""Business rules for notification delivery.

The contract every other module relies on
-----------------------------------------
``notify()`` **never raises and never blocks the operation that called it.** A
booking must still be confirmed when the mail server is down, when the
recipient has muted the category, when the title is too long, or when the
notifications table is mid-migration. Everything in this module that is called
from a business path therefore swallows its own failures and logs them.

Delivery decisions live here, not in the models and not in the views:

* a muted category is silent on every channel;
* ``in_app_enabled=False`` stops the row being created at all — an empty bell
  menu is a promise, not a suggestion;
* e-mail is sent for warnings and errors by default, and quiet hours suppress
  it (but never suppress the in-app entry, which waits patiently in the list).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.enums import ACTIVE_BOOKING_STATUSES

from .models import (
    EMAIL_WORTHY_LEVELS,
    MAX_RENDERED_BODY,
    MAX_RENDERED_TITLE,
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationLevel,
    NotificationPreference,
    NotificationTemplate,
)

logger = logging.getLogger(__name__)

User = get_user_model()

#: A lesson reminder fires when the session starts inside this window. The beat
#: schedule runs the task every 10 minutes, so a 10-minute-wide window means
#: every lesson is caught exactly once even if one run is skipped.
REMINDER_LEAD_MIN_MINUTES = 30
REMINDER_LEAD_MAX_MINUTES = 40

#: Field names ``lessons.Lesson`` may use for its start moment. The lessons
#: module is written independently; probing keeps the reminder working without
#: a hard compile-time dependency on one spelling.
LESSON_START_FIELD_CANDIDATES = (
    "start_datetime",
    "start_at",
    "starts_at",
    "scheduled_start",
    "start",
)

#: Template code looked up before falling back to the built-in wording.
LESSON_REMINDER_TEMPLATE_CODE = "lesson-reminder"


# ---------------------------------------------------------------------------
# Core delivery
# ---------------------------------------------------------------------------
def notify(
    recipient,
    category: str,
    title: str,
    body: str = "",
    level: str = NotificationLevel.INFO,
    link_url: str = "",
    related=None,
    *,
    send_email: bool | None = None,
    preference: NotificationPreference | None = None,
) -> Notification | None:
    """Deliver one notification to one user.

    Returns the stored :class:`~apps.notifications.models.Notification`, or
    ``None`` when the user's preferences silenced it (or when anything at all
    went wrong — this function is a dead end for exceptions by design).

    ``send_email`` overrides the default policy: ``None`` means "e-mail
    warnings and errors only", ``True`` forces an attempt, ``False`` forbids it.
    ``preference`` lets a batch caller supply an already-loaded preference row
    so a broadcast costs one query instead of one per recipient.
    """
    try:
        if recipient is None or getattr(recipient, "pk", None) is None:
            return None
        if not getattr(recipient, "is_active", True):
            return None

        category = _valid_category(category)
        level = _valid_level(level)
        title = " ".join(str(title or "").split())[:MAX_RENDERED_TITLE]
        if not title:
            logger.warning("Refusing to send a notification without a title")
            return None
        body = str(body or "")[:MAX_RENDERED_BODY]

        if preference is None:
            preference = NotificationPreference.for_user(recipient)
        wants_email = level in EMAIL_WORTHY_LEVELS if send_email is None else bool(send_email)

        in_app_allowed = preference.allows(category, NotificationChannel.IN_APP)
        email_allowed = wants_email and preference.allows(category, NotificationChannel.EMAIL)

        if not in_app_allowed and not email_allowed:
            return None

        notification: Notification | None = None
        if in_app_allowed:
            notification = Notification(
                recipient=recipient,
                category=category,
                level=level,
                title=title,
                body=body,
                link_url=_safe_link(link_url),
            )
            notification.set_related(related)
            try:
                # ``recipient`` is excluded because validating a foreign key
                # costs an extra SELECT per notification, and the caller handed
                # us the object — it demonstrably exists.
                notification.full_clean(exclude={"recipient"}, validate_unique=False)
            except ValidationError:
                logger.warning(
                    "Notification failed validation and was not stored",
                    extra={"category": category, "recipient_id": recipient.pk},
                    exc_info=True,
                )
                return None
            notification.save()

        if email_allowed:
            if notification is not None:
                _dispatch_email(notification)
            else:
                # In-app is off but e-mail is on: send directly, nothing to track.
                send_email_message(
                    recipient, title, body, _safe_link(link_url)
                )

        return notification
    except Exception:  # noqa: BLE001 - notification must never break the caller
        logger.exception("Failed to deliver notification")
        return None


def notify_many(
    recipients, category: str, title: str, body: str = "", **kwargs
) -> list[Notification]:
    """Deliver the same message to several users; silenced users are skipped.

    Preferences are loaded in a single query, so a broadcast to forty
    instructors is two queries plus one insert each — not eighty.
    """
    kwargs.pop("preference", None)
    unique: dict[int, object] = {}
    for recipient in recipients or ():
        pk = getattr(recipient, "pk", None)
        if pk is not None and pk not in unique:
            unique[pk] = recipient
    if not unique:
        return []

    preferences: dict[int, NotificationPreference] = {}
    try:
        preferences = {
            preference.user_id: preference
            for preference in NotificationPreference.objects.filter(user_id__in=unique)
        }
    except DatabaseError:
        logger.exception("Could not preload notification preferences")

    delivered: list[Notification] = []
    for pk, recipient in unique.items():
        result = notify(
            recipient,
            category,
            title,
            body,
            preference=preferences.get(pk) or NotificationPreference(user=recipient),
            **kwargs,
        )
        if result is not None:
            delivered.append(result)
    return delivered


def notify_role(
    role,
    category: str,
    title: str,
    body: str = "",
    level: str = NotificationLevel.INFO,
    link_url: str = "",
    related=None,
    *,
    send_email: bool | None = None,
    exclude_user=None,
) -> list[Notification]:
    """Notify every **active** user holding *role*.

    *role* accepts a single role value or an iterable of them, so a safety
    warning can reach instructors and lifeguards in one call.
    """
    roles = [role] if isinstance(role, str) else [str(r) for r in (role or ())]
    if not roles:
        return []
    try:
        queryset = User.objects.filter(role__in=roles, is_active=True)
        if exclude_user is not None and getattr(exclude_user, "pk", None):
            queryset = queryset.exclude(pk=exclude_user.pk)
        recipients = list(queryset)
    except DatabaseError:
        logger.exception("Could not resolve recipients for role notification")
        return []

    return notify_many(
        recipients,
        category,
        title,
        body,
        level=level,
        link_url=link_url,
        related=related,
        send_email=send_email,
    )


def notify_from_template(
    code: str,
    recipient,
    context: dict | None = None,
    *,
    level: str | None = None,
    link_url: str = "",
    related=None,
    send_email: bool | None = None,
) -> Notification | None:
    """Render the stored template *code* in the recipient's language and send it."""
    try:
        template = NotificationTemplate.objects.filter(code=code, is_active=True).first()
    except DatabaseError:
        logger.exception("Notification template lookup failed", extra={"code": code})
        return None

    if template is None:
        logger.warning("No active notification template with code %r", code)
        return None

    language = getattr(recipient, "language", None) or settings.LANGUAGE_CODE
    title, body = template.render(language, context)
    return notify(
        recipient,
        template.category,
        title,
        body,
        level=level or template.level,
        link_url=link_url,
        related=related,
        send_email=send_email,
    )


# ---------------------------------------------------------------------------
# Read state
# ---------------------------------------------------------------------------
def mark_read(notification: Notification, user) -> bool:
    """Mark *notification* read on behalf of *user*.

    Raises :class:`PermissionDenied` if the notification belongs to somebody
    else — reading another person's inbox is never an accident worth tolerating.
    """
    _assert_owner(notification, user)
    return notification.mark_read()


def mark_unread(notification: Notification, user) -> bool:
    """Return a notification to the unread state (undo for a mis-click)."""
    _assert_owner(notification, user)
    return notification.mark_unread()


def mark_all_read(user, *, category: str = "") -> int:
    """Mark every unread notification of *user* read. Returns the row count."""
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    queryset = Notification.objects.filter(recipient=user, is_read=False)
    if category:
        queryset = queryset.filter(category=category)
    return queryset.update(is_read=True, read_at=timezone.now(), updated_at=timezone.now())


def _assert_owner(notification: Notification, user) -> None:
    if notification is None:
        raise PermissionDenied(_("Notification not found."))
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied(_("Authentication is required."))
    if notification.recipient_id != user.pk:
        raise PermissionDenied(_("This notification belongs to another user."))


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
def send_email_message(recipient, title: str, body: str, link_url: str = "") -> bool:
    """Send one plain-text notification e-mail. Returns ``True`` when accepted."""
    address = (getattr(recipient, "email", "") or "").strip()
    if not address:
        return False

    school = settings.SCHOOL["NAME"]
    lines = [body or title]
    if link_url:
        lines += ["", _("Open: %(link)s") % {"link": link_url}]
    lines += ["", _("— %(school)s") % {"school": school}]

    try:
        sent = send_mail(
            subject=f"[{school}] {title}",
            message="\n".join(str(line) for line in lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[address],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 - a dead mail server must not break the app
        logger.exception("Notification e-mail could not be sent")
        return False
    return bool(sent)


def send_notification_email(notification_id: int) -> bool:
    """Send the e-mail copy of a stored notification and stamp ``is_emailed``."""
    notification = Notification.objects.filter(pk=notification_id).select_related("recipient").first()
    if notification is None or notification.is_emailed:
        return False
    delivered = send_email_message(
        notification.recipient,
        notification.title,
        notification.body,
        notification.link_url,
    )
    if delivered:
        notification.mark_emailed()
    return delivered


def _dispatch_email(notification: Notification) -> None:
    """Hand the e-mail to Celery, falling back to an inline send.

    ``transaction.on_commit`` matters: with a real broker the worker can pick
    the task up before the surrounding transaction commits and would then find
    no row.
    """

    def _send() -> None:
        try:
            # Imported here: tasks.py imports this module.
            from . import tasks  # noqa: PLC0415

            tasks.deliver_notification_email.delay(notification.pk)
        except Exception:  # noqa: BLE001 - no broker: do it here and now
            logger.debug("Celery unavailable, sending notification e-mail inline")
            send_notification_email(notification.pk)

    transaction.on_commit(_send)


# ---------------------------------------------------------------------------
# Lesson reminders
# ---------------------------------------------------------------------------
def send_lesson_reminders(*, now=None) -> int:
    """Remind everyone attached to a lesson starting in 30–40 minutes.

    One reminder per booking, made idempotent by ``Booking.reminder_sent`` so a
    retried Celery run cannot double-message a customer. The instructor gets a
    single reminder per lesson, deduplicated through the notification's soft
    reference to that lesson.

    Returns the number of notifications created.
    """
    now = now or timezone.now()
    window_start = now + timedelta(minutes=REMINDER_LEAD_MIN_MINUTES)
    window_end = now + timedelta(minutes=REMINDER_LEAD_MAX_MINUTES)

    try:
        booking_model = django_apps.get_model("bookings", "Booking")
        lesson_model = django_apps.get_model("lessons", "Lesson")
    except LookupError:
        logger.info("Lesson reminders skipped: bookings/lessons are not installed")
        return 0

    booking_fields = {f.name for f in booking_model._meta.get_fields()}
    if "lesson" not in booking_fields or "reminder_sent" not in booking_fields:
        logger.error(
            "Lesson reminders skipped: bookings.Booking needs 'lesson' and 'reminder_sent'"
        )
        return 0

    start_field = _lesson_start_field(lesson_model)
    if start_field is None:
        logger.error("Lesson reminders skipped: no start datetime field on lessons.Lesson")
        return 0

    lookup = f"lesson__{start_field}"
    try:
        queryset = booking_model.objects.filter(
            reminder_sent=False,
            **{f"{lookup}__gte": window_start, f"{lookup}__lte": window_end},
        )
        if "status" in booking_fields:
            queryset = queryset.filter(status__in=list(ACTIVE_BOOKING_STATUSES))
        related = [name for name in ("lesson", "customer", "student") if name in booking_fields]
        if related:
            queryset = queryset.select_related(*related)
        bookings = list(queryset[:500])
    except DatabaseError:
        logger.exception("Lesson reminder query failed")
        return 0

    created = 0
    instructors_reminded: set[int] = set()

    for booking in bookings:
        # Claim first, then send. A conditional UPDATE is the only thing that
        # keeps two concurrent workers — or a retried task — from messaging the
        # same customer twice; losing a reminder to a crash between the two
        # steps is the cheaper failure.
        try:
            claimed = booking_model.objects.filter(pk=booking.pk, reminder_sent=False).update(
                reminder_sent=True
            )
        except DatabaseError:
            logger.exception("Could not claim booking reminder", extra={"booking_id": booking.pk})
            continue
        if not claimed:
            continue

        try:
            created += _remind_one_booking(booking, start_field, now, instructors_reminded)
        except Exception:  # noqa: BLE001 - one bad booking must not stop the batch
            logger.exception("Lesson reminder failed", extra={"booking_id": booking.pk})

    if created:
        logger.info("Sent %s lesson reminder(s)", created)
    return created


def _remind_one_booking(booking, start_field: str, now, instructors_reminded: set[int]) -> int:
    """Notify the people attached to *booking*. Returns the number sent."""
    lesson = getattr(booking, "lesson", None)
    if lesson is None:
        return 0

    starts_at = getattr(lesson, start_field, None)
    if starts_at is None:
        return 0

    minutes = max(1, int((starts_at - now).total_seconds() // 60))
    local_start = timezone.localtime(starts_at)
    link = _booking_link(booking)

    title = _("Lesson starts in %(minutes)s minutes") % {"minutes": minutes}
    body = _("%(lesson)s starts at %(time)s.") % {
        "lesson": str(lesson),
        "time": local_start.strftime("%H:%M"),
    }
    spot = getattr(lesson, "spot", None) or getattr(lesson, "location", None)
    if spot is not None:
        body = _("%(body)s Meeting point: %(spot)s.") % {"body": body, "spot": str(spot)}

    sent = 0
    for user in _booking_recipients(booking):
        result = notify(
            user,
            NotificationCategory.LESSON_REMINDER,
            title,
            body,
            level=NotificationLevel.INFO,
            link_url=link,
            related=booking,
        )
        if result is not None:
            sent += 1

    sent += _remind_instructor(lesson, minutes, local_start, instructors_reminded)
    return sent


def _remind_instructor(lesson, minutes: int, local_start, already: set[int]) -> int:
    """One reminder per lesson for the coach who is running it."""
    instructor = getattr(lesson, "instructor", None)
    user = getattr(instructor, "user", None) if instructor is not None else None
    if user is None or getattr(user, "pk", None) is None:
        return 0
    if user.pk in already:
        return 0
    already.add(user.pk)

    try:
        if Notification.objects.about(lesson).filter(
            recipient=user, category=NotificationCategory.LESSON_REMINDER
        ).exists():
            return 0
    except DatabaseError:
        logger.exception("Instructor reminder dedupe check failed")
        return 0

    result = notify(
        user,
        NotificationCategory.LESSON_REMINDER,
        _("Your lesson starts in %(minutes)s minutes") % {"minutes": minutes},
        _("%(lesson)s at %(time)s.") % {"lesson": str(lesson), "time": local_start.strftime("%H:%M")},
        level=NotificationLevel.INFO,
        link_url=_lesson_link(lesson),
        related=lesson,
    )
    return 1 if result is not None else 0


def _booking_recipients(booking) -> list:
    """The user accounts behind a booking's customer and student, if any."""
    users = []
    for attribute in ("customer", "student"):
        holder = getattr(booking, attribute, None)
        user = getattr(holder, "user", None) if holder is not None else None
        if user is not None and getattr(user, "pk", None) and getattr(user, "is_active", True):
            users.append(user)
    # A customer booking for themselves appears twice.
    unique: dict[int, object] = {u.pk: u for u in users}
    return list(unique.values())


def _lesson_start_field(model) -> str | None:
    """Find the ``DateTimeField`` that holds a lesson's start moment."""
    fields = {
        field.name: field
        for field in model._meta.get_fields()
        if hasattr(field, "get_internal_type")
    }
    for name in LESSON_START_FIELD_CANDIDATES:
        field = fields.get(name)
        if field is not None and field.get_internal_type() == "DateTimeField":
            return name
    for name, field in fields.items():
        if "start" in name and field.get_internal_type() == "DateTimeField":
            return name
    return None


def _booking_link(booking) -> str:
    try:
        return reverse("bookings:detail", kwargs={"pk": booking.pk})
    except NoReverseMatch:
        return ""


def _lesson_link(lesson) -> str:
    try:
        return reverse("lessons:detail", kwargs={"pk": lesson.pk})
    except NoReverseMatch:
        return ""


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def purge_old_notifications(*, days: int = 180) -> int:
    """Soft-delete read notifications older than *days*. Returns the row count.

    Unread notifications are never purged: something nobody has looked at is
    still owed to its recipient.
    """
    days = max(7, int(days))
    cutoff = timezone.now() - timedelta(days=days)
    try:
        # ``SoftDeleteQuerySet.delete()`` returns the number of rows flagged.
        return int(Notification.objects.filter(is_read=True, created_at__lt=cutoff).delete() or 0)
    except DatabaseError:
        logger.exception("Notification purge failed")
        return 0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _valid_category(value) -> str:
    value = str(value or "")
    return value if value in NotificationCategory.values else NotificationCategory.SYSTEM


def _valid_level(value) -> str:
    value = str(value or "")
    return value if value in NotificationLevel.values else NotificationLevel.INFO


def _safe_link(link_url: str) -> str:
    """Keep only same-site relative paths.

    A notification link is followed by a redirect view, so an absolute URL
    arriving from a caller (or, one day, from an integration) would turn the
    bell menu into an open-redirect gadget.
    """
    link = str(link_url or "").strip()
    if not link:
        return ""
    if not link.startswith("/") or link.startswith("//") or "\\" in link:
        return ""
    return link[:500]
