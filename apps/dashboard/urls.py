from __future__ import annotations

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardHomeView.as_view(), name="home"),
    path("search/", views.GlobalSearchView.as_view(), name="search"),
    # HTMX fragment: the tile grid refreshes itself without a page reload.
    path("dashboard/tiles/", views.DashboardTilesView.as_view(), name="tiles"),
]
