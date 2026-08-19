"""Forms for the shop catalogue, stock corrections and voids."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.enums import PaymentMethod
from apps.core.forms_base import TailwindFormMixin

from .models import Product, ProductCategory, Sale, StockMovement

ZERO = Decimal("0.00")

#: Movement types an operator may raise by hand. ``SALE`` is written by the till
#: and ``INITIAL`` by product creation — offering either here would let somebody
#: forge a sale or a second opening balance.
MANUAL_MOVEMENT_TYPES = (
    StockMovement.MovementType.PURCHASE,
    StockMovement.MovementType.RETURN,
    StockMovement.MovementType.ADJUSTMENT,
    StockMovement.MovementType.DAMAGE,
    StockMovement.MovementType.TRANSFER,
)

#: Movement types whose direction is not negotiable.
INBOUND_ONLY = (
    StockMovement.MovementType.PURCHASE,
    StockMovement.MovementType.RETURN,
)
OUTBOUND_ONLY = (StockMovement.MovementType.DAMAGE,)

DIRECTION_IN = "in"
DIRECTION_OUT = "out"


class ProductCategoryForm(TailwindFormMixin, forms.ModelForm):
    """Create / edit a shop category."""

    class Meta:
        model = ProductCategory
        fields = ("code", "name", "parent", "icon", "sort_order", "is_active")
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "wax"}),
            "icon": forms.TextInput(attrs={"placeholder": "package"}),
            "sort_order": forms.NumberInput(attrs={"min": "0", "step": "10"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        queryset = ProductCategory.objects.order_by("sort_order", "name")
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = queryset
        self.fields["parent"].empty_label = _("No parent — top level")

    def clean_code(self) -> str:
        code = (self.cleaned_data.get("code") or "").strip().lower()
        clash = ProductCategory.objects.filter(code=code).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("A category with this code already exists."))
        return code

    def clean_name(self) -> str:
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError(_("Give the category a name."))
        return name


class ProductForm(TailwindFormMixin, forms.ModelForm):
    """Create / edit a product.

    ``stock_quantity`` is deliberately absent: the shelf count is derived from
    the stock ledger, so it can only be moved by a movement. On creation the
    operator states the stock they counted and that becomes the opening
    balance; afterwards corrections go through the stock adjustment screen.
    """

    opening_stock = forms.DecimalField(
        label=_("Stock counted now"),
        required=False,
        min_value=ZERO,
        max_digits=8,
        decimal_places=2,
        initial=ZERO,
        help_text=_("Recorded as the opening balance of the stock ledger."),
        widget=forms.NumberInput(attrs={"step": "1", "min": "0", "inputmode": "decimal"}),
    )

    class Meta:
        model = Product
        fields = (
            "sku",
            "barcode",
            "name",
            "description",
            "category",
            "cost_price",
            "sale_price",
            "tax_rate",
            "unit",
            "track_stock",
            "low_stock_threshold",
            "supplier",
            "photo",
            "is_active",
            "sort_order",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "sku": forms.TextInput(attrs={"placeholder": "WAX-COLD-01", "autocapitalize": "characters"}),
            "barcode": forms.TextInput(
                attrs={"placeholder": "8690000000000", "inputmode": "numeric"}
            ),
            "cost_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
            "sale_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
            "tax_rate": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "low_stock_threshold": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "sort_order": forms.NumberInput(attrs={"step": "10", "min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ProductCategory.objects.filter(
            is_active=True
        ).order_by("sort_order", "name")
        self.fields["category"].empty_label = _("Choose a category")
        if self.instance.pk:
            # Editing never touches stock — the ledger owns it.
            del self.fields["opening_stock"]

    def clean_sku(self) -> str:
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        if not sku:
            raise forms.ValidationError(_("Give the product an SKU."))
        clash = Product.all_objects.filter(sku=sku).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another product already uses this SKU."))
        return sku

    def clean_barcode(self) -> str:
        barcode = (self.cleaned_data.get("barcode") or "").strip()
        if not barcode:
            return ""
        if not barcode.isdigit():
            raise forms.ValidationError(_("A barcode contains digits only."))
        clash = Product.objects.filter(barcode=barcode).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another product already uses this barcode."))
        return barcode

    def clean(self):
        cleaned = super().clean()
        unit = cleaned.get("unit")
        opening = cleaned.get("opening_stock")
        if (
            opening is not None
            and unit in (Product.Unit.PIECE, Product.Unit.PAIR, Product.Unit.SET)
            and opening != opening.to_integral_value()
        ):
            self.add_error("opening_stock", _("This unit is counted in whole numbers."))

        if not cleaned.get("track_stock") and opening:
            self.add_error(
                "opening_stock",
                _("Turn stock tracking on before recording an opening balance."),
            )

        sale_price = cleaned.get("sale_price")
        cost_price = cleaned.get("cost_price")
        if sale_price is not None and cost_price is not None and sale_price < cost_price:
            # Selling below cost is legitimate (end-of-season clearance) but is
            # almost always a typo, so it has to be stated deliberately.
            self.add_error(
                "sale_price",
                _(
                    "The shelf price is below the cost price. Correct it, or lower "
                    "the cost price if this is a clearance line."
                ),
            )
        return cleaned


class StockAdjustmentForm(TailwindFormMixin, forms.Form):
    """Record a delivery, a breakage or a stock-take correction.

    The direction is a separate field on purpose: asking an operator to type a
    negative number at the end of a long day is how stock counts go wrong.
    """

    DIRECTION_CHOICES = (
        (DIRECTION_IN, _("Into stock")),
        (DIRECTION_OUT, _("Out of stock")),
    )

    product = forms.ModelChoiceField(
        label=_("Product"),
        queryset=Product.objects.none(),
        empty_label=_("Choose a product"),
    )
    movement_type = forms.ChoiceField(
        label=_("Reason type"),
        choices=[
            (value, label)
            for value, label in StockMovement.MovementType.choices
            if value in MANUAL_MOVEMENT_TYPES
        ],
        initial=StockMovement.MovementType.PURCHASE,
    )
    direction = forms.ChoiceField(
        label=_("Direction"), choices=DIRECTION_CHOICES, initial=DIRECTION_IN
    )
    quantity = forms.DecimalField(
        label=_("Quantity"),
        min_value=Decimal("0.01"),
        max_digits=8,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "1", "min": "0.01", "inputmode": "decimal"}),
    )
    reference = forms.CharField(
        label=_("Reference"),
        required=False,
        max_length=80,
        help_text=_("Supplier invoice or stock-take number, if there is one."),
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Recorded on the ledger row. Be specific enough to audit later."),
    )

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(is_active=True, track_stock=True)
            .select_related("category")
            .order_by("name")
        )
        if product is not None:
            self.fields["product"].initial = product.pk

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 4:
            raise forms.ValidationError(_("Describe the correction in a few words."))
        return reason

    def clean(self):
        cleaned = super().clean()
        movement_type = cleaned.get("movement_type")
        direction = cleaned.get("direction")
        product = cleaned.get("product")
        quantity = cleaned.get("quantity")

        if movement_type in INBOUND_ONLY and direction == DIRECTION_OUT:
            self.add_error("direction", _("This movement type only adds stock."))
        if movement_type in OUTBOUND_ONLY and direction == DIRECTION_IN:
            self.add_error("direction", _("This movement type only removes stock."))

        if product is not None and quantity is not None:
            if (
                product.unit in (Product.Unit.PIECE, Product.Unit.PAIR, Product.Unit.SET)
                and quantity != quantity.to_integral_value()
            ):
                self.add_error("quantity", _("This product is counted in whole units only."))
            if direction == DIRECTION_OUT and quantity > product.stock_balance:
                self.add_error(
                    "quantity",
                    _("Only %(available)s in stock — the ledger cannot go negative.")
                    % {"available": product.stock_display},
                )
        return cleaned

    @property
    def signed_quantity(self) -> Decimal:
        """The delta to append to the ledger, sign included."""
        quantity = self.cleaned_data["quantity"]
        if self.cleaned_data["direction"] == DIRECTION_OUT:
            return -quantity
        return quantity


class SaleVoidForm(TailwindFormMixin, forms.Form):
    """Voiding is a money operation, so the reason is mandatory."""

    reason = forms.CharField(
        label=_("Why is this sale being voided?"),
        widget=forms.Textarea(attrs={"rows": 3, "autofocus": True}),
        help_text=_("Kept on the receipt forever. The sale itself is never deleted."),
    )

    def clean_reason(self) -> str:
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError(_("Give a reason somebody could audit later."))
        return reason


class ProductFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the product list."""

    STOCK_CHOICES = (
        ("", _("Any stock level")),
        ("low", _("At or below reorder point")),
        ("out", _("Out of stock")),
        ("in", _("In stock")),
        ("untracked", _("Not stock tracked")),
    )
    STATUS_CHOICES = (
        ("active", _("Active")),
        ("inactive", _("Withdrawn")),
        ("all", _("All")),
    )

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Name, SKU, barcode or supplier…")}),
    )
    category = forms.ModelChoiceField(
        label=_("Category"),
        required=False,
        queryset=ProductCategory.objects.none(),
        empty_label=_("Every category"),
    )
    stock = forms.ChoiceField(label=_("Stock"), required=False, choices=STOCK_CHOICES)
    status = forms.ChoiceField(label=_("Status"), required=False, choices=STATUS_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ProductCategory.objects.order_by(
            "sort_order", "name"
        )


class SaleFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the sales list."""

    STATUS_CHOICES = (("", _("Any status")),) + tuple(Sale.Status.choices)
    METHOD_CHOICES = (("", _("Any method")),) + tuple(PaymentMethod.choices)

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Receipt number or customer…")}),
    )
    status = forms.ChoiceField(label=_("Status"), required=False, choices=STATUS_CHOICES)
    payment_method = forms.ChoiceField(
        label=_("Payment"), required=False, choices=METHOD_CHOICES
    )


class MovementFilterForm(TailwindFormMixin, forms.Form):
    """The filter bar above the stock ledger."""

    TYPE_CHOICES = (("", _("Any movement")),) + tuple(StockMovement.MovementType.choices)

    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=forms.TextInput(attrs={"placeholder": _("Product, reference or note…")}),
    )
    movement_type = forms.ChoiceField(
        label=_("Movement"), required=False, choices=TYPE_CHOICES
    )
    product = forms.ModelChoiceField(
        label=_("Product"),
        required=False,
        queryset=Product.objects.none(),
        empty_label=_("Every product"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.order_by("name")
