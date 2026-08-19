from __future__ import annotations

from django.urls import path

from . import views

app_name = "rentals"

urlpatterns = [
    # --- board ------------------------------------------------------------
    path("", views.RentalListView.as_view(), name="list"),
    path("out/", views.OutNowView.as_view(), name="out_now"),
    # --- check-out --------------------------------------------------------
    path("new/", views.RentalCheckOutView.as_view(), name="create"),
    path("basket/add/", views.BasketAddView.as_view(), name="basket_add"),
    path("basket/remove/", views.BasketRemoveView.as_view(), name="basket_remove"),
    path("basket/clear/", views.BasketClearView.as_view(), name="basket_clear"),
    path("basket/preview/", views.BasketPreviewView.as_view(), name="basket_preview"),
    path("search/", views.EntitySearchView.as_view(), name="search"),
    # --- check-in ---------------------------------------------------------
    path("quick-return/", views.QuickReturnView.as_view(), name="quick_return"),
    path("<int:pk>/return/", views.RentalReturnView.as_view(), name="return"),
    path("<int:pk>/return/preview/", views.ReturnPreviewView.as_view(), name="return_preview"),
    path(
        "<int:pk>/items/<int:item_pk>/return/",
        views.RentalItemReturnView.as_view(),
        name="item_return",
    ),
    # --- contract ---------------------------------------------------------
    path("<int:pk>/", views.RentalDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.RentalUpdateView.as_view(), name="update"),
    path("<int:pk>/extend/", views.RentalExtendView.as_view(), name="extend"),
    path("<int:pk>/cancel/", views.RentalCancelView.as_view(), name="cancel"),
    path("<int:pk>/lost/", views.RentalLostView.as_view(), name="lost"),
    path("<int:pk>/payment/", views.RentalPaymentView.as_view(), name="payment"),
    path("<int:pk>/delete/", views.RentalDeleteView.as_view(), name="delete"),
]
