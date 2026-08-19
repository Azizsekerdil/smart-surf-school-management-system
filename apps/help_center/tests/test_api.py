from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role

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
def super_admin(db):
    return User.objects.create_user(
        username="root", email="root@example.com", password="pw-test-12345",
        role=Role.SUPER_ADMIN,
    )


def test_article_list_requires_authentication(client):
    response = client.get(reverse("helparticle-list"))
    assert response.status_code in {401, 403}


def test_article_list_returns_published_content(client, receptionist):
    HelpArticleFactory(title_en="Taking a booking")
    client.force_login(receptionist)
    response = client.get(reverse("helparticle-list"))
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_article_detail_exposes_sanitised_html(client, receptionist):
    article = HelpArticleFactory(body_en="## Heading\n\n<script>alert(1)</script>Body.")
    client.force_login(receptionist)

    response = client.get(reverse("helparticle-detail", args=[article.slug]), HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200
    payload = response.json()
    assert "<h2" in payload["body_html"]
    assert "<script" not in payload["body_html"]
    assert payload["table_of_contents"][0]["name"] == "Heading"


def test_search_endpoint_ranks_results(client, receptionist):
    HelpArticleFactory(title_en="Waitlist handling")
    HelpArticleFactory(title_en="Something else", body_en="mentions the waitlist")
    client.force_login(receptionist)

    response = client.get(reverse("helparticle-search"), {"q": "waitlist"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["title"] == "Waitlist handling"


def test_feedback_endpoint_records_a_vote(client, receptionist):
    article = HelpArticleFactory()
    client.force_login(receptionist)

    response = client.post(
        reverse("helparticle-feedback", args=[article.slug]),
        {"helpful": True},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["helpful_count"] == 1


def test_receptionist_cannot_create_an_article(client, receptionist):
    category = HelpCategoryFactory()
    client.force_login(receptionist)
    response = client.post(
        reverse("helparticle-list"),
        {
            "category": category.pk,
            "title_en": "New",
            "title_tr": "Yeni",
            "body_en": "Body",
            "body_tr": "Gövde",
        },
        content_type="application/json",
    )
    assert response.status_code == 403


def test_super_admin_can_create_an_article(client, super_admin):
    category = HelpCategoryFactory()
    client.force_login(super_admin)
    response = client.post(
        reverse("helparticle-list"),
        {
            "category": category.pk,
            "title_en": "New page",
            "title_tr": "Yeni sayfa",
            "body_en": "Body",
            "body_tr": "Gövde",
        },
        content_type="application/json",
    )
    assert response.status_code == 201
    assert response.json()["slug"] == "new-page"


def test_category_list_includes_article_counts(client, receptionist):
    category = HelpCategoryFactory()
    HelpArticleFactory(category=category)
    client.force_login(receptionist)

    response = client.get(reverse("helpcategory-list"))
    assert response.status_code == 200
    assert response.json()["results"][0]["article_count"] == 1
