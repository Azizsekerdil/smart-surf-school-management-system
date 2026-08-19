"""Read queries for the Help Center.

Kept apart from :mod:`services` so the view layer can compose querysets without
pulling in the write paths, and so every screen shares one definition of
"published article".
"""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from .models import HelpArticle, HelpCategory


def active_categories() -> QuerySet[HelpCategory]:
    """Categories shown in the navigation, cheapest form."""
    return HelpCategory.objects.filter(is_active=True)


def categories_with_counts() -> QuerySet[HelpCategory]:
    """Active categories annotated with their published article count.

    One query for the whole home-page grid — the count must never cost a query
    per card.
    """
    return active_categories().annotate(
        article_count=Count(
            "articles",
            filter=Q(articles__is_published=True, articles__is_deleted=False),
            distinct=True,
        )
    )


def published_articles() -> QuerySet[HelpArticle]:
    """Every article a reader is allowed to open."""
    return HelpArticle.objects.filter(
        is_published=True, category__is_active=True
    ).select_related("category")


def articles_in_category(category: HelpCategory) -> QuerySet[HelpArticle]:
    return published_articles().filter(category=category).order_by("sort_order", "title_en")


def article_by_slug(slug: str) -> QuerySet[HelpArticle]:
    """Queryset form so views can add their own ``get_object_or_404``."""
    return published_articles().filter(slug=slug)


def popular_articles(limit: int = 6) -> QuerySet[HelpArticle]:
    """The most-opened articles — what the team keeps coming back to."""
    return published_articles().order_by("-view_count", "sort_order")[:limit]


def articles_for_module(module: str) -> QuerySet[HelpArticle]:
    """Articles documenting one module, for contextual help from that screen."""
    if not module:
        return published_articles().none()
    return published_articles().filter(related_module=module).order_by("sort_order")
