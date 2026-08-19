from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.backups import services
from apps.backups.models import BackupRecord, BackupScope, BackupStatus, BackupType

from .factories import BackupRecordFactory, RestoreRecordFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def backup_volume(settings, tmp_path):
    root = tmp_path / "backup-volume"
    settings.BACKUP = {**settings.BACKUP, "ROOT": root}
    return root


@pytest.fixture
def media_tree(settings, tmp_path):
    root = tmp_path / "media"
    (root / "documents").mkdir(parents=True)
    (root / "documents" / "waiver.pdf").write_bytes(b"signed waiver bytes")
    settings.MEDIA_ROOT = root
    return root


@pytest.fixture
def manager():
    return User.objects.create_user(
        username="manager", email="manager@example.com", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def customer():
    return User.objects.create_user(
        username="guest", email="guest@example.com", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


def test_anonymous_access_is_refused(api):
    assert api.get("/api/v1/backups/").status_code in (401, 403)


def test_a_customer_is_refused(api, customer):
    api.force_authenticate(customer)
    assert api.get("/api/v1/backups/").status_code == 403


def test_a_manager_can_list_backups(api, manager):
    BackupRecordFactory(backup_code="BKP-20260101-001")
    api.force_authenticate(manager)

    response = api.get("/api/v1/backups/")

    assert response.status_code == 200
    payload = response.json()
    rows = payload["results"] if isinstance(payload, dict) else payload
    assert rows[0]["backup_code"] == "BKP-20260101-001"


def test_the_serializer_never_exposes_the_server_file_path(api, manager, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    api.force_authenticate(manager)

    response = api.get(f"/api/v1/backups/{record.pk}/")

    assert response.status_code == 200
    body = response.json()
    assert "file_path" not in body
    assert body["checksum_sha256"] == record.checksum_sha256
    assert body["size_display"] == record.size_display


def test_a_manager_can_run_a_backup_through_the_api(api, manager, backup_volume, media_tree):
    api.force_authenticate(manager)

    response = api.post("/api/v1/backups/run/", {"scope": BackupScope.MEDIA}, format="json")

    assert response.status_code == 201
    record = BackupRecord.objects.get()
    assert record.status == BackupStatus.COMPLETED
    assert record.created_by == manager
    assert record.is_verified is True


def test_verify_reports_a_damaged_artefact_with_a_conflict(
    api, manager, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    with Path(record.file_path).open("ab") as handle:
        handle.write(b"damage")

    api.force_authenticate(manager)
    response = api.post(f"/api/v1/backups/{record.pk}/verify/")

    assert response.status_code == 409
    assert response.json()["verified"] is False
    record.refresh_from_db()
    assert record.status == BackupStatus.CORRUPT


def test_statistics_endpoint(api, manager, backup_volume, media_tree):
    services.create_backup(BackupType.DAILY, BackupScope.MEDIA)
    api.force_authenticate(manager)

    response = api.get("/api/v1/backups/statistics/")

    assert response.status_code == 200
    body = response.json()
    assert body["completed"] == 1
    assert body["health"] == services.HEALTH_HEALTHY
    assert body["retention"]["daily"] >= 1
    assert body["last_successful"]["backup_code"]


def test_restore_history_is_readable_but_not_writable(api, manager):
    RestoreRecordFactory()
    api.force_authenticate(manager)

    listing = api.get("/api/v1/backup-restores/")
    assert listing.status_code == 200

    # There is deliberately no way to start a restore over the API.
    creation = api.post("/api/v1/backup-restores/", {}, format="json")
    assert creation.status_code in (403, 405)


def test_the_api_exposes_no_restore_route(api, manager, backup_volume, media_tree):
    """Overwriting live data is an HTML-only, type-the-code decision."""
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    api.force_authenticate(manager)

    assert api.post(f"/api/v1/backups/{record.pk}/restore/").status_code == 404
    assert api.delete(f"/api/v1/backups/{record.pk}/").status_code in (403, 405)
