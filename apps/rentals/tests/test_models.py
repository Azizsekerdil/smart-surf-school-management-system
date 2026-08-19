"""Model-level rules: codes, totals, overdue arithmetic, constraints."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.enums import EquipmentCondition, PaymentStatus
from apps.rentals.models import Rental, RentalItem

from .factories import RentalFactory, RentalItemFactory, make_equipment

pytestmark = pytest.mark.django_db


def test_rental_code_is_sequential_and_unique():
    first = RentalFactory()
    second = RentalFactory()
    assert first.rental_code.startswith("RNT")
    assert first.rental_code != second.rental_code
    assert str(first).startswith(first.rental_code)


def test_item_line_total_is_derived_from_price_and_quantity():
    item = RentalItemFactory(unit_price=Decimal("35.50"), quantity=3)
    assert item.line_total == Decimal("106.50")
    assert str(item).endswith("× 3")


def test_recalculate_totals_sums_lines_and_derives_payment_status():
    rental = RentalFactory(discount_amount=Decimal("10.00"))
    RentalItemFactory(rental=rental, unit_price=Decimal("40.00"), quantity=2)
    RentalItemFactory(rental=rental, unit_price=Decimal("20.00"), quantity=1)

    total = rental.recalculate_totals()

    assert rental.subtotal == Decimal("100.00")
    assert total == Decimal("90.00")
    assert rental.payment_status == PaymentStatus.UNPAID

    rental.paid_amount = Decimal("40.00")
    rental.recalculate_totals()
    assert rental.payment_status == PaymentStatus.PARTIAL

    rental.paid_amount = Decimal("90.00")
    rental.recalculate_totals()
    assert rental.payment_status == PaymentStatus.PAID


def test_discount_can_never_exceed_the_hire_charge():
    rental = RentalFactory(discount_amount=Decimal("500.00"))
    RentalItemFactory(rental=rental, unit_price=Decimal("40.00"))
    rental.recalculate_totals()
    assert rental.discount_amount == Decimal("40.00")
    assert rental.total_amount == Decimal("0.00")


def test_damage_charges_flow_into_the_total():
    rental = RentalFactory()
    item = RentalItemFactory(rental=rental, unit_price=Decimal("40.00"))
    item.damage_reported = True
    item.damage_type = "ding"
    item.damage_charge = Decimal("25.00")
    item.save()

    rental.recalculate_totals()
    assert rental.damage_fee == Decimal("25.00")
    assert rental.total_amount == Decimal("65.00")


def test_overdue_properties():
    now = timezone.now()
    rental = RentalFactory(
        start_at=now - timedelta(days=2), expected_return_at=now - timedelta(hours=5)
    )
    assert rental.is_overdue is True
    assert rental.hours_overdue >= Decimal("4.99")

    rental.status = Rental.Status.RETURNED
    rental.returned_at = now - timedelta(hours=3)
    assert rental.is_overdue is False
    # A late return still reports how late it was.
    assert rental.hours_overdue == Decimal("2.00")


def test_duration_and_item_counts():
    now = timezone.now()
    rental = RentalFactory(start_at=now, expected_return_at=now + timedelta(hours=6))
    RentalItemFactory(rental=rental, quantity=2)
    RentalItemFactory(rental=rental, quantity=1)

    assert rental.duration_hours == Decimal("6.00")
    assert rental.item_count == 3
    assert rental.open_item_count == 3
    assert rental.can_check_in is True


def test_due_back_must_be_after_the_start():
    now = timezone.now()
    rental = RentalFactory.build(
        customer=RentalFactory().customer,
        start_at=now,
        expected_return_at=now - timedelta(hours=1),
    )
    with pytest.raises(ValidationError) as excinfo:
        rental.clean()
    assert "expected_return_at" in excinfo.value.message_dict


def test_deposit_returned_cannot_exceed_deposit_taken():
    rental = RentalFactory(deposit_amount=Decimal("50.00"))
    rental.deposit_returned = Decimal("80.00")
    with pytest.raises(ValidationError) as excinfo:
        rental.clean()
    assert "deposit_returned" in excinfo.value.message_dict


def test_damage_charge_requires_reported_damage():
    item = RentalItemFactory()
    item.damage_charge = Decimal("30.00")
    with pytest.raises(ValidationError) as excinfo:
        item.clean()
    assert "damage_charge" in excinfo.value.message_dict


def test_reported_damage_requires_a_type():
    item = RentalItemFactory()
    item.damage_reported = True
    with pytest.raises(ValidationError) as excinfo:
        item.clean()
    assert "damage_type" in excinfo.value.message_dict


def test_the_same_asset_cannot_be_listed_twice_on_one_rental():
    rental = RentalFactory()
    asset = make_equipment()
    RentalItemFactory(rental=rental, equipment=asset)
    with pytest.raises(IntegrityError), transaction.atomic():
        RentalItem.objects.create(
            rental=rental,
            equipment=asset,
            unit_price=Decimal("10.00"),
            condition_out=EquipmentCondition.GOOD,
        )


def test_soft_delete_hides_the_rental_from_the_default_manager():
    rental = RentalFactory()
    rental.delete()
    assert not Rental.objects.filter(pk=rental.pk).exists()
    assert Rental.all_objects.filter(pk=rental.pk).exists()
