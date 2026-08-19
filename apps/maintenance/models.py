"""Maintenance data model.

Two records carry the whole workflow:

* :class:`MaintenanceRecord` — one reported problem on one piece of equipment,
  from "an instructor found a ding on return" through diagnosis and repair to
  the item going back into service, with the money it cost.
* :class:`MaintenanceSchedule` — the preventive plan for an item (every N days,
  with a check list), which the repair workflow rolls forward automatically.

Everything money-related is :func:`~apps.core.models.money_field` (Decimal), and
every status vocabulary comes from :mod:`apps.core.enums` so a "resolved"
maintenance record means the same thing as a "resolved" safety incident.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import DamageType, EquipmentCondition, GenericStatus, Severity
from apps.core.models import BaseModel, money_field
from apps.core.utils import to_decimal
from apps.core.validators import validate_image_upload

# ---------------------------------------------------------------------------
# Workflow vocabulary
# ---------------------------------------------------------------------------
#: A record in one of these statuses still needs somebody's attention.
OPEN_STATUSES: tuple[str, ...] = (
    GenericStatus.OPEN,
    GenericStatus.IN_PROGRESS,
    GenericStatus.ON_HOLD,
)

#: A record in one of these statuses is finished, for better or worse.
CLOSED_STATUSES: tuple[str, ...] = (
    GenericStatus.RESOLVED,
    GenericStatus.CLOSED,
    GenericStatus.CANCELLED,
)

#: Statuses whose costs count as realised spending.
COSTED_STATUSES: tuple[str, ...] = (GenericStatus.RESOLVED, GenericStatus.CLOSED)

#: Ordinal rank of a severity, for sorting and for the risk model.
SEVERITY_RANK: dict[str, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

#: How much each past failure contributes to the failure-history risk signal.
SEVERITY_WEIGHT: dict[str, float] = {
    Severity.LOW: 0.25,
    Severity.MEDIUM: 0.50,
    Severity.HIGH: 0.80,
    Severity.CRITICAL: 1.00,
}

#: Severities that mean "this is broken", not merely "this needs a service".
DAMAGING_SEVERITIES: tuple[str, ...] = (Severity.HIGH, Severity.CRITICAL)

#: Risk contribution of the condition grade an operator last recorded.
CONDITION_RISK: dict[str, float] = {
    EquipmentCondition.NEW: 0.00,
    EquipmentCondition.EXCELLENT: 0.05,
    EquipmentCondition.GOOD: 0.20,
    EquipmentCondition.FAIR: 0.50,
    EquipmentCondition.POOR: 0.85,
    EquipmentCondition.UNUSABLE: 1.00,
}

#: Fallback preventive interval when an item has no schedule and its category
#: offers no baseline. Derived from the drying/inspection cadence a school fleet
#: realistically sustains; used only to *report* a gap, never silently.
DEFAULT_INTERVAL_DAYS = 90

#: Below this fleet age a "no failures yet" observation says nothing useful.
MIN_HISTORY_DAYS = 60


class MaintenanceRecord(BaseModel):
    """One reported problem on one piece of equipment, and its repair."""

    record_code = models.CharField(
        _("record code"),
        max_length=20,
        unique=True,
        db_index=True,
        help_text=_("Automatically assigned, e.g. MNT00001."),
    )
    equipment = models.ForeignKey(
        "equipment.Equipment",
        verbose_name=_("equipment"),
        on_delete=models.PROTECT,
        related_name="maintenance_records",
    )

    damage_type = models.CharField(
        _("damage type"),
        max_length=20,
        choices=DamageType.choices,
        default=DamageType.GENERAL,
        db_index=True,
    )
    severity = models.CharField(
        _("severity"),
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=GenericStatus.choices,
        default=GenericStatus.OPEN,
        db_index=True,
    )

    # --- who and when -----------------------------------------------------
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reported by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_maintenance",
    )
    reported_at = models.DateTimeField(_("reported at"), default=timezone.now, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("assigned to"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_maintenance",
    )
    started_at = models.DateTimeField(_("work started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    # --- narrative --------------------------------------------------------
    description = models.TextField(
        _("problem description"),
        help_text=_("What was found, where on the item, and how it happened."),
    )
    diagnosis = models.TextField(_("diagnosis"), blank=True)
    resolution = models.TextField(_("resolution"), blank=True)
    parts_used = models.TextField(
        _("parts and materials used"),
        blank=True,
        help_text=_("One per line, e.g. 'epoxy resin 250 ml'."),
    )

    # --- cost -------------------------------------------------------------
    labour_hours = models.DecimalField(
        _("labour hours"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    parts_cost = money_field(_("parts cost"))
    labour_cost = money_field(_("labour cost"))
    total_cost = money_field(_("total cost"))

    # --- evidence ---------------------------------------------------------
    photo_before = models.ImageField(
        _("photo — before"),
        upload_to="maintenance/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_image_upload],
    )
    photo_after = models.ImageField(
        _("photo — after"),
        upload_to="maintenance/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_image_upload],
    )

    # --- provenance -------------------------------------------------------
    rental_item = models.ForeignKey(
        "rentals.RentalItem",
        verbose_name=_("found on rental return"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_records",
        help_text=_("Set when the damage was discovered as a rental came back."),
    )
    made_unusable = models.BooleanField(
        _("taken out of service"),
        default=True,
        help_text=_("Whether the item was withdrawn from rentals and lessons."),
    )

    class Meta(BaseModel.Meta):
        verbose_name = _("maintenance record")
        verbose_name_plural = _("maintenance records")
        ordering = ["-reported_at", "-id"]
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["equipment", "-reported_at"]),
            models.Index(fields=["-reported_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.record_code} · {self.get_damage_type_display()}"

    # ------------------------------------------------------------------ properties
    @property
    def is_open(self) -> bool:
        """True while the record still needs attention."""
        return self.status in OPEN_STATUSES

    @property
    def age_days(self) -> int:
        """Calendar days since the problem was reported."""
        if not self.reported_at:
            return 0
        return max(0, (timezone.now() - self.reported_at).days)

    @property
    def downtime_days(self) -> int:
        """Days the item spent out of service because of this record.

        An item that was never withdrawn (a cosmetic ding kept in the fleet)
        has zero downtime even while the record is open — use
        :attr:`age_days` for the elapsed-time question.
        """
        if not self.made_unusable or not self.reported_at:
            return 0
        end = self.completed_at or timezone.now()
        return max(0, (end - self.reported_at).days)

    @property
    def repair_days(self) -> int | None:
        """Days between starting the work and finishing it."""
        if not (self.started_at and self.completed_at):
            return None
        return max(0, (self.completed_at - self.started_at).days)

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    @property
    def rental_pk(self) -> int | None:
        """Primary key of the parent rental, when the damage came from one.

        Returned defensively so a template can link to the rental without
        assuming the rentals module's internal field layout.
        """
        if not self.rental_item_id:
            return None
        return getattr(self.rental_item, "rental_id", None)

    # ------------------------------------------------------------------ behaviour
    def recalculate_cost(self, save: bool = False) -> Decimal:
        """Recompute ``total_cost`` from parts + labour.

        Kept as a method (not a property) because the total is stored: cost
        reports aggregate it in SQL, and a repaired board's cost must not shift
        if a price list changes later.
        """
        total = to_decimal(self.parts_cost) + to_decimal(self.labour_cost)
        self.total_cost = total
        if save and self.pk:
            self.save(update_fields=["total_cost", "updated_at"])
        return total

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if self.reported_at and self.started_at and self.started_at < self.reported_at:
            errors.setdefault("started_at", []).append(
                _("Work cannot start before the problem was reported.")
            )
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            errors.setdefault("completed_at", []).append(
                _("Completion cannot be earlier than the start of the work.")
            )
        if self.reported_at and self.completed_at and self.completed_at < self.reported_at:
            errors.setdefault("completed_at", []).append(
                _("Completion cannot be earlier than the report.")
            )
        if self.status in COSTED_STATUSES and not (self.resolution or "").strip():
            errors.setdefault("resolution", []).append(
                _("Describe what was done before closing the record.")
            )
        for field in ("parts_cost", "labour_cost", "total_cost", "labour_hours"):
            value = to_decimal(getattr(self, field, None))
            if value < 0:
                errors.setdefault(field, []).append(_("This value cannot be negative."))

        # A rental item can only ever explain damage to its own equipment.
        if self.rental_item_id and self.equipment_id:
            rental_equipment_id = getattr(self.rental_item, "equipment_id", None)
            if rental_equipment_id is not None and rental_equipment_id != self.equipment_id:
                errors.setdefault("rental_item", []).append(
                    _("That rental line is for a different piece of equipment.")
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.record_code:
            from apps.core.utils import next_sequential_code

            self.record_code = next_sequential_code(
                MaintenanceRecord, "record_code", "MNT", width=5
            )
        update_fields = kwargs.get("update_fields")
        if update_fields is None:
            self.total_cost = to_decimal(self.parts_cost) + to_decimal(self.labour_cost)
        super().save(*args, **kwargs)


class MaintenanceSchedule(BaseModel):
    """The preventive-maintenance plan for one piece of equipment."""

    equipment = models.OneToOneField(
        "equipment.Equipment",
        verbose_name=_("equipment"),
        on_delete=models.CASCADE,
        related_name="maintenance_schedule",
    )
    interval_days = models.PositiveIntegerField(
        _("interval (days)"),
        default=DEFAULT_INTERVAL_DAYS,
        validators=[MinValueValidator(1)],
        help_text=_("How often the check list below must be worked through."),
    )
    last_performed_on = models.DateField(_("last performed on"), null=True, blank=True)
    next_due_on = models.DateField(_("next due on"), null=True, blank=True, db_index=True)
    check_items = models.JSONField(
        _("check list"),
        default=list,
        blank=True,
        help_text=_("The individual checks to perform, one per line."),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("maintenance schedule")
        verbose_name_plural = _("maintenance schedules")
        ordering = ["next_due_on", "id"]
        indexes = [
            models.Index(fields=["is_active", "next_due_on"]),
        ]

    def __str__(self) -> str:
        return str(_("Service plan for %(item)s") % {"item": self.equipment})

    # ------------------------------------------------------------------ properties
    @property
    def is_due(self) -> bool:
        if not self.is_active or not self.next_due_on:
            return False
        return self.next_due_on <= timezone.localdate()

    @property
    def days_until_due(self) -> int | None:
        """Days remaining; negative when overdue, ``None`` when never scheduled."""
        if not self.next_due_on:
            return None
        return (self.next_due_on - timezone.localdate()).days

    @property
    def is_overdue(self) -> bool:
        days = self.days_until_due
        return bool(self.is_active and days is not None and days < 0)

    @property
    def overdue_days(self) -> int:
        """How many days late this service is (zero when not overdue)."""
        days = self.days_until_due
        return -days if days is not None and days < 0 else 0

    @property
    def check_item_list(self) -> list[str]:
        """The check list, defensively normalised to a list of strings."""
        raw = self.check_items
        if isinstance(raw, (list, tuple)):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str) and raw.strip():
            return [line.strip() for line in raw.splitlines() if line.strip()]
        return []

    # ------------------------------------------------------------------ behaviour
    def mark_performed(self, performed_on: date | None = None, save: bool = True):
        """Record a completed service and roll the due date forward."""
        performed_on = performed_on or timezone.localdate()
        self.last_performed_on = performed_on
        self.next_due_on = performed_on + timedelta(days=max(1, int(self.interval_days or 1)))
        if save and self.pk:
            self.save(
                update_fields=["last_performed_on", "next_due_on", "updated_at"]
            )
        return self

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list] = {}

        if not self.interval_days or int(self.interval_days) < 1:
            errors.setdefault("interval_days", []).append(
                _("The interval must be at least one day.")
            )
        raw = self.check_items
        if raw not in (None, "") and not isinstance(raw, (list, tuple)):
            errors.setdefault("check_items", []).append(
                _("The check list must be a list of individual checks.")
            )
        if (
            self.last_performed_on
            and self.next_due_on
            and self.next_due_on < self.last_performed_on
        ):
            errors.setdefault("next_due_on", []).append(
                _("The next service cannot fall before the last one.")
            )
        if self.last_performed_on and self.last_performed_on > timezone.localdate():
            errors.setdefault("last_performed_on", []).append(
                _("The last service cannot be in the future.")
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if isinstance(self.check_items, str):
            self.check_items = [
                line.strip() for line in self.check_items.splitlines() if line.strip()
            ]
        elif self.check_items is None:
            self.check_items = []
        if not self.next_due_on:
            base = self.last_performed_on or timezone.localdate()
            self.next_due_on = base + timedelta(days=max(1, int(self.interval_days or 1)))
        super().save(*args, **kwargs)
