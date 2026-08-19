"""Request context for audit entries.

Model-level auditing happens in signal handlers that have no access to the
request. This middleware stashes the current request in a context variable so
those handlers can attribute a change to the right user, IP and request id.

``ContextVar`` (not ``threading.local``) is used so the value is also correct
under ASGI and inside ``asyncio`` tasks.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_audit_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


@dataclass(slots=True)
class AuditContext:
    user: Any = None
    ip_address: str | None = None
    user_agent: str = ""
    path: str = ""
    request_id: str = ""
    source: str = "web"


def get_audit_context() -> AuditContext | None:
    """Return the audit context of the request in flight, if any."""
    return _audit_context.get()


def set_audit_context(context: AuditContext | None):
    """Set the context explicitly (used by Celery tasks and the AI layer)."""
    return _audit_context.set(context)


def reset_audit_context(token) -> None:
    try:
        _audit_context.reset(token)
    except (ValueError, LookupError):  # pragma: no cover - defensive
        _audit_context.set(None)


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """Populates the audit context for the duration of each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.audit_request_id = request_id

        path = request.path
        if path.startswith("/api/"):
            source = "api"
        elif "/admin/" in path:
            source = "admin"
        elif "/ai-terminal/" in path:
            source = "ai_terminal"
        elif "/ai/" in path:
            source = "ai"
        else:
            source = "web"

        token = _audit_context.set(
            AuditContext(
                user=getattr(request, "user", None),
                ip_address=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:400],
                path=path[:400],
                request_id=request_id,
                source=source,
            )
        )
        try:
            response = self.get_response(request)
        finally:
            reset_audit_context(token)

        response.headers.setdefault("X-Request-ID", request_id)
        return response
