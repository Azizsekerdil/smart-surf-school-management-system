"""CRM data model.

Four entities, each answering one operational question:

``Lead``
    Somebody who wants to surf but is not yet a customer. Carries the sales
    pipeline stage, the money at stake and the next thing a human must do.
``Interaction``
    Every contact with a lead or a customer — the shared memory of the school,
    so the next person on reception knows what was already said.
``Segment``
    A saved, declarative audience definition. Criteria are a **whitelisted**
    JSON document; they are never evaluated as code and never passed straight
    into ``.filter()``.
``Campaign``
    A marketing action aimed at a segment, with money in and money out so ROI
    is a fact rather than a feeling.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import BookingSource
from apps.core.models import BaseModel, money_field, percent_field
from apps.core.utils import next_sequential_code
from apps.core.validators import phone_validator, slug_code_validator

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

#: How far into the future an ``occurred_at`` may sit before we call it a typo.
#: A few minutes of clock skew between a tablet on the beach and the server is
#: normal; an hour is not.
FUTURE_TOLERANCE = timezone.timedelta(minutes=5)


class Lead(BaseModel):
    """A prospective customer moving through the sales pipeline."""

    class Status(models.TextChoices):
        NEW = "new", _("New")
        CONTACTED = "contacted", _("Contacted")
        QUALIFIED = "qualified", _("Qualified")
        PROPOSAL_SENT = "proposal_sent", _("Proposal sent")
        WON = "won", _("Won")
        LOST = "lost", _("Lost")

    #: Stages where the lead is still worth working.
    OPEN_STATUSES = (
        Status.NEW,
        Status.CONTACTED,
        Status.QUALIFIED,
        Status.PROPOSAL_SENT,
    )
    #: Stages that end the pipeline.
    CLOSED_STATUSES = (Status.WON, Status.LOST)
    #: Forward order of the funnel, used to detect skipped or reversed moves.
    STAGE_ORDER = (
        Status.NEW,
        Status.CONTACTED,
        Status.QUALIFIED,
        Status.PROPOSAL_SENT,
        Status.WON,
    )

    # --- who ---------------------------------------------------------------
    first_name = models.CharField(_("first name"), max_length=80)
    last_name = models.CharField(_("last name"), max_length=80, blank=True)
    email = models.EmailField(_("e-mail"), blank=True, db_index=True)
    phone = models.CharField(
        _("phone"), max_length=25, blank=True, db_index=True, validators=[phone_validator]
    )

    # --- where they came from and what they want ---------------------------
    source = models.CharField(
        _("source"),
        max_length=20,
        choices=BookingSource.choices,
        default=BookingSource.WEBSITE,
        db_index=True,
    )
    interest = models.TextField(
        _("interest"),
        blank=True,
        help_text=_("What the person asked for: level, dates, group size, budget."),
    )

    # --- pipeline ----------------------------------------------------------
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.NEW, db_index=True
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("assigned to"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
    )
    expected_value = money_field(
        _("expected value"), help_text=_("Estimated revenue if this lead converts.")
    )
    probability = percent_field(
        _("probability (%)"),
        default=Decimal("10.00"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(HUNDRED)],
        help_text=_("Chance of winning this lead, from 0 to 100."),
    )

    # --- the next human action --------------------------------------------
    next_action = models.CharField(
        _("next action"),
        max_length=200,
        blank=True,
        help_text=_("The single next step, e.g. “Call back about August dates”."),
    )
    next_action_at = models.DateTimeField(_("next action due"), null=True, blank=True, db_index=True)

    # --- outcome -----------------------------------------------------------
    converted_customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("converted customer"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="origin_leads",
    )
    converted_at = models.DateTimeField(_("converted at"), null=True, blank=True)
    lost_reason = models.CharField(_("lost reason"), max_length=200, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("lead")
        verbose_name_plural = _("leads")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="crm_lead_status_created"),
            models.Index(fields=["assigned_to", "next_action_at"], name="crm_lead_owner_due"),
            models.Index(fields=["source", "status"], name="crm_lead_source_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(probability__gte=0) & models.Q(probability__lte=100),
                name="crm_lead_probability_range",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_value__gte=0),
                name="crm_lead_expected_value_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name

    # -- derived values -----------------------------------------------------
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def weighted_value(self) -> Decimal:
        """Expected value discounted by the win probability."""
        value = self.expected_value or ZERO
        probability = self.probability or ZERO
        return (value * probability / HUNDRED).quantize(Decimal("0.01"))

    @property
    def is_converted(self) -> bool:
        return self.converted_customer_id is not None

    @property
    def is_action_overdue(self) -> bool:
        return bool(
            self.is_open and self.next_action_at and self.next_action_at < timezone.now()
        )

    @property
    def contact_summary(self) -> str:
        return self.email or self.phone or ""

    @property
    def days_open(self) -> int:
        end = self.converted_at if self.converted_at else timezone.now()
        if not self.created_at:
            return 0
        return max((end - self.created_at).days, 0)

    # -- validation ---------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        # A lead you cannot contact is not a lead.
        if not (self.email or "").strip() and not (self.phone or "").strip():
            message = _("Give at least an e-mail address or a phone number.")
            errors.setdefault("email", []).append(message)
            errors.setdefault("phone", []).append(message)

        if self.status == self.Status.LOST and not (self.lost_reason or "").strip():
            errors.setdefault("lost_reason", []).append(
                _("Record why the lead was lost — it is the only way to fix the funnel.")
            )

        if self.status != self.Status.LOST and (self.lost_reason or "").strip():
            errors.setdefault("lost_reason", []).append(
                _("Clear the lost reason before reopening this lead.")
            )

        if self.next_action_at and not (self.next_action or "").strip():
            errors.setdefault("next_action", []).append(
                _("Describe the action that is due at that moment.")
            )

        if self.status == self.Status.WON and self.converted_customer_id is None:
            # Non-field: ``converted_customer`` is never exposed on a form.
            errors.setdefault("__all__", []).append(
                _("Use “Convert” to win a lead — a won lead must point at a customer.")
            )

        if self.converted_customer_id is not None and self.status != self.Status.WON:
            errors.setdefault("status", []).append(
                _("This lead is already linked to a customer, so its status must stay “Won”.")
            )

        if errors:
            raise ValidationError(errors)


class Interaction(BaseModel):
    """One recorded contact with a lead or a customer."""

    class Kind(models.TextChoices):
        CALL = "call", _("Phone call")
        EMAIL = "email", _("E-mail")
        MEETING = "meeting", _("Meeting")
        MESSAGE = "message", _("Message / chat")
        VISIT = "visit", _("Visit")
        REVIEW = "review", _("Review")
        COMPLAINT = "complaint", _("Complaint")
        NOTE = "note", _("Internal note")

    class Direction(models.TextChoices):
        INBOUND = "inbound", _("Inbound")
        OUTBOUND = "outbound", _("Outbound")

    class Sentiment(models.TextChoices):
        POSITIVE = "positive", _("Positive")
        NEUTRAL = "neutral", _("Neutral")
        NEGATIVE = "negative", _("Negative")

    #: Kinds that always deserve a follow-up, whoever logged them.
    ESCALATING_KINDS = (Kind.COMPLAINT,)
    #: Kinds where a duration is meaningful.
    TIMED_KINDS = (Kind.CALL, Kind.MEETING, Kind.VISIT)

    kind = models.CharField(
        _("type"), max_length=20, choices=Kind.choices, default=Kind.CALL, db_index=True
    )
    direction = models.CharField(
        _("direction"),
        max_length=10,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
        db_index=True,
    )
    subject = models.CharField(_("subject"), max_length=200)
    body = models.TextField(_("details"), blank=True)

    # History must never be orphaned: the customer row is protected, while a
    # lead's interactions are genuine child rows and go with it.
    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("customer"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interactions",
    )
    lead = models.ForeignKey(
        Lead,
        verbose_name=_("lead"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="interactions",
    )

    occurred_at = models.DateTimeField(_("occurred at"), default=timezone.now, db_index=True)
    duration_minutes = models.PositiveIntegerField(
        _("duration (minutes)"),
        null=True,
        blank=True,
        validators=[MaxValueValidator(60 * 24)],
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("handled by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_interactions",
    )
    follow_up_required = models.BooleanField(_("follow-up required"), default=False, db_index=True)
    follow_up_at = models.DateTimeField(_("follow-up due"), null=True, blank=True, db_index=True)
    sentiment = models.CharField(
        _("sentiment"), max_length=10, choices=Sentiment.choices, blank=True
    )

    class Meta(BaseModel.Meta):
        verbose_name = _("interaction")
        verbose_name_plural = _("interactions")
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["customer", "-occurred_at"], name="crm_inter_customer_time"),
            models.Index(fields=["lead", "-occurred_at"], name="crm_inter_lead_time"),
            models.Index(
                fields=["follow_up_required", "follow_up_at"], name="crm_inter_followup"
            ),
            models.Index(fields=["kind", "-occurred_at"], name="crm_inter_kind_time"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.subject}"

    # -- derived values -----------------------------------------------------
    @property
    def contact_display(self) -> str:
        if self.customer_id:
            return str(self.customer)
        if self.lead_id:
            return self.lead.full_name
        return ""

    @property
    def is_follow_up_overdue(self) -> bool:
        return bool(
            self.follow_up_required and self.follow_up_at and self.follow_up_at < timezone.now()
        )

    @property
    def is_negative(self) -> bool:
        return self.sentiment == self.Sentiment.NEGATIVE or self.kind == self.Kind.COMPLAINT

    # -- validation ---------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.customer_id is None and self.lead_id is None:
            errors.setdefault("__all__", []).append(
                _("Attach the interaction to a customer or to a lead.")
            )

        if self.occurred_at and self.occurred_at > timezone.now() + FUTURE_TOLERANCE:
            errors.setdefault("occurred_at", []).append(
                _("An interaction cannot be logged before it happens.")
            )

        if self.follow_up_required and not self.follow_up_at:
            errors.setdefault("follow_up_at", []).append(
                _("Set the date the follow-up is due, otherwise nobody will do it.")
            )

        if self.follow_up_at and self.occurred_at and self.follow_up_at < self.occurred_at:
            errors.setdefault("follow_up_at", []).append(
                _("The follow-up cannot be due before the interaction happened.")
            )

        if self.duration_minutes and self.kind not in self.TIMED_KINDS:
            errors.setdefault("duration_minutes", []).append(
                _("A duration only applies to a call, a meeting or a visit.")
            )

        if errors:
            raise ValidationError(errors)


class Segment(BaseModel):
    """A saved, declarative customer audience.

    ``criteria`` is a small JSON document with a **fixed vocabulary**. Every key
    is checked against :data:`apps.crm.selectors.CRITERIA_SPECS` and every value
    is coerced to its declared type before a query is built, so nothing an
    operator types can reach the ORM as a lookup path.
    """

    name = models.CharField(_("name"), max_length=120, unique=True)
    description = models.TextField(_("description"), blank=True)
    criteria = models.JSONField(
        _("criteria"),
        default=dict,
        blank=True,
        help_text=_("Declarative audience filter, e.g. surf level, consent, visit recency."),
    )
    is_dynamic = models.BooleanField(
        _("dynamic"),
        default=True,
        help_text=_("Dynamic segments are re-evaluated every time they are used."),
    )
    cached_count = models.PositiveIntegerField(_("cached size"), default=0)
    last_calculated_at = models.DateTimeField(_("last calculated"), null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("segment")
        verbose_name_plural = _("segments")
        ordering = ["name"]
        indexes = [models.Index(fields=["is_dynamic", "name"], name="crm_segment_dynamic_name")]

    def __str__(self) -> str:
        return self.name

    # -- criteria -----------------------------------------------------------
    def resolve(self):
        """Return the ``customers.Customer`` queryset this segment describes.

        Never evaluates user input. Unknown or unsupported keys are ignored
        here and surfaced separately by :meth:`criteria_issues`.
        """
        from .selectors import build_customer_queryset

        return build_customer_queryset(self.criteria or {})

    def criteria_issues(self) -> list[str]:
        """Human-readable problems with the stored criteria (may be empty)."""
        from .selectors import criteria_runtime_issues

        return criteria_runtime_issues(self.criteria or {})

    def describe_criteria(self) -> list[str]:
        """One readable line per rule, for display on list and detail screens."""
        from .selectors import describe_criteria

        return describe_criteria(self.criteria or {})

    @property
    def is_stale(self) -> bool:
        """True when the cached count is missing or older than a day."""
        if not self.last_calculated_at:
            return True
        return self.last_calculated_at < timezone.now() - timezone.timedelta(days=1)

    def clean(self) -> None:
        super().clean()
        from .selectors import validate_criteria

        if self.criteria in (None, ""):
            self.criteria = {}
        if not isinstance(self.criteria, dict):
            # Non-field: ``criteria`` is assembled by the form, not typed raw.
            raise ValidationError(_("Segment criteria must be a mapping of rule name to value."))

        problems = validate_criteria(self.criteria)
        if problems:
            raise ValidationError(problems)


class Campaign(BaseModel):
    """A marketing action with a budget, an audience and a measurable result."""

    class Channel(models.TextChoices):
        EMAIL = "email", _("E-mail")
        SMS = "sms", _("SMS")
        SOCIAL = "social", _("Social media")
        PRINT = "print", _("Print")
        PARTNER = "partner", _("Partner / agency")
        EVENT = "event", _("Event")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SCHEDULED = "scheduled", _("Scheduled")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    #: Channels where a written message is actually sent to people.
    MESSAGING_CHANNELS = (Channel.EMAIL, Channel.SMS)
    #: Statuses from which a campaign may still be edited freely.
    EDITABLE_STATUSES = (Status.DRAFT, Status.SCHEDULED)
    #: Allowed status moves. Anything else is refused by the service layer.
    STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
        Status.DRAFT: (Status.SCHEDULED, Status.RUNNING, Status.CANCELLED),
        Status.SCHEDULED: (Status.RUNNING, Status.DRAFT, Status.CANCELLED),
        Status.RUNNING: (Status.COMPLETED, Status.CANCELLED),
        Status.COMPLETED: (),
        Status.CANCELLED: (),
    }

    name = models.CharField(_("name"), max_length=150)
    code = models.CharField(
        _("code"),
        max_length=30,
        unique=True,
        blank=True,
        validators=[slug_code_validator],
        help_text=_("Left blank, a code such as CMP00007 is generated."),
    )
    channel = models.CharField(
        _("channel"),
        max_length=20,
        choices=Channel.choices,
        default=Channel.EMAIL,
        db_index=True,
    )

    start_date = models.DateField(_("start date"), db_index=True)
    end_date = models.DateField(_("end date"), db_index=True)

    budget = money_field(_("budget"))
    actual_spend = money_field(_("actual spend"))

    target_segment = models.ForeignKey(
        Segment,
        verbose_name=_("target segment"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )

    message_subject = models.CharField(_("message subject"), max_length=200, blank=True)
    message_body = models.TextField(_("message body"), blank=True)

    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    sent_count = models.PositiveIntegerField(_("sent"), default=0)
    opened_count = models.PositiveIntegerField(_("opened"), default=0)
    converted_count = models.PositiveIntegerField(_("converted"), default=0)
    revenue_attributed = money_field(_("revenue attributed"))

    class Meta(BaseModel.Meta):
        verbose_name = _("campaign")
        verbose_name_plural = _("campaigns")
        ordering = ["-start_date", "-id"]
        indexes = [
            models.Index(fields=["status", "-start_date"], name="crm_campaign_status_start"),
            models.Index(fields=["channel", "status"], name="crm_campaign_channel_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="crm_campaign_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(budget__gte=0) & models.Q(actual_spend__gte=0),
                name="crm_campaign_money_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} · {self.name}" if self.code else self.name

    # -- derived values -----------------------------------------------------
    @property
    def roi(self) -> Decimal | None:
        """Return on investment as a percentage, or ``None`` when nothing was spent."""
        spend = self.actual_spend or ZERO
        if spend <= ZERO:
            return None
        revenue = self.revenue_attributed or ZERO
        return ((revenue - spend) / spend * HUNDRED).quantize(Decimal("0.01"))

    @property
    def conversion_rate(self) -> Decimal | None:
        """Converted recipients as a percentage of recipients reached."""
        if not self.sent_count:
            return None
        return (
            Decimal(self.converted_count) / Decimal(self.sent_count) * HUNDRED
        ).quantize(Decimal("0.01"))

    @property
    def open_rate(self) -> Decimal | None:
        if not self.sent_count:
            return None
        return (
            Decimal(self.opened_count) / Decimal(self.sent_count) * HUNDRED
        ).quantize(Decimal("0.01"))

    @property
    def cost_per_conversion(self) -> Decimal | None:
        if not self.converted_count:
            return None
        return ((self.actual_spend or ZERO) / Decimal(self.converted_count)).quantize(
            Decimal("0.01")
        )

    @property
    def budget_used_percent(self) -> Decimal | None:
        budget = self.budget or ZERO
        if budget <= ZERO:
            return None
        return ((self.actual_spend or ZERO) / budget * HUNDRED).quantize(Decimal("0.01"))

    @property
    def is_over_budget(self) -> bool:
        return (self.actual_spend or ZERO) > (self.budget or ZERO)

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.RUNNING

    @property
    def days_remaining(self) -> int | None:
        if self.status in (self.Status.COMPLETED, self.Status.CANCELLED):
            return None
        return (self.end_date - timezone.localdate()).days

    def allowed_next_statuses(self) -> tuple[str, ...]:
        return self.STATUS_TRANSITIONS.get(self.status, ())

    # -- persistence --------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_sequential_code(Campaign, "code", "CMP")
        super().save(*args, **kwargs)

    # -- validation ---------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors.setdefault("end_date", []).append(
                _("The end date cannot be before the start date.")
            )

        if self.opened_count > self.sent_count:
            errors.setdefault("opened_count", []).append(
                _("More opens than sends is impossible — check the figures.")
            )

        if self.converted_count > self.sent_count:
            errors.setdefault("converted_count", []).append(
                _("More conversions than sends is impossible — check the figures.")
            )

        if self.status in (self.Status.SCHEDULED, self.Status.RUNNING):
            if self.channel in self.MESSAGING_CHANNELS:
                if not (self.message_subject or "").strip():
                    errors.setdefault("message_subject", []).append(
                        _("A scheduled %(channel)s campaign needs a subject line.")
                        % {"channel": self.get_channel_display()}
                    )
                if not (self.message_body or "").strip():
                    errors.setdefault("message_body", []).append(
                        _("A scheduled %(channel)s campaign needs a message body.")
                        % {"channel": self.get_channel_display()}
                    )
                if self.target_segment_id is None:
                    errors.setdefault("target_segment", []).append(
                        _("Choose the audience before scheduling a message campaign.")
                    )

        if self.revenue_attributed and self.revenue_attributed < ZERO:
            errors.setdefault("revenue_attributed", []).append(
                _("Attributed revenue cannot be negative.")
            )

        if errors:
            raise ValidationError(errors)
