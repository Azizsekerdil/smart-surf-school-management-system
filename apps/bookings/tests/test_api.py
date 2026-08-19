"""REST API tests: the same rules apply over HTTP as at the desk."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.bookings.models import Booking
from apps.core.enums import BookingStatus

from .factories import BookingFactory, build_customer, build_lesson, build_student

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def manager(db):
    return User.objects.create_user(
        username="api-manager", password="surf-school-pass-1", role=Role.MANAGER
    )


@pytest.fixture
def photographer(db):
    """Has bookings.view but no bookings.add / change."""
    return User.objects.create_user(
        username="api-photo", password="surf-school-pass-1", role=Role.PHOTOGRAPHER
    )


def test_listing_requires_authentication(api):
    assert api.get("/api/v1/bookings/").status_code in (401, 403)


def test_manager_can_list_bookings(api, manager):
    booking = BookingFactory()
    api.force_authenticate(manager)
    response = api.get("/api/v1/bookings/")
    assert response.status_code == 200
    codes = [row["booking_code"] for row in response.data["results"]]
    assert booking.booking_code in codes


def test_creating_a_booking_over_the_api(api, manager):
    customer = build_customer()
    student = build_student(customer=customer)
    lesson = build_lesson()
    api.force_authenticate(manager)

    response = api.post(
        "/api/v1/bookings/",
        {
            "booking_type": "lesson",
            "customer": customer.pk,
            "student": student.pk,
            "lesson": lesson.pk,
            "participants": 1,
            "confirm": True,
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == BookingStatus.CONFIRMED
    assert Booking.objects.filter(booking_code=response.data["booking_code"]).exists()


def test_the_api_refuses_an_overbooking_with_409(api, manager):
    lesson = build_lesson(max_students=1)
    BookingFactory(lesson=lesson, participants=1, status=BookingStatus.CONFIRMED)
    customer = build_customer()
    api.force_authenticate(manager)

    response = api.post(
        "/api/v1/bookings/",
        {
            "booking_type": "lesson",
            "customer": customer.pk,
            "student": build_student(customer=customer).pk,
            "lesson": lesson.pk,
            "participants": 1,
        },
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["detail"]["conflicts"]


def test_a_read_only_role_cannot_create(api, photographer):
    customer = build_customer()
    api.force_authenticate(photographer)
    response = api.post(
        "/api/v1/bookings/",
        {"booking_type": "lesson", "customer": customer.pk, "participants": 1},
        format="json",
    )
    assert response.status_code == 403


def test_the_conflicts_endpoint_is_a_dry_run(api, manager):
    lesson = build_lesson()
    student = build_student()
    api.force_authenticate(manager)

    response = api.post(
        "/api/v1/bookings/conflicts/",
        {"booking_type": "lesson", "lesson": lesson.pk, "student": student.pk, "participants": 1},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["ok"] is True
    assert Booking.objects.count() == 0


def test_cancel_action(api, manager):
    booking = BookingFactory(
        status=BookingStatus.CONFIRMED,
        lesson=build_lesson(start=timezone.now() + timedelta(days=6)),
    )
    api.force_authenticate(manager)

    response = api.post(
        f"/api/v1/bookings/{booking.pk}/cancel/", {"reason": "Weather"}, format="json"
    )
    assert response.status_code == 200
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


def test_active_bookings_cannot_be_deleted(api, manager):
    booking = BookingFactory(status=BookingStatus.CONFIRMED)
    api.force_authenticate(manager)
    response = api.delete(f"/api/v1/bookings/{booking.pk}/")
    assert response.status_code == 400
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_calendar_endpoint_returns_events(api, manager):
    lesson = build_lesson(start=timezone.now() + timedelta(days=1), max_students=6)
    BookingFactory(lesson=lesson, participants=2, status=BookingStatus.CONFIRMED)
    api.force_authenticate(manager)

    response = api.get(
        "/api/v1/bookings/calendar/",
        {
            "start": timezone.localdate().isoformat(),
            "end": (timezone.localdate() + timedelta(days=3)).isoformat(),
        },
    )
    assert response.status_code == 200
    labels = [event["capacity_label"] for event in response.data["events"]]
    assert "2/6" in labels
