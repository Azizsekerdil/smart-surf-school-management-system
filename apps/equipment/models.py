"""Equipment inventory models.

Design notes
------------
* **One row per physical item.** A school does not own "12 softboards", it owns
  twelve boards each with its own dings, its own repair history and its own QR
  label. Availability, liability and depreciation are all per-item questions.
* ``asset_code`` (``EQ00001``) is the human-readable identifier printed on the
  label; ``public_id`` (UUID, from :class:`~apps.core.models.BaseModel`) is what
  the QR code actually encodes, so scanning a label never leaks how many items
  the school owns.
* Status vocabulary comes from :mod:`apps.core.enums` — never redefined here, so
  "available" means the same thing in rentals, lessons and maintenance.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.enums import EquipmentCondition, EquipmentStatus, SurfLevel, level_rank
from apps.core.models import BaseModel, TimeStampedModel, money_field
from apps.core.utils import make_qr_svg, next_sequential_code
from apps.core.validators import validate_image_upload, validate_not_negative

#: Asset codes are a single global series: a scanned code identifies an item
#: without the scanner needing to know which category it belongs to.
ASSET_CODE_PREFIX = "EQ"
ASSET_CODE_WIDTH = 5

#: Prefix of the string encoded in every equipment QR label.
QR_PREFIX = "SURF:EQ:"

#: Hours a day an item can realistically be on the water. Used as the
#: denominator of the utilisation figure so 100 % means "out every operating
#: hour since the day it was bought", not "out 24/7".
OPERATING_HOURS_PER_DAY = Decimal("8")


class EquipmentCategory(TimeStampedModel):
    """A node in the equipment taxonomy (Surfboard › Softboard, Wetsuit, …).

    Kept as data rather than an enum because every school stocks a different
    mix, and the tree is shallow enough that a self-FK beats anything cleverer.
    """

    code = models.SlugField(
        _("code"),
        max_length=40,
        unique=True,
        help_text=_("Stable identifier used by imports and integrations, e.g. softboard."),
    )
    name = models.CharField(_("name"), max_length=100)
    parent = models.ForeignKey(
        "self",
        verbose_name=_("parent category"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    icon = models.CharField(
        _("icon"),
        max_length=40,
        default="package",
        help_text=_("Name of a vendored Lucide icon, e.g. waves."),
    )
    sort_order = models.PositiveIntegerField(_("sort order"), default=100)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("equipment category")
        verbose_name_plural = _("equipment categories")
        ordering = ["sort_order", "name"]
        indexes = [models.Index(fields=["parent", "sort_order"])]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """Reject a parent chain that loops back onto this category."""
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": _("A category cannot be its own parent.")})
        seen: set[int] = {self.pk} if self.pk else set()
        node = self.parent
        while node is not None:
            if node.pk in seen:
                raise ValidationError({"parent": _("This parent would create a loop.")})
            seen.add(node.pk)
            node = node.parent

    @property
    def full_path(self) -> str:
        """``"Surfboard › Softboard"`` — the whole ancestor chain."""
        parts = [self.name]
        node = self.parent
        guard = 0
        while node is not None and guard < 10:
            parts.append(node.name)
            node = node.parent
            guard += 1
        return " › ".join(reversed(parts))

    @property
    def descendant_ids(self) -> list[int]:
        """This category's id plus every id below it (depth-limited)."""
        ids = [self.pk]
        frontier = [self.pk]
        guard = 0
        while frontier and guard < 10:
            frontier = list(
                EquipmentCategory.objects.filter(parent_id__in=frontier).values_list(
                    "pk", flat=True
                )
            )
            ids.extend(frontier)
            guard += 1
        return ids


class Equipment(BaseModel):
    """One physical item of equipment."""

    asset_code = models.CharField(
        _("asset code"),
        max_length=20,
        unique=True,
        blank=True,
        help_text=_("Printed on the QR label. Generated automatically when left blank."),
    )
    category = models.ForeignKey(
        EquipmentCategory,
        verbose_name=_("category"),
        on_delete=models.PROTECT,
        related_name="items",
    )

    # --- identity ---------------------------------------------------------
    name = models.CharField(_("name"), max_length=120)
    brand = models.CharField(_("brand"), max_length=80, blank=True)
    model = models.CharField(_("model"), max_length=80, blank=True)
    serial_number = models.CharField(
        _("serial number"), max_length=80, blank=True, db_index=True
    )

    # --- dimensions -------------------------------------------------------
    size_label = models.CharField(
        _("size"),
        max_length=20,
        blank=True,
        help_text=_("As printed on the item, e.g. 6'2\" for a board or M for a wetsuit."),
    )
    length_cm = models.DecimalField(
        _("length (cm)"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )
    width_cm = models.DecimalField(
        _("width (cm)"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )
    thickness_cm = models.DecimalField(
        _("thickness (cm)"),
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )
    volume_litres = models.DecimalField(
        _("volume (L)"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[validate_not_negative],
        help_text=_("Boards only. Drives the board-size recommendation."),
    )
    wetsuit_thickness = models.CharField(
        _("wetsuit thickness"),
        max_length=10,
        blank=True,
        help_text=_("Torso/limb millimetres, e.g. 4/3."),
    )

    # --- suitability ------------------------------------------------------
    suitable_min_level = models.CharField(
        _("suitable from level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.FIRST_TIME,
        db_index=True,
    )
    suitable_max_level = models.CharField(
        _("suitable up to level"),
        max_length=20,
        choices=SurfLevel.choices,
        default=SurfLevel.COMPETITION,
    )
    min_rider_weight_kg = models.DecimalField(
        _("minimum rider weight (kg)"),
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )
    max_rider_weight_kg = models.DecimalField(
        _("maximum rider weight (kg)"),
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[validate_not_negative],
    )

    # --- purchase & value -------------------------------------------------
    purchase_date = models.DateField(_("purchase date"), null=True, blank=True)
    purchase_price = money_field(_("purchase price"))
    current_value = money_field(_("current value"))
    supplier = models.CharField(_("supplier"), max_length=120, blank=True)

    # --- state ------------------------------------------------------------
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=EquipmentStatus.choices,
        default=EquipmentStatus.AVAILABLE,
        db_index=True,
    )
    condition = models.CharField(
        _("condition"),
        max_length=20,
        choices=EquipmentCondition.choices,
        default=EquipmentCondition.GOOD,
        db_index=True,
    )
    storage_location = models.CharField(
        _("storage location"),
        max_length=120,
        blank=True,
        help_text=_("Where staff physically find it, e.g. Container A / Rack 3."),
    )

    # --- commercial -------------------------------------------------------
    is_rentable = models.BooleanField(
        _("rentable"),
        default=False,
        help_text=_("Customers may rent this item. Requires at least one rental price."),
    )
    is_lesson_stock = models.BooleanField(
        _("lesson stock"),
        default=True,
        help_text=_("May be handed to students during a lesson."),
    )
    rental_price_hourly = money_field(_("rental price / hour"))
    rental_price_daily = money_field(_("rental price / day"))
    rental_price_weekly = money_field(_("rental price / week"))
    deposit_amount = money_field(_("deposit"))

    # --- usage counters ---------------------------------------------------
    total_rentals = models.PositiveIntegerField(_("total rentals"), default=0)
    total_rental_hours = models.DecimalField(
        _("total rental hours"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_not_negative],
    )

    # --- maintenance ------------------------------------------------------
    last_maintenance_date = models.DateField(_("last service"), null=True, blank=True)
    next_maintenance_date = models.DateField(
        _("next service due"), null=True, blank=True, db_index=True
    )

    # --- lifecycle --------------------------------------------------------
    notes = models.TextField(_("notes"), blank=True)
    retired_at = models.DateTimeField(_("retired at"), null=True, blank=True)
    retired_reason = models.CharField(_("retirement reason"), max_length=250, blank=True)

    class Meta:
        verbose_name = _("equipment item")
        verbose_name_plural = _("equipment")
        ordering = ["asset_code"]
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["status", "category"], name="equip_status_category_idx"),
            models.Index(fields=["is_rentable", "status"], name="equip_rentable_status_idx"),
            models.Index(fields=["next_maintenance_date"], name="equip_next_service_idx"),
        ]

    def __str__(self) -> str:
        label = self.name
        if self.size_label:
            label = f"{label} {self.size_label}"
        return f"{self.asset_code} · {label}" if self.asset_code else label

    # ------------------------------------------------------------------ save
    def save(self, *args, **kwargs):
        if not self.asset_code:
            self.asset_code = next_sequential_code(
                Equipment, "asset_code", ASSET_CODE_PREFIX, width=ASSET_CODE_WIDTH
            )
        super().save(*args, **kwargs)

    def clean(self) -> None:
        """Cross-field rules that must hold for every item, however it was created."""
        errors: dict[str, object] = {}

        if level_rank(self.suitable_min_level) > level_rank(self.suitable_max_level):
            errors["suitable_max_level"] = _(
                "The maximum level cannot be below the minimum level."
            )

        if (
            self.min_rider_weight_kg is not None
            and self.max_rider_weight_kg is not None
            and self.min_rider_weight_kg > self.max_rider_weight_kg
        ):
            errors["max_rider_weight_kg"] = _(
                "The maximum rider weight cannot be below the minimum."
            )

        if self.is_rentable:
            prices = (
                self.rental_price_hourly or Decimal("0.00"),
                self.rental_price_daily or Decimal("0.00"),
                self.rental_price_weekly or Decimal("0.00"),
            )
            if not any(price > 0 for price in prices):
                errors["is_rentable"] = _(
                    "A rentable item needs an hourly, daily or weekly price — "
                    "otherwise it would be handed out for free."
                )

        if self.condition == EquipmentCondition.UNUSABLE and self.status in (
            EquipmentStatus.AVAILABLE,
            EquipmentStatus.RESERVED,
            EquipmentStatus.RENTED,
            EquipmentStatus.IN_LESSON,
        ):
            errors["status"] = _(
                "An unusable item cannot be in circulation. Send it to maintenance "
                "or retire it."
            )

        if self.status == EquipmentStatus.RETIRED and not (self.retired_reason or "").strip():
            errors["retired_reason"] = _("Record why the item was retired.")

        if self.retired_at and self.status != EquipmentStatus.RETIRED:
            errors["status"] = _(
                "This item carries a retirement date, so its status must be “Retired”."
            )

        if (
            self.last_maintenance_date
            and self.next_maintenance_date
            and self.next_maintenance_date < self.last_maintenance_date
        ):
            errors["next_maintenance_date"] = _(
                "The next service cannot be due before the last one happened."
            )

        if errors:
            raise ValidationError(errors)

    # -------------------------------------------------------------- properties
    @property
    def is_available(self) -> bool:
        """True when the item may be handed to a person right now."""
        return (
            self.status == EquipmentStatus.AVAILABLE
            and not self.is_deleted
            and self.retired_at is None
            and self.condition != EquipmentCondition.UNUSABLE
        )

    @property
    def needs_maintenance(self) -> bool:
        """True when the item is overdue for service or visibly worn out."""
        if self.condition in (EquipmentCondition.POOR, EquipmentCondition.UNUSABLE):
            return True
        if self.status in (EquipmentStatus.DAMAGED, EquipmentStatus.MAINTENANCE):
            return True
        return bool(
            self.next_maintenance_date and self.next_maintenance_date <= timezone.localdate()
        )

    @property
    def age_days(self) -> int | None:
        """Days since purchase, or ``None`` when the purchase date is unknown."""
        if not self.purchase_date:
            return None
        return max((timezone.localdate() - self.purchase_date).days, 0)

    @property
    def depreciation_percent(self) -> Decimal | None:
        """How much of the purchase price has been written off."""
        purchase = self.purchase_price or Decimal("0.00")
        if purchase <= 0:
            return None
        lost = purchase - (self.current_value or Decimal("0.00"))
        percent = (lost / purchase) * Decimal("100")
        percent = max(Decimal("0.00"), min(percent, Decimal("100.00")))
        return percent.quantize(Decimal("0.01"))

    @property
    def utilisation_rate(self) -> Decimal | None:
        """Percentage of operating hours since purchase that the item was out."""
        days = self.age_days
        if not days:
            return None
        capacity = Decimal(days) * OPERATING_HOURS_PER_DAY
        if capacity <= 0:
            return None
        rate = (Decimal(self.total_rental_hours or 0) / capacity) * Decimal("100")
        return min(rate, Decimal("100.00")).quantize(Decimal("0.01"))

    @property
    def primary_photo(self):
        """The photo to show in the grid, falling back to the newest one."""
        photos = list(self.photos.all())
        for photo in photos:
            if photo.is_primary:
                return photo
        return photos[0] if photos else None

    @property
    def specification_summary(self) -> str:
        """Compact one-line spec used in tables and on labels."""
        parts = [part for part in (self.brand, self.model, self.size_label) if part]
        if self.volume_litres:
            parts.append(f"{self.volume_litres:.1f} L")
        if self.wetsuit_thickness:
            parts.append(self.wetsuit_thickness)
        return " · ".join(parts)

    # ----------------------------------------------------------------- QR code
    @property
    def qr_payload(self) -> str:
        """The exact string encoded in the label.

        Uses ``public_id`` rather than the primary key so a scanned label cannot
        be used to enumerate the fleet.
        """
        return f"{QR_PREFIX}{self.public_id}"

    def qr_svg(self, scale: int = 4, border: int = 2) -> str:
        """Inline SVG QR code — sharp on screen and on a label printer.

        Prefers the shared :func:`apps.core.utils.make_qr_svg` helper. With
        segno 1.6 that helper hands the SVG serialiser a text buffer while the
        serialiser emits bytes, so the ``TypeError`` branch renders the same
        QR code through a binary buffer instead of failing the page. Delete the
        fallback once the shared helper is fixed.
        """
        try:
            return make_qr_svg(self.qr_payload, scale=scale, border=border)
        except TypeError:
            import io

            import segno

            buffer = io.BytesIO()
            segno.make(self.qr_payload, error="m").save(
                buffer,
                kind="svg",
                scale=scale,
                border=border,
                dark="#0f172a",
                xmldecl=False,
                svgns=True,
            )
            return buffer.getvalue().decode("utf-8")


class EquipmentPhoto(TimeStampedModel):
    """A photograph of an item — condition evidence as much as a catalogue shot."""

    equipment = models.ForeignKey(
        Equipment,
        verbose_name=_("equipment"),
        on_delete=models.CASCADE,
        related_name="photos",
    )
    image = models.ImageField(
        _("photo"),
        upload_to="equipment/photos/%Y/%m/",
        validators=[validate_image_upload],
    )
    caption = models.CharField(_("caption"), max_length=200, blank=True)
    is_primary = models.BooleanField(_("primary photo"), default=False)
    taken_at = models.DateTimeField(_("taken at"), null=True, blank=True)

    class Meta:
        verbose_name = _("equipment photo")
        verbose_name_plural = _("equipment photos")
        ordering = ["-is_primary", "-taken_at", "-created_at"]
        indexes = [models.Index(fields=["equipment", "-is_primary"])]

    def __str__(self) -> str:
        return self.caption or f"{self.equipment_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            # Exactly one primary photo per item.
            EquipmentPhoto.objects.filter(equipment_id=self.equipment_id).exclude(
                pk=self.pk
            ).update(is_primary=False)
