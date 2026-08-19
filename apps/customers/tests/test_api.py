from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.customers.models import Customer

from .factories import CustomerFactory, UserFactory

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/customers/"


@pytest.fixture()
def api():
    return APIClient()


def _login(api, role):
    user = UserFactory(role=role, password="surf-school-test-pw")
    api.force_authenticate(user=user)
    return user


def test_anonymous_access_is_rejected(api):
    assert api.get(LIST_URL).status_code in (401, 403)


def test_reception_can_list_customers(api):
    _login(api, Role.RECEPTION)
    CustomerFactory(first_name="Deniz")
    response = api.get(LIST_URL)
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_serializer_exposes_derived_fields(api):
    _login(api, Role.RECEPTION)
    customer = CustomerFactory()
    response = api.get(f"{LIST_URL}{customer.pk}/")
    assert response.status_code == 200
    assert response.data["full_name"] == customer.full_name
    assert response.data["has_valid_waiver"] is False
    assert response.data["customer_code"] == customer.customer_code


def test_create_via_api_normalises_contact_details(api):
    _login(api, Role.RECEPTION)
    response = api.post(
        LIST_URL,
        {
            "first_name": "Deniz",
            "last_name": "Kaya",
            "email": "Deniz@Example.TEST",
            "phone": "+90 555 000 11 22",
            "source": "phone",
        },
        format="json",
    )
    assert response.status_code == 201
    customer = Customer.objects.get(last_name="Kaya")
    assert customer.email == "deniz@example.test"
    assert customer.phone == "+905550001122"


def test_create_rejects_a_duplicate_contact(api):
    _login(api, Role.RECEPTION)
    CustomerFactory(email="same@example.test")
    response = api.post(
        LIST_URL,
        {"first_name": "A", "last_name": "B", "email": "same@example.test"},
        format="json",
    )
    assert response.status_code == 400


def test_photographer_may_read_but_not_write(api):
    _login(api, Role.PHOTOGRAPHER)
    CustomerFactory()
    assert api.get(LIST_URL).status_code == 200
    response = api.post(
        LIST_URL, {"first_name": "A", "last_name": "B", "phone": "+905550000000"}, format="json"
    )
    assert response.status_code == 403


def test_duplicates_action_requires_manage(api):
    _login(api, Role.RECEPTION)
    assert api.get(f"{LIST_URL}duplicates/").status_code == 403


def test_manager_can_merge_over_the_api(api):
    _login(api, Role.MANAGER)
    primary = CustomerFactory()
    duplicate = CustomerFactory()
    response = api.post(
        f"{LIST_URL}{primary.pk}/merge/", {"duplicate_id": duplicate.pk}, format="json"
    )
    assert response.status_code == 200
    duplicate.refresh_from_db()
    assert duplicate.is_deleted is True


def test_consent_action_records_the_opt_in(api):
    _login(api, Role.MANAGER)
    customer = CustomerFactory(marketing_consent=False)
    response = api.post(
        f"{LIST_URL}{customer.pk}/consent/", {"granted": True}, format="json"
    )
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.marketing_consent is True


def test_destroy_archives_instead_of_deleting(api):
    _login(api, Role.MANAGER)
    customer = CustomerFactory()
    response = api.delete(f"{LIST_URL}{customer.pk}/")
    assert response.status_code == 204
    customer.refresh_from_db()
    assert customer.is_deleted is True
    assert customer.is_active is False


def test_summary_action(api):
    _login(api, Role.RECEPTION)
    customer = CustomerFactory()
    response = api.get(f"{LIST_URL}{customer.pk}/summary/")
    assert response.status_code == 200
    assert response.data["bookings_total"] == 0
