"""Business rules: the workflow, the money, and the risk model."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.enums import (
    DamageType,
    EquipmentCondition,
    EquipmentStatus,
    GenericStatus,
    Severity,
)

from .. import services
from .factories import (
    MaintenanceRecordFactory,
    MaintenanceScheduleFactory,
    UserFactory,
    make_equipment,
)

pytestmark = pytest.mark.django_db


def _status(equipment) -> str | None:
    equipment.refresh_from_db()
    return getattr(equipment, "status", None)


class TestReportIssue:
    def test_creates_a_record_and_withdraws_the_item(self):
        equipment = make_equipment()
        user = UserFactory()

        record = services.report_issue(
            equipment=equipment,
            damage_type=DamageType.CRACK,
            severity=Severity.MEDIUM,
            description="Hairline crack along the left rail.",
            user=user,
        )

        assert record.record_code.startswith("MNT")
        assert record.status == GenericStatus.OPEN
        assert record.reported_by == user
        assert _status(equipment) == EquipmentStatus.MAINTENANCE

    def test_high_severity_marks_the_item_damaged(self):
        equipment = make_equipment()
        services.report_issue(
            equipment=equipment,
            damage_type=DamageType.SNAPPED,
            severity=Severity.CRITICAL,
            description="Board snapped through at the centre.",
            user=UserFactory(),
        )
        assert _status(equipment) == EquipmentStatus.DAMAGED

    def test_cosmetic_report_leaves_the_item_in_service(self):
        equipment = make_equipment()
        services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.LOW,
            description="Cosmetic scuff on the deck.",
            user=UserFactory(),
            make_unusable=False,
        )
        assert _status(equipment) == EquipmentStatus.AVAILABLE

    def test_duplicate_open_report_is_refused_unless_forced(self):
        equipment = make_equipment()
        user = UserFactory()
        services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.LOW,
            description="Ding near the nose.",
            user=user,
        )

        with pytest.raises(ValidationError):
            services.report_issue(
                equipment=equipment,
                damage_type=DamageType.DING,
                severity=Severity.LOW,
                description="Ding near the nose (reported again).",
                user=user,
            )

        forced = services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.MEDIUM,
            description="A second, separate ding on the tail.",
            user=user,
            force=True,
        )
        assert forced.pk is not None

    def test_retired_equipment_cannot_take_new_work(self):
        equipment = make_equipment(status=EquipmentStatus.RETIRED)
        with pytest.raises(ValidationError):
            services.report_issue(
                equipment=equipment,
                damage_type=DamageType.GENERAL,
                severity=Severity.LOW,
                description="Anything.",
                user=UserFactory(),
            )

    def test_empty_description_is_refused(self):
        with pytest.raises(ValidationError):
            services.report_issue(
                equipment=make_equipment(),
                damage_type=DamageType.GENERAL,
                severity=Severity.LOW,
                description="   ",
                user=UserFactory(),
            )


class TestWorkflow:
    def test_start_work_stamps_and_assigns(self):
        record = MaintenanceRecordFactory()
        user = UserFactory()

        services.start_work(record, user=user)

        record.refresh_from_db()
        assert record.status == GenericStatus.IN_PROGRESS
        assert record.started_at is not None
        assert record.assigned_to == user

    def test_a_finished_record_cannot_be_restarted(self):
        record = MaintenanceRecordFactory(
            status=GenericStatus.RESOLVED, resolution="Repaired and cured."
        )
        with pytest.raises(ValidationError):
            services.start_work(record, user=UserFactory())

    def test_hold_requires_a_reason(self):
        record = MaintenanceRecordFactory()
        with pytest.raises(ValidationError):
            services.put_on_hold(record, reason="   ", user=UserFactory())

        services.put_on_hold(record, reason="Waiting for epoxy.", user=UserFactory())
        record.refresh_from_db()
        assert record.status == GenericStatus.ON_HOLD
        assert "Waiting for epoxy." in record.diagnosis


class TestCompleteMaintenance:
    def test_returns_the_item_to_service_and_books_the_cost(self):
        equipment = make_equipment()
        user = UserFactory()
        record = services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.MEDIUM,
            description="Open ding on the bottom.",
            user=user,
        )

        services.complete_maintenance(
            record,
            resolution="Sanded, filled with epoxy, cured and polished.",
            costs={
                "labour_hours": Decimal("2.50"),
                "parts_cost": Decimal("45.00"),
                "labour_cost": Decimal("125.00"),
            },
            user=user,
            condition_after=EquipmentCondition.GOOD,
        )

        record.refresh_from_db()
        assert record.status == GenericStatus.RESOLVED
        assert record.completed_at is not None
        assert record.total_cost == Decimal("170.00")
        assert _status(equipment) == EquipmentStatus.AVAILABLE

    def test_labour_cost_is_derived_from_the_workshop_rate(self):
        from apps.core.models import SystemSetting

        SystemSetting.set(services.LABOUR_RATE_SETTING, "40.00")
        record = MaintenanceRecordFactory()

        services.complete_maintenance(
            record,
            resolution="Replaced the leash plug.",
            costs={"labour_hours": Decimal("1.50"), "parts_cost": Decimal("10.00")},
            user=UserFactory(),
        )

        record.refresh_from_db()
        assert record.labour_cost == Decimal("60.00")
        assert record.total_cost == Decimal("70.00")

    def test_item_stays_out_of_service_while_another_record_is_open(self):
        equipment = make_equipment()
        user = UserFactory()
        first = services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.MEDIUM,
            description="Ding on the deck.",
            user=user,
        )
        services.report_issue(
            equipment=equipment,
            damage_type=DamageType.FIN_DAMAGE,
            severity=Severity.HIGH,
            description="Fin box is loose.",
            user=user,
        )

        services.complete_maintenance(
            first, resolution="Ding filled.", costs={}, user=user
        )

        assert _status(equipment) != EquipmentStatus.AVAILABLE

    def test_still_unusable_keeps_the_item_damaged(self):
        equipment = make_equipment()
        record = MaintenanceRecordFactory(equipment=equipment)
        services.complete_maintenance(
            record,
            resolution="Temporary patch only — needs a proper laminate.",
            costs={},
            user=UserFactory(),
            still_unusable=True,
        )
        assert _status(equipment) == EquipmentStatus.DAMAGED

    def test_write_off_retires_the_item(self):
        equipment = make_equipment()
        record = MaintenanceRecordFactory(equipment=equipment)
        services.complete_maintenance(
            record,
            resolution="Snapped beyond economical repair.",
            costs={},
            user=UserFactory(),
            retire_equipment=True,
        )
        assert _status(equipment) == EquipmentStatus.RETIRED

    def test_completion_rolls_the_service_plan_forward(self):
        equipment = make_equipment()
        schedule = MaintenanceScheduleFactory(equipment=equipment, interval_days=60)
        record = MaintenanceRecordFactory(equipment=equipment)

        services.complete_maintenance(
            record, resolution="Full service.", costs={}, user=UserFactory()
        )

        schedule.refresh_from_db()
        assert schedule.last_performed_on == timezone.localdate()
        assert schedule.next_due_on == timezone.localdate() + timedelta(days=60)

    def test_resolution_is_required(self):
        record = MaintenanceRecordFactory()
        with pytest.raises(ValidationError):
            services.complete_maintenance(record, resolution="", costs={}, user=UserFactory())


class TestCancelMaintenance:
    def test_cancelling_releases_the_item(self):
        equipment = make_equipment()
        user = UserFactory()
        record = services.report_issue(
            equipment=equipment,
            damage_type=DamageType.DING,
            severity=Severity.LOW,
            description="Reported in error.",
            user=user,
        )

        services.cancel_maintenance(record, reason="Wrong board scanned.", user=user)

        record.refresh_from_db()
        assert record.status == GenericStatus.CANCELLED
        assert _status(equipment) == EquipmentStatus.AVAILABLE

    def test_a_completed_record_cannot_be_cancelled(self):
        record = MaintenanceRecordFactory()
        services.complete_maintenance(
            record, resolution="Done.", costs={}, user=UserFactory()
        )
        with pytest.raises(ValidationError):
            services.cancel_maintenance(record, reason="Changed my mind.", user=UserFactory())


class TestScheduleServices:
    def test_due_for_scheduled_maintenance_returns_only_due_active_plans(self):
        due = MaintenanceScheduleFactory(
            next_due_on=timezone.localdate() - timedelta(days=1)
        )
        MaintenanceScheduleFactory(next_due_on=timezone.localdate() + timedelta(days=30))
        MaintenanceScheduleFactory(
            is_active=False, next_due_on=timezone.localdate() - timedelta(days=5)
        )

        results = list(services.due_for_scheduled_maintenance())
        assert [s.pk for s in results] == [due.pk]

    def test_look_ahead_window(self):
        MaintenanceScheduleFactory(next_due_on=timezone.localdate() + timedelta(days=10))
        assert services.due_for_scheduled_maintenance().count() == 0
        assert services.due_for_scheduled_maintenance(within_days=14).count() == 1

    def test_mark_schedule_performed_rejects_a_future_date(self):
        schedule = MaintenanceScheduleFactory()
        with pytest.raises(ValidationError):
            services.mark_schedule_performed(
                schedule, performed_on=timezone.localdate() + timedelta(days=1)
            )


class TestCostReport:
    def test_only_completed_repairs_are_counted(self):
        user = UserFactory()
        completed = MaintenanceRecordFactory()
        services.complete_maintenance(
            completed,
            resolution="Repaired.",
            costs={"parts_cost": Decimal("100.00"), "labour_cost": Decimal("50.00")},
            user=user,
        )
        # An open record with costs already entered must not inflate the report.
        MaintenanceRecordFactory(parts_cost=Decimal("999.00"))

        report = services.maintenance_cost_report()

        assert report["records"] == 1
        assert report["total_cost"] == Decimal("150.00")
        assert report["parts_cost"] == Decimal("100.00")
        assert report["average_cost"] == Decimal("150.00")

    def test_report_is_bounded_by_the_date_range(self):
        user = UserFactory()
        record = MaintenanceRecordFactory()
        services.complete_maintenance(
            record, resolution="Repaired.", costs={"parts_cost": Decimal("10.00")}, user=user
        )

        past_start = timezone.now() - timedelta(days=60)
        past_end = timezone.now() - timedelta(days=30)
        assert services.maintenance_cost_report(past_start, past_end)["records"] == 0
        assert services.maintenance_cost_report(past_start, timezone.now())["records"] == 1


class TestPredictMaintenanceNeeds:
    def test_returns_a_bounded_score_for_every_active_item(self):
        make_equipment()
        make_equipment()
        make_equipment(status=EquipmentStatus.RETIRED)

        predictions = services.predict_maintenance_needs()

        assert len(predictions) == 2
        for prediction in predictions:
            assert 0.0 <= prediction["risk_score"] <= 100.0
            assert 0.0 <= prediction["confidence"] <= 1.0
            assert prediction["signals_total"] == len(services.SIGNAL_WEIGHTS)

    def test_is_deterministic(self):
        make_equipment()
        make_equipment()
        first = services.predict_maintenance_needs()
        second = services.predict_maintenance_needs()
        assert [(p["equipment_id"], p["risk_score"]) for p in first] == [
            (p["equipment_id"], p["risk_score"]) for p in second
        ]

    def test_results_are_sorted_by_descending_risk(self):
        for _ in range(4):
            make_equipment()
        scores = [p["risk_score"] for p in services.predict_maintenance_needs()]
        assert scores == sorted(scores, reverse=True)

    def test_an_overdue_schedule_raises_the_score(self):
        calm = make_equipment()
        overdue = make_equipment()
        MaintenanceScheduleFactory(
            equipment=calm,
            interval_days=90,
            last_performed_on=timezone.localdate() - timedelta(days=5),
            next_due_on=timezone.localdate() + timedelta(days=85),
        )
        MaintenanceScheduleFactory(
            equipment=overdue,
            interval_days=30,
            last_performed_on=timezone.localdate() - timedelta(days=120),
            next_due_on=timezone.localdate() - timedelta(days=90),
        )

        by_id = {p["equipment_id"]: p for p in services.predict_maintenance_needs()}
        assert by_id[overdue.pk]["risk_score"] > by_id[calm.pk]["risk_score"]

    def test_missing_data_lowers_confidence_and_is_stated(self):
        equipment = make_equipment()
        prediction = next(
            p
            for p in services.predict_maintenance_needs()
            if p["equipment_id"] == equipment.pk
        )

        unavailable = [r for r in prediction["reasons"] if not r["available"]]
        assert unavailable, "an item with no history must report unmeasured signals"
        assert prediction["confidence"] < 1.0
        assert prediction["signals_used"] < prediction["signals_total"]

        # Every unmeasured signal must say so in plain language, never guess.
        for reason in unavailable:
            assert services.describe_reason(reason)
            assert reason["score"] == 0.0

    def test_severe_failure_history_raises_the_score(self):
        quiet = make_equipment()
        broken = make_equipment()
        for _ in range(3):
            MaintenanceRecordFactory(
                equipment=broken,
                severity=Severity.CRITICAL,
                status=GenericStatus.RESOLVED,
                resolution="Repaired.",
                reported_at=timezone.now() - timedelta(days=30),
                completed_at=timezone.now() - timedelta(days=29),
            )

        by_id = {p["equipment_id"]: p for p in services.predict_maintenance_needs()}
        assert by_id[broken.pk]["risk_score"] > by_id[quiet.pk]["risk_score"]

    def test_cached_predictions_round_trip(self):
        make_equipment()
        payload = services.store_maintenance_predictions()
        assert "generated_at" in payload
        cached = services.cached_maintenance_predictions()
        assert cached["generated_at"] == payload["generated_at"]

    def test_reason_texts_are_attached_for_display(self):
        make_equipment()
        predictions = services.annotate_prediction_texts(
            services.predict_maintenance_needs()
        )
        assert predictions[0]["action_label"]
        assert all("text" in r for r in predictions[0]["reason_texts"])


class TestPressureCurve:
    """The scoring curve is the one place a silent change would skew every card."""

    def test_curve_is_monotone_and_bounded(self):
        assert services._pressure(0) == 0.0
        assert services._pressure(0.5) == pytest.approx(0.35)
        assert services._pressure(1.0) == pytest.approx(0.70)
        assert services._pressure(1.5) == pytest.approx(1.0)
        assert services._pressure(9.0) == pytest.approx(1.0)

        previous = -1.0
        for step in range(30):
            value = services._pressure(step / 10)
            assert value >= previous
            previous = value
