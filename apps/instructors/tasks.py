"""Background jobs for the instructor module.

The certification check runs every morning (see ``config/celery.py``). It must
never crash the beat worker: notifications are optional infrastructure, and this
app has to keep working on an installation where the notifications module has no
templates, no rows, or not even its tables yet.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.utils.translation import gettext as _

from apps.accounts.constants import Role
from apps.audit.models import AuditAction
from apps.audit.services import record_system_event

from . import services
from .models import EXPIRY_WARNING_DAYS

logger = logging.getLogger(__name__)

#: Roles that must be told when a coach's paperwork is about to lapse.
NOTIFY_ROLES = (
    Role.SUPER_ADMIN,
    Role.MANAGER,
    Role.OPERATIONS_MANAGER,
    Role.HEAD_INSTRUCTOR,
)

#: Candidate field names on ``notifications.Notification``, probed in order.
RECIPIENT_FIELDS = ("recipient", "user", "to_user")
TITLE_FIELDS = ("title", "subject", "heading")
BODY_FIELDS = ("body", "message", "text", "content")
LEVEL_FIELDS = ("level", "severity", "priority")
CATEGORY_FIELDS = ("category", "kind", "notification_type", "type")


def _field_names(model) -> set[str]:
    return {field.name for field in model._meta.get_fields()}


def _first(names: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in names), None)


def _notification_model():
    """The notifications model, or ``None`` when that app is not usable yet."""
    try:
        return apps.get_model("notifications", "Notification")
    except Exception:  # noqa: BLE001 - LookupError / AppRegistryNotReady
        return None


def _notification_recipients(instructor):
    """The instructor plus every manager who can act on the warning."""
    user_model = get_user_model()
    recipients = list(
        user_model.objects.filter(role__in=list(NOTIFY_ROLES), is_active=True)
    )
    if instructor.user_id and instructor.user.is_active:
        recipients.append(instructor.user)
    unique: dict[int, object] = {user.pk: user for user in recipients}
    return list(unique.values())


def _build_message(entry) -> tuple[str, str]:
    instructor = entry["instructor"]
    names = ", ".join(
        f"{certification.name} ({certification.expires_on:%d.%m.%Y})"
        for certification in entry["certifications"]
    )
    if entry["has_expired"]:
        title = _("Expired certification: %(name)s") % {"name": instructor.full_name}
    else:
        title = _("Certification expiring: %(name)s") % {"name": instructor.full_name}
    body = _(
        "%(instructor)s (%(code)s) has certification requiring attention: %(list)s. "
        "An instructor without a current rescue or first-aid award must not be "
        "assigned to lessons."
    ) % {
        "instructor": instructor.full_name,
        "code": instructor.instructor_code,
        "list": names,
    }
    return str(title), str(body)


def _create_notifications(entries) -> int:
    """Write one notification per recipient per instructor. Best effort."""
    model = _notification_model()
    if model is None:
        logger.info("Notifications app unavailable; certification warnings logged only.")
        return 0

    names = _field_names(model)
    recipient_field = _first(names, RECIPIENT_FIELDS)
    title_field = _first(names, TITLE_FIELDS)
    body_field = _first(names, BODY_FIELDS)
    if not (recipient_field and body_field):
        logger.info("Notification model has no recognisable recipient/body fields.")
        return 0

    level_field = _first(names, LEVEL_FIELDS)
    category_field = _first(names, CATEGORY_FIELDS)

    created = 0
    for entry in entries:
        title, body = _build_message(entry)
        for recipient in _notification_recipients(entry["instructor"]):
            payload = {recipient_field: recipient, body_field: body}
            if title_field:
                payload[title_field] = title[:200]
            if level_field:
                payload[level_field] = "warning" if not entry["has_expired"] else "critical"
            if category_field:
                payload[category_field] = "certification"
            try:
                model.objects.create(**payload)
                created += 1
            except (DatabaseError, TypeError, ValueError) as exc:
                logger.warning("Could not create certification notification: %s", exc)
                return created
    return created


@shared_task(ignore_result=True)
def check_certification_expiry(days: int = EXPIRY_WARNING_DAYS) -> dict:
    """Warn about certifications expiring within *days* days.

    Returns a summary so the result is meaningful when Celery runs eagerly and
    when an operator triggers the task by hand.
    """
    entries = services.check_certification_expiry(days)
    if not entries:
        logger.info("Certification check: nothing expiring within %s days.", days)
        return {"instructors": 0, "certifications": 0, "notifications": 0}

    certification_count = sum(len(entry["certifications"]) for entry in entries)
    for entry in entries:
        logger.warning(
            "Certification attention required: %s (%s) — soonest expiry %s",
            entry["instructor"].full_name,
            entry["instructor"].instructor_code,
            entry["soonest_expiry"],
        )

    notifications = 0
    try:
        notifications = _create_notifications(entries)
    except Exception as exc:  # noqa: BLE001 - the warning must survive a broken sink
        logger.exception("Certification notification dispatch failed: %s", exc)

    record_system_event(
        AuditAction.SYSTEM,
        description=str(
            _("Certification expiry check: %(instructors)s instructor(s), %(certs)s certificate(s)")
            % {"instructors": len(entries), "certs": certification_count}
        ),
    )
    return {
        "instructors": len(entries),
        "certifications": certification_count,
        "notifications": notifications,
    }


@shared_task(ignore_result=True)
def refresh_instructor_statistics() -> dict:
    """Rebuild rating and lesson counters for every active instructor."""
    from .models import Instructor

    updated = 0
    for instructor in Instructor.objects.filter(is_active=True):
        try:
            services.refresh_instructor_statistics(instructor)
            updated += 1
        except DatabaseError as exc:
            logger.warning("Could not refresh statistics for %s: %s", instructor.pk, exc)
    return {"instructors": updated}
