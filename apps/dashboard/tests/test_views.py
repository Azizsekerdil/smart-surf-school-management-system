"""HTML views: access control, role dispatch, the HTMX fragment and search."""

from __future__ import annotations

import pytest
from django.urls import reverse

from .conftest import (
    build,
    make_booking,
    make_condition,
    make_customer,
    make_instructor,
    make_lesson,
    make_payment,
    make_spot,
    model_available,
)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_home_requires_authentication(client):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_home_is_denied_when_the_capability_is_revoked(client, blocked_user):
    client.force_login(blocked_user)
    assert client.get(reverse("dashboard:home")).status_code == 403


@pytest.mark.django_db
def test_search_requires_authentication(client):
    response = client.get(reverse("dashboard:search"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_tiles_fragment_is_denied_when_the_capability_is_revoked(client, blocked_user):
    client.force_login(blocked_user)
    assert client.get(reverse("dashboard:tiles")).status_code == 403


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_home_renders_for_a_manager_on_an_empty_database(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 200
    assert b"dashboard-tiles" in response.content


@pytest.mark.django_db
def test_home_renders_for_every_staff_role(client, rental_clerk, maintenance_user):
    for user in (rental_clerk, maintenance_user):
        client.force_login(user)
        assert client.get(reverse("dashboard:home")).status_code == 200


@pytest.mark.django_db
def test_customer_gets_the_self_service_template(client, customer_user):
    client.force_login(customer_user)
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 200
    assert response.templates[0].name == "dashboard/home_customer.html"


@pytest.mark.django_db
def test_staff_gets_the_operations_template(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:home"))
    assert response.templates[0].name == "dashboard/home.html"


@pytest.mark.django_db
def test_revenue_card_is_absent_for_a_role_without_finance(client, maintenance_user):
    client.force_login(maintenance_user)
    response = client.get(reverse("dashboard:home"))
    assert b"revenue-sparkline" not in response.content


# ---------------------------------------------------------------------------
# HTMX tile refresh
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_tiles_endpoint_returns_only_the_fragment(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:tiles"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content.lower()
    assert b"dashboard-tiles" in response.content


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_search_without_a_term_renders_the_prompt(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:search"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_search_with_one_character_is_rejected_politely(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:search"), {"q": "a"})
    assert response.status_code == 200
    assert response.context["results"]["too_short"] is True


@pytest.mark.django_db
def test_exact_asset_code_jumps_to_the_record(client, manager_user):
    if not model_available("equipment", "Equipment"):
        pytest.skip("equipment module not installed")
    item = build("equipment", "EquipmentFactory")
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:search"), {"q": item.asset_code})
    assert response.status_code == 302
    assert str(item.pk) in response["Location"]


@pytest.mark.django_db
def test_partial_code_lists_results_instead_of_redirecting(client, manager_user):
    if not model_available("equipment", "Equipment"):
        pytest.skip("equipment module not installed")
    item = build("equipment", "EquipmentFactory")
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:search"), {"q": item.asset_code[:4]})
    assert response.status_code == 200


@pytest.mark.django_db
def test_search_finds_a_customer_by_name(client, manager_user):
    customer = make_customer(first_name="Deniz", last_name="Ozdemir")
    client.force_login(manager_user)
    response = client.get(reverse("dashboard:search"), {"q": "Ozdemir"})
    assert response.status_code == 200
    assert customer.full_name.encode() in response.content


# ---------------------------------------------------------------------------
# A full day, end to end
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_a_busy_day_renders_every_panel(client, manager_user):
    """One realistic day: a lesson, an unpaid booking, a payment, conditions."""
    from decimal import Decimal

    from apps.core.enums import BookingStatus, PaymentStatus

    spot = make_spot(name="Alacati Point", is_primary=True)
    lesson = make_lesson(spot=spot)
    customer = make_customer(first_name="Deniz", last_name="Ozdemir")
    make_booking(
        customer=customer,
        status=BookingStatus.CONFIRMED,
        payment_status=PaymentStatus.PARTIAL,
        unit_price=Decimal("900.00"),
        total_amount=Decimal("900.00"),
        paid_amount=Decimal("400.00"),
    )
    if model_available("finance", "Payment"):
        make_payment(customer=customer, amount="400.00")
    if model_available("surf_conditions", "SurfCondition"):
        make_condition(spot=spot)

    client.force_login(manager_user)
    response = client.get(reverse("dashboard:home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert lesson.lesson_type.name in body        # today's schedule
    assert "Alacati Point" in body                # surf conditions panel
    assert "dashboard-tiles" in body
    if model_available("finance", "Payment"):
        assert "revenue-sparkline" in body        # 14-day chart


@pytest.mark.integration
@pytest.mark.django_db
def test_the_dashboard_query_count_does_not_grow_with_the_schedule(client, manager_user):
    """Twelve lessons must cost exactly what two cost.

    The dashboard is the most-loaded page in the product, so a regression into
    per-row queries is a real outage. Comparing two renders pins the invariant
    without hard-coding a number that every new module would have to bump.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    spot = make_spot(is_primary=True)
    for _index in range(2):
        make_lesson(spot=spot)
    make_condition(spot=spot)
    client.force_login(manager_user)

    with CaptureQueriesContext(connection) as small:
        assert client.get(reverse("dashboard:home")).status_code == 200

    for _index in range(10):
        make_lesson(spot=spot)

    with CaptureQueriesContext(connection) as large:
        assert client.get(reverse("dashboard:home")).status_code == 200

    assert len(large) == len(small)


@pytest.mark.integration
@pytest.mark.django_db
def test_instructor_page_shows_their_own_lesson_only(client, instructor_user):
    profile = make_instructor(user=instructor_user)
    mine = make_lesson(instructor=profile)
    theirs = make_lesson()

    client.force_login(instructor_user)
    body = client.get(reverse("dashboard:home")).content.decode()

    assert mine.lesson_type.name in body
    assert theirs.lesson_type.name not in body


@pytest.mark.integration
@pytest.mark.django_db
def test_customer_page_shows_their_own_booking(client, customer_user):
    from decimal import Decimal

    from apps.core.enums import BookingStatus, PaymentStatus

    customer = make_customer(user=customer_user)
    mine = make_booking(
        customer=customer,
        status=BookingStatus.CONFIRMED,
        payment_status=PaymentStatus.PARTIAL,
        unit_price=Decimal("900.00"),
        total_amount=Decimal("900.00"),
        paid_amount=Decimal("400.00"),
    )
    theirs = make_booking()

    client.force_login(customer_user)
    body = client.get(reverse("dashboard:home")).content.decode()

    assert mine.booking_code in body
    assert theirs.booking_code not in body
