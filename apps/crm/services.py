"""CRM business rules.

Everything that decides something lives here: what a legal pipeline move is,
what happens when a lead becomes a customer, when a follow-up is mandatory, and
how a campaign's numbers are turned into a verdict. Views orchestrate, models
validate, services decide.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.utils import safe_divide

from .models import Campaign, Interaction, Lead, Segment
from .selectors import (
    VISIT_STATUSES,
    booking_relation,
    customer_model,
    visit_filter,
)

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

#: Default window after which a customer with no visit is a churn candidate.
DEFAULT_CHURN_DAYS = 180

#: How long a complaint may sit before somebody must come back to the customer.
COMPLAINT_FOLLOW_UP_HOURS = 24

#: Fields we are willing to copy from a lead onto a brand-new customer. Only
#: those that actually exist on ``customers.Customer`` are used, so the CRM does
#: not break when the customers module evolves.
LEAD_TO_CUSTOMER_FIELDS = ("first_name", "last_name", "email", "phone", "source")


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
def advance_lead_status(lead: Lead, new_status: str, user=None, lost_reason: str = "") -> Lead:
    """Move *lead* to *new_status*, enforcing the pipeline rules.

    Rules
    -----
    * A closed lead (won or lost) is never silently reopened — reopening is an
      explicit move back to an open stage and clears the outcome fields.
    * ``WON`` is not reachable here: winning a lead means creating or linking a
      customer, which is :func:`convert_lead_to_customer`.
    * ``LOST`` requires a reason, because a funnel without loss reasons cannot
      be improved.
    """
    if new_status not in Lead.Status.values:
        raise ValidationError(_("“%(status)s” is not a pipeline stage.") % {"status": new_status})

    if new_status == lead.status:
        return lead

    if new_status == Lead.Status.WON:
        raise ValidationError(
            _("Win a lead by converting it, so the customer record is created properly.")
        )

    if lead.status == Lead.Status.WON and lead.converted_customer_id:
        raise ValidationError(
            _("This lead was already converted into a customer and cannot be moved.")
        )

    previous_status = lead.status
    lead.status = new_status

    if new_status == Lead.Status.LOST:
        reason = (lost_reason or lead.lost_reason or "").strip()
        if not reason:
            raise ValidationError({"lost_reason": _("Record why the lead was lost.")})
        lead.lost_reason = reason[:200]
        lead.probability = ZERO
        lead.next_action = ""
        lead.next_action_at = None
    else:
        # Reopening: drop the closing fields so the record is not contradictory.
        lead.lost_reason = ""
        lead.converted_at = None
        if lead.probability <= ZERO:
            lead.probability = Decimal("10.00")

    if user is not None and getattr(user, "is_authenticated", False):
        lead.updated_by = user

    lead.full_clean()
    lead.save()

    record_audit(
        None,
        action=AuditAction.UPDATE,
        instance=lead,
        user=user,
        description=_("Lead %(name)s moved from %(old)s to %(new)s")
        % {
            "name": lead.full_name,
            "old": dict(Lead.Status.choices).get(previous_status, previous_status),
            "new": lead.get_status_display(),
        },
        changes={"status": [previous_status, lead.status]},
    )
    return lead


def find_matching_customer(email: str = "", phone: str = ""):
    """Return an existing customer with the same e-mail or phone, if any.

    Creating a second customer record for somebody who already books with the
    school destroys their history, their package balance and their loyalty. The
    conversion flow therefore always looks first.
    """
    customer = customer_model()
    if customer is None:
        return None

    email = (email or "").strip()
    phone = (phone or "").strip()
    condition = Q()
    if email:
        condition |= Q(email__iexact=email)
    if phone:
        condition |= Q(phone=phone)
    if not condition:
        return None
    return customer.objects.filter(condition).order_by("pk").first()


@transaction.atomic
def convert_lead_to_customer(lead: Lead, user=None, customer=None):
    """Turn *lead* into a customer and close the pipeline entry.

    Pass ``customer`` to link an existing record; otherwise a new one is created
    from the lead's details. Either way the lead's interaction history moves
    across, so the customer's timeline starts before their first booking.
    """
    if lead.converted_customer_id:
        raise ValidationError(_("This lead has already been converted."))
    if lead.status == Lead.Status.LOST:
        raise ValidationError(
            _("Reopen the lead before converting it — a lost lead cannot be won.")
        )
    if not (lead.email or "").strip() and not (lead.phone or "").strip():
        raise ValidationError(
            _("Add an e-mail address or a phone number before converting this lead.")
        )

    customer_cls = customer_model()
    if customer_cls is None:  # pragma: no cover - customers is always installed
        raise ValidationError(_("The customers module is unavailable."))

    created = False
    if customer is None:
        customer = find_matching_customer(lead.email, lead.phone)

    if customer is None:
        values = {
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "email": (lead.email or "").strip(),
            "phone": (lead.phone or "").strip(),
            "source": lead.source,
        }
        field_names = {field.name for field in customer_cls._meta.get_fields()}
        payload = {
            name: values[name]
            for name in LEAD_TO_CUSTOMER_FIELDS
            if name in field_names and values.get(name) not in (None, "")
        }
        customer = customer_cls(**payload)
        if user is not None and getattr(user, "is_authenticated", False):
            if hasattr(customer, "created_by_id"):
                customer.created_by = user
            if hasattr(customer, "updated_by_id"):
                customer.updated_by = user

        # Validate what we supplied; let the customer model's own save() take
        # care of anything it generates itself (codes, defaults, sequences).
        exclude = [name for name in field_names if name not in payload]
        try:
            customer.full_clean(exclude=exclude)
        except ValidationError as exc:
            raise ValidationError(
                _(
                    "The customer record could not be created from this lead: %(detail)s "
                    "Create the customer manually, then link it here."
                )
                % {"detail": "; ".join(exc.messages)}
            ) from exc
        customer.save()
        created = True

    now = timezone.now()
    lead.converted_customer = customer
    lead.converted_at = now
    lead.status = Lead.Status.WON
    lead.probability = HUNDRED
    lead.lost_reason = ""
    lead.next_action = ""
    lead.next_action_at = None
    if user is not None and getattr(user, "is_authenticated", False):
        lead.updated_by = user
    lead.full_clean()
    lead.save()

    # Carry the conversation across so nothing is lost at the handover.
    moved = lead.interactions.filter(customer__isnull=True).update(customer=customer)

    record_audit(
        None,
        action=AuditAction.UPDATE,
        instance=lead,
        user=user,
        description=(
            _("Lead %(name)s converted into a new customer")
            if created
            else _("Lead %(name)s linked to an existing customer")
        )
        % {"name": lead.full_name},
        changes={
            "status": [Lead.Status.PROPOSAL_SENT, Lead.Status.WON],
            "converted_customer": [None, str(customer)],
            "interactions_moved": [0, moved],
        },
    )
    return customer


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------
def log_interaction(
    *,
    kind: str,
    subject: str,
    body: str = "",
    customer=None,
    lead: Lead | None = None,
    direction: str = Interaction.Direction.OUTBOUND,
    occurred_at=None,
    duration_minutes: int | None = None,
    handled_by=None,
    follow_up_required: bool = False,
    follow_up_at=None,
    sentiment: str = "",
    user=None,
) -> Interaction:
    """Record one contact and apply the school's follow-up policy.

    A complaint always creates a follow-up. Left to a busy reception desk it
    would not, and an unanswered complaint is how a school loses a customer and
    earns a one-star review.
    """
    if customer is None and lead is None:
        raise ValidationError(_("Attach the interaction to a customer or to a lead."))

    actor = handled_by or user
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None

    occurred_at = occurred_at or timezone.now()

    if kind in Interaction.ESCALATING_KINDS:
        follow_up_required = True
        if not follow_up_at:
            follow_up_at = occurred_at + timezone.timedelta(hours=COMPLAINT_FOLLOW_UP_HOURS)
        if not sentiment:
            sentiment = Interaction.Sentiment.NEGATIVE

    if sentiment == Interaction.Sentiment.NEGATIVE and not follow_up_required:
        follow_up_required = True
        follow_up_at = follow_up_at or occurred_at + timezone.timedelta(
            hours=COMPLAINT_FOLLOW_UP_HOURS
        )

    if not follow_up_required:
        follow_up_at = None

    interaction = Interaction(
        kind=kind,
        direction=direction,
        subject=(subject or "").strip()[:200],
        body=body or "",
        customer=customer,
        lead=lead,
        occurred_at=occurred_at,
        duration_minutes=duration_minutes,
        handled_by=actor,
        follow_up_required=follow_up_required,
        follow_up_at=follow_up_at,
        sentiment=sentiment,
    )
    if actor is not None:
        interaction.created_by = actor
        interaction.updated_by = actor

    interaction.full_clean()
    interaction.save()

    # Touching a lead is progress: a brand-new lead becomes "contacted".
    if lead is not None and lead.status == Lead.Status.NEW and kind != Interaction.Kind.NOTE:
        lead.status = Lead.Status.CONTACTED
        if actor is not None:
            lead.updated_by = actor
        lead.save(update_fields=["status", "updated_by", "updated_at"])

    record_audit(
        None,
        action=AuditAction.CREATE,
        instance=interaction,
        user=actor,
        description=_("%(kind)s logged for %(who)s")
        % {"kind": interaction.get_kind_display(), "who": interaction.contact_display},
    )
    return interaction


def complete_follow_up(interaction: Interaction, user=None) -> Interaction:
    """Mark an outstanding follow-up as handled."""
    if not interaction.follow_up_required:
        return interaction
    interaction.follow_up_required = False
    if user is not None and getattr(user, "is_authenticated", False):
        interaction.updated_by = user
    interaction.save(update_fields=["follow_up_required", "updated_by", "updated_at"])
    record_audit(
        None,
        action=AuditAction.UPDATE,
        instance=interaction,
        user=user,
        description=_("Follow-up completed for %(who)s") % {"who": interaction.contact_display},
        changes={"follow_up_required": [True, False]},
    )
    return interaction


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
def resolve_segment(segment: Segment):
    """Resolve *segment* to a customer queryset and cache its size."""
    queryset = segment.resolve()
    count = queryset.count()
    segment.cached_count = count
    segment.last_calculated_at = timezone.now()
    if segment.pk:
        segment.save(update_fields=["cached_count", "last_calculated_at", "updated_at"])
    return queryset


def preview_segment_size(criteria: dict) -> int:
    """Count the customers an unsaved set of criteria would reach."""
    from .selectors import build_customer_queryset

    return build_customer_queryset(criteria or {}).count()


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
def set_campaign_status(campaign: Campaign, new_status: str, user=None) -> Campaign:
    """Move a campaign through its lifecycle, refusing illegal jumps."""
    if new_status not in Campaign.Status.values:
        raise ValidationError(_("“%(status)s” is not a campaign status.") % {"status": new_status})
    if new_status == campaign.status:
        return campaign
    if new_status not in campaign.allowed_next_statuses():
        raise ValidationError(
            _("A %(current)s campaign cannot become %(target)s.")
            % {
                "current": campaign.get_status_display(),
                "target": dict(Campaign.Status.choices).get(new_status, new_status),
            }
        )

    if new_status == Campaign.Status.RUNNING and campaign.end_date < timezone.localdate():
        raise ValidationError(
            _("This campaign's end date has passed — move the end date before starting it.")
        )

    previous = campaign.status
    campaign.status = new_status
    if user is not None and getattr(user, "is_authenticated", False):
        campaign.updated_by = user
    campaign.full_clean()
    campaign.save()

    record_audit(
        None,
        action=AuditAction.UPDATE,
        instance=campaign,
        user=user,
        description=_("Campaign %(code)s is now %(status)s")
        % {"code": campaign.code, "status": campaign.get_status_display()},
        changes={"status": [previous, new_status]},
    )
    return campaign


def campaign_performance(campaign: Campaign) -> dict:
    """Return the full performance picture for one campaign."""
    audience = campaign.target_segment.cached_count if campaign.target_segment_id else 0
    reach = safe_divide(campaign.sent_count, audience) * 100 if audience else None
    revenue = campaign.revenue_attributed or ZERO
    spend = campaign.actual_spend or ZERO

    if campaign.status in (Campaign.Status.COMPLETED, Campaign.Status.CANCELLED):
        verdict_days = None
    else:
        verdict_days = campaign.days_remaining

    return {
        "campaign": campaign,
        "audience_size": audience,
        "sent": campaign.sent_count,
        "opened": campaign.opened_count,
        "converted": campaign.converted_count,
        "reach_percent": round(reach, 2) if reach is not None else None,
        "open_rate": campaign.open_rate,
        "conversion_rate": campaign.conversion_rate,
        "budget": campaign.budget or ZERO,
        "spend": spend,
        "budget_used_percent": campaign.budget_used_percent,
        "is_over_budget": campaign.is_over_budget,
        "revenue": revenue,
        "profit": (revenue - spend).quantize(Decimal("0.01")),
        "roi": campaign.roi,
        "cost_per_conversion": campaign.cost_per_conversion,
        "revenue_per_conversion": (
            (revenue / Decimal(campaign.converted_count)).quantize(Decimal("0.01"))
            if campaign.converted_count
            else None
        ),
        "days_remaining": verdict_days,
        "is_profitable": revenue > spend,
    }


def campaign_leaderboard(limit: int = 5) -> list[dict]:
    """Best-performing campaigns by attributed profit."""
    campaigns = (
        Campaign.objects.exclude(status=Campaign.Status.DRAFT)
        .select_related("target_segment")
        .order_by("-revenue_attributed")[: max(limit, 1)]
    )
    return [campaign_performance(campaign) for campaign in campaigns]


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def customer_retention_stats(start, end, churn_days: int = DEFAULT_CHURN_DAYS) -> dict:
    """New vs returning customers, repeat rate and churn candidates.

    "New" means the customer's first counted visit falls inside the window;
    "returning" means they visited inside the window and had visited before it.
    A visit is a booking that was not cancelled and was not a no-show — the two
    statuses where the customer never actually reached the water.
    """
    empty = {
        "start": start,
        "end": end,
        "churn_days": churn_days,
        "active_customers": 0,
        "new_customers": 0,
        "returning_customers": 0,
        "repeat_rate": None,
        "churn_candidates": 0,
        "churn_queryset": None,
        "data_available": False,
    }

    customer_cls = customer_model()
    query_name, date_field, status_field = booking_relation()
    booking = django_apps.get_model("bookings", "Booking") if query_name else None
    if customer_cls is None or booking is None or not date_field:
        return empty

    window = booking.objects.all()
    if status_field:
        window = window.filter(**{f"{status_field}__in": VISIT_STATUSES})
    if start is not None:
        window = window.filter(**{f"{date_field}__gte": start})
    if end is not None:
        window = window.filter(**{f"{date_field}__lte": end})

    active_ids = set(
        window.exclude(customer__isnull=True).values_list("customer_id", flat=True).distinct()
    )
    if not active_ids:
        return {**empty, "data_available": True}

    earlier = booking.objects.filter(customer_id__in=active_ids)
    if status_field:
        earlier = earlier.filter(**{f"{status_field}__in": VISIT_STATUSES})
    if start is not None:
        earlier = earlier.filter(**{f"{date_field}__lt": start})
    returning_ids = set(earlier.values_list("customer_id", flat=True).distinct())

    active = len(active_ids)
    returning = len(returning_ids & active_ids)
    new = active - returning

    cutoff = timezone.now() - timezone.timedelta(days=churn_days)
    churn_queryset = (
        customer_cls.objects.annotate(
            crm_last_visit=Max(
                f"{query_name}__{date_field}", filter=visit_filter(query_name, status_field)
            ),
            crm_visit_count=Count(
                query_name, filter=visit_filter(query_name, status_field), distinct=True
            ),
        )
        .filter(crm_visit_count__gt=0, crm_last_visit__lt=cutoff)
        .order_by("crm_last_visit")
    )

    return {
        "start": start,
        "end": end,
        "churn_days": churn_days,
        "active_customers": active,
        "new_customers": new,
        "returning_customers": returning,
        "repeat_rate": round(safe_divide(returning, active) * 100, 2),
        "churn_candidates": churn_queryset.count(),
        "churn_queryset": churn_queryset,
        "data_available": True,
    }
