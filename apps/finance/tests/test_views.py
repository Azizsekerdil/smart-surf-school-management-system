"""View tests: what each role can reach, and what the screens actually do."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.core.enums import PaymentMethod
from apps.customers.tests.factories import CustomerFactory
from apps.finance import services
from apps.finance.models import CommissionRecord, CustomerPackage, Expense, Invoice, Payment

from .factories import (
    CommissionRecordFactory,
    CustomerPackageFactory,
    ExpenseCategoryFactory,
    ExpenseFactory,
    InvoiceFactory,
    InvoiceLineFactory,
    PaymentFactory,
    PricePackageFactory,
    build_booking,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def finance_user(db):
    return User.objects.create_user(
        username="treasurer", email="treasurer@example.test",
        password="pw-test-12345", role=Role.FINANCE,
    )


@pytest.fixture
def reception_user(db):
    """Holds finance.view and finance.add, but never finance.refund."""
    return User.objects.create_user(
        username="frontdesk", email="frontdesk@example.test",
        password="pw-test-12345", role=Role.RECEPTION,
    )


@pytest.fixture
def outsider(db):
    """A photographer holds no ``finance.*`` capability at all."""
    return User.objects.create_user(
        username="snapper", email="snapper@example.test",
        password="pw-test-12345", role=Role.PHOTOGRAPHER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("finance:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.parametrize(
    "url_name",
    ["finance:dashboard", "finance:payment_list", "finance:invoice_list",
     "finance:expense_list", "finance:commission_list", "finance:package_list",
     "finance:customer_package_list"],
)
def test_outsider_is_refused_every_finance_screen(client, outsider, url_name):
    client.force_login(outsider)
    assert client.get(reverse(url_name)).status_code == 403


def test_reception_cannot_open_the_refund_screen(client, reception_user, finance_user):
    payment = services.record_payment(
        CustomerFactory(), Decimal("100.00"), user=finance_user
    )
    client.force_login(reception_user)
    assert client.get(reverse("finance:payment_refund", args=[payment.pk])).status_code == 403


def test_reception_cannot_approve_a_commission(client, reception_user):
    record = CommissionRecordFactory()
    client.force_login(reception_user)
    response = client.post(reverse("finance:commission_approve", args=[record.pk]))
    assert response.status_code == 403
    record.refresh_from_db()
    assert record.status == CommissionRecord.Status.PENDING


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_renders_the_period_summary(client, finance_user):
    services.record_payment(CustomerFactory(), Decimal("500.00"), user=finance_user)
    client.force_login(finance_user)
    response = client.get(reverse("finance:dashboard"))

    assert response.status_code == 200
    assert response.context["summary"]["revenue"]["current"] == Decimal("500.00")
    assert "chart" in response.context


def test_dashboard_honours_a_custom_range(client, finance_user):
    old = timezone.now() - timedelta(days=90)
    services.record_payment(CustomerFactory(), Decimal("500.00"), paid_at=old, user=finance_user)
    client.force_login(finance_user)

    response = client.get(reverse("finance:dashboard"), {"range": "7"})
    assert response.context["summary"]["revenue"]["current"] == Decimal("0.00")

    response = client.get(reverse("finance:dashboard"), {"range": "365"})
    assert response.context["summary"]["revenue"]["current"] == Decimal("500.00")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def test_payment_list_shows_rows_and_totals(client, finance_user):
    payment = services.record_payment(
        CustomerFactory(), Decimal("250.00"), user=finance_user
    )
    client.force_login(finance_user)
    response = client.get(reverse("finance:payment_list"))

    assert response.status_code == 200
    assert payment.payment_code.encode() in response.content
    assert response.context["totals"]["net"] == Decimal("250.00")


def test_payment_list_filters_by_category(client, finance_user):
    customer = CustomerFactory()
    lesson_payment = services.record_payment(
        customer, Decimal("100.00"), category=Payment.Category.LESSON, user=finance_user
    )
    shop_payment = services.record_payment(
        customer, Decimal("30.00"), category=Payment.Category.SHOP, user=finance_user
    )
    client.force_login(finance_user)

    response = client.get(reverse("finance:payment_list"), {"category": "shop"})
    assert shop_payment.payment_code.encode() in response.content
    assert lesson_payment.payment_code.encode() not in response.content


def test_payment_list_htmx_request_returns_the_partial(client, finance_user):
    PaymentFactory()
    client.force_login(finance_user)
    response = client.get(reverse("finance:payment_list"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert "finance/partials/payment_table.html" in [t.name for t in response.templates]


def test_taking_a_payment_updates_the_invoice(client, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("400.00"))
    invoice.recalculate()
    services.issue_invoice(invoice, user=finance_user)

    client.force_login(finance_user)
    response = client.post(
        reverse("finance:payment_create"),
        {
            "customer": invoice.customer_id,
            "amount": "400.00",
            "method": PaymentMethod.CARD,
            "category": Payment.Category.LESSON,
            "invoice": invoice.pk,
            "booking": "",
            "rental": "",
            "paid_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "reference": "AUTH-12345",
            "notes": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("400.00")
    assert invoice.status == Invoice.Status.PAID


def test_a_payment_cannot_be_attached_to_another_customers_invoice(client, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("400.00"))
    invoice.recalculate()
    services.issue_invoice(invoice, user=finance_user)
    stranger = CustomerFactory()

    client.force_login(finance_user)
    response = client.post(
        reverse("finance:payment_create"),
        {
            "customer": stranger.pk,
            "amount": "400.00",
            "method": PaymentMethod.CASH,
            "category": Payment.Category.LESSON,
            "invoice": invoice.pk,
            "paid_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        },
    )

    assert response.status_code == 200
    assert "invoice" in response.context["form"].errors
    assert Payment.objects.count() == 0


def test_a_zero_payment_is_rejected_by_the_form(client, finance_user):
    customer = CustomerFactory()
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:payment_create"),
        {
            "customer": customer.pk,
            "amount": "0.00",
            "method": PaymentMethod.CASH,
            "category": Payment.Category.OTHER,
            "paid_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        },
    )
    assert "amount" in response.context["form"].errors
    assert Payment.objects.count() == 0


def test_refund_screen_writes_the_counterpart(client, finance_user):
    payment = services.record_payment(
        CustomerFactory(), Decimal("300.00"), user=finance_user
    )
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:payment_refund", args=[payment.pk]),
        {"amount": "120.00", "reason": "Session cancelled by the school."},
        follow=True,
    )

    assert response.status_code == 200
    refund = Payment.objects.get(is_refund=True)
    assert refund.amount == Decimal("-120.00")
    payment.refresh_from_db()
    assert payment.amount == Decimal("300.00")


def test_refund_screen_refuses_more_than_is_left(client, finance_user):
    payment = services.record_payment(CustomerFactory(), Decimal("50.00"), user=finance_user)
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:payment_refund", args=[payment.pk]),
        {"amount": "500.00", "reason": "Too much."},
    )
    assert "amount" in response.context["form"].errors
    assert Payment.objects.filter(is_refund=True).count() == 0


def test_payment_detail_renders(client, finance_user):
    payment = PaymentFactory()
    client.force_login(finance_user)
    response = client.get(reverse("finance:payment_detail", args=[payment.pk]))
    assert response.status_code == 200
    assert payment.payment_code.encode() in response.content


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
def test_invoice_list_and_detail_render(client, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, description="Beginner group lesson")
    invoice.recalculate()

    client.force_login(finance_user)
    assert client.get(reverse("finance:invoice_list")).status_code == 200

    response = client.get(reverse("finance:invoice_detail", args=[invoice.pk]))
    assert response.status_code == 200
    assert b"Beginner group lesson" in response.content


def test_creating_an_invoice_skips_blank_lines(client, finance_user):
    customer = CustomerFactory()
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:invoice_create"),
        {
            "customer": customer.pk,
            "issue_date": timezone.localdate().isoformat(),
            "due_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
            "discount_amount": "0.00",
            "tax_rate": "10.00",
            "notes": "",
            "terms": "",
            "lines-TOTAL_FORMS": "4",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "25",
            "lines-0-description": "Private lesson",
            "lines-0-quantity": "2.00",
            "lines-0-unit_price": "500.00",
            "lines-0-discount_amount": "0.00",
            "lines-1-description": "",
            "lines-1-quantity": "",
            "lines-1-unit_price": "",
            "lines-1-discount_amount": "",
            "lines-2-description": "",
            "lines-2-quantity": "",
            "lines-2-unit_price": "",
            "lines-2-discount_amount": "",
            "lines-3-description": "",
            "lines-3-quantity": "",
            "lines-3-unit_price": "",
            "lines-3-discount_amount": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    invoice = Invoice.objects.get(customer=customer)
    assert invoice.lines.count() == 1
    assert invoice.subtotal == Decimal("1000.00")
    assert invoice.total_amount == Decimal("1100.00")
    assert invoice.status == Invoice.Status.DRAFT


def test_an_invoice_with_no_usable_line_is_rejected(client, finance_user):
    customer = CustomerFactory()
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:invoice_create"),
        {
            "customer": customer.pk,
            "issue_date": timezone.localdate().isoformat(),
            "due_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
            "discount_amount": "0.00",
            "tax_rate": "0.00",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "25",
            "lines-0-description": "",
            "lines-0-quantity": "",
            "lines-0-unit_price": "",
            "lines-0-discount_amount": "",
        },
    )
    assert response.status_code == 200
    assert Invoice.objects.count() == 0


def test_issuing_an_invoice_from_the_detail_screen(client, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"))
    invoice.recalculate()

    client.force_login(finance_user)
    client.post(reverse("finance:invoice_issue", args=[invoice.pk]), follow=True)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED


def test_issue_rejects_a_get(client, finance_user):
    invoice = InvoiceFactory()
    client.force_login(finance_user)
    assert client.get(reverse("finance:invoice_issue", args=[invoice.pk])).status_code == 405


def test_cancelling_an_unpaid_invoice(client, finance_user):
    invoice = InvoiceFactory()
    InvoiceLineFactory(invoice=invoice, unit_price=Decimal("100.00"))
    invoice.recalculate()
    services.issue_invoice(invoice, user=finance_user)

    client.force_login(finance_user)
    client.post(
        reverse("finance:invoice_cancel", args=[invoice.pk]),
        {"reason": "Duplicate."},
        follow=True,
    )
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.CANCELLED


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
def test_expense_list_totals_the_period(client, finance_user):
    ExpenseFactory(amount=Decimal("100.00"), tax_amount=Decimal("20.00"))
    client.force_login(finance_user)
    response = client.get(reverse("finance:expense_list"))

    assert response.status_code == 200
    assert response.context["expense_total"] == Decimal("120.00")


def test_recording_an_expense(client, finance_user):
    category = ExpenseCategoryFactory()
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:expense_create"),
        {
            "category": category.pk,
            "description": "Van fuel",
            "amount": "450.00",
            "tax_amount": "90.00",
            "spent_on": timezone.localdate().isoformat(),
            "supplier": "Shell",
            "invoice_reference": "",
            "equipment": "",
            "is_recurring": "",
            "recurrence_months": "",
        },
        follow=True,
    )

    assert response.status_code == 200
    expense = Expense.objects.get(description="Van fuel")
    assert expense.total_amount == Decimal("540.00")
    assert expense.paid_by == finance_user
    assert expense.created_by == finance_user


def test_a_future_dated_expense_is_rejected(client, finance_user):
    category = ExpenseCategoryFactory()
    client.force_login(finance_user)
    response = client.post(
        reverse("finance:expense_create"),
        {
            "category": category.pk,
            "description": "Next month's rent",
            "amount": "100.00",
            "tax_amount": "0.00",
            "spent_on": (timezone.localdate() + timedelta(days=30)).isoformat(),
        },
    )
    assert "spent_on" in response.context["form"].errors
    assert Expense.objects.count() == 0


def test_editing_an_expense(client, finance_user):
    expense = ExpenseFactory(description="Old text")
    client.force_login(finance_user)
    client.post(
        reverse("finance:expense_update", args=[expense.pk]),
        {
            "category": expense.category_id,
            "description": "Corrected text",
            "amount": "100.00",
            "tax_amount": "20.00",
            "spent_on": expense.spent_on.isoformat(),
            "supplier": "",
            "invoice_reference": "",
            "equipment": "",
            "is_recurring": "",
            "recurrence_months": "",
        },
        follow=True,
    )
    expense.refresh_from_db()
    assert expense.description == "Corrected text"


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
def test_commission_list_renders_with_the_owed_total(client, finance_user):
    CommissionRecordFactory(commission_amount=Decimal("175.00"))
    client.force_login(finance_user)
    response = client.get(reverse("finance:commission_list"))

    assert response.status_code == 200
    assert response.context["owed_total"] == Decimal("175.00")


def test_approving_and_paying_a_commission(client, finance_user):
    record = CommissionRecordFactory()
    client.force_login(finance_user)

    client.post(reverse("finance:commission_approve", args=[record.pk]))
    record.refresh_from_db()
    assert record.status == CommissionRecord.Status.APPROVED

    client.post(reverse("finance:commission_pay", args=[record.pk]))
    record.refresh_from_db()
    assert record.status == CommissionRecord.Status.PAID


def test_generating_commission_needs_an_instructor(client, finance_user):
    client.force_login(finance_user)
    response = client.post(reverse("finance:commission_generate"), {}, follow=True)
    assert response.status_code == 200
    assert CommissionRecord.objects.count() == 0


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
def test_package_list_renders(client, finance_user):
    PricePackageFactory(name="Five lesson card")
    client.force_login(finance_user)
    response = client.get(reverse("finance:package_list"))
    assert response.status_code == 200
    assert b"Five lesson card" in response.content


def test_creating_a_package(client, finance_user):
    from apps.finance.models import PricePackage

    client.force_login(finance_user)
    client.post(
        reverse("finance:package_create"),
        {
            "name": "Ten lesson card",
            "code": "ten10",
            "description": "",
            "lesson_type": "",
            "lesson_count": "10",
            "price": "6000.00",
            "validity_days": "365",
            "is_active": "on",
            "sort_order": "0",
        },
        follow=True,
    )
    package = PricePackage.objects.get(name="Ten lesson card")
    assert package.code == "TEN10"
    assert package.price_per_lesson == Decimal("600.00")


def test_selling_a_package_from_the_counter(client, finance_user):
    customer = CustomerFactory()
    package = PricePackageFactory(lesson_count=5, price=Decimal("3000.00"))

    client.force_login(finance_user)
    response = client.post(
        reverse("finance:package_sell"),
        {
            "customer": customer.pk,
            "package": package.pk,
            "payment_method": PaymentMethod.CARD,
            "reference": "AUTH-999",
        },
        follow=True,
    )

    assert response.status_code == 200
    card = CustomerPackage.objects.get(customer=customer)
    assert card.lessons_total == 5
    assert Payment.objects.filter(category=Payment.Category.PACKAGE).count() == 1


def test_using_a_package_lesson_from_the_screen(client, finance_user):
    customer = CustomerFactory()
    card = CustomerPackageFactory(customer=customer, lessons_total=5, lessons_used=0)
    booking = build_booking(customer=customer, unit_price=Decimal("600.00"))

    client.force_login(finance_user)
    client.post(
        reverse("finance:customer_package_use", args=[card.pk]),
        {"booking": booking.pk},
        follow=True,
    )

    card.refresh_from_db()
    booking.refresh_from_db()
    assert card.lessons_used == 1
    assert booking.paid_amount == Decimal("600.00")


def test_customer_package_list_renders(client, finance_user):
    CustomerPackageFactory()
    client.force_login(finance_user)
    response = client.get(reverse("finance:customer_package_list"))
    assert response.status_code == 200
    assert response.context["active_count"] == 1
