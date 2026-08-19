from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from apps.accounts.constants import Role
from apps.audit.models import AuditAction, AuditLog
from apps.reporting import services
from apps.reporting.models import GeneratedReport, ReportFormat, ReportStatus

from .factories import ReportDefinitionFactory, ScheduledDefinitionFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager",
        email="manager@example.com",
        password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def rental_staff(db):
    """Holds rentals.view but no finance capability at all."""
    return User.objects.create_user(
        username="hire",
        email="hire@example.com",
        password="pw-test-12345",
        role=Role.RENTAL_STAFF,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_generate_report_stores_a_file_and_records_the_metrics(manager):
    report = services.generate_report("equipment_inventory", ReportFormat.PDF, {}, manager)

    assert report.status == ReportStatus.COMPLETED
    assert report.is_downloadable
    assert report.file_size_bytes > 0
    assert report.file.read().startswith(b"%PDF")
    assert report.generated_by == manager
    assert report.generation_ms >= 0
    assert report.title


def test_generate_report_writes_an_export_audit_entry(manager):
    services.generate_report("equipment_inventory", ReportFormat.CSV, {}, manager)
    entry = AuditLog.objects.filter(action=AuditAction.EXPORT).first()
    assert entry is not None
    assert entry.is_sensitive is True
    assert entry.user == manager


def test_filters_are_stored_in_a_json_safe_shape(manager):
    report = services.generate_report(
        "student_list", ReportFormat.CSV, {"range": "all", "include_inactive": True}, manager
    )
    assert report.filters_used == {"range": "all", "include_inactive": True}


@pytest.mark.parametrize("fmt", ["pdf", "excel", "csv"])
def test_every_format_produces_a_file(manager, fmt):
    report = services.generate_report("equipment_inventory", fmt, {}, manager)
    assert report.is_downloadable
    assert report.file.name.endswith(services.get_exporter(fmt).file_extension)


# ---------------------------------------------------------------------------
# Failure paths — the service never raises
# ---------------------------------------------------------------------------
def test_an_unknown_report_key_fails_without_raising(manager):
    report = services.generate_report("no_such_report", ReportFormat.PDF, {}, manager)
    assert report.status == ReportStatus.FAILED
    assert "Unknown report" in report.error_message
    assert not report.file


def test_an_unknown_format_fails_without_raising(manager):
    report = services.generate_report("equipment_inventory", "docx", {}, manager)
    assert report.status == ReportStatus.FAILED
    assert report.error_message


@pytest.mark.security
def test_a_user_without_the_capability_cannot_generate(rental_staff):
    report = services.generate_report("profit_loss", ReportFormat.PDF, {}, rental_staff)
    assert report.status == ReportStatus.FAILED
    assert "permission" in report.error_message.lower()
    assert not report.file


@pytest.mark.security
def test_an_anonymous_caller_is_refused():
    from django.contrib.auth.models import AnonymousUser

    report = services.generate_report(
        "equipment_inventory", ReportFormat.PDF, {}, AnonymousUser()
    )
    assert report.status == ReportStatus.FAILED


@pytest.mark.security
def test_a_definition_can_only_narrow_access(manager):
    """A saved config must not become a way around the report's own capability."""
    definition = ReportDefinitionFactory(
        report_key="equipment_inventory", required_capability="backups.restore"
    )
    report = services.generate_report(
        definition.report_key,
        definition.default_format,
        definition.filter_dict,
        user=manager,
        definition=definition,
    )
    assert report.status == ReportStatus.FAILED


def test_a_builder_error_is_captured_on_the_row(manager, monkeypatch):
    from apps.reporting import reports as reports_module

    spec = reports_module.REGISTRY["equipment_inventory"]

    def explode(user, filters):
        raise RuntimeError("database on fire")

    # ReportSpec is frozen, so the registry entry is swapped rather than mutated.
    monkeypatch.setitem(
        reports_module.REGISTRY,
        "equipment_inventory",
        reports_module.ReportSpec(
            key=spec.key,
            title=spec.title,
            description=spec.description,
            area=spec.area,
            capability=spec.capability,
            builder=explode,
        ),
    )

    report = services.generate_report("equipment_inventory", ReportFormat.PDF, {}, manager)
    assert report.status == ReportStatus.FAILED
    assert "database on fire" in report.error_message


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
def test_preview_truncates_but_reports_the_full_size(manager):
    equipment_model = services.get_report("equipment_inventory")
    assert equipment_model is not None

    spec, data, error = services.preview_report("equipment_inventory", {}, manager, limit=2)
    assert spec is not None
    assert error == ""
    assert data.row_count <= 2
    assert any("full export" in str(key) for key in data.summary)


@pytest.mark.security
def test_preview_refuses_a_user_without_the_capability(rental_staff):
    _spec, data, error = services.preview_report("profit_loss", {}, rental_staff)
    assert data is None
    assert error


def test_preview_of_an_unknown_report_reports_the_key():
    _spec, data, error = services.preview_report("nope", {}, None)
    assert data is None
    assert "nope" in error


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def test_due_definitions_only_returns_active_scheduled_rows():
    monday_seven = timezone.make_aware(datetime(2026, 8, 17, 7, 0))
    ScheduledDefinitionFactory(code="due", schedule_cron="0 7 * * 1")
    ScheduledDefinitionFactory(code="not-due", schedule_cron="0 9 * * 1")
    ScheduledDefinitionFactory(code="paused", schedule_cron="0 7 * * 1", is_active=False)
    ReportDefinitionFactory(code="manual")

    codes = {definition.code for definition in services.due_definitions(monday_seven)}
    assert codes == {"due"}


def test_running_a_schedule_generates_and_emails(manager):
    definition = ScheduledDefinitionFactory(
        code="weekly-inventory",
        report_key="equipment_inventory",
        schedule_cron="* * * * *",
        recipients=["ops@surfschool.test"],
        created_by=manager,
    )

    results = services.run_scheduled_reports()
    assert len(results) == 1
    assert results[0].is_downloadable
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ops@surfschool.test"]
    assert mail.outbox[0].attachments

    definition.refresh_from_db()
    assert definition.last_run_at is not None


def test_the_same_schedule_is_not_run_twice_in_one_window(manager):
    ScheduledDefinitionFactory(
        code="every-minute", schedule_cron="* * * * *", created_by=manager
    )
    services.run_scheduled_reports()
    services.run_scheduled_reports()
    assert GeneratedReport.objects.filter(status=ReportStatus.COMPLETED).count() == 1


def test_delivery_failure_does_not_lose_the_file(manager, monkeypatch):
    definition = ScheduledDefinitionFactory(
        code="broken-mail", schedule_cron="* * * * *", created_by=manager
    )

    def broken_send(self, *args, **kwargs):
        raise OSError("smtp unreachable")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", broken_send)

    report = services.generate_report(
        definition.report_key,
        definition.default_format,
        {},
        user=manager,
        definition=definition,
        deliver=True,
    )
    report.refresh_from_db()
    assert report.file  # the export itself survived
    assert "delivery failed" in report.error_message.lower()
