"""Safety records.

Design notes
------------
* **AI output and human decisions never share a field.** ``WeatherWarning``
  stores what a model suggested (``ai_suggested``, ``ai_rationale``, and
  ``source = AI_SUGGESTED``) apart from what a person decided
  (``acknowledged_by``, ``acknowledged_at``). ``is_authoritative`` is the single
  predicate every other module must use — an AI suggestion that nobody has
  signed off is *not* an active warning, it is a proposal on a screen.
* **Nothing is hard-deleted.** An incident, a check or a restriction stays on
  file; records are closed, cleared or deactivated instead. Safety history is
  the evidence pack an inspection asks for.
* **Validation lives in ``clean()``** and the services call ``full_clean()``, so
  the same rules apply to the HTML forms, the REST API and the admin.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import Role
from apps.core.enums import GenericStatus, Severity
from apps.core.models import BaseModel, TimeStampedModel
from apps.core.utils import next_sequential_code
from apps.core.validators import (
    phone_validator,
    validate_document_upload,
    validate_image_upload,
    validate_not_negative,
)

INCIDENT_CODE_PREFIX = "INC"
INCIDENT_CODE_WIDTH = 5

#: Severity ordered from least to most serious. Kept local rather than imported
#: so safety screens can sort without reaching into another module.
SEVERITY_RANK: dict[str, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

#: Statuses in which an incident still needs someone's attention.
OPEN_INCIDENT_STATUSES: tuple[str, ...] = (
    GenericStatus.OPEN,
    GenericStatus.IN_PROGRESS,
    GenericStatus.ON_HOLD,
)

#: Severities that may not be closed without a written corrective action.
CORRECTIVE_ACTION_REQUIRED_SEVERITIES: tuple[str, ...] = (
    Severity.HIGH,
    Severity.CRITICAL,
)


def _combine(day: date, moment) -> datetime:
    """Return an aware datetime for *day* at *moment* (a ``time``)."""
    naive = datetime.combine(day, moment)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class SafetyIncident(BaseModel):
    """Anything that went wrong, or nearly did.

    Near misses are recorded with the same weight as injuries: they are the
    cheapest safety data a school will ever collect, and the pattern they form
    is what stops the injury that follows.
    """

    class IncidentType(models.TextChoices):
        NEAR_MISS = "near_miss", _("Near miss")
        INJURY = "injury", _("Injury")
        RESCUE = "rescue", _("Rescue / assist")
        EQUIPMENT_FAILURE = "equipment_failure", _("Equipment failure")
        MEDICAL = "medical", _("Medical episode")
        LOST_PERSON = "lost_person", _("Lost or separated person")
        MARINE_LIFE = "marine_life", _("Marine life")
        WEATHER = "weather", _("Weather / conditions")
        COLLISION = "collision", _("Collision")
        OTHER = "other", _("Other")

    incident_code = models.CharField(
        _("incident code"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Generated automatically, e.g. INC00001."),
    )
    occurred_at = models.DateTimeField(_("occurred at"), db_index=True)

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
    )
    lesson = models.ForeignKey(
        "lessons.Lesson",
        verbose_name=_("lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
    )

    incident_type = models.CharField(
        _("type"),
        max_length=20,
        choices=IncidentType.choices,
        default=IncidentType.NEAR_MISS,
        db_index=True,
    )
    severity = models.CharField(
        _("severity"),
        max_length=10,
        choices=Severity.choices,
        default=Severity.LOW,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=GenericStatus.choices,
        default=GenericStatus.OPEN,
        db_index=True,
    )

    people_involved = models.ManyToManyField(
        "students.Student",
        verbose_name=_("students involved"),
        blank=True,
        related_name="incidents",
    )
    staff_involved = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("staff involved"),
        blank=True,
        related_name="safety_incidents",
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reported by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_incidents",
    )

    description = models.TextField(
        _("what happened"),
        help_text=_("Facts only: who, what, where, when. Opinions belong in the review."),
    )
    immediate_action = models.TextField(
        _("immediate action taken"),
        blank=True,
        help_text=_("What was done at the scene, in the first minutes."),
    )
    root_cause = models.TextField(
        _("root cause"), blank=True, help_text=_("Completed during the review.")
    )
    corrective_action = models.TextField(
        _("corrective action"),
        blank=True,
        help_text=_("The change that stops this happening again."),
    )

    medical_attention_required = models.BooleanField(
        _("medical attention required"), default=False, db_index=True
    )
    emergency_services_called = models.BooleanField(
        _("emergency services called"), default=False, db_index=True
    )

    conditions_at_time = models.JSONField(
        _("conditions at the time"),
        default=dict,
        blank=True,
        help_text=_(
            "Snapshot of wave height, wind, tide and water temperature as recorded "
            "when the incident happened. Never edited afterwards."
        ),
    )
    photo = models.ImageField(
        _("photo"),
        upload_to="safety/incidents/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_image_upload],
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reviewed by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_incidents",
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    follow_up_required = models.BooleanField(
        _("follow-up required"), default=False, db_index=True
    )
    follow_up_due = models.DateField(_("follow-up due"), null=True, blank=True)

    class Meta:
        verbose_name = _("safety incident")
        verbose_name_plural = _("safety incidents")
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-occurred_at"], name="saf_inc_status_date"),
            models.Index(fields=["severity", "-occurred_at"], name="saf_inc_sev_date"),
            models.Index(fields=["spot", "-occurred_at"], name="saf_inc_spot_date"),
            models.Index(
                fields=["follow_up_required", "follow_up_due"], name="saf_inc_followup"
            ),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return f"{self.incident_code} · {self.get_incident_type_display()}"

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.occurred_at and self.occurred_at > timezone.now() + timedelta(minutes=5):
            errors["occurred_at"] = _("An incident cannot be recorded in the future.")

        if self.follow_up_required and not self.follow_up_due:
            errors["follow_up_due"] = _(
                "Give the follow-up a due date, otherwise nobody owns it."
            )
        if self.follow_up_due and not self.follow_up_required:
            errors["follow_up_required"] = _(
                "A follow-up date was entered — tick “follow-up required”."
            )
        if (
            self.follow_up_due
            and self.occurred_at
            and self.follow_up_due < timezone.localtime(self.occurred_at).date()
        ):
            errors["follow_up_due"] = _("The follow-up cannot fall before the incident.")

        if self.reviewed_at and not self.reviewed_by_id:
            errors["reviewed_by"] = _("A review must name the member of staff who did it.")
        if self.reviewed_by_id and not self.reviewed_at:
            errors["reviewed_at"] = _("Record when the review took place.")
        if self.reviewed_at and self.occurred_at and self.reviewed_at < self.occurred_at:
            errors["reviewed_at"] = _("The review cannot predate the incident.")

        closing = self.status in (GenericStatus.RESOLVED, GenericStatus.CLOSED)
        if closing and self.severity in CORRECTIVE_ACTION_REQUIRED_SEVERITIES:
            if not (self.corrective_action or "").strip():
                errors["corrective_action"] = _(
                    "A high or critical incident cannot be closed without a written "
                    "corrective action."
                )
            if not self.reviewed_by_id:
                errors["reviewed_by"] = _(
                    "A high or critical incident must be reviewed by a named member "
                    "of staff before it is closed."
                )

        if self.emergency_services_called and not (self.immediate_action or "").strip():
            errors["immediate_action"] = _(
                "Emergency services were called — record what was done at the scene."
            )

        if self.conditions_at_time is not None and not isinstance(
            self.conditions_at_time, dict
        ):
            errors["conditions_at_time"] = _("Conditions must be recorded as a mapping.")

        if errors:
            raise ValidationError(errors)

    # -- persistence -------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.incident_code:
            self.incident_code = next_sequential_code(
                type(self),
                "incident_code",
                INCIDENT_CODE_PREFIX,
                width=INCIDENT_CODE_WIDTH,
            )
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = sorted(set(update_fields) | {"incident_code"})
        return super().save(*args, **kwargs)

    # -- derived values ----------------------------------------------------
    @property
    def is_open(self) -> bool:
        """Does this incident still need someone to act on it?"""
        return self.status in OPEN_INCIDENT_STATUSES

    @property
    def days_open(self) -> int:
        """Whole days between the incident and its closure (or now, if open)."""
        if not self.occurred_at:
            return 0
        end = timezone.now() if self.is_open else (self.reviewed_at or self.updated_at)
        if end is None:
            end = timezone.now()
        return max(0, (end - self.occurred_at).days)

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    @property
    def is_serious(self) -> bool:
        return self.severity in CORRECTIVE_ACTION_REQUIRED_SEVERITIES

    @property
    def is_reviewed(self) -> bool:
        return bool(self.reviewed_by_id and self.reviewed_at)

    @property
    def is_follow_up_overdue(self) -> bool:
        return bool(
            self.follow_up_required
            and self.follow_up_due
            and self.is_open
            and self.follow_up_due < timezone.localdate()
        )

    @property
    def conditions_summary(self) -> list[tuple[str, object]]:
        """``conditions_at_time`` as ordered label/value pairs for templates."""
        if not isinstance(self.conditions_at_time, dict):
            return []
        return [
            (str(key).replace("_", " ").capitalize(), value)
            for key, value in self.conditions_at_time.items()
            if value not in (None, "")
        ]


# ---------------------------------------------------------------------------
# Lifeguard cover
# ---------------------------------------------------------------------------
class LifeguardAssignment(BaseModel):
    """One lifeguard, one spot, one shift.

    Cover is the difference between "someone might see it" and "someone is
    watching", so a shift is only counted once it has been confirmed.
    """

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.PROTECT,
        related_name="lifeguard_assignments",
    )
    lifeguard = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("lifeguard"),
        on_delete=models.PROTECT,
        related_name="lifeguard_shifts",
    )
    date = models.DateField(_("date"), db_index=True)
    start_time = models.TimeField(_("start"))
    end_time = models.TimeField(_("end"))
    is_confirmed = models.BooleanField(
        _("confirmed"),
        default=False,
        db_index=True,
        help_text=_("Only confirmed shifts count as cover."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("lifeguard assignment")
        verbose_name_plural = _("lifeguard assignments")
        ordering = ["date", "start_time", "id"]
        indexes = [
            models.Index(fields=["date", "spot"], name="saf_lg_date_spot"),
            models.Index(fields=["lifeguard", "date"], name="saf_lg_person_date"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["lifeguard", "date", "start_time"],
                name="saf_lifeguard_shift_unique",
            ),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        who = self.lifeguard.get_display_name() if self.lifeguard_id else "—"
        return f"{who} · {self.date:%d.%m.%Y} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = _("The shift must end after it starts.")

        # One person cannot watch two beaches at once. The unique constraint only
        # catches an identical start time; this catches a genuine overlap.
        if self.lifeguard_id and self.date and self.start_time and self.end_time:
            clash = (
                type(self)
                .objects.filter(
                    lifeguard_id=self.lifeguard_id,
                    date=self.date,
                    start_time__lt=self.end_time,
                    end_time__gt=self.start_time,
                )
                .exclude(pk=self.pk)
                .first()
            )
            if clash is not None:
                errors["start_time"] = _(
                    "This lifeguard already covers %(start)s–%(end)s at %(spot)s on this day."
                ) % {
                    "start": clash.start_time.strftime("%H:%M"),
                    "end": clash.end_time.strftime("%H:%M"),
                    "spot": clash.spot.name if clash.spot_id else "—",
                }

        if errors:
            raise ValidationError(errors)

    # -- derived values ----------------------------------------------------
    @property
    def duration_minutes(self) -> int:
        if not (self.start_time and self.end_time):
            return 0
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        return max(0, end - start)

    @property
    def starts_at(self) -> datetime | None:
        return _combine(self.date, self.start_time) if self.date and self.start_time else None

    @property
    def ends_at(self) -> datetime | None:
        return _combine(self.date, self.end_time) if self.date and self.end_time else None

    def covers(self, moment: datetime | None = None) -> bool:
        """Is this shift live at *moment* (defaults to now)?"""
        moment = moment or timezone.now()
        start, end = self.starts_at, self.ends_at
        if start is None or end is None:
            return False
        return start <= moment <= end

    @property
    def is_current(self) -> bool:
        return self.is_confirmed and self.covers()


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------
class EmergencyContact(TimeStampedModel):
    """A number somebody dials while holding a casualty's head above water.

    ``spot`` left empty means the contact applies everywhere; a spot-specific
    contact (the harbour master, the beach clinic) sits above the general ones
    on that spot's card.
    """

    class Kind(models.TextChoices):
        AMBULANCE = "ambulance", _("Ambulance")
        COASTGUARD = "coastguard", _("Coastguard / sea rescue")
        POLICE = "police", _("Police")
        FIRE = "fire", _("Fire service")
        HOSPITAL = "hospital", _("Hospital")
        DOCTOR = "doctor", _("Doctor / clinic")
        SCHOOL_MANAGER = "school_manager", _("School manager")
        OTHER = "other", _("Other")

    name = models.CharField(_("name"), max_length=120)
    organisation = models.CharField(_("organisation"), max_length=150, blank=True)
    kind = models.CharField(
        _("kind"), max_length=20, choices=Kind.choices, default=Kind.OTHER, db_index=True
    )
    phone = models.CharField(_("phone"), max_length=25, validators=[phone_validator])
    alternate_phone = models.CharField(
        _("alternate phone"), max_length=25, blank=True, validators=[phone_validator]
    )
    address = models.CharField(_("address"), max_length=250, blank=True)
    notes = models.TextField(
        _("notes"),
        blank=True,
        help_text=_("Languages spoken, opening hours, where the ambulance meets you."),
    )
    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="emergency_contacts",
        help_text=_("Leave empty when the contact applies to every spot."),
    )
    sort_order = models.PositiveSmallIntegerField(
        _("sort order"), default=0, help_text=_("Lower numbers appear first on the card.")
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("emergency contact")
        verbose_name_plural = _("emergency contacts")
        ordering = ["sort_order", "kind", "name"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="saf_contact_active_order"),
            models.Index(fields=["spot", "is_active"], name="saf_contact_spot_active"),
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.phone}"

    def clean(self) -> None:
        super().clean()
        if self.alternate_phone and self.alternate_phone == self.phone:
            raise ValidationError(
                {"alternate_phone": _("The alternate number repeats the main number.")}
            )

    @property
    def applies_everywhere(self) -> bool:
        return self.spot_id is None

    @property
    def scope_label(self) -> str:
        return str(_("All spots")) if self.applies_everywhere else self.spot.name


# ---------------------------------------------------------------------------
# Evacuation plans
# ---------------------------------------------------------------------------
class EvacuationPlan(BaseModel):
    """What everyone does when the beach has to be cleared.

    ``steps`` is an ordered list of plain strings — deliberately not a related
    table, because the plan is read aloud in sequence and is only ever edited as
    a whole.
    """

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.PROTECT,
        related_name="evacuation_plans",
    )
    title = models.CharField(_("title"), max_length=150)
    trigger_conditions = models.TextField(
        _("trigger conditions"),
        help_text=_("What starts this plan: red flag, lightning, missing person, tsunami siren."),
    )
    assembly_point = models.CharField(
        _("assembly point"),
        max_length=200,
        help_text=_("Where everyone is counted. Name a place a stranger could find."),
    )
    steps = models.JSONField(
        _("steps"),
        default=list,
        blank=True,
        help_text=_("Ordered list of actions, one line each."),
    )
    responsible_role = models.CharField(
        _("responsible role"),
        max_length=32,
        choices=Role.choices,
        default=Role.HEAD_INSTRUCTOR,
        help_text=_("The role that runs the plan — a role, never a single person."),
    )
    document = models.FileField(
        _("document"),
        upload_to="safety/evacuation/%Y/",
        null=True,
        blank=True,
        validators=[validate_document_upload],
    )
    last_drill_date = models.DateField(_("last drill"), null=True, blank=True)
    next_drill_due = models.DateField(_("next drill due"), null=True, blank=True, db_index=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("evacuation plan")
        verbose_name_plural = _("evacuation plans")
        ordering = ["spot__name", "title"]
        indexes = [
            models.Index(fields=["spot", "is_active"], name="saf_plan_spot_active"),
            models.Index(fields=["is_active", "next_drill_due"], name="saf_plan_drill_due"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.steps is None:
            self.steps = []
        if not isinstance(self.steps, list):
            errors["steps"] = _("Steps must be an ordered list of lines.")
        else:
            cleaned = [str(step).strip() for step in self.steps if str(step).strip()]
            if not cleaned:
                errors["steps"] = _("A plan with no steps is not a plan. Add at least one.")
            else:
                self.steps = cleaned

        if (
            self.last_drill_date
            and self.next_drill_due
            and self.next_drill_due < self.last_drill_date
        ):
            errors["next_drill_due"] = _("The next drill cannot fall before the last one.")

        if self.last_drill_date and self.last_drill_date > timezone.localdate():
            errors["last_drill_date"] = _("A drill cannot have happened in the future.")

        if errors:
            raise ValidationError(errors)

    # -- derived values ----------------------------------------------------
    @property
    def step_list(self) -> list[str]:
        if not isinstance(self.steps, list):
            return []
        return [str(step) for step in self.steps if str(step).strip()]

    @property
    def step_count(self) -> int:
        return len(self.step_list)

    @property
    def is_drill_overdue(self) -> bool:
        return bool(
            self.is_active and self.next_drill_due and self.next_drill_due < timezone.localdate()
        )

    @property
    def days_until_drill(self) -> int | None:
        if not self.next_drill_due:
            return None
        return (self.next_drill_due - timezone.localdate()).days

    @property
    def has_never_been_drilled(self) -> bool:
        return self.is_active and self.last_drill_date is None


# ---------------------------------------------------------------------------
# Equipment safety checks
# ---------------------------------------------------------------------------
class EquipmentSafetyCheck(BaseModel):
    """A dated pass/fail inspection of one item.

    This is the safety record, not the maintenance job: a failed check states
    what was wrong and what was done about it, and the maintenance module owns
    the repair itself.
    """

    equipment = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("equipment"),
        on_delete=models.PROTECT,
        related_name="safety_checks",
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("checked by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="equipment_safety_checks",
    )
    checked_at = models.DateTimeField(_("checked at"), default=timezone.now, db_index=True)
    passed = models.BooleanField(_("passed"), default=True, db_index=True)
    checklist = models.JSONField(
        _("checklist"),
        default=dict,
        blank=True,
        help_text=_("Mapping of checklist item to pass/fail, e.g. {\"Leash\": true}."),
    )
    issues_found = models.TextField(_("issues found"), blank=True)
    action_taken = models.TextField(_("action taken"), blank=True)
    next_check_due = models.DateField(_("next check due"), null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = _("equipment safety check")
        verbose_name_plural = _("equipment safety checks")
        ordering = ["-checked_at", "-id"]
        indexes = [
            models.Index(fields=["equipment", "-checked_at"], name="saf_chk_item_date"),
            models.Index(fields=["passed", "next_check_due"], name="saf_chk_pass_due"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        outcome = _("passed") if self.passed else _("failed")
        return f"{self.equipment} · {timezone.localtime(self.checked_at):%d.%m.%Y} · {outcome}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.checked_at and self.checked_at > timezone.now() + timedelta(minutes=5):
            errors["checked_at"] = _("A check cannot be dated in the future.")

        if self.checklist is None:
            self.checklist = {}
        if not isinstance(self.checklist, dict):
            errors["checklist"] = _("The checklist must map each item to pass or fail.")
        elif any(not isinstance(value, bool) for value in self.checklist.values()):
            errors["checklist"] = _("Every checklist item must be either passed or failed.")

        if not self.passed and not (self.issues_found or "").strip():
            errors["issues_found"] = _("A failed check must say what is wrong with the item.")

        if isinstance(self.checklist, dict) and self.checklist:
            any_failed = any(value is False for value in self.checklist.values())
            if any_failed and self.passed:
                errors["passed"] = _(
                    "A checklist item is marked as failed — the check cannot be a pass."
                )

        if (
            self.next_check_due
            and self.checked_at
            and self.next_check_due < timezone.localtime(self.checked_at).date()
        ):
            errors["next_check_due"] = _("The next check cannot fall before this one.")

        if errors:
            raise ValidationError(errors)

    # -- derived values ----------------------------------------------------
    @property
    def failed_items(self) -> list[str]:
        if not isinstance(self.checklist, dict):
            return []
        return sorted(key for key, value in self.checklist.items() if value is False)

    @property
    def passed_items(self) -> list[str]:
        if not isinstance(self.checklist, dict):
            return []
        return sorted(key for key, value in self.checklist.items() if value is True)

    @property
    def is_overdue(self) -> bool:
        return bool(self.next_check_due and self.next_check_due < timezone.localdate())

    @property
    def days_overdue(self) -> int:
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.next_check_due).days


# ---------------------------------------------------------------------------
# Weather warnings — where AI and human decisions are kept apart
# ---------------------------------------------------------------------------
class WeatherWarning(BaseModel):
    """A warning about the conditions at one spot, or at every spot.

    Three sources, one rule
    -----------------------
    ``MANUAL`` — a person typed it; authoritative immediately.
    ``PROVIDER`` — a met office / forecast feed; authoritative immediately.
    ``AI_SUGGESTED`` — a model proposed it; **not** authoritative until a named
    member of staff acknowledges it. Until then it renders as
    "AI Recommendation — awaiting staff confirmation" and every other module
    must ignore it when asking "is there a warning here?".
    """

    class Source(models.TextChoices):
        MANUAL = "manual", _("Entered by staff")
        PROVIDER = "provider", _("Weather provider")
        AI_SUGGESTED = "ai_suggested", _("AI suggestion")

    spot = models.ForeignKey(
        "locations.SurfSpot",
        verbose_name=_("surf spot"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weather_warnings",
        help_text=_("Leave empty when the warning covers every spot."),
    )
    title = models.CharField(_("title"), max_length=150)
    severity = models.CharField(
        _("severity"),
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    source = models.CharField(
        _("source"), max_length=20, choices=Source.choices, default=Source.MANUAL, db_index=True
    )
    description = models.TextField(_("description"), blank=True)
    starts_at = models.DateTimeField(_("starts at"), db_index=True)
    ends_at = models.DateTimeField(_("ends at"))
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    ai_suggested = models.BooleanField(
        _("suggested by AI"),
        default=False,
        db_index=True,
        help_text=_("An AI suggestion is never an active warning until staff confirm it."),
    )
    ai_rationale = models.TextField(
        _("AI rationale"),
        blank=True,
        help_text=_("The model's reasoning, shown to the person who signs it off."),
    )

    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("confirmed by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_warnings",
    )
    acknowledged_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("weather warning")
        verbose_name_plural = _("weather warnings")
        ordering = ["-starts_at", "-id"]
        indexes = [
            models.Index(fields=["is_active", "-starts_at"], name="saf_warn_active_start"),
            models.Index(fields=["spot", "is_active"], name="saf_warn_spot_active"),
            models.Index(fields=["ai_suggested", "is_active"], name="saf_warn_ai_active"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return self.title

    # -- validation --------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = _("The warning must end after it starts.")

        # The flag and the source describe the same fact; they may never disagree.
        if self.source == self.Source.AI_SUGGESTED and not self.ai_suggested:
            self.ai_suggested = True
        if self.ai_suggested and self.source != self.Source.AI_SUGGESTED:
            self.source = self.Source.AI_SUGGESTED

        if self.ai_suggested and not (self.ai_rationale or "").strip():
            errors["ai_rationale"] = _(
                "An AI suggestion must carry the reasoning a person is asked to judge."
            )

        if self.acknowledged_at and not self.acknowledged_by_id:
            errors["acknowledged_by"] = _("A confirmation must name the member of staff.")
        if self.acknowledged_by_id and not self.acknowledged_at:
            errors["acknowledged_at"] = _("Record when the warning was confirmed.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Belt and braces: keep the pair consistent even on paths that skip
        # full_clean(), because everything downstream trusts these two fields.
        if self.source == self.Source.AI_SUGGESTED:
            self.ai_suggested = True
        elif self.ai_suggested:
            self.source = self.Source.AI_SUGGESTED
        return super().save(*args, **kwargs)

    # -- derived values ----------------------------------------------------
    @property
    def is_authoritative(self) -> bool:
        """May other modules treat this as a real, active warning?

        An AI suggestion counts only once a named member of staff has signed it
        off. This property is the contract the rest of the system reads.
        """
        return self.is_active and (not self.ai_suggested or self.acknowledged_by_id is not None)

    @property
    def awaiting_confirmation(self) -> bool:
        return self.is_active and self.ai_suggested and self.acknowledged_by_id is None

    @property
    def is_current(self) -> bool:
        """Is the warning window open right now?"""
        now = timezone.now()
        if not (self.starts_at and self.ends_at):
            return False
        return self.starts_at <= now <= self.ends_at

    @property
    def is_in_force(self) -> bool:
        """Authoritative *and* inside its window — the operational test."""
        return self.is_authoritative and self.is_current

    @property
    def display_title(self) -> str:
        """Title as it must appear on screen, never hiding an unconfirmed AI call."""
        if self.awaiting_confirmation:
            return str(
                _("AI Recommendation — awaiting staff confirmation: %(title)s")
                % {"title": self.title}
            )
        return self.title

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    @property
    def is_blocking(self) -> bool:
        """A high or critical warning in force stops the school entering the water."""
        return self.is_in_force and self.severity in (Severity.HIGH, Severity.CRITICAL)

    @property
    def scope_label(self) -> str:
        return str(_("All spots")) if self.spot_id is None else self.spot.name


# ---------------------------------------------------------------------------
# Student restrictions
# ---------------------------------------------------------------------------
class StudentRestriction(BaseModel):
    """A limit that travels with one student.

    A restriction outlives the reason for it (a healed shoulder, a passed swim
    test), so it is deactivated or given an end date rather than deleted — the
    school must be able to show why a student was held back last August.
    """

    class RestrictionType(models.TextChoices):
        MEDICAL = "medical", _("Medical")
        SKILL = "skill", _("Skill / competence")
        BEHAVIOUR = "behaviour", _("Behaviour")
        AGE = "age", _("Age")
        EQUIPMENT = "equipment", _("Equipment")
        TEMPORARY = "temporary", _("Temporary")

    student = models.ForeignKey(
        "students.Student",
        verbose_name=_("student"),
        on_delete=models.PROTECT,
        related_name="restrictions",
    )
    restriction_type = models.CharField(
        _("type"),
        max_length=20,
        choices=RestrictionType.choices,
        default=RestrictionType.MEDICAL,
        db_index=True,
    )
    description = models.TextField(
        _("description"),
        help_text=_("What the limit is and why, in words the coach on the beach can act on."),
    )
    max_wave_height_m = models.FloatField(
        _("maximum wave height (m)"),
        null=True,
        blank=True,
        validators=[validate_not_negative],
        help_text=_("Leave empty when wave height is not the limiting factor."),
    )
    max_wind_kmh = models.FloatField(
        _("maximum wind (km/h)"),
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )
    requires_supervision = models.BooleanField(
        _("requires close supervision"),
        default=False,
        help_text=_("The student stays within arm's reach of an instructor."),
    )
    cannot_surf = models.BooleanField(
        _("cannot surf"),
        default=False,
        db_index=True,
        help_text=_("An absolute stop. Overrides every threshold below."),
    )
    starts_on = models.DateField(_("starts on"), default=timezone.localdate, db_index=True)
    ends_on = models.DateField(_("ends on"), null=True, blank=True, db_index=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("issued by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_restrictions",
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("student restriction")
        verbose_name_plural = _("student restrictions")
        ordering = ["-starts_on", "-id"]
        indexes = [
            models.Index(fields=["student", "is_active"], name="saf_restr_student_active"),
            models.Index(fields=["is_active", "starts_on", "ends_on"], name="saf_restr_window"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return f"{self.get_restriction_type_display()} · {self.student}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, object] = {}

        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            errors["ends_on"] = _("The end date cannot fall before the start date.")

        if self.restriction_type == self.RestrictionType.TEMPORARY and not self.ends_on:
            errors["ends_on"] = _("A temporary restriction needs an end date.")

        if self.cannot_surf and (
            self.max_wave_height_m is not None or self.max_wind_kmh is not None
        ):
            errors["cannot_surf"] = _(
                "“Cannot surf” is absolute — remove the wave and wind limits, they "
                "would only create the impression of an exception."
            )

        if not self.cannot_surf and not self.requires_supervision:
            if self.max_wave_height_m is None and self.max_wind_kmh is None:
                errors["description"] = _(
                    "This restriction limits nothing. Set a wave or wind limit, tick "
                    "supervision, or mark the student as unable to surf."
                )

        if errors:
            raise ValidationError(errors)

    # -- derived values ----------------------------------------------------
    @property
    def is_current(self) -> bool:
        """Is this restriction in force today?"""
        if not self.is_active or not self.starts_on:
            return False
        today = timezone.localdate()
        if self.starts_on > today:
            return False
        return self.ends_on is None or self.ends_on >= today

    @property
    def is_permanent(self) -> bool:
        return self.is_active and self.ends_on is None

    @property
    def days_remaining(self) -> int | None:
        if not self.ends_on:
            return None
        return (self.ends_on - timezone.localdate()).days

    @property
    def limit_summary(self) -> list[str]:
        """Human-readable list of what this restriction actually forbids."""
        parts: list[str] = []
        if self.cannot_surf:
            parts.append(str(_("Must not enter the water")))
        if self.requires_supervision:
            parts.append(str(_("Close supervision required")))
        if self.max_wave_height_m is not None:
            parts.append(
                str(_("Waves up to %(value).1f m") % {"value": self.max_wave_height_m})
            )
        if self.max_wind_kmh is not None:
            parts.append(str(_("Wind up to %(value).0f km/h") % {"value": self.max_wind_kmh}))
        return parts
