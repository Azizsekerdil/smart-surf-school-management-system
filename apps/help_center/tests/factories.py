"""Factories for Help Center content."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.help_center.models import HelpArticle, HelpCategory


class HelpCategoryFactory(DjangoModelFactory):
    class Meta:
        model = HelpCategory
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"section-{n}")
    name_en = factory.Sequence(lambda n: f"Section {n}")
    name_tr = factory.Sequence(lambda n: f"Bölüm {n}")
    icon = "circle-help"
    sort_order = 100
    is_active = True


class HelpArticleFactory(DjangoModelFactory):
    class Meta:
        model = HelpArticle

    category = factory.SubFactory(HelpCategoryFactory)
    title_en = factory.Sequence(lambda n: f"Taking a booking {n}")
    title_tr = factory.Sequence(lambda n: f"Rezervasyon almak {n}")
    # Deliberately neutral: a default that mentioned "booking" made every
    # article match a search for it, so a test asserting a single title hit
    # silently got two. Tests that need a term in the body or the keywords set
    # them explicitly.
    body_en = (
        "## Before you start\n\n"
        "Open the record first.\n\n"
        "## Next steps\n\n"
        "Choose an option, then confirm."
    )
    body_tr = (
        "## Başlamadan önce\n\n"
        "Önce kaydı açın.\n\n"
        "## Sonraki adımlar\n\n"
        "Bir seçenek belirleyin, sonra onaylayın."
    )
    keywords = ""
    related_module = "bookings"
    sort_order = 10
    is_published = True
