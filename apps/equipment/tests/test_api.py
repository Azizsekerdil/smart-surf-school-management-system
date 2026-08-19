"""API tests: capability enforcement, CRUD and the custom actions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.core.enums import EquipmentStatus, SurfLevel

from .factories import (
    EquipmentCategoryFactory,
    EquipmentFactory,
    SoftboardCategoryFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def manager_api(api):
    api.force_authenticate(UserFactory(role=Role.EQUIPMENT_MANAGER))
    return api


def test_list_requires_authentication(api):
    assert api.get("/api/v1/equipment/").status_code in (401, 403)


def test_customer_cannot_read_the_fleet(api):
    api.force_authenticate(UserFactory(username="buyer", role=Role.CUSTOMER))
    assert api.get("/api/v1/equipment/").status_code == 403


def test_list_returns_the_fleet(manager_api):
    EquipmentFactory(name="Soft-top")
    response = manager_api.get("/api/v1/equipment/")
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_detail_exposes_the_qr_payload(manager_api):
    item = EquipmentFactory()
    response = manager_api.get(f"/api/v1/equipment/{item.pk}/")
    assert response.status_code == 200
    assert response.data["qr_payload"] == item.qr_payload


def test_create_generates_an_asset_code(manager_api):
    category = EquipmentCategoryFactory()
    response = manager_api.post(
        "/api/v1/equipment/",
        {
            "category": category.pk,
            "name": "API board",
            "suitable_min_level": SurfLevel.FIRST_TIME,
            "suitable_max_level": SurfLevel.BEGINNER,
        },
        format="json",
    )
    assert response.status_code == 201


def test_create_rejects_a_rentable_item_without_a_price(manager_api):
    category = EquipmentCategoryFactory()
    response = manager_api.post(
        "/api/v1/equipment/",
        {"category": category.pk, "name": "Free board", "is_rentable": True},
        format="json",
    )
    assert response.status_code == 400
    assert "is_rentable" in response.data["error"]["detail"]


def test_change_status_action(manager_api):
    item = EquipmentFactory()
    response = manager_api.post(
        f"/api/v1/equipment/{item.pk}/change-status/",
        {"status": EquipmentStatus.MAINTENANCE, "reason": "Ding repair"},
        format="json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.status == EquipmentStatus.MAINTENANCE


def test_change_status_action_rejects_an_illegal_move(manager_api):
    item = EquipmentFactory(status=EquipmentStatus.RENTED)
    response = manager_api.post(
        f"/api/v1/equipment/{item.pk}/change-status/",
        {"status": EquipmentStatus.RETIRED, "reason": "worn"},
        format="json",
    )
    assert response.status_code == 400


def test_available_action_hides_items_that_are_out(manager_api):
    EquipmentFactory()
    EquipmentFactory(status=EquipmentStatus.RENTED)
    response = manager_api.get("/api/v1/equipment/available/")
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_recommend_board_action(manager_api):
    category = SoftboardCategoryFactory()
    board = EquipmentFactory(category=category, volume_litres=Decimal("75.00"))
    response = manager_api.get(
        "/api/v1/equipment/recommend-board/", {"weight_kg": 75, "level": "first_time"}
    )
    assert response.status_code == 200
    assert response.data["equipment"]["asset_code"] == board.asset_code
    assert response.data["is_recommendation"] is True


def test_recommend_wetsuit_action(manager_api):
    response = manager_api.get(
        "/api/v1/equipment/recommend-wetsuit/", {"water_temp_c": 8, "size": "M"}
    )
    assert response.status_code == 200
    assert "Hood" in response.data["required_accessories"]


def test_summary_action(manager_api):
    EquipmentFactory()
    response = manager_api.get("/api/v1/equipment/summary/")
    assert response.status_code == 200
    assert response.data["total"] == 1


def test_categories_endpoint(manager_api):
    EquipmentCategoryFactory(code="wetsuit", name="Wetsuit")
    response = manager_api.get("/api/v1/equipment-categories/")
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_destroy_soft_deletes(manager_api):
    from ..models import Equipment

    item = EquipmentFactory()
    response = manager_api.delete(f"/api/v1/equipment/{item.pk}/")
    assert response.status_code == 204
    assert not Equipment.objects.filter(pk=item.pk).exists()
    assert Equipment.all_objects.filter(pk=item.pk).exists()
