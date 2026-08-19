"""Negative authorisation tests: what a customer or a student must **not** see.

Most of a test suite asserts that the right people can do the right things.
This module does the opposite, because the defect it guards against was
invisible to a positive suite: every endpoint answered ``200`` for staff, so
every test passed, while the object-level rule that was supposed to narrow the
rows for customers and students had never been attached to a single viewset.

The rule under test is :mod:`apps.accounts.scoping`. The two external roles
hold ``finance.view``, ``rentals.view``, ``lessons.view`` and
``surf_camps.view`` so that a self-service portal can exist at all; without a
row-level rule those capabilities read the whole school.

Two of these tests are about children specifically. A surf school runs junior
weeks and family lessons, so a camp participant row or a lesson register is
frequently a record about a minor, complete with the room they sleep in and
their dietary and medical flags. Those two tests are marked and named so that
nobody deletes them to make a refactor go green.

Convention used throughout: an external user asking for a row that is not
theirs gets **404, not 403**. 403 would confirm that the row exists.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import EXTERNAL_ROLES, Role
from apps.accounts.scoping import DENY, OWN, SHARED, is_external_user, scope_queryset
from apps.customers.tests.factories import CustomerFactory
from apps.finance.models import Invoice, Payment
from apps.finance.tests.factories import (
    CustomerPackageFactory,
    ExpenseFactory,
    InvoiceFactory,
    PaymentFactory,
)
from apps.lessons.models import LessonAttendance
from apps.lessons.tests.factories import LessonAttendanceFactory, LessonFactory
from apps.students.tests.factories import MinorStudentFactory, StudentFactory
from apps.surf_camps.models import CampParticipant
from apps.surf_camps.tests.factories import CampParticipantFactory, SurfCampFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures: one customer who owns things, one who owns nothing
# ---------------------------------------------------------------------------
def make_login_user(role: str, username: str):
    """A user with a guaranteed-unique e-mail (the column is unique)."""
    return User.objects.create_user(
        username=username,
        email=f"{username}-{User.objects.count()}@example.test",
        password="not-a-real-password",
        role=role,
    )


def _external_login(client, role: str, username: str):
    user = make_login_user(role, username)
    client.force_login(user)
    return user


def make_lesson(**kwargs):
    """A lesson on a fully-specified spot.

    The lessons app ships a deliberately minimal ``SurfSpotFactory``; the
    locations app owns the real one, which fills in the coordinates the model
    requires.
    """
    from apps.instructors.tests.factories import InstructorFactory
    from apps.locations.tests.factories import SurfSpotFactory

    kwargs.setdefault("spot", SurfSpotFactory())
    kwargs.setdefault("instructor", InstructorFactory())
    return LessonFactory(**kwargs)


@pytest.fixture
def customer_login(client):
    """An authenticated Role.CUSTOMER with a linked Customer record."""
    user = _external_login(client, Role.CUSTOMER, "portal-customer")
    customer = CustomerFactory(user=user)
    return user, customer


@pytest.fixture
def other_customer():
    """Somebody else's records. Nothing here belongs to ``customer_login``."""
    return CustomerFactory()


def _rows(response) -> list[dict]:
    payload = response.json()
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------
def test_an_unset_policy_shows_an_external_user_nothing():
    """Fail-closed: a view that declares nothing must not leak.

    The previous implementation returned the *unfiltered* queryset when no
    ownership path was declared, which is how a whole module can be published
    by omission.
    """
    InvoiceFactory()
    user = make_login_user(Role.CUSTOMER, "fail-closed")

    assert scope_queryset(Invoice.objects.all(), user, access=DENY).count() == 0
    assert scope_queryset(Invoice.objects.all(), user, access=OWN, lookups=()).count() == 0


def test_staff_are_never_narrowed():
    InvoiceFactory()
    staff = make_login_user(Role.RECEPTION, "desk")

    assert not is_external_user(staff)
    assert scope_queryset(Invoice.objects.all(), staff, access=DENY).count() == 1


def test_an_anonymous_caller_gets_nothing_even_from_a_shared_surface():
    from django.contrib.auth.models import AnonymousUser

    InvoiceFactory()
    assert scope_queryset(Invoice.objects.all(), AnonymousUser(), access=SHARED).count() == 0


def test_every_external_role_is_treated_as_external():
    for role in EXTERNAL_ROLES:
        assert is_external_user(make_login_user(role, f"ext-{role}")), role


# ---------------------------------------------------------------------------
# Finance — the module that carries other people's money
# ---------------------------------------------------------------------------
@pytest.mark.security
def test_a_customer_lists_only_their_own_invoices(client, customer_login, other_customer):
    _user, mine = customer_login
    my_invoice = InvoiceFactory(customer=mine)
    their_invoice = InvoiceFactory(customer=other_customer)

    response = client.get(reverse("finance-invoice-list"))

    assert response.status_code == 200
    ids = {row["id"] for row in _rows(response)}
    assert ids == {my_invoice.pk}
    assert their_invoice.pk not in ids


@pytest.mark.security
def test_a_customer_cannot_open_someone_elses_invoice(client, customer_login, other_customer):
    their_invoice = InvoiceFactory(customer=other_customer)

    response = client.get(reverse("finance-invoice-detail", args=[their_invoice.pk]))

    # 404 rather than 403: an external user must not be able to probe which
    # invoice ids exist.
    assert response.status_code == 404


@pytest.mark.security
def test_a_customer_lists_only_their_own_payments(client, customer_login, other_customer):
    _user, mine = customer_login
    my_payment = PaymentFactory(customer=mine)
    PaymentFactory(customer=other_customer)

    response = client.get(reverse("finance-payment-list"))

    assert response.status_code == 200
    assert {row["id"] for row in _rows(response)} == {my_payment.pk}


@pytest.mark.security
def test_a_customer_sees_no_expenses_at_all(client, customer_login):
    ExpenseFactory()

    response = client.get(reverse("finance-expense-list"))

    assert response.status_code == 200
    assert _rows(response) == []


@pytest.mark.security
def test_a_customer_sees_no_commission_records(client, customer_login):
    response = client.get(reverse("finance-commission-list"))

    assert response.status_code == 200
    assert _rows(response) == []


@pytest.mark.security
def test_a_customer_cannot_read_the_revenue_summary(client, customer_login):
    response = client.get(reverse("finance-payment-summary"))

    assert response.status_code == 403


@pytest.mark.security
def test_a_customer_sees_only_their_own_package_cards(client, customer_login, other_customer):
    _user, mine = customer_login
    my_card = CustomerPackageFactory(customer=mine)
    CustomerPackageFactory(customer=other_customer)

    response = client.get(reverse("finance-customer-package-list"))

    assert response.status_code == 200
    assert {row["id"] for row in _rows(response)} == {my_card.pk}


def test_the_price_list_stays_visible_to_customers(client, customer_login):
    """Scoping must not break the things a portal legitimately needs."""
    from apps.finance.tests.factories import PricePackageFactory

    package = PricePackageFactory()

    response = client.get(reverse("finance-package-list"))

    assert response.status_code == 200
    assert package.pk in {row["id"] for row in _rows(response)}


@pytest.mark.security
def test_the_overdue_invoice_action_is_scoped_too(client, customer_login, other_customer):
    """A custom @action builds its own queryset and must scope it by hand."""
    _user, mine = customer_login
    yesterday = timezone.localdate() - timedelta(days=10)
    mine_overdue = InvoiceFactory(
        customer=mine,
        due_date=yesterday,
        status=Invoice.Status.ISSUED,
        total_amount=Decimal("100"),
    )
    InvoiceFactory(
        customer=other_customer,
        due_date=yesterday,
        status=Invoice.Status.ISSUED,
        total_amount=Decimal("100"),
    )

    response = client.get(reverse("finance-invoice-overdue"))

    assert response.status_code == 200
    assert {row["id"] for row in _rows(response)} <= {mine_overdue.pk}


# ---------------------------------------------------------------------------
# Finance HTML screens — the same rule, the other door
# ---------------------------------------------------------------------------
@pytest.mark.security
def test_the_payment_list_page_is_scoped(client, customer_login, other_customer):
    _user, mine = customer_login
    PaymentFactory(customer=mine, amount=Decimal("11.00"))
    PaymentFactory(customer=other_customer, amount=Decimal("22.00"))

    response = client.get(reverse("finance:payment_list"))

    assert response.status_code == 200
    codes = {payment.pk for payment in response.context["payments"]}
    assert codes == set(Payment.objects.filter(customer=mine).values_list("pk", flat=True))


@pytest.mark.security
def test_the_payment_detail_page_refuses_another_customers_row(
    client, customer_login, other_customer
):
    theirs = PaymentFactory(customer=other_customer)

    response = client.get(reverse("finance:payment_detail", args=[theirs.pk]))

    assert response.status_code == 404


@pytest.mark.security
def test_the_finance_dashboard_needs_the_revenue_capability(client, customer_login):
    response = client.get(reverse("finance:dashboard"))

    assert response.status_code == 403


@pytest.mark.security
def test_a_counter_role_can_take_money_without_reading_the_takings(client):
    """PDF/README claim: a rental clerk cannot see revenue.

    Asserted here against the capability matrix *and* the views, so the claim
    cannot drift away from the code again.
    """
    clerk = make_login_user(Role.RENTAL_STAFF, "hire-desk")
    client.force_login(clerk)

    assert clerk.has_capability("finance.view")
    assert not clerk.has_capability("finance.revenue")

    assert client.get(reverse("finance:payment_list")).status_code == 200
    assert client.get(reverse("finance:dashboard")).status_code == 403
    assert client.get(reverse("finance:commission_list")).status_code == 403
    assert client.get(reverse("finance-payment-summary")).status_code == 403


def test_a_counter_role_sees_no_school_wide_totals_on_the_payment_list(client):
    clerk = make_login_user(Role.RENTAL_STAFF, "hire-desk-2")
    client.force_login(clerk)
    PaymentFactory(amount=Decimal("500.00"))

    response = client.get(reverse("finance:payment_list"))

    assert response.status_code == 200
    assert response.context["totals"] is None
    assert response.context["by_method"] is None


# ---------------------------------------------------------------------------
# Rentals
# ---------------------------------------------------------------------------
@pytest.mark.security
def test_a_customer_lists_only_their_own_rentals(client, customer_login, other_customer):
    from apps.rentals.tests.factories import RentalFactory

    _user, mine = customer_login
    my_rental = RentalFactory(customer=mine)
    RentalFactory(customer=other_customer)

    response = client.get(reverse("rental-list"))

    assert response.status_code == 200
    assert {row["id"] for row in _rows(response)} == {my_rental.pk}


@pytest.mark.security
def test_a_customer_cannot_open_the_hire_counter_search(client, customer_login):
    """The picker searches customers and students by name."""
    response = client.get(reverse("rentals:search"), {"kind": "customer", "q": "a"})

    assert response.status_code == 403


@pytest.mark.security
def test_a_customer_cannot_open_the_stock_board(client, customer_login):
    response = client.get(reverse("rentals:out_now"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Lessons — registers name children
# ---------------------------------------------------------------------------
@pytest.mark.security
@pytest.mark.parametrize("role", sorted(EXTERNAL_ROLES))
def test_an_external_account_sees_only_the_lessons_it_is_on(client, role):
    """MINORS: a lesson register is a list of who is in the water.

    Do not delete or relax this test. ``lessons.view`` is granted to both
    external roles, so without the ownership rule this endpoint returns every
    register in the school.
    """
    user = _external_login(client, role, f"lesson-{role}")
    my_customer = CustomerFactory(user=user)
    my_student = StudentFactory(customer=my_customer)

    my_lesson = make_lesson()
    LessonAttendanceFactory(lesson=my_lesson, student=my_student)

    someone_elses_lesson = make_lesson()
    LessonAttendanceFactory(
        lesson=someone_elses_lesson, student=MinorStudentFactory()
    )

    response = client.get(reverse("lesson-list"))

    assert response.status_code == 200
    ids = {row["id"] for row in _rows(response)}
    assert ids == {my_lesson.pk}
    assert someone_elses_lesson.pk not in ids


@pytest.mark.security
def test_a_customer_cannot_read_another_childs_attendance_row(client, customer_login):
    """MINORS: attendance rows carry the instructor's notes about a child."""
    minor = MinorStudentFactory()
    theirs = LessonAttendanceFactory(lesson=make_lesson(), student=minor)

    listing = client.get(reverse("lessonattendance-list"))
    assert listing.status_code == 200
    assert _rows(listing) == []

    detail = client.get(reverse("lessonattendance-detail", args=[theirs.pk]))
    assert detail.status_code == 404


@pytest.mark.security
def test_the_lesson_calendar_feed_is_scoped(client, customer_login):
    """The calendar action bypasses get_queryset, so it is scoped separately."""
    LessonAttendanceFactory(lesson=make_lesson(), student=MinorStudentFactory())

    response = client.get(reverse("lesson-calendar"))

    assert response.status_code == 200
    assert response.json()["events"] == []


@pytest.mark.security
def test_a_customer_cannot_open_the_day_run_sheet(client, customer_login):
    response = client.get(reverse("lessons:day"))

    assert response.status_code == 403


@pytest.mark.security
def test_the_lesson_list_page_does_not_explode_for_a_customer(client, customer_login):
    """Regression: the HTML ownership path used to be an invalid ORM lookup.

    ``attendances__student__user`` does not exist — Student reaches a login
    through its Customer — so the first customer ever to open this page raised
    FieldError. Only staff had exercised the screen, so nothing caught it.
    """
    _user, mine = customer_login
    my_student = StudentFactory(customer=mine)
    my_lesson = make_lesson()
    LessonAttendanceFactory(lesson=my_lesson, student=my_student)
    LessonAttendanceFactory(lesson=make_lesson(), student=MinorStudentFactory())

    response = client.get(reverse("lessons:list"))

    assert response.status_code == 200
    assert {lesson.pk for lesson in response.context["lessons"]} == {my_lesson.pk}


# ---------------------------------------------------------------------------
# Surf camps — the highest-risk rows in the product
# ---------------------------------------------------------------------------
@pytest.mark.security
def test_a_customer_sees_only_their_own_camp_place(client, customer_login, other_customer):
    """MINORS: participant rows carry room numbers, flights and dietary notes.

    Do not delete or relax this test.
    """
    _user, mine = customer_login
    my_student = StudentFactory(customer=mine)
    camp = SurfCampFactory()
    mine_participant = CampParticipantFactory(camp=camp, student=my_student)
    another_family = CampParticipantFactory(camp=camp, student=MinorStudentFactory())

    response = client.get(reverse("campparticipant-list"))

    assert response.status_code == 200
    ids = {row["id"] for row in _rows(response)}
    assert ids == {mine_participant.pk}

    detail = client.get(reverse("campparticipant-detail", args=[another_family.pk]))
    assert detail.status_code == 404


@pytest.mark.security
@pytest.mark.parametrize("action", ["participants", "roster", "finance"])
def test_camp_overview_endpoints_refuse_external_accounts(client, customer_login, action):
    """MINORS: the daily register counts children and prints medical flags."""
    camp = SurfCampFactory()
    CampParticipantFactory(camp=camp, student=MinorStudentFactory())

    response = client.get(reverse(f"surfcamp-{action}", args=[camp.pk]))

    assert response.status_code == 403


@pytest.mark.security
def test_camp_takings_are_hidden_from_customers(client, customer_login):
    camp = SurfCampFactory()

    response = client.get(reverse("surfcamp-detail", args=[camp.pk]))

    assert response.status_code == 200
    assert response.json()["total_revenue"] is None


@pytest.mark.security
@pytest.mark.parametrize(
    "url_name", ["surf_camps:list", "surf_camps:detail", "surf_camps:roster"]
)
def test_the_camp_back_office_screens_refuse_external_accounts(
    client, customer_login, url_name
):
    """MINORS: these three templates render the participant list."""
    camp = SurfCampFactory()
    CampParticipantFactory(camp=camp, student=MinorStudentFactory())

    args = [] if url_name == "surf_camps:list" else [camp.pk]
    response = client.get(reverse(url_name, args=args))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------
@pytest.mark.security
def test_a_customer_cannot_open_the_daily_run_sheet(client, customer_login):
    response = client.get(reverse("booking-schedule"))

    assert response.status_code == 403


@pytest.mark.security
def test_a_customer_cannot_open_the_booking_calendar_feed(client, customer_login):
    response = client.get(reverse("booking-calendar"))

    assert response.status_code == 403


@pytest.mark.security
def test_a_customer_cannot_open_the_booking_calendar_page(client, customer_login):
    response = client.get(reverse("bookings:calendar"))

    assert response.status_code == 403


@pytest.mark.security
def test_a_customer_sees_only_their_own_waiting_list_entries(
    client, customer_login, other_customer
):
    from apps.bookings.tests.factories import WaitlistEntryFactory

    _user, mine = customer_login
    my_entry = WaitlistEntryFactory(customer=mine)
    WaitlistEntryFactory(customer=other_customer)

    response = client.get(reverse("waitlist-list"))

    assert response.status_code == 200
    assert {row["id"] for row in _rows(response)} == {my_entry.pk}


# ---------------------------------------------------------------------------
# Structural guard: no external-reachable viewset may forget to declare a rule
# ---------------------------------------------------------------------------
def _external_capabilities() -> set[str]:
    from apps.accounts.constants import capabilities_for

    caps: set[str] = set()
    for role in EXTERNAL_ROLES:
        caps |= set(capabilities_for(role))
    return caps


def test_every_viewset_an_external_role_can_reach_declares_an_ownership_rule():
    """A new module cannot quietly repeat the original mistake.

    Walks the auto-discovered API router, and for every viewset whose
    ``capability_prefix`` an external role holds, asserts that the class mixes
    in the scoping engine. Adding an endpoint under ``lessons``, ``finance``,
    ``rentals``, ``surf_camps`` or ``bookings`` without a policy fails here.
    """
    from config.api_urls import router

    external_prefixes = {cap.split(".", 1)[0] for cap in _external_capabilities()}
    offenders = []
    for _prefix, viewset, _basename in router.registry:
        capability_prefix = getattr(viewset, "capability_prefix", None)
        if capability_prefix not in external_prefixes:
            continue
        # The declaration must appear in the class body, not be inherited: a
        # new viewset has to state a policy rather than pick one up by
        # accident from a base class.
        if "external_access" not in vars(viewset):
            offenders.append(f"{viewset.__module__}.{viewset.__name__}")

    assert offenders == [], (
        "these viewsets are reachable by a customer or student but declare no "
        f"row-level ownership rule: {offenders}"
    )


def test_the_ownership_lookups_are_valid_orm_paths():
    """Every declared lookup must actually resolve.

    An invalid path (the historical ``attendances__student__user``) raises
    FieldError at request time — for external users only, which is precisely
    the group a staff-shaped test suite never exercises.
    """
    from django.core.exceptions import FieldError

    from config.api_urls import router

    user = make_login_user(Role.CUSTOMER, "lookup-probe")
    CustomerFactory(user=user)

    broken = []
    for _prefix, viewset, _basename in router.registry:
        lookups = tuple(getattr(viewset, "owner_lookups", ()) or ())
        if not lookups:
            continue
        model = getattr(getattr(viewset, "queryset", None), "model", None)
        if model is None:
            continue
        for lookup in lookups:
            try:
                list(model.objects.filter(**{lookup: user})[:1])
            except FieldError as exc:  # pragma: no cover - the failure path
                broken.append(f"{viewset.__name__}.{lookup}: {exc}")

    assert broken == []


def test_the_html_ownership_lookups_are_valid_orm_paths():
    """Same check for the class-based views that declare ``owner_lookup``."""
    from django.core.exceptions import FieldError

    user = make_login_user(Role.CUSTOMER, "html-probe")
    CustomerFactory(user=user)

    declared = [
        (Invoice, "customer__user"),
        (Payment, "customer__user"),
        (LessonAttendance, "student__customer__user"),
        (CampParticipant, "student__customer__user"),
        (CampParticipant, "booking__customer__user"),
    ]
    broken = []
    for model, lookup in declared:
        try:
            list(model.objects.filter(**{lookup: user})[:1])
        except FieldError as exc:  # pragma: no cover - the failure path
            broken.append(f"{model.__name__}.{lookup}: {exc}")

    assert broken == []
