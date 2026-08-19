"""REST API tests: the endpoints obey exactly the same money rules."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import PaymentMethod
from apps.customers.tests.factories import CustomerFactory
from apps.finance import services
from apps.finance.models import CommissionRecord, CustomerPackage, Invoice, Payment

from .factories import (
    CommissionRecordFactory,
    InvoiceFactory,
    InvoiceLineFactory,
    PricePackageFactory,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def finance_user(db):
    return User.objects.create_user(
        username="treasurer", email="treasurer@example.test",
        password="pw-test-12345", role=Role.FINANCE,
    )


@pytest.fixture
def reception_user(db):
    return User.objects.create_user(
        username="frontdesk", email="frontdesk@example.test",
        password="pw-test-12345", role=Role.RECEPTION,
    )


@pytest.fixture
def outsider(db):
    return User.objects.create_user(
        username="snapper", email="snapper@example.test",
        password="pw-test-12345", role=Role.PHOTOGRAPHER,
    )


PAYMENTS_URL = "/api/v1/finance/payments/"
INVOICES_URL = "/api/v1/finance/invoices/"
COMMISSIONS_URL = "/api/v1/finance/commissions/"
PACKAGES_URL = "/api/v1/finance/packages/"
CUSTOMER_PACKAGES_URL = "/api/v1/finance/customer-packages/"


# ---------------------------------------------------------------------------
# Authentication & capabilities
# ---------------------------------------------------------------------------
def test_payments_endpoint_requires_authentication(api):
    assert api.get(PAYMENTS_URL).status_code in (401, 403)


def test_outsider_cannot_list_payments(api, outsider):
    api.force_authenticate(outsider)
    assert api.get(PAYMENTS_URL).status_code == 403


def test_finance_user_can_list_payments(api, finance_user):
    services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    api.force_authenticate(finance_user)
    response = api.get(PAYMENTS_URL)
    assert response.status_code == 200
    assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def test_creating_a_payment_through_the_api_updates_the_invoice(api, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("400.00"))
    invoice.recalculate()
    services.issue_invoice(invoice, user=finance_user)

    api.force_authenticate(finance_user)
    response = api.post(
        PAYMENTS_URL,
        {
            "customer": invoice.customer_id,
            "invoice": invoice.pk,
            "amount": "400.00",
            "method": PaymentMethod.TRANSFER,
            "category": Payment.Category.LESSON,
        },
        format="json",
    )

    assert response.status_code == 201
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("400.00")
    assert invoice.status == Invoice.Status.PAID


def test_a_negative_payment_is_refused_by_the_api(api, finance_user):
    customer = CustomerFactory()
    api.force_authenticate(finance_user)
    response = api.post(
        PAYMENTS_URL,
        {"customer": customer.pk, "amount": "-50.00", "method": PaymentMethod.CASH},
        format="json",
    )
    assert response.status_code == 400
    assert Payment.objects.count() == 0


def test_payments_cannot_be_edited_or_deleted(api, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    api.force_authenticate(finance_user)
    detail = f"{PAYMENTS_URL}{payment.pk}/"

    assert api.patch(detail, {"amount": "1.00"}, format="json").status_code == 405
    assert api.delete(detail).status_code == 405


def test_refund_action_requires_the_refund_capability(api, reception_user, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    api.force_authenticate(reception_user)
    response = api.post(
        f"{PAYMENTS_URL}{payment.pk}/refund/",
        {"amount": "10.00", "reason": "Nope."},
        format="json",
    )
    assert response.status_code == 403
    assert Payment.objects.filter(is_refund=True).count() == 0


def test_refund_action_writes_the_counterpart(api, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    api.force_authenticate(finance_user)
    response = api.post(
        f"{PAYMENTS_URL}{payment.pk}/refund/",
        {"amount": "40.00", "reason": "Half the session lost to fog."},
        format="json",
    )

    assert response.status_code == 201
    assert Decimal(response.data["amount"]) == Decimal("-40.00")
    payment.refresh_from_db()
    assert payment.amount == Decimal("100.00")
    assert payment.refundable_amount == Decimal("60.00")


def test_refund_over_the_remaining_amount_is_refused(api, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("100.00"), user=finance_user)
    api.force_authenticate(finance_user)
    response = api.post(
        f"{PAYMENTS_URL}{payment.pk}/refund/",
        {"amount": "500.00", "reason": "Too much."},
        format="json",
    )
    assert response.status_code == 400


def test_summary_endpoint_reports_the_period(api, finance_user):
    services.record_payment(CustomerFactory(), Decimal("250.00"), user=finance_user)
    api.force_authenticate(finance_user)
    response = api.get(f"{PAYMENTS_URL}summary/", {"range": "30"})

    assert response.status_code == 200
    assert Decimal(str(response.data["revenue"]["current"])) == Decimal("250.00")
    assert "revenue_by_category" in response.data


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
def test_creating_an_invoice_with_lines(api, finance_user):
    customer = CustomerFactory()
    api.force_authenticate(finance_user)
    response = api.post(
        INVOICES_URL,
        {
            "customer": customer.pk,
            "due_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
            "tax_rate": "20.00",
            "lines": [
                {"description": "Private lesson", "quantity": "2.00", "unit_price": "500.00"}
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    assert Decimal(response.data["total_amount"]) == Decimal("1200.00")
    assert response.data["invoice_number"].startswith("INV-")


def test_an_invoice_without_lines_is_refused(api, finance_user):
    api.force_authenticate(finance_user)
    response = api.post(
        INVOICES_URL,
        {"customer": CustomerFactory().pk, "due_date": timezone.localdate().isoformat(),
         "lines": []},
        format="json",
    )
    assert response.status_code == 400


def test_issue_action_moves_the_invoice(api, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"))
    invoice.recalculate()

    api.force_authenticate(finance_user)
    response = api.post(f"{INVOICES_URL}{invoice.pk}/issue/")
    assert response.status_code == 200
    assert response.data["status"] == Invoice.Status.ISSUED


def test_overdue_action_lists_only_late_invoices(api, finance_user):
    invoice = InvoiceFactory(due_date=timezone.localdate() - timedelta(days=5))
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"))
    invoice.recalculate()
    services.issue_invoice(invoice, user=finance_user)

    fresh = InvoiceFactory()
    InvoiceLineFactory(invoice=fresh, unit_price=Decimal("100.00"))
    fresh.recalculate()
    services.issue_invoice(fresh, user=finance_user)

    api.force_authenticate(finance_user)
    response = api.get(f"{INVOICES_URL}overdue/")
    numbers = [row["invoice_number"] for row in response.data["results"]]
    assert numbers == [invoice.invoice_number]


# ---------------------------------------------------------------------------
# Commission & packages
# ---------------------------------------------------------------------------
def test_approve_and_pay_commission_through_the_api(api, finance_user):
    record = CommissionRecordFactory()
    api.force_authenticate(finance_user)

    assert api.post(f"{COMMISSIONS_URL}{record.pk}/approve/").status_code == 200
    record.refresh_from_db()
    assert record.status == CommissionRecord.Status.APPROVED

    assert api.post(f"{COMMISSIONS_URL}{record.pk}/pay/").status_code == 200
    record.refresh_from_db()
    assert record.status == CommissionRecord.Status.PAID


def test_reception_cannot_approve_commission_through_the_api(api, reception_user):
    record = CommissionRecordFactory()
    api.force_authenticate(reception_user)
    assert api.post(f"{COMMISSIONS_URL}{record.pk}/approve/").status_code == 403


def test_package_list_exposes_the_derived_price(api, finance_user):
    PricePackageFactory(lesson_count=5, price=Decimal("3000.00"))
    api.force_authenticate(finance_user)
    response = api.get(PACKAGES_URL)

    assert response.status_code == 200
    assert Decimal(response.data["results"][0]["price_per_lesson"]) == Decimal("600.00")


def test_selling_a_package_through_the_api(api, finance_user):
    customer = CustomerFactory()
    package = PricePackageFactory(lesson_count=5, price=Decimal("3000.00"))

    api.force_authenticate(finance_user)
    response = api.post(
        f"{CUSTOMER_PACKAGES_URL}sell/",
        {
            "customer": customer.pk,
            "package": package.pk,
            "payment_method": PaymentMethod.CARD,
        },
        format="json",
    )

    assert response.status_code == 201
    assert CustomerPackage.objects.filter(customer=customer).count() == 1
    assert Payment.objects.filter(category=Payment.Category.PACKAGE).count() == 1


def test_customer_packages_are_read_only(api, finance_user):
    api.force_authenticate(finance_user)
    response = api.post(
        CUSTOMER_PACKAGES_URL, {"customer": CustomerFactory().pk}, format="json"
    )
    assert response.status_code == 405


def test_api_routes_are_registered():
    """Guards against a typo in ``ROUTES`` silently unpublishing the module."""
    assert reverse("finance-payment-list") == PAYMENTS_URL
    assert reverse("finance-invoice-list") == INVOICES_URL
