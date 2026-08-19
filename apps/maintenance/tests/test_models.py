"""Model behaviour: codes, derived values, and the validation that guards them."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import GenericStatus, Severity

from ..models import MaintenanceRecord, MaintenanceSchedule
from .factories import (
    MaintenanceRecordFactory,
    MaintenanceScheduleFactory,
    make_equipment,
)

pytestmark = pytest.mark.django_db


class TestMaintenanceRecord:
    def test_code_is_assigned_sequentially(self):
        first = MaintenanceRecordFactory()
        second = MaintenanceRecordFactory()
        assert first.record_code == "MNT00001"
        assert second.record_code == "MNT00002"

    def test_str_includes_code_and_damage(self):
        record = MaintenanceRecordFactory()
        assert record.record_code in str(record)
        assert str(record.get_damage_type_display()) in str(record)

    def test_recalculate_cost_sums_parts_and_labour(self):
        record = MaintenanceRecordFactory(
            parts_cost=Decimal("120.50"), labour_cost=Decimal("80.00")
        )
        assert record.total_cost == Decimal("200.50")

        record.parts_cost = Decimal("10.00")
        assert record.recalculate_cost(save=True) == Decimal("90.00")
        record.refresh_from_db()
        assert record.total_cost == Decimal("90.00")

    def test_is_open_tracks_status(self):
        record = MaintenanceRecordFactory(status=GenericStatus.OPEN)
        assert record.is_open is True
        record.status = GenericStatus.RESOLVED
        assert record.is_open is False

    def test_downtime_only_counts_items_taken_out_of_service(self):
        reported = timezone.now() - timedelta(days=5)
        withdrawn = MaintenanceRecordFactory(reported_at=reported, made_unusable=True)
        cosmetic = MaintenanceRecordFactory(reported_at=reported, made_unusable=False)

        assert withdrawn.downtime_days == 5
        assert cosmetic.downtime_days == 0
        # Elapsed time is still reported for both.
        assert cosmetic.age_days == 5

    def test_repair_days_needs_both_timestamps(self):
        record = MaintenanceRecordFactory()
        assert record.repair_days is None
        record.started_at = timezone.now() - timedelta(days=2)
        record.completed_at = timezone.now()
        assert record.repair_days == 2

    def test_clean_rejects_completion_before_start(self):
        record = MaintenanceRecordFactory(
            started_at=timezone.now(), completed_at=timezone.now() - timedelta(hours=1)
        )
        with pytest.raises(ValidationError) as excinfo:
            record.clean()
        assert "completed_at" in excinfo.value.message_dict

    def test_clean_requires_a_resolution_before_closing(self):
        record = MaintenanceRecordFactory(status=GenericStatus.RESOLVED, resolution="")
        with pytest.raises(ValidationError) as excinfo:
            record.clean()
        assert "resolution" in excinfo.value.message_dict

    def test_clean_rejects_negative_money(self):
        record = MaintenanceRecordFactory(parts_cost=Decimal("-1.00"))
        with pytest.raises(ValidationError) as excinfo:
            record.clean()
        assert "parts_cost" in excinfo.value.message_dict

    def test_severity_rank_orders_records(self):
        low = MaintenanceRecordFactory(severity=Severity.LOW)
        critical = MaintenanceRecordFactory(severity=Severity.CRITICAL)
        assert critical.severity_rank > low.severity_rank

    def test_soft_delete_hides_the_row_from_the_default_manager(self):
        record = MaintenanceRecordFactory()
        record.delete()
        assert not MaintenanceRecord.objects.filter(pk=record.pk).exists()
        assert MaintenanceRecord.all_objects.filter(pk=record.pk).exists()


class TestMaintenanceSchedule:
    def test_save_computes_the_next_due_date(self):
        schedule = MaintenanceScheduleFactory(interval_days=30, last_performed_on=None)
        assert schedule.next_due_on == timezone.localdate() + timedelta(days=30)

    def test_is_due_and_days_until_due(self):
        schedule = MaintenanceScheduleFactory(
            interval_days=30, next_due_on=timezone.localdate() - timedelta(days=3)
        )
        assert schedule.is_due is True
        assert schedule.is_overdue is True
        assert schedule.days_until_due == -3
        assert schedule.overdue_days == 3

        future = MaintenanceScheduleFactory(
            next_due_on=timezone.localdate() + timedelta(days=10)
        )
        assert future.is_due is False
        assert future.days_until_due == 10

    def test_inactive_schedule_is_never_due(self):
        schedule = MaintenanceScheduleFactory(
            is_active=False, next_due_on=timezone.localdate() - timedelta(days=30)
        )
        assert schedule.is_due is False

    def test_mark_performed_rolls_the_plan_forward(self):
        schedule = MaintenanceScheduleFactory(interval_days=45)
        performed = timezone.localdate() - timedelta(days=1)
        schedule.mark_performed(performed)
        schedule.refresh_from_db()

        assert schedule.last_performed_on == performed
        assert schedule.next_due_on == performed + timedelta(days=45)

    def test_check_item_list_normalises_stored_values(self):
        schedule = MaintenanceScheduleFactory(check_items=["  Fins  ", "", "Leash"])
        assert schedule.check_item_list == ["Fins", "Leash"]

    def test_clean_rejects_a_future_service_date(self):
        schedule = MaintenanceScheduleFactory()
        schedule.last_performed_on = timezone.localdate() + timedelta(days=1)
        with pytest.raises(ValidationError) as excinfo:
            schedule.clean()
        assert "last_performed_on" in excinfo.value.message_dict

    def test_one_schedule_per_item(self):
        equipment = make_equipment()
        MaintenanceScheduleFactory(equipment=equipment)

        duplicate = MaintenanceSchedule(equipment=equipment, interval_days=60)
        with pytest.raises(ValidationError) as excinfo:
            duplicate.full_clean()
        assert "equipment" in excinfo.value.message_dict
