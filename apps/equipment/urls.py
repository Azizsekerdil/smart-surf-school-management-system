from __future__ import annotations

from django.urls import path

from . import views

app_name = "equipment"

urlpatterns = [
    # --- inventory ---------------------------------------------------------
    path("", views.EquipmentListView.as_view(), name="list"),
    path("new/", views.EquipmentCreateView.as_view(), name="create"),
    path("export.csv", views.EquipmentExportView.as_view(), name="export"),
    path("labels/", views.EquipmentLabelSheetView.as_view(), name="labels"),
    path("utilisation/", views.EquipmentUtilisationView.as_view(), name="utilisation"),
    path("advisor/", views.EquipmentAdvisorView.as_view(), name="advisor"),
    # --- CSV import --------------------------------------------------------
    path("import/", views.EquipmentImportView.as_view(), name="import"),
    path("import/template.csv", views.ImportTemplateView.as_view(), name="import_template"),
    # --- categories --------------------------------------------------------
    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path(
        "categories/<int:pk>/edit/",
        views.CategoryUpdateView.as_view(),
        name="category_update",
    ),
    path("categories/load-defaults/", views.CategorySeedView.as_view(), name="category_seed"),
    # --- QR scan resolution ------------------------------------------------
    path("scan/<uuid:public_id>/", views.EquipmentScanView.as_view(), name="scan"),
    # --- single item -------------------------------------------------------
    path("<int:pk>/", views.EquipmentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.EquipmentUpdateView.as_view(), name="update"),
    path("<int:pk>/archive/", views.EquipmentDeleteView.as_view(), name="delete"),
    path("<int:pk>/status/", views.EquipmentStatusChangeView.as_view(), name="status_change"),
    path("<int:pk>/photos/", views.EquipmentPhotoCreateView.as_view(), name="photo_create"),
    path(
        "<int:pk>/photos/<int:photo_pk>/delete/",
        views.EquipmentPhotoDeleteView.as_view(),
        name="photo_delete",
    ),
]
