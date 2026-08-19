"""HTML views for the Help Center.

Every capability in the matrix includes ``help_center.view`` through
``BASE_CAPABILITIES``, so these screens are reachable by everyone who can log
in — including customers and students. That is deliberate: the manual is the
one screen a confused user must always be able to reach. Nothing here writes
business data; the only mutation is an anonymous helpfulness counter.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.core.mixins import HtmxPartialMixin

from . import selectors, services
from .forms import HelpSearchForm
from .models import HelpArticle, HelpCategory

#: Shown on the landing page under each section card.
PREVIEW_ARTICLES_PER_CATEGORY = 4


class HelpHomeView(CapabilityRequiredMixin, TemplateView):
    """Landing page: the section grid, a search box and the popular pages."""

    capability = "help_center.view"
    template_name = "help_center/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        categories = list(selectors.categories_with_counts())
        # One extra query for the previews rather than one per card.
        previews: dict[int, list[HelpArticle]] = {category.pk: [] for category in categories}
        for article in selectors.published_articles().order_by("sort_order", "title_en"):
            bucket = previews.get(article.category_id)
            if bucket is not None and len(bucket) < PREVIEW_ARTICLES_PER_CATEGORY:
                bucket.append(article)

        context["categories"] = [
            {"category": category, "articles": previews.get(category.pk, [])}
            for category in categories
        ]
        context["search_form"] = HelpSearchForm()
        context["popular_articles"] = selectors.popular_articles()
        context["stats"] = services.help_center_stats()
        return context


class HelpCategoryDetailView(CapabilityRequiredMixin, DetailView):
    """Every article in one section of the manual."""

    capability = "help_center.view"
    model = HelpCategory
    template_name = "help_center/category_detail.html"
    context_object_name = "category"
    slug_field = "code"
    slug_url_kwarg = "code"

    def get_queryset(self):
        return HelpCategory.objects.filter(is_active=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["articles"] = selectors.articles_in_category(self.object)
        context["search_form"] = HelpSearchForm(initial={"category": self.object.code})
        context["other_categories"] = selectors.active_categories().exclude(pk=self.object.pk)
        return context


class HelpArticleDetailView(CapabilityRequiredMixin, DetailView):
    """One article: sanitised body, table of contents, related pages, feedback."""

    capability = "help_center.view"
    model = HelpArticle
    template_name = "help_center/article_detail.html"
    context_object_name = "article"

    def get_queryset(self):
        return selectors.published_articles()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        article: HelpArticle = self.object

        services.register_article_view(article, self.request)

        context["toc"] = article.table_of_contents()
        context["related"] = services.related_articles(article)
        context["category_articles"] = selectors.articles_in_category(article.category)
        context["current_vote"] = services.current_vote(article, self.request)
        return context


class HelpSearchView(CapabilityRequiredMixin, HtmxPartialMixin, ListView):
    """Full-text-ish search across both languages."""

    capability = "help_center.view"
    model = HelpArticle
    template_name = "help_center/search.html"
    partial_template_name = "help_center/partials/search_results.html"
    context_object_name = "articles"
    paginate_by = 20

    def get_search_term(self) -> str:
        return (self.request.GET.get("q") or "").strip()

    def get_category_code(self) -> str:
        return (self.request.GET.get("category") or "").strip()

    def get_queryset(self):
        return services.search_articles(
            self.get_search_term(), category_code=self.get_category_code()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.get_search_term()
        code = self.get_category_code()
        context["search_term"] = term
        context["search_form"] = HelpSearchForm(initial={"q": term, "category": code})
        context["active_category"] = (
            selectors.active_categories().filter(code=code).first() if code else None
        )
        context["has_query"] = bool(term or code)
        return context


class ArticleFeedbackView(CapabilityRequiredMixin, View):
    """POST-only helpfulness vote, swapped back in place by HTMX."""

    capability = "help_center.view"
    template_name = "help_center/partials/article_feedback.html"

    def post(self, request, slug: str, *args, **kwargs):
        answer = (request.POST.get("answer") or "").strip().lower()
        if answer not in {"yes", "no"}:
            return HttpResponseBadRequest("Unknown answer.")

        article = get_object_or_404(selectors.published_articles(), slug=slug)
        vote = services.record_feedback(article, request, helpful=answer == "yes")

        if not getattr(request, "htmx", False):
            # Without JavaScript the same form still works: post, then land back
            # on the article rather than on a bare fragment.
            messages.success(request, _("Thank you — your feedback was recorded."))
            return redirect(article.get_absolute_url() + "#article-feedback")

        return render(
            request,
            self.template_name,
            {"article": article, "current_vote": vote, "just_voted": True},
        )


class ModuleHelpView(CapabilityRequiredMixin, View):
    """Contextual help panel: the articles documenting one module.

    Any screen can drop in ``hx-get="{% url 'help_center:module' 'bookings' %}"``
    and get a small list of relevant articles without importing this app.
    """

    capability = "help_center.view"
    template_name = "help_center/partials/module_help.html"

    def get(self, request, module: str, *args, **kwargs):
        articles = list(selectors.articles_for_module(module)[:5])
        if not articles and not selectors.active_categories().exists():
            raise Http404("No help content has been loaded yet.")

        return render(request, self.template_name, {"articles": articles, "module": module})
