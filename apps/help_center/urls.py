from __future__ import annotations

from django.urls import path

from . import views

app_name = "help_center"

urlpatterns = [
    path("", views.HelpHomeView.as_view(), name="home"),
    path("search/", views.HelpSearchView.as_view(), name="search"),
    path("section/<slug:code>/", views.HelpCategoryDetailView.as_view(), name="category"),
    path("article/<slug:slug>/", views.HelpArticleDetailView.as_view(), name="article"),
    path(
        "article/<slug:slug>/feedback/",
        views.ArticleFeedbackView.as_view(),
        name="article_feedback",
    ),
    # Contextual help panel other modules can pull in over HTMX.
    path("for/<slug:module>/", views.ModuleHelpView.as_view(), name="module"),
]
