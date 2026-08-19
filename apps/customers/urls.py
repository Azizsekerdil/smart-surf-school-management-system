from __future__ import annotations

from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.CustomerListView.as_view(), name="list"),
    path("new/", views.CustomerCreateView.as_view(), name="create"),
    # --- HTMX helpers used by the booking / rental screens ----------------
    path("quick-create/", views.CustomerQuickCreateView.as_view(), name="quick_create"),
    path("search/", views.CustomerSearchView.as_view(), name="search"),
    # --- duplicates --------------------------------------------------------
    path("duplicates/", views.CustomerDuplicateListView.as_view(), name="duplicates"),
    # --- single customer ---------------------------------------------------
    path("<int:pk>/", views.CustomerDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="update"),
    path("<int:pk>/tabs/<slug:tab>/", views.CustomerTabView.as_view(), name="tab"),
    path("<int:pk>/notes/add/", views.CustomerNoteCreateView.as_view(), name="note_create"),
    path(
        "<int:pk>/documents/add/",
        views.CustomerDocumentCreateView.as_view(),
        name="document_create",
    ),
    path("<int:pk>/toggle-active/", views.CustomerToggleActiveView.as_view(), name="toggle_active"),
    path("<int:pk>/consent/", views.CustomerConsentView.as_view(), name="consent"),
    path("<int:pk>/recalculate/", views.CustomerRecalculateView.as_view(), name="recalculate"),
    path(
        "<int:pk>/merge/<int:duplicate_pk>/",
        views.CustomerMergeView.as_view(),
        name="merge",
    ),
]
