from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.help_center.models import HelpArticle

from .factories import HelpArticleFactory, HelpCategoryFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def receptionist(db):
    return User.objects.create_user(
        username="reception", email="reception@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def student_user(db):
    """Students hold help_center.view through the base capabilities."""
    return User.objects.create_user(
        username="pupil", email="pupil@example.com", password="pw-test-12345",
        role=Role.STUDENT,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_home_requires_authentication(client):
    response = client.get(reverse("help_center:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_every_signed_in_role_can_read_the_manual(client, student_user):
    """The manual is the one screen a confused user must always reach."""
    HelpArticleFactory()
    client.force_login(student_user)
    assert client.get(reverse("help_center:home")).status_code == 200


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
def test_home_lists_categories_and_counts(client, receptionist):
    category = HelpCategoryFactory(name_en="Bookings")
    HelpArticleFactory(category=category, title_en="Taking a booking")
    HelpArticleFactory(category=category, title_en="Cancellations", is_published=False)

    client.force_login(receptionist)
    response = client.get(reverse("help_center:home"))

    assert response.status_code == 200
    entry = response.context["categories"][0]
    assert entry["category"].article_count == 1
    assert b"Taking a booking" in response.content
    assert b"Cancellations" not in response.content


def test_home_hides_inactive_categories(client, receptionist):
    HelpCategoryFactory(name_en="Retired section", is_active=False)
    client.force_login(receptionist)
    response = client.get(reverse("help_center:home"))
    assert b"Retired section" not in response.content


def test_home_renders_an_empty_state_on_a_fresh_install(client, receptionist):
    client.force_login(receptionist)
    response = client.get(reverse("help_center:home"))
    assert response.status_code == 200
    assert response.context["stats"]["articles"] == 0


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
def test_category_detail_lists_its_articles(client, receptionist):
    category = HelpCategoryFactory(code="bookings", name_en="Bookings")
    HelpArticleFactory(category=category, title_en="Taking a booking")
    client.force_login(receptionist)

    response = client.get(reverse("help_center:category", args=["bookings"]))
    assert response.status_code == 200
    assert b"Taking a booking" in response.content


def test_inactive_category_is_not_reachable(client, receptionist):
    HelpCategoryFactory(code="hidden", is_active=False)
    client.force_login(receptionist)
    assert client.get(reverse("help_center:category", args=["hidden"])).status_code == 404


# ---------------------------------------------------------------------------
# Article
# ---------------------------------------------------------------------------
def test_article_detail_renders_body_toc_and_counts_the_view(client, receptionist):
    article = HelpArticleFactory(
        title_en="Taking a booking",
        body_en="## Before you start\n\nOpen the customer.\n\n## Then\n\nConfirm it.",
    )
    client.force_login(receptionist)

    response = client.get(reverse("help_center:article", args=[article.slug]), HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200
    assert len(response.context["toc"]) == 2

    article.refresh_from_db()
    assert article.view_count == 1


def test_refreshing_an_article_does_not_inflate_the_counter(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)
    url = reverse("help_center:article", args=[article.slug])
    client.get(url)
    client.get(url)
    article.refresh_from_db()
    assert article.view_count == 1


def test_unpublished_article_is_not_reachable(client, receptionist):
    article = HelpArticleFactory(is_published=False)
    client.force_login(receptionist)
    assert client.get(reverse("help_center:article", args=[article.slug])).status_code == 404


def test_article_body_is_sanitised_before_it_reaches_the_page(client, receptionist):
    article = HelpArticleFactory(body_en="<script>alert(1)</script>\n\nReal content.")
    client.force_login(receptionist)
    response = client.get(reverse("help_center:article", args=[article.slug]))
    assert b"<script>alert(1)</script>" not in response.content


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_search_filters_results(client, receptionist):
    HelpArticleFactory(title_en="Taking a booking")
    HelpArticleFactory(title_en="Adding a surfboard")
    client.force_login(receptionist)

    response = client.get(reverse("help_center:search"), {"q": "surfboard"})
    assert b"Adding a surfboard" in response.content
    assert b"Taking a booking" not in response.content


def test_search_htmx_request_returns_the_partial(client, receptionist):
    HelpArticleFactory(title_en="Taking a booking")
    client.force_login(receptionist)
    response = client.get(
        reverse("help_center:search"), {"q": "booking"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert "help_center/partials/search_results.html" in [t.name for t in response.templates]


def test_search_with_no_match_still_renders(client, receptionist):
    HelpArticleFactory(title_en="Taking a booking")
    client.force_login(receptionist)
    response = client.get(reverse("help_center:search"), {"q": "zzzznothing"})
    assert response.status_code == 200
    assert response.context["paginator"].count == 0


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
def test_feedback_records_a_vote_over_htmx(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)

    response = client.post(
        reverse("help_center:article_feedback", args=[article.slug]),
        {"answer": "yes"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    article.refresh_from_db()
    assert article.helpful_count == 1


def test_feedback_without_htmx_redirects_back_to_the_article(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)
    response = client.post(
        reverse("help_center:article_feedback", args=[article.slug]), {"answer": "no"}
    )
    assert response.status_code == 302
    assert article.get_absolute_url() in response.url
    article.refresh_from_db()
    assert article.not_helpful_count == 1


def test_feedback_rejects_an_unknown_answer(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)
    response = client.post(
        reverse("help_center:article_feedback", args=[article.slug]), {"answer": "maybe"}
    )
    assert response.status_code == 400
    assert HelpArticle.objects.get(pk=article.pk).helpful_count == 0


def test_feedback_rejects_get(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)
    assert (
        client.get(reverse("help_center:article_feedback", args=[article.slug])).status_code == 405
    )


# ---------------------------------------------------------------------------
# Contextual module panel
# ---------------------------------------------------------------------------
def test_module_panel_lists_articles_for_that_module(client, receptionist):
    HelpArticleFactory(title_en="Taking a booking", related_module="bookings")
    HelpArticleFactory(title_en="Adding a surfboard", related_module="equipment")
    client.force_login(receptionist)

    response = client.get(reverse("help_center:module", args=["bookings"]))
    assert response.status_code == 200
    assert b"Taking a booking" in response.content
    assert b"Adding a surfboard" not in response.content
