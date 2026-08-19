from __future__ import annotations

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from apps.help_center import services
from apps.help_center.models import HelpArticle

from .factories import HelpArticleFactory, HelpCategoryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_with_session():
    def _build():
        request = RequestFactory().get("/help/")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    return _build


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_search_matches_the_title():
    HelpArticleFactory(title_en="Taking a booking", title_tr="Rezervasyon almak")
    HelpArticleFactory(title_en="Adding a surfboard", title_tr="Tahta eklemek")
    results = services.search_articles("booking")
    assert [a.title_en for a in results] == ["Taking a booking"]


def test_search_matches_turkish_text_too():
    HelpArticleFactory(title_en="Taking a booking", title_tr="Rezervasyon almak")
    assert services.search_articles("rezervasyon").count() == 1


def test_search_matches_keywords_and_body():
    HelpArticleFactory(title_en="Unrelated", keywords="deposit, kapora", body_en="Nothing here.")
    HelpArticleFactory(title_en="Also unrelated", keywords="", body_en="Mentions the leash.")
    assert services.search_articles("kapora").count() == 1
    assert services.search_articles("leash").count() == 1


def test_search_ranks_title_hits_above_body_hits():
    HelpArticleFactory(title_en="Body mention", body_en="This talks about the waitlist a lot.")
    HelpArticleFactory(title_en="Waitlist handling", body_en="Nothing relevant.")
    results = list(services.search_articles("waitlist"))
    assert results[0].title_en == "Waitlist handling"


def test_search_with_a_short_term_returns_the_manual_in_order():
    HelpArticleFactory(title_en="A")
    HelpArticleFactory(title_en="B")
    assert services.search_articles("x").count() == 0
    assert services.search_articles("").count() == 2


def test_search_can_be_restricted_to_a_category():
    bookings = HelpCategoryFactory(code="bookings")
    equipment = HelpCategoryFactory(code="equipment")
    HelpArticleFactory(category=bookings, title_en="Deposit rules")
    HelpArticleFactory(category=equipment, title_en="Deposit for rentals")
    assert services.search_articles("deposit", category_code="bookings").count() == 1


def test_search_excludes_unpublished_articles():
    HelpArticleFactory(title_en="Draft page", is_published=False)
    assert services.search_articles("Draft").count() == 0


def test_search_excludes_articles_in_an_inactive_category():
    category = HelpCategoryFactory(is_active=False)
    HelpArticleFactory(category=category, title_en="Hidden page")
    assert services.search_articles("Hidden").count() == 0


# ---------------------------------------------------------------------------
# Related articles
# ---------------------------------------------------------------------------
def test_related_articles_prefers_the_same_category():
    category = HelpCategoryFactory()
    article = HelpArticleFactory(category=category)
    sibling = HelpArticleFactory(category=category)
    HelpArticleFactory()  # different category

    related = services.related_articles(article, limit=5)
    assert sibling in related
    assert article not in related


def test_related_articles_falls_back_to_the_same_module():
    article = HelpArticleFactory(related_module="bookings")
    other_category_same_module = HelpArticleFactory(related_module="bookings")
    related = services.related_articles(article, limit=5)
    assert other_category_same_module in related


def test_related_articles_respects_the_limit():
    category = HelpCategoryFactory()
    article = HelpArticleFactory(category=category)
    for _ in range(6):
        HelpArticleFactory(category=category)
    assert len(services.related_articles(article, limit=3)) == 3


# ---------------------------------------------------------------------------
# View counting
# ---------------------------------------------------------------------------
def test_view_is_counted_once_per_session(request_with_session):
    article = HelpArticleFactory()
    request = request_with_session()

    assert services.register_article_view(article, request) is True
    assert services.register_article_view(article, request) is False

    article.refresh_from_db()
    assert article.view_count == 1


def test_a_second_session_counts_again(request_with_session):
    article = HelpArticleFactory()
    services.register_article_view(article, request_with_session())
    services.register_article_view(article, request_with_session())
    article.refresh_from_db()
    assert article.view_count == 2


# ---------------------------------------------------------------------------
# Helpfulness feedback
# ---------------------------------------------------------------------------
def test_feedback_is_recorded(request_with_session):
    article = HelpArticleFactory()
    request = request_with_session()

    assert services.record_feedback(article, request, helpful=True) == services.HELPFUL
    article.refresh_from_db()
    assert article.helpful_count == 1
    assert article.not_helpful_count == 0


def test_repeating_the_same_vote_changes_nothing(request_with_session):
    article = HelpArticleFactory()
    request = request_with_session()
    services.record_feedback(article, request, helpful=True)
    services.record_feedback(article, request, helpful=True)
    article.refresh_from_db()
    assert article.helpful_count == 1


def test_changing_the_vote_moves_the_count(request_with_session):
    article = HelpArticleFactory()
    request = request_with_session()
    services.record_feedback(article, request, helpful=True)
    services.record_feedback(article, request, helpful=False)

    article.refresh_from_db()
    assert article.helpful_count == 0
    assert article.not_helpful_count == 1
    assert services.current_vote(article, request) == services.NOT_HELPFUL


def test_counts_never_go_negative_after_an_admin_reset(request_with_session):
    """The session remembers a vote that the database no longer holds."""
    article = HelpArticleFactory()
    request = request_with_session()
    services.record_feedback(article, request, helpful=True)

    HelpArticle.all_objects.filter(pk=article.pk).update(helpful_count=0)
    services.record_feedback(article, request, helpful=False)

    article.refresh_from_db()
    assert article.helpful_count == 0
    assert article.not_helpful_count == 1


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def test_stats_are_zero_on_an_empty_installation():
    stats = services.help_center_stats()
    assert stats == {"categories": 0, "articles": 0, "reads": 0, "helpful": 0}


def test_stats_count_published_articles_only():
    category = HelpCategoryFactory()
    HelpArticleFactory(category=category)
    HelpArticleFactory(category=category, is_published=False)
    stats = services.help_center_stats()
    assert stats["categories"] == 1
    assert stats["articles"] == 1
