"""Read queries for the equipment screens.

Kept apart from :mod:`services` because none of these decide anything — they
only assemble what a screen needs, including the two histories that live in
other apps and may not be installed yet.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet

from apps.audit.models import AuditLog

from .models import Equipment, EquipmentCategory

#: Filters the list screen understands, in the order they appear in the toolbar.
LIST_FILTER_PARAMS = ("category", "status", "condition", "rentable", "needs_service")


def equipment_queryset() -> QuerySet[Equipment]:
    """Base queryset for every equipment list, with the joins already made."""
    return Equipment.objects.select_related("category").prefetch_related("photos")


def apply_list_filters(queryset: QuerySet[Equipment], params) -> QuerySet[Equipment]:
    """Apply the toolbar filters from a ``request.GET``-like mapping."""
    category = (params.get("category") or "").strip()
    if category.isdigit():
        node = EquipmentCategory.objects.filter(pk=int(category)).first()
        queryset = (
            queryset.filter(category_id__in=node.descendant_ids)
            if node
            else queryset.none()
        )

    status = (params.get("status") or "").strip()
    if status:
        queryset = queryset.filter(status=status)

    condition = (params.get("condition") or "").strip()
    if condition:
        queryset = queryset.filter(condition=condition)

    rentable = (params.get("rentable") or "").strip()
    if rentable == "yes":
        queryset = queryset.filter(is_rentable=True)
    elif rentable == "no":
        queryset = queryset.filter(is_rentable=False)

    if (params.get("needs_service") or "").strip() == "yes":
        from django.utils import timezone  # noqa: PLC0415 - local, avoids a stale date

        queryset = queryset.filter(
            Q(next_maintenance_date__lte=timezone.localdate())
            | Q(condition__in=("poor", "unusable"))
            | Q(status__in=("maintenance", "damaged"))
        )

    return queryset


def category_tree() -> list[EquipmentCategory]:
    """All categories, parents before children, ready for an indented select."""
    categories = list(EquipmentCategory.objects.select_related("parent"))
    by_parent: dict[int | None, list[EquipmentCategory]] = {}
    for category in categories:
        by_parent.setdefault(category.parent_id, []).append(category)

    ordered: list[EquipmentCategory] = []

    def walk(parent_id, depth: int) -> None:
        if depth > 6:
            return
        for node in by_parent.get(parent_id, []):
            node.depth = depth
            ordered.append(node)
            walk(node.pk, depth + 1)

    walk(None, 0)
    # Anything orphaned by a data edit still has to appear somewhere.
    seen = {node.pk for node in ordered}
    for category in categories:
        if category.pk not in seen:
            category.depth = 0
            ordered.append(category)
    return ordered


def status_history(equipment: Equipment, limit: int = 30) -> QuerySet[AuditLog]:
    """Audit entries for this item — the authoritative status history."""
    content_type = ContentType.objects.get_for_model(Equipment)
    return AuditLog.objects.filter(
        content_type=content_type, object_id=str(equipment.pk)
    ).select_related("user")[:limit]


def rental_history(equipment: Equipment, limit: int = 20) -> list:
    """Recent rentals of this item, or ``[]`` when rentals are not installed yet."""
    try:
        rental_item = django_apps.get_model("rentals.RentalItem")
    except (LookupError, ValueError):
        return []
    try:
        return list(
            rental_item._default_manager.filter(equipment=equipment)
            .select_related("rental")
            .order_by("-pk")[:limit]
        )
    except Exception:  # noqa: BLE001 - a differently shaped rentals app must not break this page
        return []


def maintenance_history(equipment: Equipment, limit: int = 20) -> list:
    """Recent maintenance records, or ``[]`` when maintenance is not installed."""
    try:
        record = django_apps.get_model("maintenance.MaintenanceRecord")
    except (LookupError, ValueError):
        return []
    try:
        return list(record._default_manager.filter(equipment=equipment).order_by("-pk")[:limit])
    except Exception:  # noqa: BLE001 - see rental_history
        return []
