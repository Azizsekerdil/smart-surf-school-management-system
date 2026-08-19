from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.core.models import Note
from apps.customers import services
from apps.customers.models import Customer, CustomerTag

from .factories import CustomerFactory, TagFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_create_customer_generates_code_and_audits():
    actor = UserFactory()
    customer = services.create_customer(
        first_name="Deniz",
        last_name="Kaya",
        email="deniz@example.test",
        phone="+90 555 000 11 22",
        actor=actor,
    )
    assert customer.customer_code.startswith("CUS")
    assert customer.phone == "+905550001122"
    assert customer.created_by == actor
    assert AuditLog.objects.filter(object_id=str(customer.pk), action="create").exists()


def test_create_customer_rejects_a_duplicate_contact():
    CustomerFactory(email="same@example.test")
    with pytest.raises(ValidationError):
        services.create_customer(
            first_name="Other", last_name="Person", email="same@example.test"
        )


def test_create_customer_allows_an_explicit_override():
    CustomerFactory(email="same@example.test")
    customer = services.create_customer(
        first_name="Other",
        last_name="Person",
        email="same@example.test",
        allow_duplicate=True,
    )
    assert customer.pk is not None


def test_create_customer_attaches_tags():
    tag = TagFactory()
    customer = services.create_customer(
        first_name="Deniz", last_name="Kaya", phone="+905550009999", tags=[tag]
    )
    assert list(customer.tags.all()) == [tag]


def test_set_marketing_consent_records_the_decision():
    customer = CustomerFactory(marketing_consent=False)
    services.set_marketing_consent(customer, True)
    customer.refresh_from_db()
    assert customer.marketing_consent is True
    assert customer.marketing_consent_at is not None

    services.set_marketing_consent(customer, False)
    customer.refresh_from_db()
    assert customer.marketing_consent is False
    assert customer.marketing_consent_at is None


def test_deactivate_and_reactivate():
    customer = CustomerFactory()
    services.deactivate_customer(customer, reason="Moved away")
    customer.refresh_from_db()
    assert customer.is_active is False

    services.reactivate_customer(customer)
    customer.refresh_from_db()
    assert customer.is_active is True


def test_recalculate_lifetime_value_is_safe_without_finance_rows():
    customer = CustomerFactory(lifetime_value=Decimal("120.00"), total_bookings=4)
    services.recalculate_lifetime_value(customer)
    customer.refresh_from_db()
    assert customer.lifetime_value == Decimal("0.00")
    assert customer.total_bookings == 0


def test_register_visit_moves_the_date_window():
    from datetime import date

    customer = CustomerFactory()
    services.register_visit(customer, date(2026, 5, 1), amount=Decimal("50.00"))
    services.register_visit(customer, date(2026, 7, 1), amount=Decimal("25.00"))
    customer.refresh_from_db()
    assert customer.first_visit_date == date(2026, 5, 1)
    assert customer.last_visit_date == date(2026, 7, 1)
    assert customer.lifetime_value == Decimal("75.00")


def test_find_duplicates_groups_by_email_and_by_phone_plus_surname():
    CustomerFactory(email="twin@example.test", phone="+905551110001", last_name="Kaya")
    CustomerFactory(email="twin@example.test", phone="+905551110002", last_name="Deniz")
    CustomerFactory(email="", phone="+905559998888", last_name="Ozturk")
    CustomerFactory(email="", phone="+905559998888", last_name="Ozturk")

    groups = services.find_duplicates()
    reasons = {str(group["reason"]) for group in groups}
    assert len(groups) == 2
    assert any("e-mail" in reason for reason in reasons)
    assert any("phone" in reason for reason in reasons)


def test_merge_moves_related_rows_and_archives_the_duplicate():
    primary = CustomerFactory(email="a@example.test", phone="", notes="Prefers mornings")
    duplicate = CustomerFactory(
        email="b@example.test", phone="+905551234567", notes="Allergic to latex"
    )
    tag = TagFactory()
    CustomerTag.objects.create(customer=duplicate, tag=tag)

    note = Note.objects.create(
        content_type=ContentType.objects.get_for_model(Customer),
        object_id=duplicate.pk,
        body="Left a wetsuit behind",
    )

    services.merge_customers(primary, duplicate)

    primary.refresh_from_db()
    duplicate.refresh_from_db()

    # Blank fields on the survivor are filled from the duplicate.
    assert primary.phone == "+905551234567"
    # Tags are unioned onto the survivor.
    assert list(primary.tags.all()) == [tag]
    # Notes text is carried over for the humans reading the record.
    assert "Allergic to latex" in primary.notes
    # The duplicate is archived, not destroyed.
    assert duplicate.is_deleted is True
    assert duplicate.is_active is False
    assert Customer.objects.filter(pk=duplicate.pk).exists() is False
    # Generic-relation rows (notes, waivers) follow the survivor.
    note.refresh_from_db()
    assert note.object_id == primary.pk


def test_merge_refuses_to_merge_a_customer_into_itself():
    customer = CustomerFactory()
    with pytest.raises(ValidationError):
        services.merge_customers(customer, customer)


def test_merge_keeps_the_survivors_customer_code():
    primary = CustomerFactory()
    duplicate = CustomerFactory()
    code = primary.customer_code
    services.merge_customers(primary, duplicate)
    primary.refresh_from_db()
    assert primary.customer_code == code


def test_merge_never_inherits_marketing_consent():
    primary = CustomerFactory(marketing_consent=False)
    duplicate = CustomerFactory(marketing_consent=True)

    services.merge_customers(primary, duplicate)
    primary.refresh_from_db()
    assert primary.marketing_consent is False


def test_merge_moves_a_waiver_so_the_survivor_can_enter_the_water():
    from django.core.files.base import ContentFile

    from apps.core.models import Document

    primary = CustomerFactory()
    duplicate = CustomerFactory()
    Document.objects.create(
        content_type=ContentType.objects.get_for_model(Customer),
        object_id=duplicate.pk,
        title="Waiver",
        category=Document.Category.WAIVER,
        file=ContentFile(b"signed", name="waiver.pdf"),
    )
    assert primary.has_valid_waiver() is False

    services.merge_customers(primary, duplicate)
    assert primary.has_valid_waiver() is True
