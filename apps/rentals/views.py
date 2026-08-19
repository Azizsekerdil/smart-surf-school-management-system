"""Hire-counter screens.

The two screens that matter are check-out and check-in. Both are built so an
operator can work at counter speed: scan an asset code, see the running total,
confirm. Every decision they trigger lives in :mod:`apps.rentals.services`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.generic import (
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.accounts.permissions import CapabilityRequiredMixin, StaffOnlyMixin
from apps.core.enums import RentalPeriod
from apps.core.mixins import (
    AuditedDeleteMixin,
    AuditedUpdateMixin,
    HtmxPartialMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
)
from apps.core.utils import to_decimal

from . import selectors, services
from .forms import (
    DEFAULT_WINDOW,
    AddItemForm,
    QuickReturnForm,
    RentalCancelForm,
    RentalCheckOutForm,
    RentalExtendForm,
    RentalLostForm,
    RentalPaymentForm,
    RentalUpdateForm,
    build_return_forms,
    item_conditions_from_forms,
)
from .models import Rental, RentalItem

ZERO = Decimal("0.00")
BASKET_SESSION_KEY = "rentals.basket"

#: Badge palette for equipment condition, passed to ``{% status_badge %}``.
CONDITION_COLORS = {
    "new": "emerald",
    "excellent": "emerald",
    "good": "sky",
    "fair": "amber",
    "poor": "rose",
    "unusable": "rose",
}


# ---------------------------------------------------------------------------
# Check-out basket (session-held: no half-finished Rental rows in the database)
# ---------------------------------------------------------------------------
def _get_basket(request) -> list[dict]:
    basket = request.session.get(BASKET_SESSION_KEY) or []
    return [line for line in basket if isinstance(line, dict) and line.get("equipment_id")]


def _save_basket(request, lines: list[dict]) -> None:
    request.session[BASKET_SESSION_KEY] = lines
    request.session.modified = True


def _clear_basket(request) -> None:
    request.session.pop(BASKET_SESSION_KEY, None)
    request.session.modified = True


def _parse_datetime(raw: str | None):
    if not raw:
        return None
    value = parse_datetime(raw)
    if value is None:
        try:
            value = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _window_from(data) -> tuple[str, datetime, datetime]:
    """Read the period and hire window out of a request payload, with defaults."""
    period = data.get("period_type") or RentalPeriod.DAILY
    if period not in RentalPeriod.values:
        period = RentalPeriod.DAILY
    start = _parse_datetime(data.get("start_at")) or timezone.now().replace(
        second=0, microsecond=0
    )
    end = _parse_datetime(data.get("expected_return_at"))
    if end is None or end <= start:
        end = start + DEFAULT_WINDOW.get(period, timedelta(days=1))
    return period, start, end


def _basket_context(request, data=None) -> dict:
    """Price the current basket so the operator sees a live running total."""
    data = data if data is not None else request.POST or request.GET
    period, start, end = _window_from(data)
    lines_raw = _get_basket(request)

    model = services.equipment_model()
    asset_ids = [line["equipment_id"] for line in lines_raw]
    assets = {obj.pk: obj for obj in model.objects.filter(pk__in=asset_ids)}

    pairs = []
    stale = []
    for line in lines_raw:
        asset = assets.get(line["equipment_id"])
        if asset is None:
            stale.append(line)
            continue
        pairs.append((asset, line.get("quantity") or 1))
    if stale:
        _save_basket(request, [line for line in lines_raw if line not in stale])

    lines = services.price_lines(pairs, period, start, end)
    subtotal = sum((line["line_total"] for line in lines), ZERO)
    discount = to_decimal(data.get("discount_amount") or ZERO)
    if discount > subtotal:
        discount = subtotal
    deposit = to_decimal(data.get("deposit_amount") or ZERO)

    warnings: list[str] = []
    for asset, _quantity in pairs:
        warnings.extend(services.equipment_conflicts(asset, start, end))

    return {
        "basket_lines": lines,
        "basket_subtotal": subtotal,
        "basket_discount": discount,
        "basket_total": max(subtotal - discount, ZERO),
        "basket_deposit": deposit,
        "basket_due_now": max(subtotal - discount, ZERO) + deposit,
        "basket_period": period,
        "basket_start": start,
        "basket_end": end,
        "basket_units": services.period_units(period, start, end),
        "basket_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# List & detail
# ---------------------------------------------------------------------------
class RentalListView(
    CapabilityRequiredMixin,
    OwnerScopedQuerysetMixin,
    SearchableListMixin,
    HtmxPartialMixin,
    ListView,
):
    capability = "rentals.view"
    model = Rental
    template_name = "rentals/rental_list.html"
    partial_template_name = "rentals/partials/rental_table.html"
    context_object_name = "rentals"
    paginate_by = 25
    # A hire can be booked by one person for another (a parent for a child),
    # so both links back to a login count as ownership.
    owner_lookups = ("customer__user", "student__customer__user")
    search_fields = (
        "rental_code",
        "customer__first_name",
        "customer__last_name",
        "notes",
    )

    @property
    def tab(self) -> str:
        requested = self.request.GET.get("tab", "active")
        return requested if requested in selectors.LIST_TABS else "active"

    def apply_search(self, queryset):
        term = self.get_search_term()
        if not term:
            return queryset
        condition = Q()
        for field in self.search_fields:
            condition |= Q(**{f"{field}__icontains": term})
        condition |= services.equipment_code_q(term, prefix="items__equipment__")
        return queryset.filter(condition).distinct()

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("customer", "student", "booking")
            .prefetch_related("items__equipment")
        )
        tab = self.tab
        if tab == "overdue":
            queryset = queryset.filter(
                Q(status=Rental.Status.OVERDUE)
                | Q(status=Rental.Status.ACTIVE, expected_return_at__lt=timezone.now()),
                returned_at__isnull=True,
            )
        else:
            queryset = queryset.filter(status__in=selectors.statuses_for_tab(tab))

        payment = self.request.GET.get("payment", "").strip()
        if payment:
            queryset = queryset.filter(payment_status=payment)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tab"] = self.tab
        context["tabs"] = [
            ("active", _("Out now")),
            ("overdue", _("Overdue")),
            ("returned", _("Returned")),
            ("all", _("All")),
        ]
        context["stats"] = selectors.counter_stats()
        context["quick_return_form"] = QuickReturnForm()
        context["current_payment"] = self.request.GET.get("payment", "")
        context["now"] = timezone.now()
        return context


class RentalDetailView(CapabilityRequiredMixin, OwnerScopedQuerysetMixin, DetailView):
    capability = "rentals.view"
    model = Rental
    template_name = "rentals/rental_detail.html"
    context_object_name = "rental"
    owner_lookups = ("customer__user", "student__customer__user")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("customer", "student", "booking", "checked_out_by", "checked_in_by")
            .prefetch_related("items__equipment")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rental = self.object
        context["items"] = rental.items.select_related("equipment")
        context["open_items"] = [item for item in context["items"] if not item.is_returned]
        context["projected_late_fee"] = (
            services.calculate_late_fee(rental) if rental.status in Rental.OPEN_STATUSES else ZERO
        )
        context["payment_form"] = RentalPaymentForm(
            initial={"amount": max(rental.balance_due, ZERO)}
        )
        context["extend_form"] = RentalExtendForm(rental=rental)
        context["is_open"] = rental.status in Rental.OPEN_STATUSES
        context["condition_colors"] = CONDITION_COLORS
        return context


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------
class RentalCheckOutView(CapabilityRequiredMixin, TemplateView):
    """The counter screen: pick a customer, scan gear, take a deposit, hand over."""

    capability = "rentals.add"
    template_name = "rentals/rental_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", self._initial_form())
        context.setdefault("add_item_form", AddItemForm())
        source = self.request.POST if self.request.method == "POST" else self.request.GET
        context.update(_basket_context(self.request, source))
        context["period_choices"] = RentalPeriod.choices
        return context

    def _initial_form(self) -> RentalCheckOutForm:
        initial = {}
        customer = self.request.GET.get("customer")
        booking = self.request.GET.get("booking")
        student = self.request.GET.get("student")
        if customer and customer.isdigit():
            initial["customer"] = int(customer)
        if booking and booking.isdigit():
            initial["booking"] = int(booking)
        if student and student.isdigit():
            initial["student"] = int(student)
        return RentalCheckOutForm(initial=initial)

    def post(self, request, *args, **kwargs):
        form = RentalCheckOutForm(request.POST)
        basket = _get_basket(request)

        if not basket:
            messages.error(request, _("Add at least one piece of equipment before checking out."))
            return self.render_to_response(self.get_context_data(form=form))

        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        model = services.equipment_model()
        assets = {obj.pk: obj for obj in model.objects.filter(
            pk__in=[line["equipment_id"] for line in basket]
        )}
        items = [
            (assets[line["equipment_id"]], line.get("quantity") or 1)
            for line in basket
            if line["equipment_id"] in assets
        ]

        data = form.cleaned_data
        try:
            rental = services.create_rental(
                customer=data["customer"],
                items=items,
                period_type=data["period_type"],
                start_at=data["start_at"],
                expected_return_at=data["expected_return_at"],
                student=data.get("student"),
                booking=data.get("booking"),
                deposit_amount=data.get("deposit_amount") or ZERO,
                discount_amount=data.get("discount_amount") or ZERO,
                paid_amount=data.get("paid_amount") or ZERO,
                id_document_held=data.get("id_document_held") or False,
                notes=data.get("notes") or "",
                user=request.user,
                request=request,
            )
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
            return self.render_to_response(self.get_context_data(form=form))

        _clear_basket(request)
        messages.success(
            request,
            _("Rental %(code)s checked out — %(count)s item(s).")
            % {"code": rental.rental_code, "count": rental.item_count},
        )
        return redirect("rentals:detail", pk=rental.pk)


class BasketMixin(CapabilityRequiredMixin):
    """Shared plumbing for the HTMX basket endpoints."""

    capability = "rentals.add"

    def render_basket(self, request, error: str = "", notice: str = ""):
        context = _basket_context(request, request.POST)
        context["basket_error"] = error
        context["basket_notice"] = notice
        context["add_item_form"] = AddItemForm()
        return render(request, "rentals/partials/checkout_basket.html", context)


class BasketAddView(BasketMixin, View):
    """Scan or type an asset code to put a line in the basket."""

    def post(self, request, *args, **kwargs):
        form = AddItemForm(request.POST)
        if not form.is_valid():
            return self.render_basket(request, error=_("Enter an asset code."))

        code = form.cleaned_data["asset_code"]
        quantity = form.cleaned_data.get("quantity") or 1
        equipment = services.find_equipment_by_code(code)
        if equipment is None:
            return self.render_basket(
                request,
                error=_("No asset matches the code “%(code)s”.") % {"code": code},
            )

        _period, start, end = _window_from(request.POST)
        conflicts = services.equipment_conflicts(equipment, start, end)
        if conflicts:
            return self.render_basket(request, error=" ".join(str(c) for c in conflicts))

        lines = _get_basket(request)
        for line in lines:
            if line["equipment_id"] == equipment.pk:
                return self.render_basket(
                    request,
                    error=_("%(item)s is already in the basket.")
                    % {"item": services.equipment_label(equipment)},
                )
        lines.append({"equipment_id": equipment.pk, "quantity": quantity})
        _save_basket(request, lines)
        return self.render_basket(
            request,
            notice=_("%(item)s added.") % {"item": services.equipment_label(equipment)},
        )


class BasketRemoveView(BasketMixin, View):
    def post(self, request, *args, **kwargs):
        raw = request.POST.get("equipment_id", "")
        equipment_id = int(raw) if raw.isdigit() else None
        lines = [line for line in _get_basket(request) if line["equipment_id"] != equipment_id]
        _save_basket(request, lines)
        return self.render_basket(request, notice=_("Line removed."))


class BasketClearView(BasketMixin, View):
    def post(self, request, *args, **kwargs):
        _clear_basket(request)
        return self.render_basket(request, notice=_("Basket cleared."))


class BasketPreviewView(BasketMixin, View):
    """Re-price the basket when the period, dates, deposit or discount change."""

    def post(self, request, *args, **kwargs):
        return self.render_basket(request)


class EntitySearchView(CapabilityRequiredMixin, StaffOnlyMixin, View):
    """HTMX picker for customers, students, bookings and equipment.

    Staff-only: this is a directory lookup over other people. External
    accounts hold ``rentals.view`` for their own hire history, not for a
    customer search box.
    """

    capability = "rentals.view"
    template_name = "rentals/partials/search_results.html"

    def get(self, request, *args, **kwargs):
        kind = request.GET.get("kind", "customer")
        term = request.GET.get("q") or request.GET.get("customer_q") or ""
        try:
            results = services.search_related(kind, term)
        except ValidationError:
            results = []
        rows = [{"pk": obj.pk, "label": str(obj)} for obj in results]
        return render(
            request,
            self.template_name,
            {"results": rows, "kind": kind, "term": term, "target": request.GET.get("target", "")},
        )


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------
class RentalReturnView(CapabilityRequiredMixin, TemplateView):
    """Check-in screen: condition per asset, damage, late fee, deposit settlement."""

    capability = "rentals.change"
    template_name = "rentals/rental_return.html"

    def get_rental(self) -> Rental:
        return get_object_or_404(
            Rental.objects.select_related("customer").prefetch_related("items__equipment"),
            pk=self.kwargs["pk"],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rental = context.get("rental") or self.get_rental()
        context["rental"] = rental
        if "item_forms" not in context:
            context["item_forms"] = build_return_forms(rental)
        context.update(self._summary(rental, context["item_forms"]))
        return context

    @staticmethod
    def _summary(rental: Rental, item_forms) -> dict:
        """What the operator must see before pressing confirm."""
        now = timezone.now()
        damage_total = ZERO
        for form in item_forms:
            if form.is_bound and form.is_valid():
                if form.cleaned_data.get("check_in"):
                    damage_total += form.cleaned_data.get("damage_charge") or ZERO
        late_fee = services.calculate_late_fee(rental, now)
        deductible = late_fee + damage_total
        withheld = min(rental.deposit_amount or ZERO, deductible)
        total = max(
            (rental.subtotal or ZERO) - (rental.discount_amount or ZERO) + late_fee + damage_total,
            ZERO,
        )
        return {
            "now": now,
            "late_fee": late_fee,
            "damage_total": damage_total,
            "projected_total": total,
            "deposit_withheld": withheld,
            "deposit_refund": (rental.deposit_amount or ZERO) - withheld,
            "projected_balance": total - (rental.paid_amount or ZERO) - withheld,
            "hours_overdue": rental.hours_overdue,
        }

    def post(self, request, *args, **kwargs):
        rental = self.get_rental()
        item_forms = build_return_forms(rental, request.POST)
        if not all(form.is_valid() for form in item_forms):
            return self.render_to_response(
                self.get_context_data(rental=rental, item_forms=item_forms)
            )

        conditions = item_conditions_from_forms(item_forms)
        if not conditions:
            messages.error(request, _("Tick at least one item as coming back."))
            return self.render_to_response(
                self.get_context_data(rental=rental, item_forms=item_forms)
            )

        try:
            rental = services.return_rental(
                rental, conditions, request.user, request=request
            )
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return self.render_to_response(
                self.get_context_data(rental=rental, item_forms=item_forms)
            )

        if rental.status == Rental.Status.RETURNED:
            messages.success(
                request,
                _("Rental %(code)s checked in. Balance: %(balance)s.")
                % {"code": rental.rental_code, "balance": rental.balance_due},
            )
        else:
            messages.success(
                request,
                _("Checked in. %(n)s item(s) still out on %(code)s.")
                % {"n": rental.open_item_count, "code": rental.rental_code},
            )
        return redirect("rentals:detail", pk=rental.pk)


class ReturnPreviewView(RentalReturnView):
    """HTMX: recompute the check-in summary while the operator types."""

    template_name = "rentals/partials/return_summary.html"

    def post(self, request, *args, **kwargs):
        rental = self.get_rental()
        item_forms = build_return_forms(rental, request.POST)
        for form in item_forms:
            form.is_valid()
        context = {"rental": rental, "item_forms": item_forms}
        context.update(self._summary(rental, item_forms))
        return render(request, self.template_name, context)


class RentalItemReturnView(CapabilityRequiredMixin, View):
    """Per-item check-in control on the detail screen."""

    capability = "rentals.change"

    def post(self, request, *args, **kwargs):
        rental = get_object_or_404(Rental, pk=self.kwargs["pk"])
        item = get_object_or_404(RentalItem, pk=self.kwargs["item_pk"], rental=rental)
        condition = request.POST.get("condition_in") or item.condition_out
        try:
            services.return_rental(
                rental, {item.pk: (condition, "", "", ZERO)}, request.user, request=request
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("%(item)s checked in.") % {"item": services.equipment_label(item.equipment)},
            )
        return redirect("rentals:detail", pk=rental.pk)


class QuickReturnView(CapabilityRequiredMixin, View):
    """Scan an asset code anywhere on the list screen and take it back."""

    capability = "rentals.change"
    template_name = "rentals/partials/quick_return_result.html"

    def post(self, request, *args, **kwargs):
        form = QuickReturnForm(request.POST)
        context: dict = {"form": QuickReturnForm()}
        if not form.is_valid():
            context["error"] = _("Enter an asset code.")
            return render(request, self.template_name, context)

        try:
            rental, item = services.quick_return_by_asset_code(
                form.cleaned_data["asset_code"], user=request.user, request=request
            )
        except ValidationError as exc:
            context["error"] = " ".join(str(m) for m in exc.messages)
            return render(request, self.template_name, context)

        context["rental"] = rental
        context["item"] = item
        context["closed"] = rental.status == Rental.Status.RETURNED
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Contract changes
# ---------------------------------------------------------------------------
class RentalUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "rentals.change"
    model = Rental
    form_class = RentalUpdateForm
    template_name = "rentals/rental_edit.html"
    context_object_name = "rental"

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.recalculate_totals()
        return response

    def get_success_url(self):
        return reverse("rentals:detail", kwargs={"pk": self.object.pk})


class RentalExtendView(CapabilityRequiredMixin, FormView):
    capability = "rentals.change"
    form_class = RentalExtendForm
    template_name = "rentals/rental_extend.html"

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["rental"] = self.rental
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rental"] = self.rental
        return context

    def form_valid(self, form):
        try:
            rental = services.extend_rental(
                self.rental,
                form.cleaned_data["new_return_at"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Rental %(code)s extended. New total: %(total)s.")
            % {"code": rental.rental_code, "total": rental.total_amount},
        )
        return HttpResponseRedirect(reverse("rentals:detail", kwargs={"pk": rental.pk}))


class RentalCancelView(CapabilityRequiredMixin, FormView):
    capability = "rentals.change"
    form_class = RentalCancelForm
    template_name = "rentals/rental_cancel.html"

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rental"] = self.rental
        context["charge"] = services.late_cancellation_charge(self.rental)
        return context

    def form_valid(self, form):
        try:
            services.cancel_rental(
                self.rental,
                user=self.request.user,
                reason=form.cleaned_data["reason"],
                request=self.request,
            )
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Rental %(code)s cancelled.") % {"code": self.rental.rental_code},
        )
        return HttpResponseRedirect(reverse("rentals:detail", kwargs={"pk": self.rental.pk}))


class RentalLostView(CapabilityRequiredMixin, FormView):
    """Write off gear that never came back."""

    capability = "rentals.manage"
    form_class = RentalLostForm
    template_name = "rentals/rental_lost.html"

    def dispatch(self, request, *args, **kwargs):
        self.rental = get_object_or_404(Rental, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rental"] = self.rental
        context["open_items"] = self.rental.items.select_related("equipment").filter(
            returned_at__isnull=True
        )
        return context

    def form_valid(self, form):
        try:
            services.mark_rental_lost(
                self.rental,
                replacement_charge=form.cleaned_data["replacement_charge"],
                reason=form.cleaned_data["reason"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Rental %(code)s written off as lost.") % {"code": self.rental.rental_code},
        )
        return HttpResponseRedirect(reverse("rentals:detail", kwargs={"pk": self.rental.pk}))


class RentalPaymentView(CapabilityRequiredMixin, View):
    capability = "rentals.change"

    def post(self, request, *args, **kwargs):
        rental = get_object_or_404(Rental, pk=self.kwargs["pk"])
        form = RentalPaymentForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Enter a valid payment amount."))
            return redirect("rentals:detail", pk=rental.pk)
        try:
            services.register_payment(
                rental,
                form.cleaned_data["amount"],
                user=request.user,
                method=form.cleaned_data["method"],
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _("%(amount)s recorded against %(code)s.")
                % {"amount": form.cleaned_data["amount"], "code": rental.rental_code},
            )
        return redirect("rentals:detail", pk=rental.pk)


class RentalDeleteView(CapabilityRequiredMixin, AuditedDeleteMixin, DeleteView):
    """Soft-delete a cancelled contract; live hires can never be deleted."""

    capability = "rentals.delete"
    model = Rental
    template_name = "rentals/rental_confirm_delete.html"
    context_object_name = "rental"
    success_url = reverse_lazy("rentals:list")

    def form_valid(self, form):
        rental = self.get_object()
        if rental.status != Rental.Status.CANCELLED:
            messages.error(
                self.request,
                _("Only a cancelled rental can be removed. Check the equipment in first."),
            )
            return redirect("rentals:detail", pk=rental.pk)
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Operational board
# ---------------------------------------------------------------------------
class OutNowView(CapabilityRequiredMixin, StaffOnlyMixin, ListView):
    """Every asset currently off the premises — the morning stock-check screen.

    A whole-inventory operational board; staff-only by construction.
    """

    capability = "rentals.view"
    template_name = "rentals/items_out.html"
    context_object_name = "items"
    paginate_by = 50

    def get_queryset(self):
        return selectors.items_currently_out()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stats"] = selectors.counter_stats()
        context["now"] = timezone.now()
        context["quick_return_form"] = QuickReturnForm()
        return context
