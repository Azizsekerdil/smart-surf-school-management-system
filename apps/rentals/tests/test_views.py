"""Screen-level tests: access control, the counter flow, the check-in flow."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import EquipmentCondition, EquipmentStatus, RentalPeriod
from apps.rentals import services
from apps.rentals.models import Rental

from .factories import RentalFactory, RentalItemFactory, make_customer, make_equipment, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client):
    user = make_user(role=Role.RENTAL_STAFF)
    client.force_login(user)
    return user


def test_list_view_renders_for_rental_staff(client, staff):
    rental = RentalFactory()
    RentalItemFactory(rental=rental)

    response = client.get(reverse("rentals:list"))

    assert response.status_code == 200
    assert rental.rental_code in response.content.decode()


def test_list_view_tabs_filter_by_status(client, staff):
    RentalFactory(status=Rental.Status.RETURNED, returned_at=timezone.now())
    overdue = RentalFactory(
        status=Rental.Status.OVERDUE,
        start_at=timezone.now() - timedelta(days=3),
        expected_return_at=timezone.now() - timedelta(days=1),
    )

    response = client.get(reverse("rentals:list"), {"tab": "overdue"})
    body = response.content.decode()

    assert response.status_code == 200
    assert overdue.rental_code in body


def test_detail_view_renders(client, staff):
    rental = RentalFactory()
    RentalItemFactory(rental=rental)
    response = client.get(reverse("rentals:detail", args=[rental.pk]))
    assert response.status_code == 200


def test_a_role_without_the_capability_is_refused(client):
    user = make_user(role=Role.PHOTOGRAPHER)
    client.force_login(user)
    response = client.get(reverse("rentals:list"))
    assert response.status_code == 403


def test_anonymous_users_are_redirected_to_login(client):
    response = client.get(reverse("rentals:list"))
    assert response.status_code in (301, 302)


def test_checkout_screen_loads(client, staff):
    response = client.get(reverse("rentals:create"))
    assert response.status_code == 200


def test_scanning_an_asset_adds_a_priced_basket_line(client, staff):
    asset = make_equipment()
    start = timezone.now()

    response = client.post(
        reverse("rentals:basket_add"),
        {
            "asset_code": services.equipment_code(asset),
            "period_type": RentalPeriod.DAILY,
            "start_at": start.strftime("%Y-%m-%dT%H:%M"),
            "expected_return_at": (start + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert services.equipment_code(asset) in body
    assert client.session["rentals.basket"][0]["equipment_id"] == asset.pk


def test_an_unknown_asset_code_is_reported_not_added(client, staff):
    response = client.post(reverse("rentals:basket_add"), {"asset_code": "NOPE-999"})
    assert response.status_code == 200
    assert "NOPE-999" in response.content.decode()
    assert not client.session.get("rentals.basket")


def test_check_out_creates_the_rental_and_empties_the_basket(client, staff):
    customer = make_customer()
    asset = make_equipment()
    start = timezone.now().replace(second=0, microsecond=0)

    client.post(
        reverse("rentals:basket_add"),
        {"asset_code": services.equipment_code(asset), "period_type": RentalPeriod.DAILY},
    )

    response = client.post(
        reverse("rentals:create"),
        {
            "customer": customer.pk,
            "period_type": RentalPeriod.DAILY,
            "start_at": start.strftime("%Y-%m-%dT%H:%M"),
            "expected_return_at": (start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "deposit_amount": "100.00",
            "discount_amount": "0.00",
            "paid_amount": "0.00",
            "notes": "",
        },
    )

    rental = Rental.objects.filter(customer=customer).first()
    assert rental is not None
    assert response.status_code == 302
    assert response["Location"] == reverse("rentals:detail", args=[rental.pk])
    assert rental.items.count() == 1
    assert rental.total_amount == Decimal("40.00")
    assert not client.session.get("rentals.basket")


def test_check_out_without_a_basket_is_refused(client, staff):
    customer = make_customer()
    start = timezone.now()
    response = client.post(
        reverse("rentals:create"),
        {
            "customer": customer.pk,
            "period_type": RentalPeriod.DAILY,
            "start_at": start.strftime("%Y-%m-%dT%H:%M"),
            "expected_return_at": (start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        },
    )
    assert response.status_code == 200
    assert not Rental.objects.filter(customer=customer).exists()


def test_check_in_screen_shows_the_settlement(client, staff):
    asset = make_equipment()
    start = timezone.now() - timedelta(days=3)
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
        deposit_amount=Decimal("100.00"),
    )

    response = client.get(reverse("rentals:return", args=[rental.pk]))

    assert response.status_code == 200
    assert response.context["late_fee"] > Decimal("0.00")


def test_check_in_posts_conditions_and_closes_the_contract(client, staff):
    asset = make_equipment()
    start = timezone.now() - timedelta(days=1)
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=2),
        deposit_amount=Decimal("100.00"),
    )
    item = rental.items.first()

    response = client.post(
        reverse("rentals:return", args=[rental.pk]),
        {
            f"item-{item.pk}-check_in": "on",
            f"item-{item.pk}-condition_in": EquipmentCondition.GOOD,
            f"item-{item.pk}-damage_charge": "0.00",
        },
    )

    rental.refresh_from_db()
    asset.refresh_from_db()
    assert response.status_code == 302
    assert rental.status == Rental.Status.RETURNED
    assert asset.status == EquipmentStatus.AVAILABLE


def test_quick_return_endpoint_checks_an_asset_back_in(client, staff):
    asset = make_equipment()
    start = timezone.now()
    rental = services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )

    response = client.post(
        reverse("rentals:quick_return"), {"asset_code": services.equipment_code(asset)}
    )

    rental.refresh_from_db()
    assert response.status_code == 200
    assert rental.status == Rental.Status.RETURNED


def test_payment_is_recorded_from_the_detail_screen(client, staff):
    rental = RentalFactory()
    RentalItemFactory(rental=rental, unit_price=Decimal("80.00"))
    rental.recalculate_totals()

    response = client.post(
        reverse("rentals:payment", args=[rental.pk]), {"amount": "30.00", "method": "cash"}
    )

    rental.refresh_from_db()
    assert response.status_code == 302
    assert rental.paid_amount == Decimal("30.00")


def test_only_a_cancelled_rental_can_be_deleted(client):
    user = make_user(role=Role.MANAGER)
    client.force_login(user)
    rental = RentalFactory(status=Rental.Status.ACTIVE)

    client.post(reverse("rentals:delete", args=[rental.pk]))

    rental.refresh_from_db()
    assert rental.is_deleted is False


def test_equipment_out_board_renders(client, staff):
    rental = RentalFactory(status=Rental.Status.ACTIVE)
    RentalItemFactory(rental=rental)
    response = client.get(reverse("rentals:out_now"))
    assert response.status_code == 200
