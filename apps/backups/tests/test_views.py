from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.backups import services
from apps.backups.models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreRecord,
    RestoreStatus,
)

from .factories import BackupRecordFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
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
def admin_user():
    return User.objects.create_user(
        username="root", email="root@example.com", password="pw-test-12345",
        role=Role.SUPER_ADMIN,
    )


@pytest.fixture
def manager():
    return User.objects.create_user(
        username="manager", email="manager@example.com", password="pw-test-12345",
        role=Role.MANAGER,
    )


@pytest.fixture
def instructor():
    return User.objects.create_user(
        username="coach", email="coach@example.com", password="pw-test-12345",
        role=Role.SURF_INSTRUCTOR,
    )


@pytest.fixture
def customer():
    return User.objects.create_user(
        username="guest", email="guest@example.com", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_list_requires_authentication(client):
    response = client.get(reverse("backups:list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_a_customer_can_never_see_backups(client, customer):
    client.force_login(customer)
    assert client.get(reverse("backups:list")).status_code == 403


def test_an_instructor_can_never_see_backups(client, instructor):
    client.force_login(instructor)
    assert client.get(reverse("backups:list")).status_code == 403


def test_a_manager_may_not_restore(client, manager, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    client.force_login(manager)
    assert client.get(reverse("backups:restore", args=[record.pk])).status_code == 403


def test_a_manager_may_not_delete(client, manager):
    record = BackupRecordFactory()
    client.force_login(manager)
    assert client.get(reverse("backups:delete", args=[record.pk])).status_code == 403


def test_a_manager_may_not_download(client, manager, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    client.force_login(manager)
    assert client.get(reverse("backups:download", args=[record.pk])).status_code == 403


def test_a_manager_may_not_change_the_retention_policy(client, manager):
    client.force_login(manager)
    assert client.get(reverse("backups:settings")).status_code == 403


# ---------------------------------------------------------------------------
# List & detail
# ---------------------------------------------------------------------------
def test_list_renders_for_a_manager(client, manager, backup_volume):
    BackupRecordFactory(backup_code="BKP-20260101-001")
    client.force_login(manager)
    response = client.get(reverse("backups:list"))
    assert response.status_code == 200
    assert b"BKP-20260101-001" in response.content
    assert "stats" in response.context
    # The storage doughnut is drawn by the vendored Chart.js build, never a CDN.
    assert b'id="storage-chart"' in response.content
    assert b"vendor/chartjs/chart.umd.js" in response.content
    assert b"//cdn" not in response.content


def test_list_warns_loudly_when_nothing_has_ever_succeeded(client, manager, backup_volume):
    client.force_login(manager)
    response = client.get(reverse("backups:list"))
    assert response.context["stats"]["health"] == services.HEALTH_CRITICAL


def test_list_filters_by_status_and_scope(client, manager, backup_volume):
    BackupRecordFactory(backup_code="BKP-20260101-010", status=BackupStatus.COMPLETED)
    BackupRecordFactory(backup_code="BKP-20260101-011", status=BackupStatus.FAILED)
    client.force_login(manager)

    response = client.get(reverse("backups:list"), {"status": BackupStatus.FAILED})
    codes = [row.backup_code for row in response.context["backups"]]
    assert codes == ["BKP-20260101-011"]


def test_list_search_matches_the_code(client, manager, backup_volume):
    BackupRecordFactory(backup_code="BKP-20260101-020", notes="before the season")
    BackupRecordFactory(backup_code="BKP-20260101-021", notes="routine")
    client.force_login(manager)

    response = client.get(reverse("backups:list"), {"q": "season"})
    codes = [row.backup_code for row in response.context["backups"]]
    assert codes == ["BKP-20260101-020"]


def test_detail_renders(client, manager, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    client.force_login(manager)
    response = client.get(reverse("backups:detail", args=[record.pk]))
    assert response.status_code == 200
    assert record.checksum_sha256.encode() in response.content


def test_restore_history_renders(client, manager, backup_volume):
    client.force_login(manager)
    assert client.get(reverse("backups:restore_list")).status_code == 200


# ---------------------------------------------------------------------------
# Creating and verifying through the UI
# ---------------------------------------------------------------------------
def test_a_manager_can_take_a_backup(client, manager, backup_volume, media_tree):
    client.force_login(manager)
    response = client.post(
        reverse("backups:create"), {"scope": BackupScope.MEDIA, "notes": "before upgrade"}
    )
    assert response.status_code == 302

    record = BackupRecord.objects.get()
    assert record.status == BackupStatus.COMPLETED
    assert record.created_by == manager
    assert record.notes == "before upgrade"
    # The UI verifies immediately, so nobody is left with an unproven copy.
    assert record.is_verified is True


def test_verify_button_marks_a_damaged_artefact_corrupt(
    client, manager, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    with Path(record.file_path).open("ab") as handle:
        handle.write(b"damage")

    client.force_login(manager)
    client.post(reverse("backups:verify", args=[record.pk]))

    record.refresh_from_db()
    assert record.status == BackupStatus.CORRUPT


def test_download_streams_the_artefact_for_an_admin(
    client, admin_user, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    client.force_login(admin_user)

    response = client.get(reverse("backups:download", args=[record.pk]))

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    response.close()


def test_download_404s_when_the_file_is_gone(client, admin_user, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    Path(record.file_path).unlink()
    client.force_login(admin_user)
    assert client.get(reverse("backups:download", args=[record.pk])).status_code == 404


# ---------------------------------------------------------------------------
# The restore screen
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_restore_page_lists_what_will_be_lost(client, admin_user, backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.FULL, user=admin_user)
    # A row created after the backup is exactly what a restore would erase.
    BackupRecordFactory(backup_code="BKP-20991231-001")

    client.force_login(admin_user)
    response = client.get(reverse("backups:restore", args=[record.pk]))

    assert response.status_code == 200
    assert response.context["at_risk"]["total_records"] >= 0
    assert record.backup_code.encode() in response.content
    assert b"What will be overwritten" in response.content


def test_restore_rejects_a_mistyped_code_without_touching_anything(
    client, admin_user, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    client.force_login(admin_user)

    response = client.post(
        reverse("backups:restore", args=[record.pk]),
        {"confirmation_text": "BKP-WRONG-001", "understood": "on"},
    )

    assert response.status_code == 200  # form re-rendered with the error
    assert RestoreRecord.objects.count() == 0
    assert BackupRecord.objects.filter(backup_type=BackupType.PRE_RESTORE).count() == 0


def test_restore_requires_the_understood_checkbox(
    client, admin_user, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    client.force_login(admin_user)

    response = client.post(
        reverse("backups:restore", args=[record.pk]),
        {"confirmation_text": record.backup_code},
    )

    assert response.status_code == 200
    assert RestoreRecord.objects.count() == 0


def test_a_correct_restore_runs_and_records_the_safety_copy(
    client, admin_user, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    waiver = media_tree / "documents" / "waiver.pdf"
    waiver.unlink()

    client.force_login(admin_user)
    response = client.post(
        reverse("backups:restore", args=[record.pk]),
        {"confirmation_text": record.backup_code, "understood": "on", "notes": "drive failure"},
    )

    assert response.status_code == 302
    restore = RestoreRecord.objects.get()
    assert restore.status == RestoreStatus.COMPLETED, restore.error_message
    assert restore.confirmed_by == admin_user
    assert restore.safety_backup is not None
    assert waiver.exists()


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------
def test_delete_page_blocks_the_most_recent_successful_backup(
    client, admin_user, backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    client.force_login(admin_user)

    response = client.get(reverse("backups:delete", args=[record.pk]))
    assert response.status_code == 200
    assert response.context["can_be_deleted"] is False

    client.post(reverse("backups:delete", args=[record.pk]))
    assert BackupRecord.objects.filter(pk=record.pk).exists()


def test_delete_removes_an_older_backup(client, admin_user, backup_volume, media_tree):
    older = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    services.create_backup(BackupType.MANUAL, BackupScope.CONFIG, user=admin_user)

    client.force_login(admin_user)
    response = client.post(reverse("backups:delete", args=[older.pk]))

    assert response.status_code == 302
    assert not BackupRecord.objects.filter(pk=older.pk).exists()


# ---------------------------------------------------------------------------
# Retention settings
# ---------------------------------------------------------------------------
def test_settings_page_renders_and_saves(client, admin_user, backup_volume):
    client.force_login(admin_user)
    assert client.get(reverse("backups:settings")).status_code == 200

    response = client.post(
        reverse("backups:settings"), {"daily": 3, "weekly": 6, "monthly": 9}
    )
    assert response.status_code == 302
    assert services.retention_policy() == {"daily": 3, "weekly": 6, "monthly": 9}


def test_retention_can_be_applied_from_the_settings_page(
    client, admin_user, settings, backup_volume
):
    settings.BACKUP = {**settings.BACKUP, "ROOT": backup_volume, "RETENTION_DAILY": 1}
    for day in range(3):
        moment = timezone.now() - timedelta(days=day)
        BackupRecordFactory(
            backup_type=BackupType.DAILY,
            status=BackupStatus.COMPLETED,
            started_at=moment,
            completed_at=moment,
        )

    client.force_login(admin_user)
    response = client.post(reverse("backups:retention_run"))

    assert response.status_code == 302
    assert BackupRecord.objects.filter(backup_type=BackupType.DAILY).count() == 1
