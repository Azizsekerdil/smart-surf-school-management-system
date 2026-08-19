"""HTML views for the finance module.

Views orchestrate: they read the request, hand the decision to
:mod:`apps.finance.services` and render the result. No view contains a money
rule, and every state-changing view is POST-only and capability-guarded.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.accounts.scoping import SHARED
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
)
from apps.core.utils import RANGE_CHOICES, parse_date_range

from . import selectors, services
from .forms import (
    CommissionFilterForm,
    ExpenseFilterForm,
    ExpenseForm,
    InvoiceFilterForm,
    InvoiceForm,
    InvoiceLineFormSet,
    PaymentFilterForm,
    PaymentForm,
    PricePackageForm,
    RefundForm,
    SellPackageForm,
    UsePackageForm,
)
from .models import (
    CommissionRecord,
    CustomerPackage,
    Expense,
    Invoice,
    Payment,
    PricePackage,
    to_money,
)

ZERO = Decimal("0.00")


class FinanceRangeMixin(DateRangeMixin):
    """Adds the shared period filter and echoes it into the template."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["range_form_action"] = self.request.path
        context["range_choices"] = RANGE_CHOICES
        context["range_start_value"] = self.request.GET.get("start", "")
        context["range_end_value"] = self.request.GET.get("end", "")
        return context


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class FinanceDashboardView(CapabilityRequiredMixin, FinanceRangeMixin, TemplateView):
    """Revenue, costs and receivables at a glance, with a period comparison.

    Gated on ``finance.revenue``, not ``finance.view``. Roles that handle money
    at a counter -- reception, rental staff -- need ``finance.view`` to take a
    payment; the school takings, margin and instructor commissions shown here
    are a separate decision.
    """

    capability = "finance.revenue"
    template_name = "finance/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, label = parse_date_range(self.request)

        summary = services.financial_summary(start, end)
        context["summary"] = summary
        context["chart"] = services.revenue_chart_payload(start, end)
        context["range_label"] = label

        context["recent_payments"] = selectors.payment_queryset().order_by("-paid_at")[:8]
        context["overdue_invoices"] = selectors.overdue_invoice_queryset().select_related(
            "customer"
        )[:8]
        context["pending_commissions"] = (
            selectors.commission_queryset()
            .filter(status__in=CommissionRecord.OWED_STATUSES)
            .order_by("-period_end")[:8]
        )
        context["expiring_packages"] = (
            selectors.customer_package_queryset()
            .filter(status=CustomerPackage.Status.ACTIVE)
            .order_by("expires_on")[:6]
        )
        return context


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class PaymentListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    FinanceRangeMixin,
    HtmxPartialMixin,
    ListView,
):
    """The ledger: every movement of money, newest first."""

    capability = "finance.view"
    owner_lookup = "customer__user"
    model = Payment
    template_name = "finance/payment_list.html"
    partial_template_name = "finance/partials/payment_table.html"
    context_object_name = "payments"
    paginate_by = 25
    date_field = "paid_at"
    search_fields = (
        "payment_code",
        "reference",
        "notes",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
    )

    def get_queryset(self):
        queryset = self.apply_date_range(
            self.apply_search(self.scope(selectors.payment_queryset()))
        )

        category = self.request.GET.get("category", "")
        if category in dict(Payment.Category.choices):
            queryset = queryset.filter(category=category)

        method = self.request.GET.get("method", "")
        if method:
            queryset = queryset.filter(method=method)

        kind = self.request.GET.get("kind", "")
        if kind == "refunds":
            queryset = queryset.filter(is_refund=True)
        elif kind == "payments":
            queryset = queryset.filter(is_refund=False)

        return queryset.order_by("-paid_at", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()
        context["filter_form"] = PaymentFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "range": self.request.GET.get("range", "30"),
                "start": self.request.GET.get("start", ""),
                "end": self.request.GET.get("end", ""),
                "category": self.request.GET.get("category", ""),
                "method": self.request.GET.get("method", ""),
                "kind": self.request.GET.get("kind", ""),
            }
        )
        # School-wide totals are a revenue disclosure, not a property of the
        # rows on screen. Only finance.revenue holders get them; for everyone
        # else the template omits the banner.
        if self.request.user.has_capability("finance.revenue"):
            context["totals"] = {
                "gross": selectors.gross_revenue(start, end),
                "refunds": selectors.refunds_total(start, end),
                "net": selectors.net_revenue(start, end),
            }
            context["by_method"] = selectors.revenue_by_method(start, end)
        else:
            context["totals"] = None
            context["by_method"] = None
        return context


class PaymentDetailView(CapabilityRequiredMixin, OwnerScopedQuerysetMixin, DetailView):
    capability = "finance.view"
    owner_lookup = "customer__user"
    model = Payment
    template_name = "finance/payment_detail.html"
    context_object_name = "payment"

    def get_queryset(self):
        return self.scope(selectors.payment_queryset().prefetch_related("refunds"))


class PaymentCreateView(CapabilityRequiredMixin, FormView):
    """Take a payment. The service does the work; the view only reports it."""

    capability = "finance.add"
    form_class = PaymentForm
    template_name = "finance/payment_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        for key in ("invoice", "booking", "rental", "customer"):
            value = self.request.GET.get(key)
            if value and value.isdigit():
                initial[key] = int(value)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Record a payment")
        context["cancel_url"] = reverse("finance:payment_list")
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            payment = services.record_payment(
                data["customer"],
                data["amount"],
                method=data["method"],
                category=data["category"],
                invoice=data.get("invoice"),
                booking=data.get("booking"),
                rental=data.get("rental"),
                paid_at=data.get("paid_at"),
                reference=data.get("reference", ""),
                notes=data.get("notes", ""),
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as error:
            _push_form_errors(form, error)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Payment %(code)s recorded.") % {"code": payment.payment_code},
        )
        return redirect("finance:payment_detail", pk=payment.pk)


class PaymentRefundView(CapabilityRequiredMixin, FormView):
    """Refund part or all of a payment. Guarded by ``finance.refund``."""

    capability = "finance.refund"
    form_class = RefundForm
    template_name = "finance/payment_refund.html"

    def get_payment(self) -> Payment:
        return get_object_or_404(
            selectors.payment_queryset().prefetch_related("refunds"), pk=self.kwargs["pk"]
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["payment"] = self.get_payment()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = self.get_payment()
        context["payment"] = payment
        context["cancel_url"] = reverse("finance:payment_detail", kwargs={"pk": payment.pk})
        return context

    def form_valid(self, form):
        payment = self.get_payment()
        try:
            refund = services.refund_payment(
                payment,
                form.cleaned_data["amount"],
                form.cleaned_data["reason"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as error:
            _push_form_errors(form, error)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Refund %(code)s issued against %(original)s.")
            % {"code": refund.payment_code, "original": payment.payment_code},
        )
        return redirect("finance:payment_detail", pk=refund.pk)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    FinanceRangeMixin,
    HtmxPartialMixin,
    ListView,
):
    capability = "finance.view"
    owner_lookup = "customer__user"
    model = Invoice
    template_name = "finance/invoice_list.html"
    partial_template_name = "finance/partials/invoice_table.html"
    context_object_name = "invoices"
    paginate_by = 25
    date_field = "issue_date"
    search_fields = (
        "invoice_number",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
    )

    def get_queryset(self):
        queryset = self.apply_date_range(
            self.apply_search(self.scope(selectors.invoice_queryset()))
        )
        status = self.request.GET.get("status", "")
        if status == "open":
            queryset = queryset.filter(status__in=Invoice.OPEN_STATUSES)
        elif status in dict(Invoice.Status.choices):
            queryset = queryset.filter(status=status)
        return selectors.with_balance(queryset).order_by("-issue_date", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = InvoiceFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "range": self.request.GET.get("range", "30"),
                "start": self.request.GET.get("start", ""),
                "end": self.request.GET.get("end", ""),
                "status": self.request.GET.get("status", ""),
            }
        )
        if self.request.user.has_capability("finance.revenue"):
            context["receivables"] = selectors.receivables_total()
            context["overdue_count"] = selectors.overdue_invoice_queryset().count()
        else:
            context["receivables"] = None
            context["overdue_count"] = self.scope(
                selectors.overdue_invoice_queryset()
            ).count()
        return context


class InvoiceDetailView(CapabilityRequiredMixin, OwnerScopedQuerysetMixin, DetailView):
    capability = "finance.view"
    owner_lookup = "customer__user"
    model = Invoice
    template_name = "finance/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return self.scope(
            selectors.invoice_queryset().prefetch_related("lines", "payments__customer")
        )


class InvoiceCreateView(CapabilityRequiredMixin, FormView):
    """Raise an invoice by hand, line by line."""

    capability = "finance.add"
    form_class = InvoiceForm
    template_name = "finance/invoice_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "line_formset" not in context:
            context["line_formset"] = InvoiceLineFormSet(
                self.request.POST or None, prefix="lines"
            )
        context["form_title"] = _("New invoice")
        context["cancel_url"] = reverse("finance:invoice_list")
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        formset = InvoiceLineFormSet(request.POST, prefix="lines")
        if not (form.is_valid() and formset.is_valid()):
            return self.render_to_response(
                self.get_context_data(form=form, line_formset=formset)
            )

        lines = []
        for index, line_form in enumerate(formset.forms):
            data = line_form.cleaned_data
            description = (data.get("description") or "").strip()
            unit_price = to_money(data.get("unit_price"))
            if not description and unit_price == ZERO:
                continue
            lines.append(
                {
                    "description": description,
                    "quantity": data.get("quantity") or Decimal("1.00"),
                    "unit_price": unit_price,
                    "discount_amount": to_money(data.get("discount_amount")),
                    "sort_order": index,
                }
            )

        if not lines:
            messages.error(request, _("Add at least one line before saving the invoice."))
            return self.render_to_response(
                self.get_context_data(form=form, line_formset=formset)
            )

        try:
            invoice = services.create_invoice(
                form.cleaned_data["customer"],
                lines,
                booking=form.cleaned_data.get("booking"),
                rental=form.cleaned_data.get("rental"),
                issue_date=form.cleaned_data.get("issue_date"),
                due_date=form.cleaned_data.get("due_date"),
                discount_amount=form.cleaned_data.get("discount_amount") or ZERO,
                tax_rate=form.cleaned_data.get("tax_rate"),
                notes=form.cleaned_data.get("notes", ""),
                terms=form.cleaned_data.get("terms", ""),
                user=request.user,
                request=request,
            )
        except ValidationError as error:
            _push_form_errors(form, error)
            return self.render_to_response(
                self.get_context_data(form=form, line_formset=formset)
            )

        messages.success(
            request, _("Invoice %(number)s created.") % {"number": invoice.invoice_number}
        )
        return redirect("finance:invoice_detail", pk=invoice.pk)


class InvoiceIssueView(CapabilityRequiredMixin, View):
    """POST-only: turn a draft into a receivable."""

    capability = "finance.change"

    def post(self, request, pk: int, *args, **kwargs):
        invoice = get_object_or_404(Invoice, pk=pk)
        try:
            services.issue_invoice(invoice, user=request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("Invoice %(number)s issued.") % {"number": invoice.invoice_number},
            )
        return redirect("finance:invoice_detail", pk=invoice.pk)


class InvoiceCancelView(CapabilityRequiredMixin, View):
    """POST-only: void an unpaid invoice."""

    capability = "finance.change"

    def post(self, request, pk: int, *args, **kwargs):
        invoice = get_object_or_404(Invoice, pk=pk)
        reason = (request.POST.get("reason") or "").strip()
        try:
            services.cancel_invoice(invoice, reason, user=request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("Invoice %(number)s cancelled.") % {"number": invoice.invoice_number},
            )
        return redirect("finance:invoice_detail", pk=invoice.pk)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
class ExpenseListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    FinanceRangeMixin,
    HtmxPartialMixin,
    ListView,
):
    capability = "finance.view"
    # No owner_lookup: the outgoings of the school have no customer
    # dimension, so the fail-closed default hides every row from external
    # accounts.
    model = Expense
    template_name = "finance/expense_list.html"
    partial_template_name = "finance/partials/expense_table.html"
    context_object_name = "expenses"
    paginate_by = 25
    date_field = "spent_on"
    search_fields = ("expense_code", "description", "supplier", "invoice_reference")

    def get_queryset(self):
        queryset = self.apply_date_range(
            self.apply_search(self.scope(selectors.expense_queryset()))
        )
        category = self.request.GET.get("category", "")
        if category and category.isdigit():
            queryset = queryset.filter(category_id=int(category))
        return queryset.order_by("-spent_on", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()
        context["filter_form"] = ExpenseFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "range": self.request.GET.get("range", "30"),
                "start": self.request.GET.get("start", ""),
                "end": self.request.GET.get("end", ""),
                "category": self.request.GET.get("category", ""),
            }
        )
        context["expense_total"] = selectors.expense_total(start, end)
        context["by_category"] = selectors.expenses_by_category(start, end)
        return context


class ExpenseCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "finance.add"
    model = Expense
    form_class = ExpenseForm
    template_name = "finance/expense_form.html"
    success_message = _lazy("Expense recorded.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if form.instance.paid_by_id is None and self.request.user.is_authenticated:
            form.instance.paid_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Record an expense")
        context["cancel_url"] = reverse("finance:expense_list")
        return context

    def get_success_url(self):
        return reverse("finance:expense_list")


class ExpenseUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "finance.change"
    model = Expense
    form_class = ExpenseForm
    template_name = "finance/expense_form.html"
    success_message = _lazy("Expense updated.")

    def get_queryset(self):
        return selectors.expense_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit expense")
        context["cancel_url"] = reverse("finance:expense_list")
        return context

    def get_success_url(self):
        return reverse("finance:expense_list")


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------
class CommissionListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    """What the school owes its instructors, and what it has already paid."""

    # Instructor pay is staff data and a revenue-adjacent figure.
    capability = "finance.revenue"
    model = CommissionRecord
    template_name = "finance/commission_list.html"
    partial_template_name = "finance/partials/commission_table.html"
    context_object_name = "commissions"
    paginate_by = 25
    search_fields = (
        "instructor__instructor_code",
        "instructor__user__first_name",
        "instructor__user__last_name",
    )

    def get_queryset(self):
        queryset = self.apply_search(self.scope(selectors.commission_queryset()))
        status = self.request.GET.get("status", "")
        if status == "owed":
            queryset = queryset.filter(status__in=CommissionRecord.OWED_STATUSES)
        elif status in dict(CommissionRecord.Status.choices):
            queryset = queryset.filter(status=status)
        return queryset.order_by("-period_end", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = CommissionFilterForm(
            initial={
                "status": self.request.GET.get("status", ""),
                "q": self.request.GET.get("q", ""),
            }
        )
        context["owed_total"] = selectors.commission_owed()
        context["pending_count"] = CommissionRecord.objects.filter(
            status=CommissionRecord.Status.PENDING
        ).count()
        context["instructors"] = _commissionable_instructors()
        # Default the calculator to the month so far — the period a school
        # actually settles commission over.
        today = timezone.localdate()
        context["default_period_start"] = today.replace(day=1)
        context["default_period_end"] = today
        context["status"] = self.request.GET.get("status", "")
        return context


def _commissionable_instructors():
    """Active instructors who actually earn a percentage."""
    from apps.instructors.models import Instructor

    return (
        Instructor.objects.filter(is_active=True, commission_percent__gt=0)
        .select_related("user")
        .order_by("user__first_name", "user__last_name")
    )


class CommissionGenerateView(CapabilityRequiredMixin, View):
    """POST-only: work out an instructor's commission for a period."""

    capability = "finance.add"

    @staticmethod
    def _parse_date(raw: str, fallback: date) -> date:
        try:
            return date.fromisoformat((raw or "").strip())
        except ValueError:
            return fallback

    def post(self, request, *args, **kwargs):
        from apps.instructors.models import Instructor

        instructor_id = request.POST.get("instructor", "")
        if not instructor_id.isdigit():
            messages.error(request, _("Choose the instructor to calculate for."))
            return redirect("finance:commission_list")

        today = timezone.localdate()
        end = self._parse_date(request.POST.get("period_end", ""), today)
        start = self._parse_date(
            request.POST.get("period_start", ""), end - timedelta(days=29)
        )

        instructor = get_object_or_404(Instructor, pk=int(instructor_id))
        try:
            records = services.calculate_commission(
                instructor,
                start,
                end,
                user=request.user,
                request=request,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return redirect("finance:commission_list")

        if records:
            total = sum((record.commission_amount for record in records), ZERO)
            messages.success(
                request,
                _("%(count)s commission record(s) totalling %(total)s created for %(name)s.")
                % {"count": len(records), "total": total, "name": instructor},
            )
        else:
            messages.info(
                request,
                _("No new commission to record for %(name)s in this period.")
                % {"name": instructor},
            )
        return redirect("finance:commission_list")


class CommissionApproveView(CapabilityRequiredMixin, View):
    """POST-only: sign off a commission row."""

    capability = "finance.approve"

    def post(self, request, pk: int, *args, **kwargs):
        record = get_object_or_404(selectors.commission_queryset(), pk=pk)
        try:
            services.approve_commission(record, user=request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(request, _("Commission approved."))
        return redirect("finance:commission_list")


class CommissionPayView(CapabilityRequiredMixin, View):
    """POST-only: mark an approved commission as paid out."""

    capability = "finance.change"

    def post(self, request, pk: int, *args, **kwargs):
        record = get_object_or_404(selectors.commission_queryset(), pk=pk)
        try:
            services.pay_commission(record, user=request.user, request=request)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(
                request,
                _("Commission of %(amount)s marked as paid.")
                % {"amount": record.commission_amount},
            )
        return redirect("finance:commission_list")


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
class PricePackageListView(
    CapabilityRequiredMixin, OwnerScopedQuerysetMixin, SearchableListMixin, ListView
):
    capability = "finance.view"
    # A price list is the same for everybody.
    external_access = SHARED
    model = PricePackage
    template_name = "finance/pricepackage_list.html"
    context_object_name = "packages"
    paginate_by = 25
    search_fields = ("name", "code", "description")

    def get_queryset(self):
        queryset = self.apply_search(self.scope(selectors.package_queryset()))
        status = self.request.GET.get("status", "active")
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset.order_by("sort_order", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status"] = self.request.GET.get("status", "active")
        context["status_tabs"] = (
            ("active", _("On sale")),
            ("inactive", _("Withdrawn")),
            ("all", _("All")),
        )
        context["package_liability"] = (
            selectors.package_liability()
            if self.request.user.has_capability("finance.revenue")
            else None
        )
        return context


class PricePackageCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "finance.add"
    model = PricePackage
    form_class = PricePackageForm
    template_name = "finance/pricepackage_form.html"
    success_message = _lazy("Package created.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New package")
        context["cancel_url"] = reverse("finance:package_list")
        return context

    def get_success_url(self):
        return reverse("finance:package_list")


class PricePackageUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "finance.change"
    model = PricePackage
    form_class = PricePackageForm
    template_name = "finance/pricepackage_form.html"
    success_message = _lazy("Package updated.")

    def get_queryset(self):
        return selectors.package_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit package")
        context["cancel_url"] = reverse("finance:package_list")
        return context

    def get_success_url(self):
        return reverse("finance:package_list")


class CustomerPackageListView(
    CapabilityRequiredMixin, OwnerScopedQuerysetMixin, SearchableListMixin, ListView
):
    """Every package card sold, and how much of it is left."""

    capability = "finance.view"
    owner_lookup = "customer__user"
    model = CustomerPackage
    template_name = "finance/customerpackage_list.html"
    context_object_name = "customer_packages"
    paginate_by = 25
    search_fields = (
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
        "package__name",
        "package__code",
    )

    def get_queryset(self):
        queryset = self.apply_search(self.scope(selectors.customer_package_queryset()))
        status = self.request.GET.get("status", "active")
        if status in dict(CustomerPackage.Status.choices):
            queryset = queryset.filter(status=status)
        return queryset.order_by("-purchased_on", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status"] = self.request.GET.get("status", "active")
        context["status_choices"] = CustomerPackage.Status.choices
        if self.request.user.has_capability("finance.revenue"):
            context["package_liability"] = selectors.package_liability()
            context["active_count"] = CustomerPackage.objects.filter(
                status=CustomerPackage.Status.ACTIVE
            ).count()
        else:
            context["package_liability"] = None
            context["active_count"] = self.scope(
                CustomerPackage.objects.filter(status=CustomerPackage.Status.ACTIVE)
            ).count()
        return context


class SellPackageView(CapabilityRequiredMixin, FormView):
    """Sell a package and take the money in one step."""

    capability = "finance.add"
    form_class = SellPackageForm
    template_name = "finance/package_sell.html"

    def get_initial(self):
        initial = super().get_initial()
        for key in ("customer", "package"):
            value = self.request.GET.get(key)
            if value and value.isdigit():
                initial[key] = int(value)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Sell a package")
        context["cancel_url"] = reverse("finance:customer_package_list")
        context["packages"] = selectors.package_queryset().filter(is_active=True)
        return context

    def form_valid(self, form):
        try:
            customer_package, payment = services.sell_package(
                form.cleaned_data["customer"],
                form.cleaned_data["package"],
                form.cleaned_data["payment_method"],
                self.request.user,
                reference=form.cleaned_data.get("reference", ""),
                request=self.request,
            )
        except ValidationError as error:
            _push_form_errors(form, error)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("%(package)s sold to %(customer)s. Payment %(code)s recorded.")
            % {
                "package": customer_package.package.name,
                "customer": customer_package.customer,
                "code": payment.payment_code,
            },
        )
        return redirect("finance:customer_package_list")


class UsePackageLessonView(CapabilityRequiredMixin, FormView):
    """Redeem one lesson from a customer's package against a booking."""

    capability = "finance.change"
    form_class = UsePackageForm
    template_name = "finance/package_use.html"

    def get_customer_package(self) -> CustomerPackage:
        return get_object_or_404(
            selectors.customer_package_queryset(), pk=self.kwargs["pk"]
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["customer_package"] = self.get_customer_package()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer_package = self.get_customer_package()
        context["customer_package"] = customer_package
        context["cancel_url"] = reverse("finance:customer_package_list")
        return context

    def form_valid(self, form):
        customer_package = self.get_customer_package()
        try:
            services.use_package_lesson(
                customer_package,
                form.cleaned_data["booking"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as error:
            _push_form_errors(form, error)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Lesson taken off the package. %(left)s remaining.")
            % {"left": customer_package.lessons_remaining},
        )
        return redirect("finance:customer_package_list")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _push_form_errors(form, error: ValidationError) -> None:
    """Surface a service-layer ``ValidationError`` on the bound form."""
    if hasattr(error, "error_dict"):
        for field, errors in error.message_dict.items():
            target = field if field in form.fields else None
            for message in errors:
                form.add_error(target, message)
    else:
        for message in error.messages:
            form.add_error(None, message)
