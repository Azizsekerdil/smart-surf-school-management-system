from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.constants import Role
from apps.customers.models import Customer

from .factories import CustomerFactory, UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "surf-school-test-pw"


@pytest.fixture()
def reception(client):
    user = UserFactory(role=Role.RECEPTION, password=PASSWORD)
    client.force_login(user)
    return user


@pytest.fixture()
def manager(client):
    user = UserFactory(role=Role.MANAGER, password=PASSWORD)
    client.force_login(user)
    return user


def test_list_requires_authentication(client):
    response = client.get(reverse("customers:list"))
    assert response.status_code == 302


def test_list_renders_for_reception(client, reception):
    CustomerFactory(first_name="Deniz", last_name="Kaya")
    response = client.get(reverse("customers:list"))
    assert response.status_code == 200
    assert b"Deniz" in response.content


def test_list_search_filters_rows(client, reception):
    CustomerFactory(first_name="Deniz", last_name="Kaya")
    CustomerFactory(first_name="Ege", last_name="Aydin")
    response = client.get(reverse("customers:list"), {"q": "Ege"})
    assert response.status_code == 200
    assert b"Ege" in response.content
    assert b"Deniz" not in response.content


def test_list_htmx_request_returns_only_the_table(client, reception):
    CustomerFactory()
    response = client.get(reverse("customers:list"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content


def test_detail_renders(client, reception):
    customer = CustomerFactory()
    response = client.get(reverse("customers:detail", args=[customer.pk]))
    assert response.status_code == 200
    assert customer.customer_code.encode() in response.content


def test_detail_warns_about_a_missing_waiver(client, reception):
    customer = CustomerFactory()
    response = client.get(reverse("customers:detail", args=[customer.pk]))
    assert b"No valid waiver on file" in response.content


def test_tab_endpoint_returns_a_partial(client, reception):
    customer = CustomerFactory()
    response = client.get(
        reverse("customers:tab", args=[customer.pk, "payments"]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert b"<html" not in response.content


def test_unknown_tab_is_404(client, reception):
    customer = CustomerFactory()
    response = client.get(reverse("customers:tab", args=[customer.pk, "nope"]))
    assert response.status_code == 404


def test_create_customer(client, reception):
    response = client.post(
        reverse("customers:create"),
        {
            "first_name": "Deniz",
            "last_name": "Kaya",
            "email": "deniz@example.test",
            "phone": "+90 555 000 11 22",
            "source": "walk_in",
            "preferred_language": "en",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    customer = Customer.objects.get(email="deniz@example.test")
    assert customer.phone == "+905550001122"


def test_create_blocks_a_duplicate_contact_unless_overridden(client, reception):
    CustomerFactory(email="same@example.test")
    payload = {
        "first_name": "Other",
        "last_name": "Person",
        "email": "same@example.test",
        "source": "walk_in",
        "preferred_language": "en",
    }
    response = client.post(reverse("customers:create"), payload)
    assert response.status_code == 200
    assert Customer.objects.filter(last_name="Person").exists() is False

    response = client.post(reverse("customers:create"), {**payload, "allow_duplicate": "on"})
    assert response.status_code == 302
    assert Customer.objects.filter(last_name="Person").exists() is True


def test_update_customer(client, reception):
    customer = CustomerFactory()
    response = client.post(
        reverse("customers:update", args=[customer.pk]),
        {
            "first_name": "Renamed",
            "last_name": customer.last_name,
            "email": customer.email,
            "phone": customer.phone,
            "source": customer.source,
            "preferred_language": customer.preferred_language,
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.first_name == "Renamed"


def test_quick_create_modal_and_trigger_header(client, reception):
    response = client.get(reverse("customers:quick_create"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200

    response = client.post(
        reverse("customers:quick_create"),
        {
            "first_name": "Walk",
            "last_name": "In",
            "phone": "+905551112233",
            "email": "",
            "source": "walk_in",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "customerCreated" in response["HX-Trigger"]
    assert Customer.objects.filter(last_name="In").exists()


def test_quick_create_rejects_a_contactless_record(client, reception):
    response = client.post(
        reverse("customers:quick_create"),
        {"first_name": "No", "last_name": "Contact", "phone": "", "email": "", "source": "walk_in"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 400
    assert Customer.objects.filter(last_name="Contact").exists() is False


def test_search_endpoint(client, reception):
    CustomerFactory(first_name="Deniz", last_name="Kaya")
    response = client.get(reverse("customers:search"), {"q": "Den"})
    assert response.status_code == 200
    assert b"Deniz" in response.content


def test_note_creation_appends_to_the_notes_tab(client, reception):
    customer = CustomerFactory()
    response = client.post(
        reverse("customers:note_create", args=[customer.pk]),
        {"body": "Prefers the 09:00 session", "is_internal": "on"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"Prefers the 09:00 session" in response.content


def test_toggle_active_archives_the_customer(client, reception):
    customer = CustomerFactory()
    response = client.post(reverse("customers:toggle_active", args=[customer.pk]))
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.is_active is False


def test_duplicates_screen_requires_manage_capability(client, reception):
    response = client.get(reverse("customers:duplicates"))
    assert response.status_code == 403


def test_duplicates_screen_for_a_manager(client, manager):
    CustomerFactory(email="twin@example.test")
    CustomerFactory(email="twin@example.test")
    response = client.get(reverse("customers:duplicates"))
    assert response.status_code == 200
    assert b"twin@example.test" in response.content


def test_merge_screen_merges_on_confirm(client, manager):
    primary = CustomerFactory()
    duplicate = CustomerFactory()
    response = client.post(
        reverse("customers:merge", args=[primary.pk, duplicate.pk]),
        {"primary": primary.pk, "duplicate": duplicate.pk, "confirm": "on"},
    )
    assert response.status_code == 302
    duplicate.refresh_from_db()
    assert duplicate.is_deleted is True


def test_a_photographer_may_not_create_customers(client):
    user = UserFactory(role=Role.PHOTOGRAPHER, password=PASSWORD)
    client.force_login(user)
    response = client.get(reverse("customers:create"))
    assert response.status_code == 403
