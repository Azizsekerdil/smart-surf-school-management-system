from __future__ import annotations

from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.FinanceDashboardView.as_view(), name="dashboard"),
    # --- payments ---------------------------------------------------------
    path("payments/", views.PaymentListView.as_view(), name="payment_list"),
    path("payments/new/", views.PaymentCreateView.as_view(), name="payment_create"),
    path("payments/<int:pk>/", views.PaymentDetailView.as_view(), name="payment_detail"),
    path("payments/<int:pk>/refund/", views.PaymentRefundView.as_view(), name="payment_refund"),
    # --- invoices ---------------------------------------------------------
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("invoices/new/", views.InvoiceCreateView.as_view(), name="invoice_create"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/issue/", views.InvoiceIssueView.as_view(), name="invoice_issue"),
    path("invoices/<int:pk>/cancel/", views.InvoiceCancelView.as_view(), name="invoice_cancel"),
    # --- expenses ---------------------------------------------------------
    path("expenses/", views.ExpenseListView.as_view(), name="expense_list"),
    path("expenses/new/", views.ExpenseCreateView.as_view(), name="expense_create"),
    path("expenses/<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="expense_update"),
    # --- commission -------------------------------------------------------
    path("commission/", views.CommissionListView.as_view(), name="commission_list"),
    path(
        "commission/calculate/",
        views.CommissionGenerateView.as_view(),
        name="commission_generate",
    ),
    path(
        "commission/<int:pk>/approve/",
        views.CommissionApproveView.as_view(),
        name="commission_approve",
    ),
    path("commission/<int:pk>/pay/", views.CommissionPayView.as_view(), name="commission_pay"),
    # --- packages ---------------------------------------------------------
    path("packages/", views.PricePackageListView.as_view(), name="package_list"),
    path("packages/new/", views.PricePackageCreateView.as_view(), name="package_create"),
    path(
        "packages/<int:pk>/edit/",
        views.PricePackageUpdateView.as_view(),
        name="package_update",
    ),
    path("packages/sell/", views.SellPackageView.as_view(), name="package_sell"),
    path(
        "customer-packages/",
        views.CustomerPackageListView.as_view(),
        name="customer_package_list",
    ),
    path(
        "customer-packages/<int:pk>/use/",
        views.UsePackageLessonView.as_view(),
        name="customer_package_use",
    ),
]
