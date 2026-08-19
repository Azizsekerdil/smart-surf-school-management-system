"""Help Center business rules.

Search, view counting and the "was this helpful?" vote live here rather than in
the views, because all three have rules that are easy to get subtly wrong:

* search must stay portable between SQLite and PostgreSQL — no full-text
  operators, no ``search`` lookup, no raw SQL;
* a view is counted **once per reader per session**, otherwise a refresh loop
  turns a rarely used page into the most popular article in the school;
* a reader may change their mind about an article, so the vote is stored in the
  session and switching it moves the count instead of adding a second one.
"""

from __future__ import annotations

from django.db.models import Case, F, IntegerField, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Coalesce

from . import selectors
from .models import HelpArticle

#: Session keys. Namespaced so they cannot collide with another module's state.
VIEWED_SESSION_KEY = "help_center.viewed"
FEEDBACK_SESSION_KEY = "help_center.feedback"

#: Cap on the remembered-views list, so a long session cannot grow the session
#: row without bound.
MAX_REMEMBERED_VIEWS = 400

#: Shortest term worth querying; one character matches almost everything.
MIN_SEARCH_LENGTH = 2

HELPFUL = "helpful"
NOT_HELPFUL = "not_helpful"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_articles(
    term: str, *, category_code: str = "", queryset: QuerySet[HelpArticle] | None = None
) -> QuerySet[HelpArticle]:
    """Find published articles matching *term*.

    Matching is case-insensitive ``icontains`` over both languages' titles, the
    keyword list and both bodies. Results are ordered by a coarse relevance
    score — a title hit beats a keyword hit, which beats a body hit — then by
    the editorial sort order, so an empty search still returns the manual in
    reading order.
    """
    base = queryset if queryset is not None else selectors.published_articles()

    if category_code:
        base = base.filter(category__code=category_code)

    cleaned = (term or "").strip()

    # No query at all means "show me the manual", so the whole thing comes back
    # in reading order. A query that is merely too short is different: the
    # operator did type something, and answering it with all 22 articles reads
    # as though everything matched. Return nothing and let the screen say so.
    if not cleaned:
        return base.order_by("category__sort_order", "sort_order", "title_en")
    if len(cleaned) < MIN_SEARCH_LENGTH:
        return base.none()

    matches = (
        Q(title_en__icontains=cleaned)
        | Q(title_tr__icontains=cleaned)
        | Q(keywords__icontains=cleaned)
        | Q(body_en__icontains=cleaned)
        | Q(body_tr__icontains=cleaned)
        | Q(category__name_en__icontains=cleaned)
        | Q(category__name_tr__icontains=cleaned)
    )

    relevance = Case(
        When(Q(title_en__iexact=cleaned) | Q(title_tr__iexact=cleaned), then=Value(0)),
        When(
            Q(title_en__icontains=cleaned) | Q(title_tr__icontains=cleaned),
            then=Value(1),
        ),
        When(keywords__icontains=cleaned, then=Value(2)),
        When(
            Q(category__name_en__icontains=cleaned) | Q(category__name_tr__icontains=cleaned),
            then=Value(3),
        ),
        default=Value(4),
        output_field=IntegerField(),
    )

    return (
        base.filter(matches)
        .annotate(relevance=relevance)
        .order_by("relevance", "category__sort_order", "sort_order", "title_en")
    )


def related_articles(article: HelpArticle, limit: int = 5) -> list[HelpArticle]:
    """Sibling articles a reader is likely to want next.

    Same category first, then anything else documenting the same module, so a
    single-article category still offers somewhere to go.
    """
    if limit <= 0:
        return []

    siblings = list(
        selectors.published_articles()
        .filter(category_id=article.category_id)
        .exclude(pk=article.pk)
        .order_by("sort_order", "title_en")[:limit]
    )

    if len(siblings) < limit and article.related_module:
        seen = {item.pk for item in siblings} | {article.pk}
        extra = (
            selectors.published_articles()
            .filter(related_module=article.related_module)
            .exclude(pk__in=seen)
            .order_by("sort_order", "title_en")[: limit - len(siblings)]
        )
        siblings.extend(extra)

    return siblings


# ---------------------------------------------------------------------------
# View counting
# ---------------------------------------------------------------------------
def register_article_view(article: HelpArticle, request) -> bool:
    """Count one read of *article*, at most once per session.

    Returns ``True`` when the counter actually moved. The in-memory instance is
    updated too, so the template shows the new number without a second query.
    """
    session = getattr(request, "session", None)
    if session is None:
        return False

    seen = list(session.get(VIEWED_SESSION_KEY, []))
    if article.pk in seen:
        return False

    HelpArticle.all_objects.filter(pk=article.pk).update(view_count=F("view_count") + 1)
    article.view_count = (article.view_count or 0) + 1

    seen.append(article.pk)
    session[VIEWED_SESSION_KEY] = seen[-MAX_REMEMBERED_VIEWS:]
    session.modified = True
    return True


# ---------------------------------------------------------------------------
# "Was this helpful?"
# ---------------------------------------------------------------------------
def current_vote(article: HelpArticle, request) -> str | None:
    """The vote this session already cast on *article*, if any."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return (session.get(FEEDBACK_SESSION_KEY) or {}).get(str(article.pk))


def record_feedback(article: HelpArticle, request, *, helpful: bool) -> str:
    """Record (or change) this session's helpfulness vote.

    Returns the vote now held. Re-sending the same vote is a no-op, so a
    double-click or a retried HTMX request cannot inflate the counter.
    """
    session = getattr(request, "session", None)
    if session is None:
        return HELPFUL if helpful else NOT_HELPFUL

    wanted = HELPFUL if helpful else NOT_HELPFUL
    votes = dict(session.get(FEEDBACK_SESSION_KEY) or {})
    previous = votes.get(str(article.pk))

    if previous == wanted:
        return wanted

    manager = HelpArticle.all_objects
    if previous == HELPFUL:
        # Guarded so the field (a PositiveIntegerField) can never go negative if
        # the row was reset in the admin while the session lived on.
        manager.filter(pk=article.pk, helpful_count__gt=0).update(
            helpful_count=F("helpful_count") - 1
        )
    elif previous == NOT_HELPFUL:
        manager.filter(pk=article.pk, not_helpful_count__gt=0).update(
            not_helpful_count=F("not_helpful_count") - 1
        )

    if wanted == HELPFUL:
        manager.filter(pk=article.pk).update(helpful_count=F("helpful_count") + 1)
    else:
        manager.filter(pk=article.pk).update(not_helpful_count=F("not_helpful_count") + 1)

    votes[str(article.pk)] = wanted
    session[FEEDBACK_SESSION_KEY] = votes
    session.modified = True

    article.refresh_from_db(fields=["helpful_count", "not_helpful_count"])
    return wanted


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def help_center_stats() -> dict:
    """Headline numbers for the Help Center landing page."""
    articles = selectors.published_articles()
    totals = articles.aggregate(
        reads=Coalesce(Sum("view_count"), Value(0), output_field=IntegerField()),
        helpful=Coalesce(Sum("helpful_count"), Value(0), output_field=IntegerField()),
    )
    return {
        "categories": selectors.active_categories().count(),
        "articles": articles.count(),
        "reads": totals["reads"],
        "helpful": totals["helpful"],
    }
