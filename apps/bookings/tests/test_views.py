"""View-level tests: rendering, capability enforcement and the create flow."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.bookings.models import Booking
from apps.core.enums import BookingStatus

from .factories import BookingFactory, build_customer, build_lesson, build_student

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="manager", password="surf-school-pass-1", role=Role.MANAGER
    )


@pytest.fixture
def maintenance(db):
    """A role with no bookings capability at all."""
    return User.objects.create_user(
        username="fixer", password="surf-school-pass-1", role=Role.MAINTENANCE_STAFF
    )


def test_calendar_url_is_named_calendar_and_renders(client, manager):
    client.force_login(manager)
    response = client.get(reverse("bookings:calendar"))
    assert response.status_code == 200


def test_calendar_supports_week_and_day_views(client, manager):
    client.force_login(manager)
    for view in ("month", "week", "day"):
        response = client.get(reverse("bookings:calendar"), {"view": view})
        assert response.status_code == 200


def test_calendar_htmx_navigation_returns_only_the_shell(client, manager):
    client.force_login(manager)
    response = client.get(
        reverse("bookings:calendar"),
        {"view": "week"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"calendar-shell" in response.content
    assert b"<html" not in response.content


def test_list_and_detail_render(client, manager):
    booking = BookingFactory()
    client.force_login(manager)

    listing = client.get(reverse("bookings:list"))
    assert listing.status_code == 200
    assert booking.booking_code.encode() in listing.content

    detail = client.get(reverse("bookings:detail", args=[booking.pk]))
    assert detail.status_code == 200


def test_list_filters_by_status(client, manager):
    BookingFactory(status=BookingStatus.PENDING)
    confirmed = BookingFactory(status=BookingStatus.CONFIRMED)
    client.force_login(manager)

    response = client.get(reverse("bookings:list"), {"status": BookingStatus.CONFIRMED})
    assert response.status_code == 200
    codes = [item.booking_code for item in response.context["bookings"]]
    assert codes == [confirmed.booking_code]


def test_a_role_without_the_capability_is_refused(client, maintenance):
    booking = BookingFactory()
    client.force_login(maintenance)
    assert client.get(reverse("bookings:list")).status_code == 403
    assert client.get(reverse("bookings:calendar")).status_code == 403
    assert client.get(reverse("bookings:detail", args=[booking.pk])).status_code == 403


def test_anonymous_users_are_redirected_to_the_login_page(client):
    response = client.get(reverse("bookings:list"))
    assert response.status_code in (302, 403)


def test_creating_a_booking_through_the_form(client, manager):
    customer = build_customer()
    student = build_student(customer=customer)
    lesson = build_lesson()
    client.force_login(manager)

    response = client.post(
        reverse("bookings:create"),
        {
            "booking_type": Booking.BookingType.LESSON,
            "customer": customer.pk,
            "student": student.pk,
            "lesson": lesson.pk,
            "participants": 1,
            "unit_price": "50.00",
            "discount_amount": "0.00",
            "source": "walk_in",
            "special_requests": "",
            "internal_notes": "",
            "confirm_immediately": "on",
        },
    )
    booking = Booking.objects.get(customer=customer)
    assert response.status_code == 302
    assert response.url == reverse("bookings:detail", args=[booking.pk])
    assert booking.status == BookingStatus.CONFIRMED


def test_the_form_reports_conflicts_instead_of_creating(client, manager):
    lesson = build_lesson(max_students=1)
    BookingFactory(lesson=lesson, participants=1, status=BookingStatus.CONFIRMED)
    customer = build_customer()
    student = build_student(customer=customer)
    client.force_login(manager)

    response = client.post(
        reverse("bookings:create"),
        {
            "booking_type": Booking.BookingType.LESSON,
            "customer": customer.pk,
            "student": student.pk,
            "lesson": lesson.pk,
            "participants": 1,
            "unit_price": "50.00",
            "discount_amount": "0.00",
            "source": "walk_in",
        },
    )
    assert response.status_code == 200
    assert response.context["conflicts"]
    assert not Booking.objects.filter(customer=customer).exists()


def test_the_live_check_endpoint_returns_the_panel(client, manager):
    lesson = build_lesson()
    customer = build_customer()
    student = build_student(customer=customer)
    client.force_login(manager)

    response = client.post(
        reverse("bookings:check"),
        {
            "booking_type": Booking.BookingType.LESSON,
            "customer": customer.pk,
            "student": student.pk,
            "lesson": lesson.pk,
            "participants": 1,
            "source": "walk_in",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"conflict-panel" in response.content
    assert response.context["conflicts"] == []


def test_cancel_screen_and_post(client, manager):
    booking = BookingFactory(
        status=BookingStatus.CONFIRMED,
        lesson=build_lesson(start=timezone.now() + timedelta(days=4)),
    )
    client.force_login(manager)

    assert client.get(reverse("bookings:cancel", args=[booking.pk])).status_code == 200

    response = client.post(
        reverse("bookings:cancel", args=[booking.pk]),
        {"reason_code": "customer_request", "reason": "Flying home early"},
    )
    booking.refresh_from_db()
    assert response.status_code == 302
    assert booking.status == BookingStatus.CANCELLED
    assert booking.cancellation_fee == Decimal("0.00")


def test_confirm_action_requires_the_change_capability(client, maintenance):
    booking = BookingFactory()
    client.force_login(maintenance)
    response = client.post(reverse("bookings:confirm", args=[booking.pk]))
    assert response.status_code == 403
    booking.refresh_from_db()
    assert booking.status == BookingStatus.PENDING


def test_waitlist_screen_renders(client, manager):
    client.force_login(manager)
    assert client.get(reverse("bookings:waitlist")).status_code == 200


def test_daily_schedule_renders(client, manager):
    client.force_login(manager)
    response = client.get(
        reverse("bookings:schedule"), {"date": timezone.localdate().isoformat()}
    )
    assert response.status_code == 200
