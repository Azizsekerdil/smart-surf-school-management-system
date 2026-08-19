"""HTML views for the equipment module.

Views orchestrate only: they gather what a screen needs, hand decisions to
:mod:`apps.equipment.services`, and turn a service error into a message the
operator can act on.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from apps.accounts.permissions import CapabilityRequiredMixin
from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.csv_safety import safe_csv_writer
from apps.core.enums import EquipmentCondition, EquipmentStatus
from apps.core.mixins import (
    AuditedCreateMixin,
    AuditedUpdateMixin,
    DateRangeMixin,
    HtmxPartialMixin,
    SearchableListMixin,
)

from .forms import (
    BoardAdvisorForm,
    EquipmentCategoryForm,
    EquipmentForm,
    EquipmentImportForm,
    EquipmentPhotoForm,
    EquipmentStatusForm,
    WetsuitAdvisorForm,
)
from .models import Equipment, EquipmentCategory, EquipmentPhoto
from .selectors import (
    apply_list_filters,
    category_tree,
    equipment_queryset,
    maintenance_history,
    rental_history,
    status_history,
)
from .services import (
    IMPORT_COLUMNS,
    MAX_IMPORT_ROWS,
    allowed_next_statuses,
    archive_equipment,
    bulk_import_from_rows,
    change_status,
    ensure_default_categories,
    fleet_summary,
    import_template_csv,
    parse_csv_file,
    recommend_board,
    recommend_wetsuit,
    utilisation_report,
)

#: Sort options offered in the list toolbar, mapped to safe ORM orderings.
SORT_OPTIONS: dict[str, tuple[str, ...]] = {
    "code": ("asset_code",),
    "name": ("name", "asset_code"),
    "status": ("status", "asset_code"),
    "category": ("category__sort_order", "category__name", "asset_code"),
    "newest": ("-created_at",),
    "value": ("-current_value", "asset_code"),
    "service": ("next_maintenance_date", "asset_code"),
}
DEFAULT_SORT = "code"

#: Session key holding the parsed rows between the import preview and confirm.
IMPORT_SESSION_KEY = "equipment_import_rows"

#: Hard ceiling on one label sheet, so a mis-click cannot spool 4 000 labels.
MAX_LABELS = 200

#: Badge palettes — the shared defaults have no entry for the equipment-only
#: states, and a rented board reading "slate" looks like a data error.
STATUS_COLORS = {
    EquipmentStatus.AVAILABLE: "emerald",
    EquipmentStatus.RESERVED: "violet",
    EquipmentStatus.RENTED: "sky",
    EquipmentStatus.IN_LESSON: "sky",
    EquipmentStatus.MAINTENANCE: "amber",
    EquipmentStatus.DAMAGED: "rose",
    EquipmentStatus.LOST: "rose",
    EquipmentStatus.RETIRED: "slate",
}
CONDITION_COLORS = {
    EquipmentCondition.NEW: "emerald",
    EquipmentCondition.EXCELLENT: "emerald",
    EquipmentCondition.GOOD: "sky",
    EquipmentCondition.FAIR: "amber",
    EquipmentCondition.POOR: "rose",
    EquipmentCondition.UNUSABLE: "rose",
}


class EquipmentListView(CapabilityRequiredMixin, SearchableListMixin, HtmxPartialMixin, ListView):
    """The fleet, as a photo grid or a dense table."""

    capability = "equipment.view"
    model = Equipment
    template_name = "equipment/equipment_list.html"
    partial_template_name = "equipment/partials/equipment_results.html"
    context_object_name = "equipment_items"
    paginate_by = 24
    search_fields = ("asset_code", "brand", "serial_number", "name", "model")

    def get_sort_key(self) -> str:
        key = (self.request.GET.get("sort") or DEFAULT_SORT).strip()
        return key if key in SORT_OPTIONS else DEFAULT_SORT

    def get_view_mode(self) -> str:
        mode = (self.request.GET.get("view") or "grid").strip()
        return mode if mode in ("grid", "table") else "grid"

    def get_queryset(self):
        queryset = apply_list_filters(equipment_queryset(), self.request.GET)
        queryset = self.apply_search(queryset)
        return queryset.order_by(*SORT_OPTIONS[self.get_sort_key()])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "categories": category_tree(),
                "statuses": EquipmentStatus.choices,
                "conditions": EquipmentCondition.choices,
                "sort_options": SORT_OPTIONS,
                "current_sort": self.get_sort_key(),
                "current_category": self.request.GET.get("category", ""),
                "current_status": self.request.GET.get("status", ""),
                "current_condition": self.request.GET.get("condition", ""),
                "current_rentable": self.request.GET.get("rentable", ""),
                "current_needs_service": self.request.GET.get("needs_service", ""),
                "view_mode": self.get_view_mode(),
                "summary": fleet_summary(),
                "status_colors": STATUS_COLORS,
                "condition_colors": CONDITION_COLORS,
            }
        )
        return context


class EquipmentDetailView(CapabilityRequiredMixin, DetailView):
    """Everything about one item, including its QR label and its histories."""

    capability = "equipment.view"
    model = Equipment
    template_name = "equipment/equipment_detail.html"
    context_object_name = "equipment"

    def get_queryset(self):
        return equipment_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        equipment = self.object
        context.update(
            {
                # Generated from the item's own UUID — no user input reaches the SVG.
                "qr_svg": mark_safe(equipment.qr_svg(scale=5)),  # noqa: S308  # nosec
                "status_form": EquipmentStatusForm(equipment=equipment),
                "allowed_statuses": allowed_next_statuses(equipment),
                "status_history": status_history(equipment),
                "rental_history": rental_history(equipment),
                "maintenance_history": maintenance_history(equipment),
                "photo_form": EquipmentPhotoForm(),
                "photos": equipment.photos.all(),
                "status_colors": STATUS_COLORS,
                "condition_colors": CONDITION_COLORS,
            }
        )
        return context


class EquipmentCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "equipment.add"
    model = Equipment
    form_class = EquipmentForm
    template_name = "equipment/equipment_form.html"
    success_message = _("Equipment added.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New equipment")
        context["has_categories"] = EquipmentCategory.objects.filter(is_active=True).exists()
        return context

    def get_success_url(self):
        return reverse("equipment:detail", kwargs={"pk": self.object.pk})


class EquipmentUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "equipment.change"
    model = Equipment
    form_class = EquipmentForm
    template_name = "equipment/equipment_form.html"
    success_message = _("Changes saved.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit equipment")
        context["has_categories"] = True
        return context

    def get_success_url(self):
        return reverse("equipment:detail", kwargs={"pk": self.object.pk})


class EquipmentStatusChangeView(CapabilityRequiredMixin, View):
    """Inline status change. Answers HTMX with the refreshed status panel."""

    capability = "equipment.change"

    def post(self, request, pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        form = EquipmentStatusForm(request.POST, equipment=equipment)
        error = ""
        if form.is_valid():
            try:
                change_status(
                    equipment,
                    form.cleaned_data["status"],
                    user=request.user,
                    reason=form.cleaned_data["reason"],
                    request=request,
                )
            except ValidationError as exc:
                error = " ".join(str(message) for message in exc.messages)
            else:
                if not request.htmx:
                    messages.success(
                        request,
                        _("%(code)s is now %(status)s.")
                        % {
                            "code": equipment.asset_code,
                            "status": equipment.get_status_display(),
                        },
                    )
                    return redirect("equipment:detail", pk=equipment.pk)
                form = EquipmentStatusForm(equipment=equipment)
        else:
            error = " ".join(
                str(message) for errors in form.errors.values() for message in errors
            )

        if not request.htmx:
            if error:
                messages.error(request, error)
            return redirect("equipment:detail", pk=equipment.pk)

        equipment.refresh_from_db()
        return render(
            request,
            "equipment/partials/status_panel.html",
            {
                "equipment": equipment,
                "status_form": EquipmentStatusForm(equipment=equipment),
                "allowed_statuses": allowed_next_statuses(equipment),
                "status_error": error,
                "status_history": status_history(equipment),
                "status_colors": STATUS_COLORS,
                "condition_colors": CONDITION_COLORS,
            },
        )


class EquipmentDeleteView(CapabilityRequiredMixin, View):
    """Archive (soft-delete) an item. History is never destroyed."""

    capability = "equipment.delete"

    def get(self, request, pk):
        equipment = get_object_or_404(equipment_queryset(), pk=pk)
        return render(
            request,
            "equipment/equipment_confirm_delete.html",
            {"equipment": equipment},
        )

    def post(self, request, pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        try:
            archive_equipment(equipment, user=request.user, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(str(message) for message in exc.messages))
            return redirect("equipment:detail", pk=equipment.pk)
        messages.success(
            request, _("%(code)s archived.") % {"code": equipment.asset_code}
        )
        return redirect("equipment:list")


class EquipmentPhotoCreateView(CapabilityRequiredMixin, View):
    capability = "equipment.change"

    def post(self, request, pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        form = EquipmentPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.equipment = equipment
            photo.save()
            record_audit(
                request,
                action=AuditAction.UPDATE,
                instance=equipment,
                description=_("Photo added to %(code)s") % {"code": equipment.asset_code},
            )
            messages.success(request, _("Photo added."))
        else:
            messages.error(
                request,
                " ".join(
                    str(message) for errors in form.errors.values() for message in errors
                ),
            )
        return redirect("equipment:detail", pk=equipment.pk)


class EquipmentPhotoDeleteView(CapabilityRequiredMixin, View):
    capability = "equipment.change"

    def post(self, request, pk, photo_pk):
        equipment = get_object_or_404(Equipment, pk=pk)
        photo = get_object_or_404(EquipmentPhoto, pk=photo_pk, equipment=equipment)
        photo.delete()
        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=equipment,
            description=_("Photo removed from %(code)s") % {"code": equipment.asset_code},
        )
        messages.success(request, _("Photo removed."))
        return redirect("equipment:detail", pk=equipment.pk)


class EquipmentLabelSheetView(CapabilityRequiredMixin, TemplateView):
    """A printable sheet of QR labels: code, name, size, storage location."""

    capability = "equipment.view"
    template_name = "equipment/label_sheet.html"

    def get_items(self):
        queryset = equipment_queryset().exclude(status=EquipmentStatus.RETIRED)
        raw_ids = (self.request.GET.get("ids") or "").strip()
        if raw_ids:
            ids = [int(value) for value in raw_ids.split(",") if value.strip().isdigit()]
            queryset = Equipment.objects.select_related("category").filter(pk__in=ids)
        else:
            queryset = apply_list_filters(queryset, self.request.GET)
        return list(queryset.order_by("asset_code")[:MAX_LABELS])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.get_items()
        context["labels"] = [
            {
                "equipment": item,
                # Rendered from the item's UUID; contains no user-supplied markup.
                "qr": mark_safe(item.qr_svg(scale=3, border=1)),  # noqa: S308  # nosec
            }
            for item in items
        ]
        context["truncated"] = len(items) >= MAX_LABELS
        context["max_labels"] = MAX_LABELS
        context["printed_at"] = timezone.now()
        return context


class EquipmentExportView(CapabilityRequiredMixin, View):
    """CSV of the current filter selection — the same columns the importer reads."""

    capability = "equipment.export"

    def get(self, request):
        queryset = apply_list_filters(equipment_queryset(), request.GET).order_by("asset_code")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="equipment-{timezone.localdate():%Y%m%d}.csv"'
        )
        response.write("﻿")  # BOM so Excel on Windows reads UTF-8 correctly
        writer = safe_csv_writer(response, lineterminator="\n")
        writer.writerow(IMPORT_COLUMNS)
        for item in queryset.iterator(chunk_size=200):
            writer.writerow(
                [
                    item.asset_code,
                    item.category.code,
                    item.name,
                    item.brand,
                    item.model,
                    item.serial_number,
                    item.size_label,
                    item.length_cm or "",
                    item.width_cm or "",
                    item.thickness_cm or "",
                    item.volume_litres or "",
                    item.wetsuit_thickness,
                    item.suitable_min_level,
                    item.suitable_max_level,
                    item.min_rider_weight_kg or "",
                    item.max_rider_weight_kg or "",
                    item.purchase_date or "",
                    item.purchase_price,
                    item.current_value,
                    item.supplier,
                    item.status,
                    item.condition,
                    item.storage_location,
                    "yes" if item.is_rentable else "no",
                    "yes" if item.is_lesson_stock else "no",
                    item.rental_price_hourly,
                    item.rental_price_daily,
                    item.rental_price_weekly,
                    item.deposit_amount,
                    item.notes.replace("\n", " ") if item.notes else "",
                ]
            )
        record_audit(
            request,
            action=AuditAction.EXPORT,
            description=_("Equipment list exported (%(count)s rows)")
            % {"count": queryset.count()},
        )
        return response


class EquipmentUtilisationView(CapabilityRequiredMixin, DateRangeMixin, TemplateView):
    """Which items earn their rack space and which are dead weight."""

    capability = "equipment.view"
    template_name = "equipment/utilisation.html"
    date_field = "created_at"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        start, end, _label = self.get_date_range()
        category = self.request.GET.get("category") or None
        rows = utilisation_report(start=start, end=end, category=category)
        context["rows"] = rows
        context["categories"] = category_tree()
        context["current_category"] = self.request.GET.get("category", "")
        context["is_lifetime"] = bool(rows) and rows[0]["is_lifetime"]
        context["total_hours"] = sum(
            (row["hours"] for row in rows), start=Decimal("0.00")
        )
        context["idle_items"] = [row for row in rows if not row["rentals"]]
        context["status_colors"] = STATUS_COLORS
        return context


class EquipmentAdvisorView(CapabilityRequiredMixin, TemplateView):
    """Board and wetsuit sizing, straight from the live fleet."""

    capability = "equipment.view"
    template_name = "equipment/advisor.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        data = self.request.GET or None
        board_form = BoardAdvisorForm(data if "weight_kg" in self.request.GET else None)
        wetsuit_form = WetsuitAdvisorForm(
            data if "water_temp_c" in self.request.GET else None
        )
        context["board_form"] = board_form
        context["wetsuit_form"] = wetsuit_form
        if board_form.is_bound and board_form.is_valid():
            context["board"] = recommend_board(
                board_form.cleaned_data["weight_kg"], board_form.cleaned_data["level"]
            )
        if wetsuit_form.is_bound and wetsuit_form.is_valid():
            context["wetsuit"] = recommend_wetsuit(
                wetsuit_form.cleaned_data["water_temp_c"],
                wetsuit_form.cleaned_data.get("size", ""),
            )
        return context


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
class CategoryListView(CapabilityRequiredMixin, ListView):
    capability = "equipment.view"
    model = EquipmentCategory
    template_name = "equipment/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        return category_tree()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_counts"] = {
            row["category_id"]: row["total"]
            for row in Equipment.objects.values("category_id").annotate(total=Count("id"))
        }
        return context


class CategoryCreateView(CapabilityRequiredMixin, AuditedCreateMixin, CreateView):
    capability = "equipment.add"
    model = EquipmentCategory
    form_class = EquipmentCategoryForm
    template_name = "equipment/category_form.html"
    success_url = reverse_lazy("equipment:category_list")
    success_message = _("Category created.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("New category")
        return context


class CategoryUpdateView(CapabilityRequiredMixin, AuditedUpdateMixin, UpdateView):
    capability = "equipment.change"
    model = EquipmentCategory
    form_class = EquipmentCategoryForm
    template_name = "equipment/category_form.html"
    success_url = reverse_lazy("equipment:category_list")
    success_message = _("Category updated.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit category")
        return context


class CategorySeedView(CapabilityRequiredMixin, View):
    """Load the standard surf-school taxonomy. Safe to run more than once."""

    capability = "equipment.manage"

    def post(self, request):
        created, untouched = ensure_default_categories()
        record_audit(
            request,
            action=AuditAction.CREATE,
            description=_("Standard equipment categories loaded (%(created)s new)")
            % {"created": created},
        )
        if created:
            messages.success(
                request,
                _("%(created)s categories added, %(kept)s already existed.")
                % {"created": created, "kept": untouched},
            )
        else:
            messages.info(request, _("Every standard category already exists."))
        return redirect("equipment:category_list")


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------
class EquipmentImportView(CapabilityRequiredMixin, TemplateView):
    """Upload, preview, then confirm. Nothing is written before the confirm."""

    capability = "equipment.add"
    template_name = "equipment/equipment_import.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", EquipmentImportForm())
        context["columns"] = IMPORT_COLUMNS
        context["max_rows"] = MAX_IMPORT_ROWS
        context["categories"] = category_tree()
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("step") == "confirm":
            return self._confirm(request)
        return self._preview(request)

    def _preview(self, request):
        form = EquipmentImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        rows, error = parse_csv_file(form.cleaned_data["file"])
        if error:
            messages.error(request, error)
            return self.render_to_response(self.get_context_data(form=form))
        if not rows:
            messages.error(request, _("The file contains no data rows."))
            return self.render_to_response(self.get_context_data(form=form))

        result = bulk_import_from_rows(rows, user=request.user, dry_run=True)
        request.session[IMPORT_SESSION_KEY] = rows
        return self.render_to_response(
            self.get_context_data(form=EquipmentImportForm(), result=result, preview=True)
        )

    def _confirm(self, request):
        rows = request.session.get(IMPORT_SESSION_KEY)
        if not rows:
            messages.error(
                request, _("The preview expired. Upload the file again.")
            )
            return redirect("equipment:import")

        result = bulk_import_from_rows(
            rows, user=request.user, dry_run=False, request=request
        )
        request.session.pop(IMPORT_SESSION_KEY, None)
        messages.success(
            request,
            _("%(created)s items created, %(updated)s updated, %(errors)s skipped.")
            % {
                "created": result.created,
                "updated": result.updated,
                "errors": result.errors,
            },
        )
        if result.errors:
            return self.render_to_response(
                self.get_context_data(result=result, preview=False)
            )
        return redirect("equipment:list")


class ImportTemplateView(CapabilityRequiredMixin, View):
    """Download the blank CSV template with the exact expected columns."""

    capability = "equipment.add"

    def get(self, request):
        response = HttpResponse(import_template_csv(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="equipment-import-template.csv"'
        return response


class EquipmentScanView(CapabilityRequiredMixin, View):
    """Resolve a scanned QR payload (``SURF:EQ:<uuid>``) to the item page."""

    capability = "equipment.view"

    def get(self, request, public_id):
        try:
            equipment = Equipment.objects.get(public_id=public_id)
        except (Equipment.DoesNotExist, ValidationError, ValueError) as exc:
            raise Http404(_("No equipment matches this code.")) from exc
        return redirect("equipment:detail", pk=equipment.pk)
