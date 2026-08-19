"""REST API: same rules as the HTML screens, enforced by the same services."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import DamageType, EquipmentStatus, GenericStatus, Severity

from ..models import MaintenanceRecord
from .factories import (
    MaintenanceRecordFactory,
    MaintenanceScheduleFactory,
    UserFactory,
    make_equipment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def manager_client(api_client):
    api_client.force_authenticate(UserFactory(role=Role.EQUIPMENT_MANAGER))
    return api_client


@pytest.fixture
def outsider_client(api_client):
    api_client.force_authenticate(UserFactory(role=Role.PHOTOGRAPHER))
    return api_client


class TestRecordEndpoints:
    def test_list_returns_records(self, manager_client):
        record = MaintenanceRecordFactory()
        response = manager_client.get(reverse("maintenance-record-list"))
        assert response.status_code == 200
        codes = [row["record_code"] for row in response.data["results"]]
        assert record.record_code in codes

    def test_anonymous_access_is_refused(self, api_client):
        response = api_client.get(reverse("maintenance-record-list"))
        assert response.status_code in (401, 403)

    def test_capability_is_enforced(self, outsider_client):
        response = outsider_client.get(reverse("maintenance-record-list"))
        assert response.status_code == 403

    def test_create_goes_through_the_service_layer(self, manager_client):
        equipment = make_equipment()
        response = manager_client.post(
            reverse("maintenance-record-list"),
            {
                "equipment": equipment.pk,
                "damage_type": DamageType.LEASH_DAMAGE,
                "severity": Severity.HIGH,
                "description": "Leash plug pulled out of the deck.",
            },
            format="json",
        )
        assert response.status_code == 201
        equipment.refresh_from_db()
        assert equipment.status == EquipmentStatus.DAMAGED
        assert MaintenanceRecord.objects.filter(equipment=equipment).exists()

    def test_duplicate_report_is_rejected(self, manager_client):
        record = MaintenanceRecordFactory(damage_type=DamageType.DING)
        response = manager_client.post(
            reverse("maintenance-record-list"),
            {
                "equipment": record.equipment_id,
                "damage_type": DamageType.DING,
                "severity": Severity.LOW,
                "description": "Same ding.",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_start_and_complete_actions(self, manager_client):
        record = MaintenanceRecordFactory()

        started = manager_client.post(
            reverse("maintenance-record-start", args=[record.pk]),
            {"diagnosis": "Foam is wet."},
            format="json",
        )
        assert started.status_code == 200
        assert started.data["status"] == GenericStatus.IN_PROGRESS

        completed = manager_client.post(
            reverse("maintenance-record-complete", args=[record.pk]),
            {
                "resolution": "Dried for 48 h, laminated and cured.",
                "labour_hours": "3.00",
                "parts_cost": "55.00",
                "labour_cost": "90.00",
            },
            format="json",
        )
        assert completed.status_code == 200
        assert completed.data["status"] == GenericStatus.RESOLVED
        assert completed.data["total_cost"] == "145.00"

    def test_records_cannot_be_deleted(self, manager_client):
        record = MaintenanceRecordFactory()
        response = manager_client.delete(
            reverse("maintenance-record-detail", args=[record.pk])
        )
        assert response.status_code == 405
        assert MaintenanceRecord.objects.filter(pk=record.pk).exists()

    def test_predictions_endpoint_declares_its_method(self, manager_client):
        make_equipment()
        response = manager_client.get(reverse("maintenance-record-predictions"))
        assert response.status_code == 200
        assert response.data["method"] == "deterministic_statistics"
        assert response.data["count"] == 1
        prediction = response.data["results"][0]
        assert 0 <= prediction["risk_score"] <= 100
        assert prediction["reason_texts"]

    def test_cost_report_endpoint(self, manager_client):
        MaintenanceRecordFactory()
        response = manager_client.get(
            reverse("maintenance-record-cost-report"), {"range": "all"}
        )
        assert response.status_code == 200
        assert "total_cost" in response.data


class TestScheduleEndpoints:
    def test_list_and_due(self, manager_client):
        MaintenanceScheduleFactory(next_due_on=timezone.localdate() - timedelta(days=1))
        MaintenanceScheduleFactory(next_due_on=timezone.localdate() + timedelta(days=90))

        listed = manager_client.get(reverse("maintenance-schedule-list"))
        assert listed.status_code == 200
        assert listed.data["count"] == 2

        due = manager_client.get(reverse("maintenance-schedule-due"))
        assert due.status_code == 200
        assert due.data["count"] == 1

    def test_performed_action_rolls_the_plan_forward(self, manager_client):
        schedule = MaintenanceScheduleFactory(interval_days=30)
        response = manager_client.post(
            reverse("maintenance-schedule-performed", args=[schedule.pk]), {}, format="json"
        )
        assert response.status_code == 200
        schedule.refresh_from_db()
        assert schedule.next_due_on == timezone.localdate() + timedelta(days=30)

    def test_capability_is_enforced(self, outsider_client):
        response = outsider_client.get(reverse("maintenance-schedule-list"))
        assert response.status_code == 403
