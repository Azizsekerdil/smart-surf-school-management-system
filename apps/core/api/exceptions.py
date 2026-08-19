"""Uniform API error envelope.

Every API error — DRF validation, Django ``ValidationError``/``PermissionDenied``
and unexpected exceptions — is returned in one shape so clients (and the AI
assistant's tool layer) can parse failures reliably::

    {"error": {"type": "validation_error", "message": "...", "detail": {...}}}
"""

from __future__ import annotations

import logging
import uuid

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _error_type_for(status_code: int) -> str:
    return {
        400: "validation_error",
        401: "authentication_required",
        403: "permission_denied",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(status_code, "error")


def api_exception_handler(exc, context):
    """DRF ``EXCEPTION_HANDLER``."""
    # Translate common Django exceptions into their DRF equivalents first.
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or {"non_field_errors": exc.messages}
        return Response(
            {
                "error": {
                    "type": "validation_error",
                    "message": _("The submitted data is not valid."),
                    "detail": detail,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            {
                "error": {
                    "type": "permission_denied",
                    "message": str(exc) or _("You do not have permission to perform this action."),
                    "detail": {},
                }
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, (Http404, ObjectDoesNotExist)):
        return Response(
            {
                "error": {
                    "type": "not_found",
                    "message": _("The requested resource was not found."),
                    "detail": {},
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Genuinely unexpected: log with a correlation id, never leak internals.
        incident = uuid.uuid4().hex[:12]
        logger.exception(
            "Unhandled API exception",
            extra={
                "incident_id": incident,
                "path": getattr(context.get("request"), "path", ""),
                "view": context.get("view").__class__.__name__ if context.get("view") else "",
            },
        )
        return Response(
            {
                "error": {
                    "type": "internal_error",
                    "message": _("An unexpected error occurred. Reference: %(id)s") % {"id": incident},
                    "detail": {"incident_id": incident},
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        message = str(detail["detail"])
        detail_body: dict = {}
    elif isinstance(detail, dict):
        message = _("The submitted data is not valid.")
        detail_body = detail
    else:
        message = _("Request failed.")
        detail_body = {"errors": detail}

    response.data = {
        "error": {
            "type": _error_type_for(response.status_code),
            "message": message,
            "detail": detail_body,
        }
    }
    return response
