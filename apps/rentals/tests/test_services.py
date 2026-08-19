"""The money and availability rules — the part a surf school cannot get wrong."""

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
    RentalPeriod,
)
from apps.rentals import services
from apps.rentals.models import Rental

from .factories import RentalFactory, RentalItemFactory, make_customer, make_equipment, make_user


# ---------------------------------------------------------------------------
# Pricing — pure arithmetic, no database needed
# ---------------------------------------------------------------------------
class StubAsset:
    """Stands in for ``equipment.Equipment`` while only rates matter."""

    def __init__(self, hourly=None, daily=None, weekly=None):
        self.hourly_rate = hourly
        self.daily_rate = daily
        self.weekly_rate = weekly


NOW = timezone.now().replace(microsecond=0)


def price(asset, period, hours=None, days=None, quantity=1):
    end = NOW + (timedelta(hours=hours) if hours is not None else timedelta(days=days))
    return services.calculate_rental_price(asset, period, NOW, end, quantity)


def test_part_hours_round_up_to_the_next_whole_hour():
    asset = StubAsset(hourly=Decimal("10.00"))
    assert price(asset, RentalPeriod.HOURLY, hours=1) == Decimal("10.00")
    assert services.calculate_rental_price(
        asset, RentalPeriod.HOURLY, NOW, NOW + timedelta(minutes=61)
    ) == Decimal("20.00")


def test_part_days_round_up_to_the_next_whole_day():
    asset = StubAsset(daily=Decimal("40.00"))
    assert price(asset, RentalPeriod.DAILY, hours=25) == Decimal("80.00")
    assert price(asset, RentalPeriod.DAILY, hours=24) == Decimal("40.00")


def test_a_long_hourly_hire_is_never_dearer_than_the_daily_rate():
    asset = StubAsset(hourly=Decimal("10.00"), daily=Decimal("40.00"))
    # 30 hours at the hourly rate would be 300; two days is 80.
    assert price(asset, RentalPeriod.HOURLY, hours=30) == Decimal("80.00")


def test_seven_days_or_more_uses_the_weekly_rate_when_it_is_cheaper():
    asset = StubAsset(daily=Decimal("40.00"), weekly=Decimal("200.00"))
    assert price(asset, RentalPeriod.DAILY, days=7) == Decimal("200.00")
    # Eight days is two weeks (400) or eight days (320) — the customer pays 320.
    assert price(asset, RentalPeriod.DAILY, days=8) == Decimal("320.00")


def test_weekly_hire_rounds_up_to_whole_weeks():
    asset = StubAsset(weekly=Decimal("200.00"))
    assert price(asset, RentalPeriod.WEEKLY, days=8) == Decimal("400.00")


def test_quantity_multiplies_the_line():
    asset = StubAsset(daily=Decimal("40.00"))
    assert price(asset, RentalPeriod.DAILY, days=1, quantity=3) == Decimal("120.00")


def test_a_missing_weekly_rate_falls_back_to_seven_days():
    asset = StubAsset(daily=Decimal("40.00"))
    assert services.equipment_rate(asset, RentalPeriod.WEEKLY) == Decimal("280.00")


def test_a_missing_hourly_rate_is_derived_from_the_day_rate():
    asset = StubAsset(daily=Decimal("40.00"))
    assert services.equipment_rate(asset, RentalPeriod.HOURLY) == Decimal("5.00")


def test_a_window_that_ends_before_it_starts_is_rejected():
    asset = StubAsset(daily=Decimal("40.00"))
    with pytest.raises(ValidationError):
        services.calculate_rental_price(asset, RentalPeriod.DAILY, NOW, NOW - timedelta(hours=1))


def test_minors_are_detected_from_a_date_of_birth():
    class Person:
        date_of_birth = timezone.localdate() - timedelta(days=365 * 15)

    class Adult:
        date_of_birth = timezone.localdate() - timedelta(days=365 * 30)

    assert services.is_minor(Person()) is True
    assert services.is_minor(Adult()) is False
    assert services.is_minor(None) is False


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_rental_prices_lines_and_takes_the_gear_out_of_stock():
    customer = make_customer()
    asset = make_equipment()
    start = timezone.now()

    rental = services.create_rental(
        customer=customer,
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=2),
        deposit_amount=Decimal("100.00"),
    )

    asset.refresh_from_db()
    assert rental.status == Rental.Status.ACTIVE
    assert rental.subtotal == Decimal("80.00")
    assert rental.total_amount == Decimal("80.00")
    assert rental.items.count() == 1
    assert asset.status == EquipmentStatus.RENTED


@pytest.mark.django_db
def test_a_future_hire_reserves_rather_than_releases_the_asset():
    asset = make_equipment()
    start = timezone.now() + timedelta(days=3)
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    asset.refresh_from_db()
    assert rental.status == Rental.Status.RESERVED
    assert asset.status == EquipmentStatus.RESERVED


@pytest.mark.django_db
def test_the_same_board_cannot_go_out_twice_at_once():
    asset = make_equipment()
    start = timezone.now()
    services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        services.create_rental(
            customer=make_customer(),
            items=[(asset, 1)],
            period_type=RentalPeriod.DAILY,
            start_at=start,
            expected_return_at=start + timedelta(days=1),
        )


@pytest.mark.django_db
def test_equipment_in_maintenance_cannot_be_hired():
    asset = make_equipment(status=EquipmentStatus.MAINTENANCE)
    start = timezone.now()
    with pytest.raises(ValidationError):
        services.create_rental(
            customer=make_customer(),
            items=[(asset, 1)],
            period_type=RentalPeriod.DAILY,
            start_at=start,
            expected_return_at=start + timedelta(days=1),
        )


@pytest.mark.django_db
def test_a_hire_to_a_minor_requires_an_identity_document(monkeypatch):
    monkeypatch.setattr(services, "is_minor", lambda person: person is not None)
    start = timezone.now()
    with pytest.raises(ValidationError):
        services.create_rental(
            customer=make_customer(),
            items=[(make_equipment(), 1)],
            period_type=RentalPeriod.DAILY,
            start_at=start,
            expected_return_at=start + timedelta(days=1),
            id_document_held=False,
        )


@pytest.mark.django_db
def test_an_empty_basket_is_refused():
    start = timezone.now()
    with pytest.raises(ValidationError):
        services.create_rental(
            customer=make_customer(),
            items=[],
            period_type=RentalPeriod.DAILY,
            start_at=start,
            expected_return_at=start + timedelta(days=1),
        )


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------
def _overdue_rental(hours_late=48, deposit="100.00"):
    """A one-day hire at 40/day that was due back *hours_late* hours ago."""
    asset = make_equipment()
    start = timezone.now() - timedelta(hours=24 + hours_late)
    return (
        services.create_rental(
            customer=make_customer(),
            items=[(asset, 1)],
            period_type=RentalPeriod.DAILY,
            start_at=start,
            expected_return_at=start + timedelta(days=1),
            deposit_amount=Decimal(deposit),
        ),
        asset,
    )


@pytest.mark.django_db
def test_returning_late_charges_the_overdue_units_and_settles_the_deposit():
    rental, asset = _overdue_rental(hours_late=48)
    user = make_user()

    rental = services.return_rental(rental, {}, user)
    asset.refresh_from_db()

    assert rental.status == Rental.Status.RETURNED
    assert rental.subtotal == Decimal("40.00")
    assert rental.late_fee == Decimal("80.00")  # two overdue days at 40
    assert rental.total_amount == Decimal("120.00")
    assert rental.deposit_returned == Decimal("20.00")
    assert rental.deposit_status == Rental.DepositStatus.FORFEITED
    assert rental.paid_amount == Decimal("80.00")
    assert rental.balance_due == Decimal("40.00")
    assert asset.status == EquipmentStatus.AVAILABLE
    assert rental.checked_in_by_id == user.pk


@pytest.mark.django_db
def test_the_late_fee_is_capped_at_three_times_the_hire_charge():
    rental, _asset = _overdue_rental(hours_late=24 * 60)
    rental = services.return_rental(rental, {}, make_user())
    assert rental.subtotal == Decimal("40.00")
    assert rental.late_fee == Decimal("120.00")


@pytest.mark.django_db
def test_returning_on_time_costs_nothing_extra_and_refunds_the_deposit():
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
        deposit_amount=Decimal("100.00"),
    )
    rental = services.return_rental(rental, {}, make_user())
    assert rental.late_fee == Decimal("0.00")
    assert rental.deposit_returned == Decimal("100.00")
    assert rental.deposit_status == Rental.DepositStatus.RETURNED


@pytest.mark.django_db
def test_damage_charges_the_customer_and_sends_the_asset_to_the_workshop():
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
        deposit_amount=Decimal("100.00"),
    )
    item = rental.items.first()

    rental = services.return_rental(
        rental,
        {item.pk: (EquipmentCondition.POOR, DamageType.DING, "Nose ding", Decimal("60.00"))},
        make_user(),
    )
    asset.refresh_from_db()
    item.refresh_from_db()

    assert item.damage_reported is True
    assert item.condition_in == EquipmentCondition.POOR
    assert rental.damage_fee == Decimal("60.00")
    assert rental.total_amount == Decimal("100.00")
    assert asset.status == EquipmentStatus.MAINTENANCE
    assert rental.deposit_returned == Decimal("40.00")


@pytest.mark.django_db
def test_unusable_gear_comes_back_as_damaged_not_maintenance():
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    item = rental.items.first()
    services.return_rental(
        rental,
        {item.pk: (EquipmentCondition.UNUSABLE, DamageType.SNAPPED, "Snapped", Decimal("0.00"))},
        make_user(),
    )
    asset.refresh_from_db()
    assert asset.status == EquipmentStatus.DAMAGED


@pytest.mark.django_db
def test_a_partial_return_keeps_the_contract_open():
    board = make_equipment()
    wetsuit = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(board, 1), (wetsuit, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    first = rental.items.first()

    rental = services.return_rental(rental, {first.pk: (EquipmentCondition.GOOD, "", "", 0)}, make_user())

    assert rental.status == Rental.Status.ACTIVE
    assert rental.returned_at is None
    assert rental.open_item_count == 1


@pytest.mark.django_db
def test_a_finished_rental_cannot_be_checked_in_twice():
    rental, _asset = _overdue_rental(hours_late=1)
    services.return_rental(rental, {}, make_user())
    with pytest.raises(ValidationError):
        services.return_rental(rental, {}, make_user())


@pytest.mark.django_db
def test_quick_return_by_asset_code_finds_the_open_contract():
    asset = make_equipment()
    start = timezone.now()
    services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    rental, item = services.quick_return_by_asset_code(
        services.equipment_code(asset), user=make_user()
    )
    assert rental.status == Rental.Status.RETURNED
    assert item.returned_at is not None

    with pytest.raises(ValidationError):
        services.quick_return_by_asset_code("NOPE-1", user=make_user())


# ---------------------------------------------------------------------------
# Contract changes and reporting
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_extending_a_hire_reprices_it():
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )
    assert rental.total_amount == Decimal("40.00")

    rental = services.extend_rental(rental, start + timedelta(days=3), user=make_user())
    assert rental.expected_return_at == start + timedelta(days=3)
    assert rental.total_amount == Decimal("120.00")


@pytest.mark.django_db
def test_an_extension_must_move_the_clock_forward():
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=2),
    )
    with pytest.raises(ValidationError):
        services.extend_rental(rental, start + timedelta(days=1), user=make_user())


@pytest.mark.django_db
def test_cancelling_a_reservation_releases_the_gear():
    asset = make_equipment()
    start = timezone.now() + timedelta(days=5)
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
        deposit_amount=Decimal("50.00"),
    )
    rental = services.cancel_rental(rental, user=make_user(), reason="Customer called off")
    asset.refresh_from_db()

    assert rental.status == Rental.Status.CANCELLED
    assert rental.total_amount == Decimal("0.00")
    assert rental.deposit_returned == Decimal("50.00")
    assert asset.status == EquipmentStatus.AVAILABLE


@pytest.mark.django_db
def test_a_live_hire_cannot_be_cancelled():
    rental, _asset = _overdue_rental(hours_late=1)
    with pytest.raises(ValidationError):
        services.cancel_rental(rental, user=make_user())


@pytest.mark.django_db
def test_writing_off_lost_gear_charges_the_replacement_and_keeps_the_deposit():
    asset = make_equipment()
    start = timezone.now() - timedelta(days=10)
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
        deposit_amount=Decimal("100.00"),
    )
    rental = services.mark_rental_lost(
        rental, replacement_charge=Decimal("450.00"), user=make_user()
    )
    asset.refresh_from_db()

    assert rental.status == Rental.Status.LOST
    assert rental.damage_fee == Decimal("450.00")
    assert rental.deposit_returned == Decimal("0.00")
    assert rental.deposit_status == Rental.DepositStatus.FORFEITED
    assert asset.status == EquipmentStatus.LOST


@pytest.mark.django_db
def test_flag_overdue_rentals_is_idempotent():
    RentalFactory(
        status=Rental.Status.ACTIVE,
        start_at=timezone.now() - timedelta(days=3),
        expected_return_at=timezone.now() - timedelta(days=1),
    )
    RentalFactory(
        status=Rental.Status.ACTIVE,
        start_at=timezone.now() - timedelta(hours=1),
        expected_return_at=timezone.now() + timedelta(days=1),
    )

    assert services.flag_overdue_rentals() == 1
    assert services.flag_overdue_rentals() == 0
    assert Rental.objects.filter(status=Rental.Status.OVERDUE).count() == 1


@pytest.mark.django_db
def test_register_payment_updates_the_balance():
    rental = RentalFactory()
    RentalItemFactory(rental=rental, unit_price=Decimal("60.00"))
    rental.recalculate_totals()

    rental = services.register_payment(rental, Decimal("25.00"), user=make_user())
    assert rental.paid_amount == Decimal("25.00")
    assert rental.balance_due == Decimal("35.00")

    with pytest.raises(ValidationError):
        services.register_payment(rental, Decimal("0.00"), user=make_user())


@pytest.mark.django_db
def test_rental_revenue_ignores_cancellations():
    start = timezone.now()
    paid = RentalFactory(start_at=start)
    RentalItemFactory(rental=paid, unit_price=Decimal("100.00"))
    paid.recalculate_totals()

    scrapped = RentalFactory(start_at=start, status=Rental.Status.CANCELLED)
    RentalItemFactory(rental=scrapped, unit_price=Decimal("500.00"))
    scrapped.recalculate_totals()

    revenue = services.rental_revenue(start - timedelta(days=1), start + timedelta(days=1))
    assert revenue == Decimal("100.00")
