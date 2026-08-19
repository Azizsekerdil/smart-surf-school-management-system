from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.core.models import Document
from apps.customers.models import Customer, normalise_phone

from .factories import CustomerFactory, MinorCustomerFactory

pytestmark = pytest.mark.django_db


def test_customer_code_is_sequential_and_unique():
    first = CustomerFactory()
    second = CustomerFactory()
    assert first.customer_code.startswith("CUS")
    assert first.customer_code != second.customer_code


def test_str_contains_name_and_code():
    customer = CustomerFactory(first_name="Deniz", last_name="Kaya")
    assert "Deniz Kaya" in str(customer)
    assert customer.customer_code in str(customer)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+90 555 123 45 67", "+905551234567"),
        ("0555-123-4567", "05551234567"),
        ("(555) 123 45 67", "5551234567"),
        ("", ""),
        (None, ""),
    ],
)
def test_phone_normalisation(raw, expected):
    assert normalise_phone(raw) == expected


def test_email_is_lowercased_on_save():
    customer = CustomerFactory(email="Deniz.KAYA@Example.TEST")
    assert customer.email == "deniz.kaya@example.test"


def test_age_and_is_minor():
    today = timezone.localdate()
    adult = CustomerFactory(birth_date=today.replace(year=today.year - 30))
    assert adult.age == 30
    assert adult.is_minor is False

    child = MinorCustomerFactory()
    assert child.is_minor is True
    assert child.age < 18


def test_customer_without_birth_date_has_unknown_age():
    customer = CustomerFactory(birth_date=None)
    assert customer.age is None
    assert customer.is_minor is False


def test_future_birth_date_is_rejected():
    customer = CustomerFactory.build(birth_date=timezone.localdate() + timedelta(days=1))
    with pytest.raises(ValidationError) as excinfo:
        customer.full_clean(exclude=["photo", "customer_code"])
    assert "birth_date" in excinfo.value.message_dict


def test_customer_needs_at_least_one_contact_channel():
    customer = CustomerFactory.build(email="", phone="")
    with pytest.raises(ValidationError):
        customer.full_clean(exclude=["photo", "customer_code"])


def test_minor_requires_emergency_contact():
    customer = CustomerFactory.build(
        birth_date=date(timezone.localdate().year - 10, 1, 1),
        emergency_contact_name="",
        emergency_contact_phone="",
    )
    with pytest.raises(ValidationError) as excinfo:
        customer.full_clean(exclude=["photo", "customer_code"])
    assert "emergency_contact_name" in excinfo.value.message_dict


def test_has_valid_waiver_is_false_without_document():
    assert CustomerFactory().has_valid_waiver() is False


def _attach_waiver(customer, expires_on=None):
    return Document.objects.create(
        content_type=ContentType.objects.get_for_model(Customer),
        object_id=customer.pk,
        title="Waiver",
        category=Document.Category.WAIVER,
        file=ContentFile(b"signed", name="waiver.pdf"),
        expires_on=expires_on,
    )


def test_has_valid_waiver_true_for_unexpiring_document():
    customer = CustomerFactory()
    _attach_waiver(customer)
    assert customer.has_valid_waiver() is True


def test_expired_waiver_does_not_count():
    customer = CustomerFactory()
    _attach_waiver(customer, expires_on=timezone.localdate() - timedelta(days=1))
    assert customer.has_valid_waiver() is False


def test_soft_delete_hides_the_customer_from_the_default_manager():
    customer = CustomerFactory()
    customer.delete()
    assert not Customer.objects.filter(pk=customer.pk).exists()
    assert Customer.all_objects.filter(pk=customer.pk).exists()


def test_marketing_consent_timestamp_is_set_and_cleared():
    customer = CustomerFactory(marketing_consent=True)
    assert customer.marketing_consent_at is not None
    customer.marketing_consent = False
    customer.save()
    assert customer.marketing_consent_at is None


def test_queryset_helpers():
    active = CustomerFactory(is_active=True, total_bookings=3)
    CustomerFactory(is_active=False)
    assert active in Customer.objects.active()
    assert active in Customer.objects.with_bookings()
    assert Customer.objects.inactive().count() == 1
