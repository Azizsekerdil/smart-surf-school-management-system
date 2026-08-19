from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.reporting.models import GeneratedReport, ReportDefinition, ReportFormat

from .factories import GeneratedReportFactory, ReportDefinitionFactory

pytestmark = pytest.mark.django_db
User = get_user_model()

DEFINITIONS_URL = "/api/v1/report-definitions/"
GENERATED_URL = "/api/v1/generated-reports/"


def _errors(response) -> dict:
    """Field errors, unwrapped from the project's envelope shape."""
    payload = response.data
    if isinstance(payload, dict) and "error" in payload:
        return payload["error"].get("detail") or {}
    return payload


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager",
        email="manager@example.com",
        password="pw-test-12345",
        role=Role.MANAGER,
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
@pytest.mark.security
def test_the_api_requires_authentication(api):
    assert api.get(DEFINITIONS_URL).status_code in (401, 403)


@pytest.mark.security
def test_a_customer_is_refused(api, customer):
    api.force_authenticate(customer)
    assert api.get(DEFINITIONS_URL).status_code == 403


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def test_catalogue_returns_only_permitted_reports(api, manager):
    api.force_authenticate(manager)
    response = api.get(f"{DEFINITIONS_URL}catalogue/")
    assert response.status_code == 200

    keys = {entry["key"] for entry in response.data}
    assert "equipment_inventory" in keys
    for entry in response.data:
        assert entry["title"]
        assert entry["capability"]


def test_catalogue_hides_reports_the_role_cannot_read(api):
    staff = User.objects.create_user(
        username="hire",
        email="hire@example.com",
        password="pw-test-12345",
        role=Role.RENTAL_STAFF,
    )
    api.force_authenticate(staff)
    keys = {entry["key"] for entry in api.get(f"{DEFINITIONS_URL}catalogue/").data}
    assert "rental_report" in keys
    assert "profit_loss" not in keys


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------
def test_listing_definitions(api, manager):
    ReportDefinitionFactory(name="Weekly inventory")
    api.force_authenticate(manager)
    response = api.get(DEFINITIONS_URL)
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_creating_a_definition_stamps_the_author(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        DEFINITIONS_URL,
        {
            "name": "Monthly P&L",
            "code": "monthly-pl",
            "report_key": "profit_loss",
            "default_format": ReportFormat.PDF,
            "default_filters": {"range": "30"},
            "recipients": [],
        },
        format="json",
    )
    assert response.status_code == 201
    assert ReportDefinition.objects.get(code="monthly-pl").created_by == manager


def test_an_unknown_report_key_is_rejected(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        DEFINITIONS_URL,
        {"name": "Nope", "code": "nope", "report_key": "not_a_report"},
        format="json",
    )
    assert response.status_code == 400
    assert "report_key" in _errors(response)


def test_a_schedule_needs_a_cron_and_recipients(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        DEFINITIONS_URL,
        {
            "name": "Broken",
            "code": "broken",
            "report_key": "equipment_inventory",
            "is_scheduled": True,
        },
        format="json",
    )
    assert response.status_code == 400


def test_an_invalid_cron_is_rejected(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        DEFINITIONS_URL,
        {
            "name": "Bad cron",
            "code": "bad-cron",
            "report_key": "equipment_inventory",
            "schedule_cron": "0 99 * * *",
        },
        format="json",
    )
    assert response.status_code == 400
    assert "schedule_cron" in _errors(response)


def test_running_a_definition_returns_the_archive_record(api, manager):
    definition = ReportDefinitionFactory(
        code="run-me", report_key="equipment_inventory", default_format=ReportFormat.CSV
    )
    api.force_authenticate(manager)
    response = api.post(f"{DEFINITIONS_URL}{definition.pk}/run/")
    assert response.status_code == 201
    assert response.data["status"] == "completed"
    assert response.data["download_url"]
    assert GeneratedReport.objects.filter(definition=definition).exists()


# ---------------------------------------------------------------------------
# Generated reports
# ---------------------------------------------------------------------------
def test_the_archive_is_read_only(api, manager):
    report = GeneratedReportFactory()
    api.force_authenticate(manager)
    assert api.delete(f"{GENERATED_URL}{report.pk}/").status_code in (403, 405)
    assert api.patch(f"{GENERATED_URL}{report.pk}/", {"title": "x"}).status_code in (403, 405)


def test_running_an_ad_hoc_report(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        f"{GENERATED_URL}run/",
        {"report_key": "equipment_inventory", "format": "csv", "filters": {}},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["row_count"] >= 0
    assert response.data["download_url"]


@pytest.mark.security
def test_an_ad_hoc_run_still_checks_the_report_capability(api):
    staff = User.objects.create_user(
        username="hire",
        email="hire@example.com",
        password="pw-test-12345",
        role=Role.RENTAL_STAFF,
    )
    api.force_authenticate(staff)
    response = api.post(
        f"{GENERATED_URL}run/",
        {"report_key": "profit_loss", "format": "csv"},
        format="json",
    )
    # Rental staff hold no reporting.export capability at all.
    assert response.status_code in (400, 403)


def test_an_unknown_key_is_rejected_by_the_serializer(api, manager):
    api.force_authenticate(manager)
    response = api.post(
        f"{GENERATED_URL}run/", {"report_key": "nope", "format": "csv"}, format="json"
    )
    assert response.status_code == 400
