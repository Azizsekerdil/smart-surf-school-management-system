"""HTML screens: they render, they enforce capabilities, and the actions work."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import DamageType, EquipmentStatus, GenericStatus, Severity

from ..models import MaintenanceRecord, MaintenanceSchedule
from .factories import (
    MaintenanceRecordFactory,
    MaintenanceScheduleFactory,
    UserFactory,
    make_equipment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def maintenance_staff():
    return UserFactory(role=Role.MAINTENANCE_STAFF)


@pytest.fixture
def outsider():
    """A role with no maintenance capability at all."""
    return UserFactory(role=Role.PHOTOGRAPHER)


@pytest.fixture
def staff_client(client, maintenance_staff):
    client.force_login(maintenance_staff)
    return client


class TestListAndDetail:
    def test_list_renders(self, staff_client):
        MaintenanceRecordFactory()
        response = staff_client.get(reverse("maintenance:list"))
        assert response.status_code == 200

    def test_list_tabs_filter_by_status(self, staff_client):
        MaintenanceRecordFactory(status=GenericStatus.OPEN)
        resolved = MaintenanceRecordFactory(
            status=GenericStatus.RESOLVED, resolution="Repaired."
        )

        response = staff_client.get(reverse("maintenance:list"), {"tab": "resolved"})
        codes = [r.record_code for r in response.context["records"]]
        assert codes == [resolved.record_code]

    def test_list_filters_by_severity(self, staff_client):
        MaintenanceRecordFactory(severity=Severity.LOW)
        critical = MaintenanceRecordFactory(severity=Severity.CRITICAL)

        response = staff_client.get(
            reverse("maintenance:list"), {"tab": "all", "severity": Severity.CRITICAL}
        )
        codes = [r.record_code for r in response.context["records"]]
        assert codes == [critical.record_code]

    def test_detail_renders(self, staff_client):
        record = MaintenanceRecordFactory()
        response = staff_client.get(reverse("maintenance:detail", args=[record.pk]))
        assert response.status_code == 200
        assert response.context["record"].pk == record.pk

    def test_list_requires_the_capability(self, client, outsider):
        client.force_login(outsider)
        response = client.get(reverse("maintenance:list"))
        assert response.status_code == 403

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(reverse("maintenance:list"))
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


class TestCreateAndUpdate:
    def test_create_reports_a_problem_and_withdraws_the_item(self, staff_client):
        equipment = make_equipment()
        response = staff_client.post(
            reverse("maintenance:create"),
            {
                "equipment": equipment.pk,
                "damage_type": DamageType.CRACK,
                "severity": Severity.HIGH,
                "description": "Crack across the tail.",
                "made_unusable": "on",
            },
        )

        record = MaintenanceRecord.objects.get(equipment=equipment)
        assert response.status_code == 302
        assert response.url == reverse("maintenance:detail", args=[record.pk])
        equipment.refresh_from_db()
        assert equipment.status == EquipmentStatus.DAMAGED

    def test_create_prefills_the_equipment_from_the_query_string(self, staff_client):
        equipment = make_equipment()
        response = staff_client.get(
            reverse("maintenance:create"), {"equipment": equipment.pk}
        )
        assert response.status_code == 200
        assert response.context["form"].initial["equipment"] == equipment.pk

    def test_create_requires_the_add_capability(self, client, outsider):
        client.force_login(outsider)
        response = client.get(reverse("maintenance:create"))
        assert response.status_code == 403

    def test_duplicate_report_is_surfaced_as_a_form_error(self, staff_client):
        record = MaintenanceRecordFactory(damage_type=DamageType.DING)
        response = staff_client.post(
            reverse("maintenance:create"),
            {
                "equipment": record.equipment_id,
                "damage_type": DamageType.DING,
                "severity": Severity.LOW,
                "description": "Same ding again.",
                "made_unusable": "on",
            },
        )
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_update_saves_changes(self, staff_client):
        record = MaintenanceRecordFactory()
        response = staff_client.post(
            reverse("maintenance:update", args=[record.pk]),
            {
                "damage_type": DamageType.DELAMINATION,
                "severity": Severity.HIGH,
                "description": "Delamination spreading along the deck.",
                "diagnosis": "Left in a hot car.",
                "made_unusable": "on",
            },
        )
        assert response.status_code == 302
        record.refresh_from_db()
        assert record.damage_type == DamageType.DELAMINATION
        assert record.severity == Severity.HIGH


class TestWorkflowActions:
    def test_start_moves_the_record_into_progress(self, staff_client):
        record = MaintenanceRecordFactory()
        response = staff_client.post(
            reverse("maintenance:start", args=[record.pk]), {"diagnosis": "Water ingress."}
        )
        assert response.status_code == 302
        record.refresh_from_db()
        assert record.status == GenericStatus.IN_PROGRESS

    def test_hold_requires_a_reason(self, staff_client):
        record = MaintenanceRecordFactory()
        staff_client.post(reverse("maintenance:hold", args=[record.pk]), {"reason": ""})
        record.refresh_from_db()
        assert record.status == GenericStatus.OPEN

    def test_complete_closes_the_record_with_its_cost(self, staff_client):
        record = MaintenanceRecordFactory()
        response = staff_client.post(
            reverse("maintenance:complete", args=[record.pk]),
            {
                "resolution": "Filled, sanded and cured.",
                "parts_used": "epoxy resin 250 ml",
                "labour_hours": "2.00",
                "parts_cost": "40.00",
                "labour_cost": "60.00",
                "condition_after": "",
            },
        )
        assert response.status_code == 302
        record.refresh_from_db()
        assert record.status == GenericStatus.RESOLVED
        assert str(record.total_cost) == "100.00"

    def test_actions_require_the_change_capability(self, client, outsider):
        record = MaintenanceRecordFactory()
        client.force_login(outsider)
        response = client.post(reverse("maintenance:start", args=[record.pk]), {})
        assert response.status_code == 403

    def test_cancel_needs_a_reason_and_records_it(self, staff_client):
        record = MaintenanceRecordFactory()
        staff_client.post(
            reverse("maintenance:cancel", args=[record.pk]),
            {"reason": "Reported against the wrong board."},
        )
        record.refresh_from_db()
        assert record.status == GenericStatus.CANCELLED
        assert "wrong board" in record.resolution


class TestPredictionBoard:
    def test_board_renders_with_ranked_cards(self, staff_client):
        make_equipment()
        make_equipment()
        response = staff_client.get(reverse("maintenance:predictions"))
        assert response.status_code == 200
        assert len(response.context["predictions"]) == 2
        scores = [p["risk_score"] for p in response.context["predictions"]]
        assert scores == sorted(scores, reverse=True)

    def test_board_reports_reasons_in_plain_language(self, staff_client):
        make_equipment()
        response = staff_client.get(reverse("maintenance:predictions"), {"refresh": "1"})
        prediction = response.context["predictions"][0]
        assert prediction["reason_texts"]
        assert any(entry["text"] for entry in prediction["reason_texts"])

    def test_board_requires_the_capability(self, client, outsider):
        client.force_login(outsider)
        assert client.get(reverse("maintenance:predictions")).status_code == 403


class TestSchedules:
    def test_schedule_list_shows_what_is_due(self, staff_client):
        due = MaintenanceScheduleFactory(
            next_due_on=timezone.localdate() - timedelta(days=2)
        )
        MaintenanceScheduleFactory(next_due_on=timezone.localdate() + timedelta(days=60))

        response = staff_client.get(reverse("maintenance:schedule_list"))
        assert response.status_code == 200
        assert [s.pk for s in response.context["schedules"]] == [due.pk]
        assert response.context["due_count"] == 1

    def test_schedule_can_be_created(self, staff_client):
        equipment = make_equipment()
        response = staff_client.post(
            reverse("maintenance:schedule_create"),
            {
                "equipment": equipment.pk,
                "interval_days": "60",
                "check_items": "Inspect leash plug\nCheck fin boxes",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        schedule = MaintenanceSchedule.objects.get(equipment=equipment)
        assert schedule.check_items == ["Inspect leash plug", "Check fin boxes"]

    def test_mark_performed_rolls_the_plan_forward(self, staff_client):
        schedule = MaintenanceScheduleFactory(
            interval_days=30, next_due_on=timezone.localdate() - timedelta(days=1)
        )
        response = staff_client.post(
            reverse("maintenance:schedule_performed", args=[schedule.pk]), {}
        )
        assert response.status_code == 302
        schedule.refresh_from_db()
        assert schedule.last_performed_on == timezone.localdate()
        assert schedule.next_due_on == timezone.localdate() + timedelta(days=30)


class TestCostReportView:
    def test_report_renders(self, staff_client):
        MaintenanceRecordFactory()
        response = staff_client.get(reverse("maintenance:cost_report"))
        assert response.status_code == 200
        assert "report" in response.context
