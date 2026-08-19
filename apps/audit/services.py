"""Public API for writing audit entries.

All call sites use :func:`record_audit`. It never raises: a failure to write an
audit row must not break the business operation that triggered it, but it is
always logged so the gap is visible.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.logging import redact_mapping

from .middleware import AuditContext, get_audit_context
from .models import AuditAction, AuditLog, AuditSource

logger = logging.getLogger("apps.audit")

#: Actions that must always be retained for compliance.
SENSITIVE_ACTIONS = {
    AuditAction.PAYMENT,
    AuditAction.REFUND,
    AuditAction.PERMISSION_CHANGE,
    AuditAction.PASSWORD_CHANGE,
    AuditAction.BACKUP_RESTORE,
    AuditAction.BACKUP_DELETE,
    AuditAction.AI_COMMAND_EXECUTED,
    AuditAction.AI_ACTION,
    AuditAction.SAFETY_INCIDENT,
    AuditAction.SETTINGS_CHANGE,
    AuditAction.EXPORT,
}

#: Fields never worth recording as a change (noise).
IGNORED_FIELDS = {"updated_at", "last_login", "last_seen_at", "password"}


def _serialise(value: Any) -> Any:
    """Convert a model field value into something JSON-serialisable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, models.Model):
        return f"{value._meta.label}:{value.pk}"
    return str(value)


def record_audit(
    request=None,
    *,
    action: str,
    instance=None,
    description: str = "",
    changes: dict | None = None,
    source: str | None = None,
    user=None,
    object_repr: str = "",
) -> AuditLog | None:
    """Write one audit entry.

    Parameters
    ----------
    request:
        The current HTTP request, when available. If omitted the ambient
        :class:`~apps.audit.middleware.AuditContext` is used.
    action:
        A value from :class:`~apps.audit.models.AuditAction`.
    instance:
        The model instance the action applies to.
    changes:
        ``{"field": [old, new]}``. Values are redacted before storage.
    """
    try:
        context: AuditContext | None = get_audit_context()

        actor = user
        if actor is None and request is not None:
            actor = getattr(request, "user", None)
        if actor is None and context is not None:
            actor = context.user
        if actor is not None and not getattr(actor, "is_authenticated", False):
            actor = None

        content_type = None
        object_id = ""
        if instance is not None:
            try:
                content_type = ContentType.objects.get_for_model(instance.__class__)
                object_id = str(instance.pk or "")
            except Exception:  # noqa: BLE001 - unmanaged/abstract objects
                content_type = None

        if not object_repr and instance is not None:
            try:
                object_repr = str(instance)[:250]
            except Exception:  # noqa: BLE001
                object_repr = instance.__class__.__name__

        # Normalise + redact the change payload.
        clean_changes: dict[str, list] = {}
        for field, pair in (changes or {}).items():
            if field in IGNORED_FIELDS:
                continue
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                clean_changes[field] = [_serialise(pair[0]), _serialise(pair[1])]
            else:
                clean_changes[field] = [None, _serialise(pair)]
        clean_changes = redact_mapping(clean_changes)

        if source is None:
            if request is not None and getattr(request, "path", "").startswith("/api/"):
                source = AuditSource.API
            elif context is not None:
                source = context.source
            else:
                source = AuditSource.SYSTEM

        entry = AuditLog(
            user=actor,
            username=(getattr(actor, "username", "") or "")[:150],
            user_role=(getattr(actor, "role", "") or "")[:32],
            action=action,
            description=str(description)[:5000],
            changes=clean_changes,
            content_type=content_type,
            object_id=object_id[:64],
            object_repr=object_repr,
            source=source,
            ip_address=(
                getattr(context, "ip_address", None)
                if context
                else (request.META.get("REMOTE_ADDR") if request else None)
            ),
            user_agent=(getattr(context, "user_agent", "") if context else "")[:400],
            request_path=(
                getattr(context, "path", "") if context else (getattr(request, "path", "") or "")
            )[:400],
            request_id=(getattr(context, "request_id", "") if context else "")[:36],
            is_sensitive=action in SENSITIVE_ACTIONS,
        )
        entry.save()
        return entry
    except Exception:  # noqa: BLE001 - auditing must never break the caller
        logger.exception("Failed to write audit entry", extra={"audit_action": action})
        return None


def diff_instances(before, after, fields: list[str] | None = None) -> dict[str, list]:
    """Return ``{field: [old, new]}`` for the fields that differ."""
    if before is None or after is None:
        return {}
    names = fields or [
        f.name
        for f in after._meta.fields
        if f.name not in IGNORED_FIELDS and not f.primary_key
    ]
    changes: dict[str, list] = {}
    for name in names:
        old = getattr(before, name, None)
        new = getattr(after, name, None)
        if old != new:
            changes[name] = [_serialise(old), _serialise(new)]
    return changes


def record_system_event(action: str, description: str, **kwargs) -> AuditLog | None:
    """Convenience wrapper for background tasks (no request in scope)."""
    return record_audit(
        None, action=action, description=description, source=AuditSource.SYSTEM, **kwargs
    )
