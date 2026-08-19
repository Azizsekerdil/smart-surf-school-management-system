"""HTML views for the shop.

The centrepiece is :class:`TerminalView` — the till. Everything it does is a
small HTMX POST that returns the re-rendered cart, so the product grid never
reloads, the scanner never loses focus and a slow network degrades into a slow
line rather than a lost sale.

Only the till writes sales. Stock corrections, the catalogue and voids live on
their own capability-guarded screens because they are management decisions, not
counter work — a receptionist may sell, but may not reprice or void.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
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
from apps.core.enums import PaymentMethod
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from . import selectors, services
from .forms import (
    MovementFilterForm,
    ProductCategoryForm,
    ProductFilterForm,
    ProductForm,
    SaleFilterForm,
    SaleVoidForm,
    StockAdjustmentForm,
)
from .models import Product, ProductCategory, Sale, StockMovement

ZERO = Decimal("0.00")

#: Payment methods the counter can actually take. ``PACKAGE`` belongs to lesson
#: bookings and would be meaningless against a tube of wax.
TILL_PAYMENT_METHODS = (
    PaymentMethod.CASH,
    PaymentMethod.CARD,
    PaymentMethod.TRANSFER,
    PaymentMethod.VOUCHER,
    PaymentMethod.OTHER,
)

CART_TEMPLATE = "pos/partials/cart.html"
RECEIPT_PANEL_TEMPLATE = "pos/partials/receipt_panel.html"

#: A voided receipt must not look like an ordinary one at a glance.
SALE_STATUS_COLORS = {"voided": "rose", "completed": "emerald", "refunded": "violet"}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _int_or_none(raw) -> int | None:
    """Coerce a query-string value to an int, or ``None``.

    Query strings are user input: ``?category=drop%20table`` must produce an
    unfiltered page, not a 500 from the database layer.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _toast(response: HttpResponse, message: str, level: str = "info") -> HttpResponse:
    """Attach a toast to an HTMX response without changing what it swaps."""
    response["HX-Trigger"] = json.dumps({"toast": {"message": str(message), "level": level}})
    return response


def _cart_context(request, cart: services.SessionCart, **extra) -> dict:
    totals = cart.totals()
    context = {
        "cart": cart,
        "cart_lines": cart.lines(),
        "totals": totals,
        "cart_customer": cart.customer(),
        "payment_methods": [
            (value, label)
            for value, label in PaymentMethod.choices
            if value in TILL_PAYMENT_METHODS
        ],
        "selected_method": cart.payment_method,
        "cart_warnings": cart.warnings,
    }
    context.update(extra)
    return context


def _render_cart(request, cart: services.SessionCart, *, error: str = "", **extra) -> HttpResponse:
    context = _cart_context(request, cart, cart_error=error, **extra)
    response = render(request, CART_TEMPLATE, context)
    if error:
        _toast(response, error, "error")
    return response


def _error_text(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return error.messages[0] if error.messages else _("The operation was refused.")
    return str(error)


# ---------------------------------------------------------------------------
# The till
# ---------------------------------------------------------------------------
class TerminalView(CapabilityRequiredMixin, TemplateView):
    """The point-of-sale screen: product grid on the left, live cart on the right."""

    capability = "pos.add"
    template_name = "pos/terminal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cart = services.SessionCart(self.request.session)

        category_id = _int_or_none(self.request.GET.get("category"))
        category = None
        if category_id is not None:
            category = ProductCategory.objects.filter(pk=category_id, is_active=True).first()

        search = self.request.GET.get("q", "").strip()

        context.update(_cart_context(self.request, cart))
        context["categories"] = selectors.selling_categories()
        context["active_category"] = category
        context["grid_search"] = search
        context["products"] = selectors.terminal_products(category=category, search=search)
        context["low_stock_count"] = Product.objects.low_stock().count()
        return context


class ProductGridView(CapabilityRequiredMixin, TemplateView):
    """The searchable/filterable product grid, re-rendered on its own."""

    capability = "pos.view"
    template_name = "pos/partials/product_grid.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category_id = _int_or_none(self.request.GET.get("category"))
        category = None
        if category_id is not None:
            category = ProductCategory.objects.filter(pk=category_id, is_active=True).first()
        search = self.request.GET.get("q", "").strip()

        context["categories"] = selectors.selling_categories()
        context["active_category"] = category
        context["grid_search"] = search
        context["products"] = selectors.terminal_products(category=category, search=search)
        return context


class CartActionView(CapabilityRequiredMixin, View):
    """Base for the POST-only cart endpoints. Every one returns the cart partial."""

    capability = "pos.add"

    def get_cart(self) -> services.SessionCart:
        return services.SessionCart(self.request.session)

    def get_product(self, key: str = "product"):
        product_id = _int_or_none(self.request.POST.get(key))
        if product_id is None:
            return None
        return selectors.product_queryset().filter(pk=product_id).first()


class CartAddView(CartActionView):
    """Add a product by id, or by a scanned/typed barcode or SKU."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        code = (request.POST.get("code") or "").strip()
        quantity = services.to_quantity(request.POST.get("quantity"), Decimal("1.00"))

        product = self.get_product()
        if product is None and code:
            product = selectors.find_by_barcode(code)
            if product is None:
                return _render_cart(
                    request,
                    cart,
                    error=_("No product matches “%(code)s”.") % {"code": code},
                    scanned_code=code,
                )
        if product is None:
            return _render_cart(request, cart, error=_("Choose a product to add."))

        try:
            cart.add(product, quantity)
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))
        return _render_cart(request, cart, added_product=product)


class CartUpdateView(CartActionView):
    """Set an absolute quantity, or step it by ``?delta=``."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        product = self.get_product()
        if product is None:
            return _render_cart(request, cart, error=_("That product is no longer in the cart."))

        delta = request.POST.get("delta")
        try:
            if delta not in (None, ""):
                current = ZERO
                for line in cart.lines():
                    if line.product.pk == product.pk:
                        current = line.quantity
                        break
                cart.set_quantity(product, current + services.to_quantity(delta))
            else:
                cart.set_quantity(product, request.POST.get("quantity"))
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))
        return _render_cart(request, cart)


class CartLineDiscountView(CartActionView):
    """Discount a single line by a fixed amount."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        product = self.get_product()
        if product is None:
            return _render_cart(request, cart, error=_("That product is no longer in the cart."))
        try:
            cart.set_line_discount(product, request.POST.get("amount"))
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))
        return _render_cart(request, cart)


class CartRemoveView(CartActionView):
    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        product = self.get_product()
        if product is not None:
            cart.remove(product)
        return _render_cart(request, cart)


class CartDiscountView(CartActionView):
    """Apply, change or clear the whole-sale discount."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        if request.POST.get("clear"):
            cart.clear_discount()
            return _render_cart(request, cart)

        percent = request.POST.get("percent")
        amount = request.POST.get("amount")
        try:
            if percent not in (None, ""):
                cart.apply_discount(percent=percent)
            elif amount not in (None, ""):
                cart.apply_discount(amount=amount)
            else:
                cart.clear_discount()
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))
        return _render_cart(request, cart)


class CartPaymentMethodView(CartActionView):
    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        try:
            cart.set_payment_method(request.POST.get("payment_method", ""))
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))
        return _render_cart(request, cart)


class CartCustomerView(CartActionView):
    """Attach a customer to the sale, or make it anonymous again."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        raw = (request.POST.get("customer") or "").strip()
        if not raw:
            cart.set_customer(None)
            return _render_cart(request, cart)

        customer_id = _int_or_none(raw)
        model = services.get_model("customers", "Customer")
        customer = (
            model.objects.filter(pk=customer_id).first()
            if (model is not None and customer_id is not None)
            else None
        )
        if customer is None:
            return _render_cart(request, cart, error=_("That customer could not be found."))
        cart.set_customer(customer)
        return _render_cart(request, cart)


class CartClearView(CartActionView):
    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        cart.clear()
        return _render_cart(request, cart)


class CustomerSearchView(CapabilityRequiredMixin, TemplateView):
    """Type-ahead customer lookup for the till (never a select of 5000 people)."""

    capability = "pos.add"
    template_name = "pos/partials/customer_results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = (self.request.GET.get("customer_q") or "").strip()
        context["term"] = term
        context["customers"] = []
        if len(term) < 2:
            return context

        model = services.get_model("customers", "Customer")
        if model is None:
            return context
        context["customers"] = model.objects.filter(
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
            | Q(phone__icontains=term)
            | Q(customer_code__icontains=term)
        ).order_by("first_name", "last_name")[:8]
        return context


class CheckoutView(CartActionView):
    """Complete the sale and show the receipt summary."""

    def post(self, request, *args, **kwargs):
        cart = self.get_cart()
        method = request.POST.get("payment_method") or cart.payment_method
        tendered = request.POST.get("amount_tendered")
        note = request.POST.get("note", "")

        try:
            sale = services.complete_sale(
                cart,
                customer=cart.customer(),
                payment_method=method,
                amount_tendered=tendered,
                user=request.user,
                note=note,
                request=request,
            )
        except ValidationError as error:
            return _render_cart(request, cart, error=_error_text(error))

        if not getattr(request, "htmx", False):
            messages.success(
                request,
                _("Sale %(number)s completed.") % {"number": sale.sale_number},
            )
            return redirect("pos:receipt", pk=sale.pk)

        fresh = services.SessionCart(request.session)
        context = _cart_context(request, fresh)
        context["sale"] = selectors.sale_detail_queryset().get(pk=sale.pk)
        response = render(request, RECEIPT_PANEL_TEMPLATE, context)
        # The grid listens for this so stock badges refresh without a reload.
        response["HX-Trigger"] = json.dumps(
            {
                "pos-sale-completed": {"sale": sale.sale_number},
                "toast": {
                    "message": str(
                        _("Sale %(number)s completed.") % {"number": sale.sale_number}
                    ),
                    "level": "success",
                },
            }
        )
        return response


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class ProductListView(CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView):
    capability = "pos.view"
    model = Product
    template_name = "pos/product_list.html"
    partial_template_name = "pos/partials/product_results.html"
    context_object_name = "products"
    paginate_by = 25
    search_fields = ("name", "sku", "barcode", "description", "supplier")

    def get_queryset(self):
        queryset = self.apply_search(selectors.product_queryset())

        status = self.request.GET.get("status", "active")
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)

        category = _int_or_none(self.request.GET.get("category"))
        if category is not None:
            queryset = queryset.filter(
                Q(category_id=category) | Q(category__parent_id=category)
            )

        stock = self.request.GET.get("stock", "")
        if stock == "low":
            queryset = queryset.filter(
                track_stock=True, stock_quantity__lte=F("low_stock_threshold")
            )
        elif stock == "out":
            queryset = queryset.filter(track_stock=True, stock_quantity__lte=0)
        elif stock == "in":
            queryset = queryset.filter(track_stock=True, stock_quantity__gt=0)
        elif stock == "untracked":
            queryset = queryset.filter(track_stock=False)

        return queryset.order_by("sort_order", "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = ProductFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "category": self.request.GET.get("category", "") or None,
                "stock": self.request.GET.get("stock", ""),
                "status": self.request.GET.get("status", "active"),
            }
        )
        context["valuation"] = services.stock_valuation()
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "category", "stock")
        ) or self.request.GET.get("status", "active") != "active"
        return context


class ProductCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "pos.change"
    model = Product
    form_class = ProductForm
    template_name = "pos/product_form.html"
    success_message = _lazy("Product added to the catalogue.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New product")
        context["cancel_url"] = reverse("pos:product_list")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        opening = form.cleaned_data.get("opening_stock")
        if opening:
            services.set_opening_stock(
                self.object, opening, user=self.request.user, reference=self.object.sku
            )
        return response

    def get_success_url(self):
        return reverse("pos:product_list")


class ProductUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "pos.change"
    model = Product
    form_class = ProductForm
    template_name = "pos/product_form.html"
    success_message = _lazy("Product updated.")

    def get_queryset(self):
        return selectors.product_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit product")
        context["cancel_url"] = reverse("pos:product_list")
        context["movements"] = selectors.movements_for_product(self.object, limit=10)
        return context

    def get_success_url(self):
        return reverse("pos:product_list")


class CategoryListView(CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView):
    capability = "pos.view"
    model = ProductCategory
    template_name = "pos/category_list.html"
    partial_template_name = "pos/partials/category_table.html"
    context_object_name = "categories"
    paginate_by = 50
    search_fields = ("name", "code")

    def get_queryset(self):
        return self.apply_search(selectors.category_queryset())


class CategoryCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "pos.change"
    model = ProductCategory
    form_class = ProductCategoryForm
    template_name = "pos/category_form.html"
    success_message = _lazy("Category created.")
    success_url = reverse_lazy("pos:category_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("New category")
        context["cancel_url"] = reverse("pos:category_list")
        return context


class CategoryUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "pos.change"
    model = ProductCategory
    form_class = ProductCategoryForm
    template_name = "pos/category_form.html"
    success_message = _lazy("Category updated.")
    success_url = reverse_lazy("pos:category_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Edit category")
        context["cancel_url"] = reverse("pos:category_list")
        return context


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------
class SaleListView(
    CapabilityRequiredMixin, SearchableListMixin, DateRangeMixin, HtmxPartialMixin, ListView
):
    capability = "pos.view"
    model = Sale
    template_name = "pos/sale_list.html"
    partial_template_name = "pos/partials/sale_results.html"
    context_object_name = "sales"
    paginate_by = 25
    date_field = "sold_at"
    search_fields = (
        "sale_number",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
        "note",
    )

    def get_queryset(self):
        queryset = self.apply_date_range(self.apply_search(selectors.sale_queryset()))

        status = self.request.GET.get("status", "")
        if status in dict(Sale.Status.choices):
            queryset = queryset.filter(status=status)

        method = self.request.GET.get("payment_method", "")
        if method in dict(PaymentMethod.choices):
            queryset = queryset.filter(payment_method=method)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()

        context["filter_form"] = SaleFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "status": self.request.GET.get("status", ""),
                "payment_method": self.request.GET.get("payment_method", ""),
            }
        )
        summary = services.sales_summary(start, end)
        context["summary"] = summary
        context["top_products"] = services.top_products(start, end, limit=5)
        context["cashiers"] = services.cashier_summary(start, end)
        context["chart_labels"] = [row["date"].isoformat() for row in summary["daily"]]
        context["chart_revenue"] = [float(row["revenue"]) for row in summary["daily"]]
        context["chart_counts"] = [row["count"] for row in summary["daily"]]
        context["status_colors"] = SALE_STATUS_COLORS
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "status", "payment_method")
        )
        return context


class SaleDetailView(CapabilityRequiredMixin, DetailView):
    capability = "pos.view"
    model = Sale
    template_name = "pos/sale_detail.html"
    context_object_name = "sale"

    def get_queryset(self):
        return selectors.sale_detail_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["line_totals"] = selectors.sale_line_totals(self.object)
        context["status_colors"] = SALE_STATUS_COLORS
        return context


class ReceiptView(CapabilityRequiredMixin, DetailView):
    """The printable receipt. Uses the print stylesheet already in the build."""

    capability = "pos.view"
    model = Sale
    template_name = "pos/receipt.html"
    context_object_name = "sale"

    def get_queryset(self):
        return selectors.sale_detail_queryset()


class SaleVoidView(CapabilityRequiredMixin, FormView):
    """Void a completed sale: reverses stock, keeps the receipt."""

    capability = "pos.delete"
    form_class = SaleVoidForm
    template_name = "pos/sale_confirm_void.html"

    def get_sale(self) -> Sale:
        return get_object_or_404(selectors.sale_detail_queryset(), pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sale = self.get_sale()
        context["sale"] = sale
        context["can_void"] = sale.can_be_voided
        return context

    def form_valid(self, form):
        sale = self.get_sale()
        try:
            services.void_sale(
                sale,
                form.cleaned_data["reason"],
                user=self.request.user,
                request=self.request,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(self.request, message)
            return redirect("pos:detail", pk=sale.pk)

        messages.success(
            self.request,
            _("Sale %(number)s voided and the stock put back.")
            % {"number": sale.sale_number},
        )
        return HttpResponseRedirect(reverse("pos:detail", kwargs={"pk": sale.pk}))


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------
class StockMovementListView(
    CapabilityRequiredMixin, SearchableListMixin, DateRangeMixin, HtmxPartialMixin, ListView
):
    capability = "pos.view"
    model = StockMovement
    template_name = "pos/movement_list.html"
    partial_template_name = "pos/partials/movement_results.html"
    context_object_name = "movements"
    paginate_by = 50
    date_field = "created_at"
    search_fields = ("product__name", "product__sku", "reference", "note")

    def get_queryset(self):
        queryset = self.apply_date_range(self.apply_search(selectors.movement_queryset()))

        movement_type = self.request.GET.get("movement_type", "")
        if movement_type in dict(StockMovement.MovementType.choices):
            queryset = queryset.filter(movement_type=movement_type)

        product = _int_or_none(self.request.GET.get("product"))
        if product is not None:
            queryset = queryset.filter(product_id=product)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = MovementFilterForm(
            initial={
                "q": self.request.GET.get("q", ""),
                "movement_type": self.request.GET.get("movement_type", ""),
                "product": self.request.GET.get("product", "") or None,
            }
        )
        context["valuation"] = services.stock_valuation()
        context["low_stock"] = services.low_stock_products(limit=8)
        context["has_filters"] = any(
            self.request.GET.get(key) for key in ("q", "movement_type", "product")
        )
        return context


class StockAdjustView(CapabilityRequiredMixin, FormView):
    """Record a delivery, a breakage or a stock-take correction."""

    capability = "pos.change"
    form_class = StockAdjustmentForm
    template_name = "pos/stock_adjust_form.html"

    def get_initial(self):
        initial = super().get_initial()
        product_id = self.request.GET.get("product", "")
        if product_id:
            initial["product"] = product_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = _("Stock correction")
        context["cancel_url"] = reverse("pos:movement_list")
        context["low_stock"] = services.low_stock_products(limit=10)
        return context

    def form_valid(self, form):
        product = form.cleaned_data["product"]
        try:
            movement = services.adjust_stock(
                product,
                form.signed_quantity,
                form.cleaned_data["reason"],
                user=self.request.user,
                movement_type=form.cleaned_data["movement_type"],
                reference=form.cleaned_data.get("reference", ""),
                request=self.request,
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("%(product)s: %(delta)s recorded, %(balance)s now on the shelf.")
            % {
                "product": product.name,
                "delta": movement.signed_display,
                "balance": movement.balance_after,
            },
        )
        return HttpResponseRedirect(
            f"{reverse('pos:movement_list')}?product={product.pk}"
        )
