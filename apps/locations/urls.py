from __future__ import annotations

from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.SurfSpotListView.as_view(), name="list"),
    path("new/", views.SurfSpotCreateView.as_view(), name="create"),
    path("<int:pk>/", views.SurfSpotDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.SurfSpotUpdateView.as_view(), name="update"),
    path("<int:pk>/archive/", views.SurfSpotDeleteView.as_view(), name="delete"),
    path("<int:pk>/set-primary/", views.SetPrimarySpotView.as_view(), name="set_primary"),
    # --- hazards ----------------------------------------------------------
    path("<int:pk>/hazards/new/", views.SpotHazardCreateView.as_view(), name="hazard_create"),
    path("hazards/<int:pk>/edit/", views.SpotHazardUpdateView.as_view(), name="hazard_update"),
    path("hazards/<int:pk>/toggle/", views.SpotHazardToggleView.as_view(), name="hazard_toggle"),
]
