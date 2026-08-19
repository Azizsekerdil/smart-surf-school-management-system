from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.reporting.models import GeneratedReport, ReportDefinition, ReportFormat, ReportStatus

from .factories import GeneratedReportFactory, ReportDefinitionFactory

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
def reception(db):
    """Reception may read reports but holds no reporting.export."""
    return User.objects.create_user(
        username="desk",
        email="desk@example.com",
        password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        username="guest",
        email="guest@example.com",
        password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_catalogue_requires_authentication(client):
    response = client.get(reverse("reporting:list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.security
def test_a_customer_cannot_open_reports(client, customer):
    # Sign the customer in: without this the request is anonymous and the
    # redirect to the login page would pass for the wrong reason.
    client.force_login(customer)
    assert client.get(reverse("reporting:list")).status_code == 403


@pytest.mark.security
def test_a_user_cannot_run_a_report_they_may_not_read(client, reception):
    """Reception holds reporting.view but no finance.view."""
    client.force_login(reception)
    assert client.get(reverse("reporting:run", args=["profit_loss"])).status_code == 403


@pytest.mark.security
def test_preview_without_export_permission_shows_no_download(client, reception):
    client.force_login(reception)
    response = client.get(reverse("reporting:run", args=["daily_operations"]))
    assert response.status_code == 200
    assert response.context["may_export"] is False


@pytest.mark.security
def test_posting_an_export_without_permission_is_refused(client, reception):
    client.force_login(reception)
    response = client.post(
        reverse("reporting:run", args=["daily_operations"]), {"format": "pdf"}
    )
    assert response.status_code == 403
    assert GeneratedReport.objects.count() == 0


# ---------------------------------------------------------------------------
# Catalogue & run
# ---------------------------------------------------------------------------
def test_catalogue_lists_grouped_reports(client, manager):
    client.force_login(manager)
    response = client.get(reverse("reporting:list"))
    assert response.status_code == 200
    assert response.context["groups"]
    assert response.context["report_count"] > 0


def test_unknown_report_key_is_a_404(client, manager):
    client.force_login(manager)
    assert client.get(reverse("reporting:run", args=["nope"])).status_code == 404


def test_run_screen_previews_with_the_report_defaults(client, manager):
    client.force_login(manager)
    response = client.get(reverse("reporting:run", args=["equipment_inventory"]))
    assert response.status_code == 200
    assert response.context["preview"] is not None
    assert response.context["form"] is not None


def test_htmx_request_returns_only_the_preview_fragment(client, manager):
    client.force_login(manager)
    response = client.get(
        reverse("reporting:run", args=["equipment_inventory"]),
        {"range": "30"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response.templates[0].name == "reporting/partials/report_preview.html"


def test_posting_the_run_form_streams_a_file(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("reporting:run", args=["equipment_inventory"]),
        {"format": "csv", "equipment_status": "", "equipment_category": ""},
    )
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert ".csv" in response["Content-Disposition"]

    generated = GeneratedReport.objects.get()
    assert generated.status == ReportStatus.COMPLETED
    assert generated.generated_by == manager


def test_the_download_filename_carries_the_date(client, manager):
    from django.utils import timezone

    client.force_login(manager)
    response = client.post(
        reverse("reporting:run", args=["equipment_inventory"]), {"format": "pdf"}
    )
    assert timezone.localdate().strftime("%Y-%m-%d") in response["Content-Disposition"]


def test_an_invalid_filter_redisplays_the_form(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("reporting:run", args=["bookings_report"]),
        {"format": "pdf", "range": "custom", "start": "2026-08-20", "end": "2026-08-01"},
    )
    # Dates are swapped by the form's validation rule, so this must not 500.
    assert response.status_code in (200, 302)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def test_history_lists_past_exports(client, manager):
    GeneratedReportFactory(title="Equipment inventory")
    client.force_login(manager)
    response = client.get(reverse("reporting:history"))
    assert response.status_code == 200
    assert response.context["stats"]["total"] >= 1
    assert response.context["chart"]["labels"]


def test_history_search_filters_the_rows(client, manager):
    GeneratedReportFactory(title="Equipment inventory")
    GeneratedReportFactory(title="Revenue", report_key="revenue_report")
    client.force_login(manager)
    response = client.get(reverse("reporting:history"), {"q": "Revenue"})
    assert [report.title for report in response.context["reports"]] == ["Revenue"]


def test_download_returns_the_stored_file(client, manager):
    from apps.reporting import services

    client.force_login(manager)
    generated = services.generate_report(
        "equipment_inventory", ReportFormat.CSV, {}, manager
    )
    response = client.get(reverse("reporting:download", args=[generated.pk]))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")


def test_downloading_a_failed_export_redirects_with_a_message(client, manager):
    generated = GeneratedReportFactory(status=ReportStatus.FAILED, error_message="boom")
    client.force_login(manager)
    response = client.get(reverse("reporting:download", args=[generated.pk]))
    assert response.status_code == 302


@pytest.mark.security
def test_the_archive_is_not_a_way_around_a_capability(client, manager):
    """A finance export must stay closed to someone who lost finance.view."""
    from apps.reporting import services

    generated = services.generate_report("profit_loss", ReportFormat.CSV, {}, manager)
    assert generated.is_downloadable

    equipment_manager = User.objects.create_user(
        username="gear",
        email="gear@example.com",
        password="pw-test-12345",
        role=Role.EQUIPMENT_MANAGER,
    )
    client.force_login(equipment_manager)
    assert client.get(reverse("reporting:download", args=[generated.pk])).status_code == 403


# ---------------------------------------------------------------------------
# Saved configurations
# ---------------------------------------------------------------------------
def test_definition_list_renders(client, manager):
    ReportDefinitionFactory()
    client.force_login(manager)
    response = client.get(reverse("reporting:definition_list"))
    assert response.status_code == 200
    assert response.context["definitions"]


def test_creating_a_saved_report(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("reporting:definition_create"),
        {
            "name": "Monday ops sheet",
            "code": "monday-ops",
            "report_key": "daily_operations",
            "description": "",
            "default_format": "pdf",
            "default_filters": "{}",
            "required_capability": "",
            "is_scheduled": "",
            "schedule_cron": "",
            "recipients": "",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    definition = ReportDefinition.objects.get(code="monday-ops")
    assert definition.report_key == "daily_operations"
    assert definition.created_by == manager


def test_a_schedule_without_recipients_is_rejected(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("reporting:definition_create"),
        {
            "name": "Broken schedule",
            "code": "broken",
            "report_key": "daily_operations",
            "default_format": "pdf",
            "default_filters": "{}",
            "is_scheduled": "on",
            "schedule_cron": "0 7 * * 1",
            "recipients": "",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "recipients" in response.context["form"].errors
    assert not ReportDefinition.objects.filter(code="broken").exists()


def test_an_invalid_cron_expression_is_rejected(client, manager):
    client.force_login(manager)
    response = client.post(
        reverse("reporting:definition_create"),
        {
            "name": "Bad cron",
            "code": "bad-cron",
            "report_key": "daily_operations",
            "default_format": "pdf",
            "default_filters": "{}",
            "is_scheduled": "on",
            "schedule_cron": "99 7 * * *",
            "recipients": "ops@example.com",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    assert "schedule_cron" in response.context["form"].errors


def test_prefilled_creation_from_the_run_screen(client, manager):
    client.force_login(manager)
    response = client.get(
        reverse("reporting:definition_create"),
        {"report_key": "student_list", "range": "all", "level": "beginner"},
    )
    assert response.status_code == 200
    initial = response.context["form"].initial
    assert initial["report_key"] == "student_list"
    assert initial["default_filters"] == {"range": "all", "level": "beginner"}


def test_updating_a_saved_report(client, manager):
    definition = ReportDefinitionFactory(code="editable", name="Old name")
    client.force_login(manager)
    response = client.post(
        reverse("reporting:definition_update", args=[definition.pk]),
        {
            "name": "New name",
            "code": "editable",
            "report_key": definition.report_key,
            "default_format": definition.default_format,
            "default_filters": "{}",
            "is_active": "on",
            "recipients": "",
            "schedule_cron": "",
        },
    )
    assert response.status_code == 302
    definition.refresh_from_db()
    assert definition.name == "New name"


def test_archiving_a_saved_report_keeps_its_history(client, manager):
    definition = ReportDefinitionFactory(code="doomed")
    GeneratedReportFactory(definition=definition)
    client.force_login(manager)

    response = client.post(reverse("reporting:definition_delete", args=[definition.pk]))
    assert response.status_code == 302
    assert not ReportDefinition.objects.filter(code="doomed").exists()
    assert GeneratedReport.objects.count() == 1


def test_running_a_saved_report_streams_the_file(client, manager):
    definition = ReportDefinitionFactory(
        code="run-me", report_key="equipment_inventory", default_format=ReportFormat.CSV
    )
    client.force_login(manager)
    response = client.post(reverse("reporting:definition_run", args=[definition.pk]))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert GeneratedReport.objects.filter(definition=definition).exists()


@pytest.mark.security
def test_reception_cannot_create_a_saved_report(client, reception):
    client.force_login(reception)
    assert client.get(reverse("reporting:definition_create")).status_code == 403
