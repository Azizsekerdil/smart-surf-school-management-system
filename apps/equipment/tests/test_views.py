"""View tests: rendering, capability enforcement and the inline status change."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.constants import Role
from apps.core.enums import EquipmentStatus

from ..models import Equipment
from .factories import (
    EquipmentCategoryFactory,
    EquipmentFactory,
    SoftboardCategoryFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "surf-test-password-1"


@pytest.fixture
def manager(client):
    user = UserFactory(role=Role.EQUIPMENT_MANAGER, password=PASSWORD)
    client.force_login(user)
    return user


@pytest.fixture
def customer(client):
    user = UserFactory(username="outsider", role=Role.CUSTOMER, password=PASSWORD)
    client.force_login(user)
    return user


def test_list_view_renders(client, manager):
    EquipmentFactory(name="Soft-top 8'0")
    response = client.get(reverse("equipment:list"))
    assert response.status_code == 200
    assert b"Soft-top 8" in response.content


def test_list_view_filters_by_status(client, manager):
    EquipmentFactory(name="Available board")
    EquipmentFactory(name="Broken board", status=EquipmentStatus.DAMAGED)
    response = client.get(reverse("equipment:list"), {"status": EquipmentStatus.DAMAGED})
    assert b"Broken board" in response.content
    assert b"Available board" not in response.content


def test_list_view_searches_by_asset_code(client, manager):
    item = EquipmentFactory(name="Findable")
    response = client.get(reverse("equipment:list"), {"q": item.asset_code})
    assert b"Findable" in response.content


def test_list_view_htmx_returns_only_the_results_partial(client, manager):
    EquipmentFactory()
    response = client.get(reverse("equipment:list"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content


def test_detail_view_renders_the_qr_code(client, manager):
    item = EquipmentFactory()
    response = client.get(reverse("equipment:detail", args=[item.pk]))
    assert response.status_code == 200
    assert b"<svg" in response.content
    assert item.qr_payload.encode() in response.content


def test_customer_is_denied_the_inventory(client, customer):
    response = client.get(reverse("equipment:list"))
    assert response.status_code == 403


def test_anonymous_is_redirected_to_login(client):
    response = client.get(reverse("equipment:list"))
    assert response.status_code in (302, 301)


def test_create_view_saves_and_generates_a_code(client, manager):
    category = EquipmentCategoryFactory()
    response = client.post(
        reverse("equipment:create"),
        {
            "asset_code": "",
            "category": category.pk,
            "name": "New softboard",
            "brand": "Demo Boards",
            "model": "Mod Fun",
            "serial_number": "",
            "size_label": "8'0",
            "length_cm": "244.0",
            "width_cm": "",
            "thickness_cm": "",
            "volume_litres": "68.00",
            "wetsuit_thickness": "",
            "suitable_min_level": "first_time",
            "suitable_max_level": "beginner",
            "min_rider_weight_kg": "",
            "max_rider_weight_kg": "",
            "condition": "good",
            "storage_location": "Container A",
            "purchase_date": "",
            "purchase_price": "9500.00",
            "current_value": "7200.00",
            "supplier": "",
            "is_lesson_stock": "on",
            "rental_price_hourly": "0.00",
            "rental_price_daily": "0.00",
            "rental_price_weekly": "0.00",
            "deposit_amount": "0.00",
            "last_maintenance_date": "",
            "next_maintenance_date": "",
            "notes": "",
        },
    )
    assert response.status_code == 302
    item = Equipment.objects.get(name="New softboard")
    assert item.asset_code.startswith("EQ")


def test_update_view_changes_a_field(client, manager):
    item = EquipmentFactory(name="Old name")
    response = client.post(
        reverse("equipment:update", args=[item.pk]),
        {
            "category": item.category.pk,
            "name": "Renamed",
            "brand": item.brand,
            "model": item.model,
            "serial_number": "",
            "size_label": item.size_label,
            "length_cm": "244.0",
            "width_cm": "",
            "thickness_cm": "",
            "volume_litres": "68.00",
            "wetsuit_thickness": "",
            "suitable_min_level": item.suitable_min_level,
            "suitable_max_level": item.suitable_max_level,
            "min_rider_weight_kg": "",
            "max_rider_weight_kg": "",
            "condition": item.condition,
            "storage_location": "",
            "purchase_date": "",
            "purchase_price": "9500.00",
            "current_value": "7200.00",
            "supplier": "",
            "is_lesson_stock": "on",
            "rental_price_hourly": "0.00",
            "rental_price_daily": "0.00",
            "rental_price_weekly": "0.00",
            "deposit_amount": "0.00",
            "last_maintenance_date": "",
            "next_maintenance_date": "",
            "notes": "",
        },
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.name == "Renamed"


def test_status_change_over_htmx_returns_the_panel(client, manager):
    item = EquipmentFactory()
    response = client.post(
        reverse("equipment:status_change", args=[item.pk]),
        {"status": EquipmentStatus.MAINTENANCE, "reason": "Deck ding"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"status-panel" in response.content
    item.refresh_from_db()
    assert item.status == EquipmentStatus.MAINTENANCE


def test_status_change_reports_a_missing_reason(client, manager):
    item = EquipmentFactory()
    response = client.post(
        reverse("equipment:status_change", args=[item.pk]),
        {"status": EquipmentStatus.DAMAGED, "reason": ""},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.status == EquipmentStatus.AVAILABLE


def test_label_sheet_renders_one_label_per_item(client, manager):
    first = EquipmentFactory()
    second = EquipmentFactory()
    response = client.get(reverse("equipment:labels"), {"ids": f"{first.pk},{second.pk}"})
    assert response.status_code == 200
    assert response.content.count(b"<svg") >= 2


def test_category_list_and_seed(client, manager):
    response = client.post(reverse("equipment:category_seed"))
    assert response.status_code == 302
    listing = client.get(reverse("equipment:category_list"))
    assert b"Softboard" in listing.content


def test_import_preview_then_confirm(client, manager):
    SoftboardCategoryFactory()
    csv_body = (
        "category_code,name,volume_litres,is_rentable,is_lesson_stock\n"
        "softboard,Imported board,68,no,yes\n"
    )
    upload = client.post(
        reverse("equipment:import"),
        {"file": _csv_upload(csv_body)},
    )
    assert upload.status_code == 200
    assert Equipment.objects.count() == 0

    confirm = client.post(reverse("equipment:import"), {"step": "confirm"})
    assert confirm.status_code == 302
    assert Equipment.objects.filter(name="Imported board").exists()


def test_import_template_download(client, manager):
    response = client.get(reverse("equipment:import_template"))
    assert response.status_code == 200
    assert b"category_code" in response.content


def test_export_requires_the_export_capability(client):
    user = UserFactory(username="photographer", role=Role.PHOTOGRAPHER, password=PASSWORD)
    client.force_login(user)
    response = client.get(reverse("equipment:export"))
    assert response.status_code == 403


def test_export_returns_csv(client, manager):
    EquipmentFactory()
    response = client.get(reverse("equipment:export"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")


def test_advisor_recommends_a_board(client, manager):
    category = SoftboardCategoryFactory()
    board = EquipmentFactory(category=category, volume_litres=Decimal("75.00"))
    response = client.get(
        reverse("equipment:advisor"), {"weight_kg": "75", "level": "first_time"}
    )
    assert response.status_code == 200
    assert board.asset_code.encode() in response.content


def test_utilisation_view_renders(client, manager):
    EquipmentFactory()
    response = client.get(reverse("equipment:utilisation"))
    assert response.status_code == 200


def test_scan_view_resolves_a_public_id(client, manager):
    item = EquipmentFactory()
    response = client.get(reverse("equipment:scan", args=[item.public_id]))
    assert response.status_code == 302
    assert str(item.pk) in response["Location"]


def test_archive_view_soft_deletes(client, manager):
    item = EquipmentFactory()
    response = client.post(reverse("equipment:delete", args=[item.pk]))
    assert response.status_code == 302
    assert not Equipment.objects.filter(pk=item.pk).exists()


def test_create_form_renders(client, manager):
    EquipmentCategoryFactory()
    response = client.get(reverse("equipment:create"))
    assert response.status_code == 200
    assert b"</form>" in response.content


def test_create_form_warns_when_no_categories_exist(client, manager):
    response = client.get(reverse("equipment:create"))
    assert response.status_code == 200
    assert b"no active categories" in response.content.lower()


def test_archive_confirmation_page_renders(client, manager):
    item = EquipmentFactory()
    response = client.get(reverse("equipment:delete", args=[item.pk]))
    assert response.status_code == 200
    assert item.asset_code.encode() in response.content


def test_archive_is_refused_while_the_item_is_out(client, manager):
    item = EquipmentFactory(status=EquipmentStatus.RENTED)
    response = client.post(reverse("equipment:delete", args=[item.pk]))
    assert response.status_code == 302
    assert Equipment.objects.filter(pk=item.pk).exists()


def test_category_create_and_update(client, manager):
    created = client.post(
        reverse("equipment:category_create"),
        {"code": "Fins", "name": "Fins", "parent": "", "icon": "package", "sort_order": "50",
         "is_active": "on"},
    )
    assert created.status_code == 302

    from ..models import EquipmentCategory

    category = EquipmentCategory.objects.get(code="fins")
    form_page = client.get(reverse("equipment:category_update", args=[category.pk]))
    assert form_page.status_code == 200

    updated = client.post(
        reverse("equipment:category_update", args=[category.pk]),
        {"code": "fins", "name": "Fins & keels", "parent": "", "icon": "package",
         "sort_order": "50", "is_active": "on"},
    )
    assert updated.status_code == 302
    category.refresh_from_db()
    assert category.name == "Fins & keels"


def test_import_page_renders(client, manager):
    response = client.get(reverse("equipment:import"))
    assert response.status_code == 200
    assert b"category_code" in response.content


def test_photo_upload_rejects_a_non_image(client, manager):
    item = EquipmentFactory()
    from django.core.files.uploadedfile import SimpleUploadedFile

    bogus = SimpleUploadedFile("not-a-photo.png", b"plain text", content_type="image/png")
    response = client.post(reverse("equipment:photo_create", args=[item.pk]), {"image": bogus})
    assert response.status_code == 302
    assert item.photos.count() == 0


def _csv_upload(body: str):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("fleet.csv", body.encode("utf-8"), content_type="text/csv")
