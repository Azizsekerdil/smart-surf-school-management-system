from __future__ import annotations

from django.urls import path

from . import views

app_name = "pos"

urlpatterns = [
    # --- the till ----------------------------------------------------------
    path("", views.TerminalView.as_view(), name="terminal"),
    path("grid/", views.ProductGridView.as_view(), name="product_grid"),
    path("cart/add/", views.CartAddView.as_view(), name="cart_add"),
    path("cart/update/", views.CartUpdateView.as_view(), name="cart_update"),
    path("cart/remove/", views.CartRemoveView.as_view(), name="cart_remove"),
    path("cart/line-discount/", views.CartLineDiscountView.as_view(), name="cart_line_discount"),
    path("cart/discount/", views.CartDiscountView.as_view(), name="cart_discount"),
    path("cart/payment-method/", views.CartPaymentMethodView.as_view(), name="cart_payment_method"),
    path("cart/customer/", views.CartCustomerView.as_view(), name="cart_customer"),
    path("cart/clear/", views.CartClearView.as_view(), name="cart_clear"),
    path("cart/customer-search/", views.CustomerSearchView.as_view(), name="customer_search"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    # --- sales -------------------------------------------------------------
    path("sales/", views.SaleListView.as_view(), name="list"),
    path("sales/<int:pk>/", views.SaleDetailView.as_view(), name="detail"),
    path("sales/<int:pk>/receipt/", views.ReceiptView.as_view(), name="receipt"),
    path("sales/<int:pk>/void/", views.SaleVoidView.as_view(), name="void"),
    # --- catalogue ---------------------------------------------------------
    path("products/", views.ProductListView.as_view(), name="product_list"),
    path("products/new/", views.ProductCreateView.as_view(), name="product_create"),
    path("products/<int:pk>/edit/", views.ProductUpdateView.as_view(), name="product_update"),
    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_update"),
    # --- stock -------------------------------------------------------------
    path("stock/", views.StockMovementListView.as_view(), name="movement_list"),
    path("stock/adjust/", views.StockAdjustView.as_view(), name="stock_adjust"),
]
