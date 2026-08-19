"""REST API contract: capability enforcement and the check-out/check-in actions."""

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
def api_staff(client):
    user = make_user(role=Role.RENTAL_STAFF)
    client.force_login(user)
    return user


def test_list_endpoint_returns_rentals(client, api_staff):
    rental = RentalFactory()
    RentalItemFactory(rental=rental)

    response = client.get(reverse("rental-list"))

    assert response.status_code == 200
    payload = response.json()
    codes = [row["rental_code"] for row in payload["results"]]
    assert rental.rental_code in codes


def test_detail_endpoint_exposes_derived_money_fields(client, api_staff):
    rental = RentalFactory()
    RentalItemFactory(rental=rental, unit_price=Decimal("60.00"))
    rental.recalculate_totals()

    response = client.get(reverse("rental-detail", args=[rental.pk]))

    assert response.status_code == 200
    body = response.json()
    assert body["total_amount"] == "60.00"
    assert body["balance_due"] == "60.00"
    assert body["item_count"] == 1


def test_a_role_without_the_capability_is_refused(client):
    client.force_login(make_user(role=Role.PHOTOGRAPHER))
    response = client.get(reverse("rental-list"))
    assert response.status_code == 403


def test_anonymous_access_is_refused(client):
    response = client.get(reverse("rental-list"))
    assert response.status_code in (401, 403)


def test_create_checks_the_gear_out(client, api_staff):
    customer = make_customer()
    asset = make_equipment()
    start = timezone.now()

    response = client.post(
        reverse("rental-list"),
        data={
            "customer": customer.pk,
            "items": [{"equipment": asset.pk, "quantity": 1}],
            "period_type": RentalPeriod.DAILY,
            "start_at": start.isoformat(),
            "expected_return_at": (start + timedelta(days=2)).isoformat(),
            "deposit_amount": "100.00",
        },
        content_type="application/json",
    )

    asset.refresh_from_db()
    assert response.status_code == 201
    assert response.json()["total_amount"] == "80.00"
    assert asset.status == EquipmentStatus.RENTED


def test_create_rejects_an_asset_that_is_already_out(client, api_staff):
    asset = make_equipment()
    start = timezone.now()
    services.create_rental(
        customer=make_customer(),
        items=[(asset, 1)],
        period_type=RentalPeriod.DAILY,
        start_at=start,
        expected_return_at=start + timedelta(days=1),
    )

    response = client.post(
        reverse("rental-list"),
        data={
            "customer": make_customer().pk,
            "items": [{"equipment": asset.pk, "quantity": 1}],
            "period_type": RentalPeriod.DAILY,
            "expected_return_at": (start + timedelta(days=1)).isoformat(),
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "validation_error"


def test_check_in_action_settles_the_contract(client, api_staff):
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
        reverse("rental-check-in", args=[rental.pk]),
        data={"items": [{"item": item.pk, "condition": EquipmentCondition.GOOD}]},
        content_type="application/json",
    )

    rental.refresh_from_db()
    assert response.status_code == 200
    assert rental.status == Rental.Status.RETURNED
    assert rental.deposit_returned == Decimal("100.00")


def test_extend_action_reprices(client, api_staff):
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
        reverse("rental-extend", args=[rental.pk]),
        data={"expected_return_at": (start + timedelta(days=3)).isoformat()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["total_amount"] == "120.00"


def test_overdue_endpoint_lists_late_contracts(client, api_staff):
    late = RentalFactory(
        status=Rental.Status.OVERDUE,
        start_at=timezone.now() - timedelta(days=4),
        expected_return_at=timezone.now() - timedelta(days=2),
    )
    RentalFactory(status=Rental.Status.ACTIVE, expected_return_at=timezone.now() + timedelta(days=2))

    response = client.get(reverse("rental-overdue"))

    assert response.status_code == 200
    codes = [row["rental_code"] for row in response.json()["results"]]
    assert codes == [late.rental_code]


def test_revenue_endpoint_reports_the_period_total(client):
    """The hire takings are readable by a role that holds finance.revenue."""
    client.force_login(make_user(role=Role.FINANCE))
    rental = RentalFactory(start_at=timezone.now())
    RentalItemFactory(rental=rental, unit_price=Decimal("150.00"))
    rental.recalculate_totals()

    response = client.get(reverse("rental-revenue"), {"range": "30"})

    assert response.status_code == 200
    assert Decimal(str(response.json()["revenue"])) == Decimal("150.00")


@pytest.mark.security
def test_rental_staff_can_run_the_counter_but_not_read_the_takings(client, api_staff):
    """The claim on the role slide, asserted against the code.

    Rental staff hold ``rentals.view`` and ``finance.view`` because they take
    money at the hire counter. They do not hold ``finance.revenue``, so the
    revenue aggregate is refused - the query never runs.
    """
    rental = RentalFactory(start_at=timezone.now())
    RentalItemFactory(rental=rental, unit_price=Decimal("150.00"))
    rental.recalculate_totals()

    assert api_staff.has_capability("rentals.view")
    assert api_staff.has_capability("finance.view")
    assert not api_staff.has_capability("finance.revenue")

    assert client.get(reverse("rental-list")).status_code == 200
    assert client.get(reverse("rental-revenue"), {"range": "30"}).status_code == 403
    assert client.get(reverse("finance-payment-summary")).status_code == 403
