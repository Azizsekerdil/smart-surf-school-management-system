"""REST projection of the dashboard: authentication, gating and search."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_summary_requires_authentication(api):
    response = api.get("/api/v1/dashboard/")
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_summary_returns_tiles_for_a_manager(api, manager_user):
    api.force_authenticate(manager_user)
    response = api.get("/api/v1/dashboard/")
    assert response.status_code == 200
    assert response.data["variant"] == "staff"
    keys = {tile["key"] for tile in response.data["tiles"]}
    assert "todays_revenue" in keys


@pytest.mark.django_db
def test_summary_hides_money_from_maintenance_staff(api, maintenance_user):
    api.force_authenticate(maintenance_user)
    response = api.get("/api/v1/dashboard/")
    assert response.status_code == 200
    keys = {tile["key"] for tile in response.data["tiles"]}
    assert "todays_revenue" not in keys
    assert "pending_payments" not in keys


@pytest.mark.django_db
def test_summary_is_capability_denied_when_dashboard_view_is_revoked(api, blocked_user):
    api.force_authenticate(blocked_user)
    assert api.get("/api/v1/dashboard/").status_code == 403


@pytest.mark.django_db
def test_search_requires_two_characters(api, manager_user):
    api.force_authenticate(manager_user)
    response = api.get("/api/v1/dashboard/search/", {"q": "a"})
    assert response.status_code == 200
    assert response.data["total"] == 0


@pytest.mark.django_db
def test_search_returns_groups(api, manager_user):
    api.force_authenticate(manager_user)
    response = api.get("/api/v1/dashboard/search/", {"q": "zz-no-such-record"})
    assert response.status_code == 200
    assert response.data["groups"] == []
    assert response.data["direct_hit_url"] is None
