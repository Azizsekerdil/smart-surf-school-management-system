from __future__ import annotations

import pytest
from django.utils import translation

from apps.help_center.content import localized, render_content
from apps.help_center.models import HelpArticle

from .factories import HelpArticleFactory, HelpCategoryFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Language-aware fields
# ---------------------------------------------------------------------------
def test_category_name_follows_the_active_language():
    category = HelpCategoryFactory(name_en="Bookings", name_tr="Rezervasyonlar")
    with translation.override("tr"):
        assert category.name == "Rezervasyonlar"
    with translation.override("en"):
        assert category.name == "Bookings"


def test_missing_translation_falls_back_to_english():
    category = HelpCategoryFactory(name_en="Bookings", name_tr="")
    with translation.override("tr"):
        assert category.name == "Bookings"


def test_unknown_language_falls_back_to_english():
    article = HelpArticleFactory(title_en="Taking a booking", title_tr="Rezervasyon almak")
    with translation.override("de"):
        assert article.title == "Taking a booking"


def test_localized_returns_the_default_when_nothing_is_filled():
    category = HelpCategoryFactory(name_en="", name_tr="")
    assert localized(category, "name", default="fallback") == "fallback"


def test_article_body_follows_the_active_language():
    article = HelpArticleFactory(body_en="English body", body_tr="Türkçe gövde")
    with translation.override("tr"):
        assert article.body == "Türkçe gövde"
    with translation.override("en"):
        assert article.body == "English body"


# ---------------------------------------------------------------------------
# Markdown rendering and sanitising
# ---------------------------------------------------------------------------
def test_rendered_body_converts_markdown():
    article = HelpArticleFactory(body_en="## Heading\n\nSome **bold** text.")
    with translation.override("en"):
        html = str(article.rendered_body())
    assert "<h2" in html
    assert "<strong>bold</strong>" in html


def test_rendered_body_strips_script_tags():
    """Article bodies are admin-editable, therefore untrusted."""
    article = HelpArticleFactory(
        body_en="Safe text.\n\n<script>alert('xss')</script>\n\nMore text."
    )
    with translation.override("en"):
        html = str(article.rendered_body())
    assert "<script" not in html
    assert "alert(" not in html
    assert "Safe text." in html


def test_rendered_body_strips_event_handlers_and_javascript_urls():
    article = HelpArticleFactory(
        body_en='<p onclick="steal()">Click</p>\n\n<a href="javascript:steal()">bad link</a>'
    )
    with translation.override("en"):
        html = str(article.rendered_body())
    assert "onclick" not in html
    assert "javascript:" not in html


def test_rendered_body_keeps_safe_links_and_tables():
    article = HelpArticleFactory(
        body_en=(
            "[Docs](https://example.org/docs)\n\n"
            "| Level | Max group |\n|---|---|\n| Beginner | 8 |\n"
        )
    )
    with translation.override("en"):
        html = str(article.rendered_body())
    assert 'href="https://example.org/docs"' in html
    assert "<table>" in html


def test_table_of_contents_lists_headings_in_order():
    article = HelpArticleFactory(
        body_en="## First section\n\ntext\n\n### Nested\n\ntext\n\n## Second section\n\ntext"
    )
    with translation.override("en"):
        toc = article.table_of_contents()
    assert [entry["name"] for entry in toc] == ["First section", "Nested", "Second section"]
    assert toc[1]["level"] == 3
    assert all(entry["id"] for entry in toc)


def test_render_content_handles_empty_source():
    result = render_content("")
    assert str(result.html) == ""
    assert result.headings == []


# ---------------------------------------------------------------------------
# Persistence and derived values
# ---------------------------------------------------------------------------
def test_slug_is_generated_from_the_english_title():
    article = HelpArticleFactory(title_en="Taking a booking", title_tr="Rezervasyon almak")
    assert article.slug.startswith("taking-a-booking")


def test_slug_collision_gets_a_suffix():
    first = HelpArticleFactory(title_en="Same title")
    second = HelpArticleFactory(title_en="Same title")
    assert first.slug != second.slug


def test_str_prefers_the_english_title():
    article = HelpArticleFactory(title_en="Taking a booking")
    assert str(article) == "Taking a booking"


def test_helpful_percent_is_none_without_votes():
    article = HelpArticleFactory()
    assert article.helpful_percent is None


def test_helpful_percent_rounds_the_share():
    article = HelpArticleFactory()
    HelpArticle.all_objects.filter(pk=article.pk).update(helpful_count=3, not_helpful_count=1)
    article.refresh_from_db()
    assert article.helpful_percent == 75


def test_keyword_list_splits_and_trims():
    article = HelpArticleFactory(keywords="booking,  rezervasyon , deposit")
    assert article.keyword_list == ["booking", "rezervasyon", "deposit"]


def test_reading_minutes_is_at_least_one():
    article = HelpArticleFactory(body_en="Short.")
    assert article.reading_minutes >= 1


def test_soft_delete_hides_the_article_from_the_default_manager():
    article = HelpArticleFactory()
    article.delete()
    assert not HelpArticle.objects.filter(pk=article.pk).exists()
    assert HelpArticle.all_objects.filter(pk=article.pk, is_deleted=True).exists()
