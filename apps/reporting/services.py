"""Business rules for generating, storing and delivering reports.

:func:`generate_report` is the single entry point. Views, the REST API, the
Celery task and the management command all call it, so the capability check, the
audit entry, the timing and the stored file behave identically whichever door
the request came through.

It never raises. A report that fails still produces a ``GeneratedReport`` row
with ``status=FAILED`` and the reason, because "the export button did nothing"
is the worst possible outcome for someone standing at the counter.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction, AuditSource
from apps.audit.services import record_audit

from .cron import cron_is_due
from .exporters.base import ReportData
from .exporters.registry import UnknownExportFormat, get_exporter
from .models import GeneratedReport, ReportDefinition, ReportFormat, ReportStatus
from .reports import ReportSpec, get_report

logger = logging.getLogger(__name__)

#: Rows shown in the on-screen preview before an operator commits to an export.
PREVIEW_ROWS = 25


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
def required_capability(spec: ReportSpec, definition: ReportDefinition | None = None) -> str:
    """The capability that governs this report.

    A saved definition may tighten the requirement; it can never loosen it,
    because the report's own capability is checked as well.
    """
    if definition is not None and definition.required_capability:
        return definition.required_capability
    return spec.capability


def user_may_run(user, spec: ReportSpec, definition: ReportDefinition | None = None) -> bool:
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if not user.has_capability(spec.capability):
        return False
    extra = definition.required_capability if definition else ""
    return user.has_capability(extra) if extra else True


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def build_report_data(spec: ReportSpec, filters: Mapping | None, user) -> ReportData:
    """Run a builder. Errors inside one report never leak into another."""
    return spec.build(user, filters or {})


def preview_report(
    report_key: str, filters: Mapping | None, user, limit: int = PREVIEW_ROWS
) -> tuple[ReportSpec | None, ReportData | None, str]:
    """Build a report and cut it down for on-screen display.

    Returns ``(spec, data, error)``. The preview is what makes the run screen
    honest: an operator sees the shape and the totals before committing a
    20 000-row export to disk and to the audit log.
    """
    spec = get_report(report_key)
    if spec is None:
        return None, None, str(_("Unknown report: %(key)s") % {"key": report_key})
    if not user_may_run(user, spec):
        return spec, None, str(_("Your role does not grant access to this report."))

    try:
        data = build_report_data(spec, filters, user)
    except Exception as error:  # noqa: BLE001 - a preview must never 500 the screen
        logger.exception("Report preview failed for %s", report_key)
        return spec, None, str(error)

    full_rows = data.row_count
    if full_rows > limit:
        data.rows = data.rows[:limit]
    data.summary = dict(data.summary)
    data.summary[str(_("Rows in full export"))] = full_rows
    return spec, data, ""


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------
def generate_report(
    report_key: str,
    fmt: str,
    filters: Mapping | None = None,
    user=None,
    *,
    definition: ReportDefinition | None = None,
    request=None,
    enforce_capability: bool = True,
    deliver: bool = False,
) -> GeneratedReport:
    """Produce, store and audit one export.

    Parameters
    ----------
    enforce_capability:
        Only the scheduler passes ``False``, and only for a definition that a
        privileged user saved. Interactive paths always enforce.
    deliver:
        Send the file to ``definition.recipients`` once it is stored.
    """
    started = time.perf_counter()
    filters = _jsonable(filters or {})
    fmt = (fmt or ReportFormat.PDF).strip().lower()

    generated = GeneratedReport.objects.create(
        definition=definition,
        report_key=(report_key or "")[:60],
        title=str(definition.name if definition else report_key)[:200],
        format=fmt if fmt in dict(ReportFormat.choices) else ReportFormat.PDF,
        filters_used=filters,
        generated_by=user if getattr(user, "is_authenticated", False) else None,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        status=ReportStatus.PENDING,
    )

    spec = get_report(report_key)
    if spec is None:
        return _fail(
            generated,
            started,
            _("Unknown report: %(key)s") % {"key": report_key},
        )

    generated.title = str(spec.title)[:200]

    if enforce_capability and not user_may_run(user, spec, definition):
        return _fail(
            generated,
            started,
            _("Missing required permission: %(cap)s")
            % {"cap": required_capability(spec, definition)},
        )

    try:
        exporter = get_exporter(fmt)
    except UnknownExportFormat as error:
        return _fail(generated, started, str(error))

    try:
        data = build_report_data(spec, filters, user)
    except Exception as error:  # noqa: BLE001 - report failures are data, not crashes
        logger.exception("Report %s failed to build", report_key)
        return _fail(generated, started, _("Could not build the report: %(error)s") % {"error": error})

    try:
        payload = exporter.render(data)
    except Exception as error:  # noqa: BLE001
        logger.exception("Report %s failed to render as %s", report_key, fmt)
        return _fail(
            generated, started, _("Could not render the %(format)s file: %(error)s")
            % {"format": fmt.upper(), "error": error}
        )

    generated.title = str(data.title)[:200]
    generated.row_count = data.row_count
    generated.file.save(exporter.filename(data), ContentFile(payload), save=False)
    generated.file_size_bytes = len(payload)
    generated.status = ReportStatus.COMPLETED
    generated.error_message = ""
    generated.generation_ms = _elapsed_ms(started)
    generated.save()

    if definition is not None:
        ReportDefinition.objects.filter(pk=definition.pk).update(last_run_at=timezone.now())

    record_audit(
        request,
        action=AuditAction.EXPORT,
        instance=generated,
        user=user,
        source=None if request is not None else AuditSource.SYSTEM,
        description=_(
            "Exported “%(title)s” as %(format)s (%(rows)s rows) with filters %(filters)s"
        )
        % {
            "title": generated.title,
            "format": fmt.upper(),
            "rows": generated.row_count,
            "filters": filters or "-",
        },
    )

    if deliver and definition is not None:
        deliver_report(generated, definition)

    return generated


def _fail(generated: GeneratedReport, started: float, message) -> GeneratedReport:
    generated.status = ReportStatus.FAILED
    generated.error_message = str(message)[:2000]
    generated.generation_ms = _elapsed_ms(started)
    generated.save(
        update_fields=["status", "error_message", "generation_ms", "updated_at"]
    )
    logger.warning("Report %s failed: %s", generated.report_key, generated.error_message)
    return generated


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _jsonable(filters: Mapping) -> dict:
    """Coerce filter values into something ``JSONField`` can store."""
    clean: dict[str, Any] = {}
    for key, value in filters.items():
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)
    return clean


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def deliver_report(generated: GeneratedReport, definition: ReportDefinition) -> bool:
    """E-mail a finished report to the definition's recipients.

    Returns ``True`` when the message was handed to the mail backend. Delivery
    failure is logged and recorded on the report but does not undo the export —
    the file is already on disk and downloadable.
    """
    recipients = definition.recipient_list
    if not recipients or not generated.is_downloadable:
        return False

    try:
        with generated.file.open("rb") as handle:
            payload = handle.read()

        message = EmailMessage(
            subject=str(
                _("%(school)s — %(title)s")
                % {"school": settings.SCHOOL.get("NAME", ""), "title": generated.title}
            ),
            body=str(
                _(
                    "Attached is the scheduled report “%(title)s”.\n"
                    "Generated: %(when)s\nRows: %(rows)s\n"
                )
                % {
                    "title": generated.title,
                    "when": timezone.localtime(generated.created_at).strftime("%d.%m.%Y %H:%M"),
                    "rows": generated.row_count,
                }
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        exporter = get_exporter(generated.format)
        message.attach(generated.download_filename(), payload, exporter.content_type)
        message.send(fail_silently=False)
    except Exception as error:  # noqa: BLE001 - a mail outage must not lose the report
        logger.exception("Could not e-mail report %s", generated.pk)
        GeneratedReport.objects.filter(pk=generated.pk).update(
            error_message=str(_("Generated, but delivery failed: %(error)s") % {"error": error})[
                :2000
            ]
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def due_definitions(moment=None, window_minutes: int = 5) -> list[ReportDefinition]:
    """Active, scheduled definitions whose cron expression fired recently."""
    moment = timezone.localtime(moment or timezone.now())
    candidates = ReportDefinition.objects.filter(is_scheduled=True, is_active=True).exclude(
        schedule_cron=""
    )
    return [
        definition
        for definition in candidates
        if cron_is_due(definition.schedule_cron, moment, window_minutes=window_minutes)
    ]


def run_scheduled_reports(moment=None, window_minutes: int = 5) -> list[GeneratedReport]:
    """Generate and deliver every report that is due.

    A definition already generated inside the same window is skipped, so an
    overlapping scheduler tick cannot mail the same file twice.
    """
    moment = timezone.localtime(moment or timezone.now())
    cutoff = moment - timedelta(minutes=max(window_minutes, 1))
    results: list[GeneratedReport] = []

    for definition in due_definitions(moment, window_minutes):
        already = GeneratedReport.objects.filter(
            definition=definition,
            created_at__gte=cutoff,
            status=ReportStatus.COMPLETED,
        ).exists()
        if already:
            continue

        results.append(
            generate_report(
                definition.report_key,
                definition.default_format,
                definition.filter_dict,
                user=definition.created_by,
                definition=definition,
                # The definition was saved by a user who held the capability;
                # the scheduler itself is not an authenticated actor.
                enforce_capability=False,
                deliver=True,
            )
        )
    return results
