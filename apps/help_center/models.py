"""Help Center content model: categories and Markdown articles.

Two decisions worth knowing about
---------------------------------
* Titles and bodies are stored as ``*_en`` / ``*_tr`` column pairs rather than
  going through the gettext catalogue. Article prose is *operational content* —
  a manager rewrites a check-in procedure at the counter — and content that
  changes without a deploy cannot live in a compiled ``.po`` file.
* ``rendered_body()`` never returns raw author HTML. See
  :mod:`apps.help_center.content` for the sanitiser policy.
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import MODULE_LABELS, MODULES
from apps.core.models import BaseModel, TimeStampedModel
from apps.core.validators import slug_code_validator

from .content import RenderedContent, localized, plain_text, render_content

#: ``related_module`` is limited to the modules the capability matrix knows
#: about, so "help for this screen" lookups can never point at a dead name.
RELATED_MODULE_CHOICES: tuple[tuple[str, object], ...] = tuple(
    (module, MODULE_LABELS.get(module, module)) for module in MODULES
)


class HelpCategory(TimeStampedModel):
    """A section of the manual — one per area of the product."""

    code = models.SlugField(
        _("code"),
        max_length=50,
        unique=True,
        validators=[slug_code_validator],
        help_text=_("Stable identifier used in URLs, e.g. getting-started."),
    )
    name_en = models.CharField(_("name (EN)"), max_length=150)
    name_tr = models.CharField(_("name (TR)"), max_length=150)
    icon = models.CharField(
        _("icon"),
        max_length=40,
        default="circle-help",
        help_text=_("Name of a vendored Lucide icon, e.g. graduation-cap."),
    )
    sort_order = models.PositiveIntegerField(_("sort order"), default=100, db_index=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("help category")
        verbose_name_plural = _("help categories")
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="help_cat_active_order"),
        ]

    def __str__(self) -> str:
        return self.name_en or self.name_tr or self.code

    # -- derived values ----------------------------------------------------
    @property
    def name(self) -> str:
        """The category name in the language the page is being rendered in."""
        return localized(self, "name", default=self.code)

    def get_absolute_url(self) -> str:
        return reverse("help_center:category", kwargs={"code": self.code})


class HelpArticle(BaseModel):
    """One page of the manual, written in Markdown in both languages."""

    category = models.ForeignKey(
        "help_center.HelpCategory",
        verbose_name=_("category"),
        on_delete=models.PROTECT,
        related_name="articles",
    )
    slug = models.SlugField(
        _("slug"),
        max_length=160,
        unique=True,
        blank=True,
        help_text=_("Permanent address of the article. Generated from the English title."),
    )
    title_en = models.CharField(_("title (EN)"), max_length=200)
    title_tr = models.CharField(_("title (TR)"), max_length=200)
    body_en = models.TextField(_("body (EN)"), help_text=_("Markdown source."))
    body_tr = models.TextField(_("body (TR)"), help_text=_("Markdown source."))
    keywords = models.CharField(
        _("keywords"),
        max_length=300,
        blank=True,
        db_index=True,
        help_text=_("Comma-separated search terms in both languages."),
    )
    sort_order = models.PositiveIntegerField(_("sort order"), default=100, db_index=True)
    is_published = models.BooleanField(_("published"), default=True, db_index=True)
    view_count = models.PositiveIntegerField(_("views"), default=0, editable=False)
    helpful_count = models.PositiveIntegerField(_("marked helpful"), default=0, editable=False)
    not_helpful_count = models.PositiveIntegerField(
        _("marked not helpful"), default=0, editable=False
    )
    related_module = models.CharField(
        _("related module"),
        max_length=50,
        blank=True,
        db_index=True,
        choices=RELATED_MODULE_CHOICES,
        help_text=_("The part of the system this article documents."),
    )

    class Meta:
        verbose_name = _("help article")
        verbose_name_plural = _("help articles")
        ordering = ["category__sort_order", "sort_order", "title_en"]
        indexes = [
            models.Index(fields=["category", "sort_order"], name="help_art_cat_order"),
            models.Index(fields=["is_published", "sort_order"], name="help_art_pub_order"),
            models.Index(fields=["related_module", "is_published"], name="help_art_module_pub"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return self.title_en or self.title_tr or self.slug

    # -- persistence -------------------------------------------------------
    def _assign_slug(self) -> None:
        base = slugify(self.title_en or self.title_tr)[:140] or "help-article"
        candidate = base
        suffix = 2
        manager = type(self).all_objects
        while manager.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base[:150]}-{suffix}"
            suffix += 1
        self.slug = candidate

    def save(self, *args, **kwargs):
        if not self.slug:
            self._assign_slug()
        return super().save(*args, **kwargs)

    # -- language-aware content -------------------------------------------
    @property
    def title(self) -> str:
        return localized(self, "title", default=self.slug)

    @property
    def body(self) -> str:
        """The Markdown source for the active language."""
        return localized(self, "body")

    def _rendered(self) -> RenderedContent:
        """Render the active-language body once per instance, per language."""
        source = self.body
        cached = getattr(self, "_render_cache", None)
        if cached is not None and cached[0] == source:
            return cached[1]
        result = render_content(source)
        self._render_cache = (source, result)
        return result

    def rendered_body(self) -> str:
        """Sanitised HTML for the active language — safe to output directly."""
        return self._rendered().html

    def table_of_contents(self) -> list[dict]:
        """Ordered ``{id, name, level}`` entries for the in-page navigation."""
        return self._rendered().headings

    @property
    def reading_minutes(self) -> int:
        return self._rendered().reading_minutes

    @property
    def excerpt(self) -> str:
        return plain_text(self.body)

    # -- feedback ----------------------------------------------------------
    @property
    def feedback_total(self) -> int:
        return self.helpful_count + self.not_helpful_count

    @property
    def helpful_percent(self) -> int | None:
        """Share of readers who found the article useful, or ``None`` if unrated."""
        total = self.feedback_total
        if not total:
            return None
        return round(self.helpful_count * 100 / total)

    @property
    def keyword_list(self) -> list[str]:
        return [part.strip() for part in (self.keywords or "").split(",") if part.strip()]

    def get_absolute_url(self) -> str:
        return reverse("help_center:article", kwargs={"slug": self.slug})
