from __future__ import annotations

import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.constants import Role
from apps.backups import services
from apps.backups.models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
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
    """Point BACKUP["ROOT"] at a directory that only this test can see."""
    root = tmp_path / "backup-volume"
    settings.BACKUP = {**settings.BACKUP, "ROOT": root}
    return root


@pytest.fixture
def media_tree(settings, tmp_path):
    """A small MEDIA_ROOT with one public file and one private one."""
    root = tmp_path / "media"
    (root / "documents").mkdir(parents=True)
    (root / "private").mkdir(parents=True)
    (root / "documents" / "waiver.pdf").write_bytes(b"signed waiver bytes")
    (root / "private" / "passport.jpg").write_bytes(b"identity document bytes")
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
    """Holds backups.view and backups.add — but neither restore nor delete."""
    return User.objects.create_user(
        username="manager", email="manager@example.com", password="pw-test-12345",
        role=Role.MANAGER,
    )


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------
def test_backup_codes_are_sequential_within_a_day(backup_volume):
    first = services.create_backup(BackupType.MANUAL, BackupScope.CONFIG)
    second = services.create_backup(BackupType.MANUAL, BackupScope.CONFIG)
    assert first.backup_code.startswith("BKP-")
    assert int(second.backup_code.split("-")[-1]) == int(first.backup_code.split("-")[-1]) + 1


def test_backup_code_numbering_survives_past_999(backup_volume):
    today = timezone.localdate()
    BackupRecordFactory(backup_code=f"BKP-{today:%Y%m%d}-999")
    BackupRecordFactory(backup_code=f"BKP-{today:%Y%m%d}-1000")
    # Lexicographic ordering would pick "999"; the numeric maximum is 1000.
    assert services._next_backup_code(today).endswith("-1001")


# ---------------------------------------------------------------------------
# Creating backups
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_database_backup_writes_a_verifiable_artefact(backup_volume, admin_user):
    # transaction=True: the backup API reads the database through its own
    # connection, and cannot copy one that another connection is holding open
    # mid-write. That is the same constraint production has.
    record = services.create_backup(BackupType.MANUAL, BackupScope.DATABASE, user=admin_user)

    assert record.status == BackupStatus.COMPLETED, record.error_message
    assert record.exists_on_disk
    assert record.file_size_bytes > 0
    assert len(record.checksum_sha256) == 64
    assert record.created_by == admin_user
    assert record.database_engine == "django.db.backends.sqlite3"

    verified, message = services.verify_backup(record)
    assert verified, message
    record.refresh_from_db()
    assert record.is_verified is True
    assert record.verified_at is not None


def test_media_backup_zips_public_files_and_never_the_private_folder(
    backup_volume, media_tree
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    assert record.status == BackupStatus.COMPLETED, record.error_message

    with zipfile.ZipFile(record.file_path) as archive:
        names = archive.namelist()
    assert "documents/waiver.pdf" in names
    assert not any(name.startswith("private/") for name in names)

    verified, message = services.verify_backup(record)
    assert verified, message


def test_config_backup_stores_env_key_names_but_never_values(
    backup_volume, settings, tmp_path
):
    settings.BASE_DIR = tmp_path
    (tmp_path / ".env").write_text(
        "# comment line\n"
        "DJANGO_SECRET_KEY=super-secret-value-do-not-leak\n"
        "OPENAI_API_KEY=sk-not-a-real-key\n"
        "\n"
        "SCHOOL_NAME=Test School\n",
        encoding="utf-8",
    )

    record = services.create_backup(BackupType.MANUAL, BackupScope.CONFIG)
    assert record.status == BackupStatus.COMPLETED, record.error_message

    with zipfile.ZipFile(record.file_path) as archive:
        manifest = json.loads(archive.read("config_manifest.json").decode("utf-8"))

    assert "DJANGO_SECRET_KEY" in manifest["env_keys"]
    assert "OPENAI_API_KEY" in manifest["env_keys"]

    # The whole artefact is searched, not just the parsed manifest: a value must
    # not reach the file by any route.
    raw = Path(record.file_path).read_bytes()
    assert b"super-secret-value-do-not-leak" not in raw
    assert b"sk-not-a-real-key" not in raw


@pytest.mark.django_db(transaction=True)
def test_full_backup_contains_database_media_and_manifest(backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.FULL)
    assert record.status == BackupStatus.COMPLETED, record.error_message

    with zipfile.ZipFile(record.file_path) as archive:
        names = archive.namelist()
    assert any(name.startswith("database/") for name in names)
    assert "media/documents/waiver.pdf" in names
    assert "config_manifest.json" in names
    assert not any(name.startswith("media/private/") for name in names)


def test_a_failing_backup_records_the_error_and_never_raises(
    backup_volume, monkeypatch, admin_user
):
    def explode(_target):
        raise OSError("the backup volume went away")

    monkeypatch.setattr(services, "_dump_database", explode)

    record = services.create_backup(BackupType.DAILY, BackupScope.DATABASE, user=admin_user)

    assert record.status == BackupStatus.FAILED
    assert "backup volume went away" in record.error_message
    assert record.file_path == ""
    assert record.checksum_sha256 == ""
    # Nothing half-written may survive: a partial file looks restorable.
    assert list(backup_volume.glob("*")) == []


def test_backup_root_is_created_when_missing(backup_volume):
    assert not backup_volume.exists()
    services.create_backup(BackupType.MANUAL, BackupScope.CONFIG)
    assert backup_volume.is_dir()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def test_a_tampered_artefact_is_marked_corrupt(backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    assert record.status == BackupStatus.COMPLETED

    with Path(record.file_path).open("ab") as handle:
        handle.write(b"tampered")

    verified, message = services.verify_backup(record)
    assert verified is False
    assert "hecksum" in message
    record.refresh_from_db()
    assert record.status == BackupStatus.CORRUPT
    assert record.is_verified is False


def test_a_missing_file_is_marked_corrupt(backup_volume, media_tree):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    Path(record.file_path).unlink()

    verified, _message = services.verify_backup(record)
    assert verified is False
    record.refresh_from_db()
    assert record.status == BackupStatus.CORRUPT


def test_verification_of_a_failed_run_says_so(backup_volume):
    record = BackupRecordFactory(status=BackupStatus.FAILED, file_path="")
    verified, message = services.verify_backup(record)
    assert verified is False
    assert "failed" in str(message).lower()


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------
def test_restore_refuses_a_wrong_confirmation_code(backup_volume, media_tree, admin_user):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)

    restore = services.restore_backup(record, admin_user, "not-the-code")

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["confirmation_typed"]["passed"] is False
    # It never got as far as taking a safety copy.
    assert restore.safety_backup is None
    assert "confirmation" in restore.error_message.lower()


def test_restore_refuses_a_user_without_the_capability(backup_volume, media_tree, manager):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)

    restore = services.restore_backup(record, manager, record.backup_code)

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["capability_backups_restore"]["passed"] is False
    assert restore.safety_backup is None


def test_restore_refuses_a_corrupt_backup(backup_volume, media_tree, admin_user):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    with Path(record.file_path).open("ab") as handle:
        handle.write(b"corruption")

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["integrity_verified"]["passed"] is False
    assert restore.safety_backup is None


def test_restore_refuses_a_config_backup(backup_volume, admin_user):
    record = services.create_backup(BackupType.MANUAL, BackupScope.CONFIG)

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["scope_restorable"]["passed"] is False


@pytest.mark.django_db(transaction=True)
def test_restore_refuses_a_backup_from_another_database_engine(
    backup_volume, admin_user
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.DATABASE, user=admin_user)
    BackupRecord.objects.filter(pk=record.pk).update(
        database_engine="django.db.backends.postgresql"
    )
    record.refresh_from_db()

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["engine_matches"]["passed"] is False


def test_media_restore_takes_a_safety_copy_first_and_puts_the_files_back(
    backup_volume, media_tree, admin_user
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    waiver = media_tree / "documents" / "waiver.pdf"
    waiver.unlink()
    assert not waiver.exists()

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.COMPLETED, restore.error_message
    assert waiver.read_bytes() == b"signed waiver bytes"

    # The safety copy exists, is linked, and is of the PRE_RESTORE type so the
    # retention sweep will never remove it.
    safety = restore.safety_backup
    assert safety is not None
    assert safety.backup_type == BackupType.PRE_RESTORE
    assert safety.status == BackupStatus.COMPLETED
    assert safety.exists_on_disk

    checks = restore.pre_restore_checks
    assert checks["integrity_verified"]["passed"] is True
    assert checks["confirmation_typed"]["passed"] is True
    assert checks["capability_backups_restore"]["passed"] is True
    assert checks["safety_backup_taken"]["passed"] is True
    assert checks["restore_applied"]["passed"] is True


def test_a_failed_restore_rolls_back_to_the_safety_copy(
    backup_volume, media_tree, admin_user, monkeypatch
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)

    calls = {"count": 0}
    original = services._apply_backup

    def fail_once(target):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("disk went read-only mid-restore")
        return original(target)

    monkeypatch.setattr(services, "_apply_backup", fail_once)

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.ROLLED_BACK
    assert restore.pre_restore_checks["restore_applied"]["passed"] is False
    assert restore.pre_restore_checks["rolled_back"]["passed"] is True
    assert calls["count"] == 2  # the failed restore, then the rollback


def test_a_restore_without_a_safety_copy_never_starts(
    backup_volume, media_tree, admin_user, monkeypatch
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)

    def failed_safety(*args, **kwargs):
        return BackupRecordFactory(
            status=BackupStatus.FAILED,
            backup_type=BackupType.PRE_RESTORE,
            error_message="no space left on device",
        )

    monkeypatch.setattr(services, "create_backup", failed_safety)
    applied = {"called": False}
    monkeypatch.setattr(
        services, "_apply_backup", lambda target: applied.update(called=True)
    )

    restore = services.restore_backup(record, admin_user, record.backup_code)

    assert restore.status == RestoreStatus.FAILED
    assert restore.pre_restore_checks["safety_backup_taken"]["passed"] is False
    assert applied["called"] is False


def test_reinstating_paperwork_refreshes_a_stale_row_and_recreates_a_missing_one(
    backup_volume, media_tree, admin_user
):
    """The restored database holds an out-of-date copy of the backup's own row.

    A full archive captures that row while the run is still ``RUNNING`` — no file
    path, no checksum. Reinstatement must correct it, or retention would discard
    the school's newest good backup and orphan its file.
    """
    source = services.create_backup(BackupType.DAILY, BackupScope.MEDIA, user=admin_user)
    safety = services.create_backup(BackupType.PRE_RESTORE, BackupScope.MEDIA, user=admin_user)
    restore = services.RestoreRecord.objects.create(
        backup=source, safety_backup=safety, status=RestoreStatus.RUNNING
    )

    # Simulate what the restored database contains: the source row as it looked
    # mid-run, and no trace of the safety copy or the restore at all.
    BackupRecord.all_objects.filter(pk=source.pk).update(
        status=BackupStatus.RUNNING, file_path="", checksum_sha256="", file_size_bytes=0
    )
    safety_public_id = safety.public_id
    restore_public_id = restore.public_id
    services.RestoreRecord.all_objects.filter(pk=restore.pk).delete()
    BackupRecord.all_objects.filter(pk=safety.pk).delete()

    reinstated = services._reinstate_records(restore, source, safety)

    refreshed = BackupRecord.all_objects.get(public_id=source.public_id)
    assert refreshed.status == BackupStatus.COMPLETED
    assert refreshed.checksum_sha256 == source.checksum_sha256
    assert refreshed.file_path == source.file_path

    recovered_safety = BackupRecord.all_objects.get(public_id=safety_public_id)
    assert recovered_safety.backup_type == BackupType.PRE_RESTORE
    assert recovered_safety.created_at == safety.created_at

    assert reinstated.public_id == restore_public_id
    assert reinstated.safety_backup_id == recovered_safety.pk
    assert reinstated.backup_id == refreshed.pk


def test_zip_slip_entries_are_refused(backup_volume, media_tree, tmp_path):
    evil = backup_volume
    evil.mkdir(parents=True, exist_ok=True)
    archive_path = evil / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../escaped.txt", "pwned")

    with pytest.raises(services.BackupError):
        services._extract_media(archive_path, "")


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------
def test_delete_requires_the_capability(backup_volume, media_tree, manager):
    newer = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA)
    older = BackupRecordFactory(completed_at=timezone.now() - timedelta(days=3))
    assert newer  # the newest copy is protected, so delete the older row

    with pytest.raises(PermissionDenied):
        services.delete_backup(older, manager)


def test_the_most_recent_successful_backup_cannot_be_deleted(
    backup_volume, media_tree, admin_user
):
    record = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    allowed, reason = services.can_delete_backup(record)
    assert allowed is False
    assert "most recent" in reason

    with pytest.raises(ValidationError):
        services.delete_backup(record, admin_user)


def test_delete_removes_the_file_and_archives_the_row(
    backup_volume, media_tree, admin_user
):
    older = services.create_backup(BackupType.MANUAL, BackupScope.MEDIA, user=admin_user)
    older_path = Path(older.file_path)
    services.create_backup(BackupType.MANUAL, BackupScope.CONFIG, user=admin_user)

    freed = services.delete_backup(older, admin_user)

    assert freed > 0
    assert not older_path.exists()
    assert not BackupRecord.objects.filter(pk=older.pk).exists()
    assert BackupRecord.all_objects.filter(pk=older.pk).exists()


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def _dated_backup(days_ago: int, backup_type: str = BackupType.DAILY) -> BackupRecord:
    moment = timezone.now() - timedelta(days=days_ago)
    return BackupRecordFactory(
        backup_type=backup_type,
        status=BackupStatus.COMPLETED,
        started_at=moment,
        completed_at=moment,
        file_size_bytes=100,
    )


def test_retention_keeps_the_configured_number_of_daily_copies(settings, backup_volume):
    settings.BACKUP = {
        **settings.BACKUP,
        "ROOT": backup_volume,
        "RETENTION_DAILY": 2,
        "RETENTION_WEEKLY": 1,
        "RETENTION_MONTHLY": 1,
    }
    for day in range(5):
        _dated_backup(days_ago=day)

    summary = services.apply_retention_policy()

    assert summary["deleted"] == 3
    remaining = BackupRecord.objects.filter(backup_type=BackupType.DAILY)
    assert remaining.count() == 2


def test_retention_never_removes_a_manual_or_pre_restore_backup(settings, backup_volume):
    settings.BACKUP = {**settings.BACKUP, "ROOT": backup_volume, "RETENTION_DAILY": 1}
    _dated_backup(days_ago=1, backup_type=BackupType.MANUAL)
    _dated_backup(days_ago=2, backup_type=BackupType.MANUAL)
    _dated_backup(days_ago=3, backup_type=BackupType.PRE_RESTORE)
    for day in range(4, 8):
        _dated_backup(days_ago=day)

    services.apply_retention_policy()

    assert BackupRecord.objects.filter(backup_type=BackupType.MANUAL).count() == 2
    assert BackupRecord.objects.filter(backup_type=BackupType.PRE_RESTORE).count() == 1


def test_retention_never_removes_the_most_recent_successful_backup(
    settings, backup_volume
):
    settings.BACKUP = {**settings.BACKUP, "ROOT": backup_volume, "RETENTION_DAILY": 1}
    newest = _dated_backup(days_ago=0)
    for day in range(1, 5):
        _dated_backup(days_ago=day)

    services.apply_retention_policy()

    # Whatever the numbers say, the school is never left with nothing.
    assert BackupRecord.objects.filter(pk=newest.pk).exists()
    assert BackupRecord.objects.count() == 1


def test_retention_preview_matches_what_the_sweep_would_remove(settings, backup_volume):
    settings.BACKUP = {**settings.BACKUP, "ROOT": backup_volume, "RETENTION_DAILY": 1}
    for day in range(4):
        _dated_backup(days_ago=day)

    preview = {row.pk for row in services.retention_preview()}
    services.apply_retention_policy()
    surviving = set(BackupRecord.objects.values_list("pk", flat=True))

    assert preview and not (preview & surviving)


def test_retention_policy_reads_the_saved_override(backup_volume, admin_user):
    services.save_retention_policy({"daily": 3, "weekly": 2, "monthly": 5}, user=admin_user)
    assert services.retention_policy() == {"daily": 3, "weekly": 2, "monthly": 5}


def test_retention_policy_clamps_nonsense_values(backup_volume, admin_user):
    services.save_retention_policy({"daily": 0, "weekly": 9999, "monthly": 4})
    policy = services.retention_policy()
    assert policy["daily"] == 1
    assert policy["weekly"] == 365


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def test_statistics_report_critical_when_nothing_has_ever_succeeded(backup_volume):
    stats = services.backup_statistics()
    assert stats["health"] == services.HEALTH_CRITICAL
    assert stats["last_successful"] is None


def test_statistics_report_healthy_after_a_fresh_backup(backup_volume, media_tree):
    services.create_backup(BackupType.DAILY, BackupScope.MEDIA)
    stats = services.backup_statistics()
    assert stats["health"] == services.HEALTH_HEALTHY
    assert stats["completed"] == 1
    assert stats["total_bytes"] > 0
    assert stats["by_scope"][BackupScope.MEDIA]["count"] == 1


def test_statistics_warn_when_the_newest_copy_is_getting_old(backup_volume):
    _dated_backup(days_ago=3)
    stats = services.backup_statistics()
    assert stats["health"] == services.HEALTH_WARNING


def test_statistics_flag_a_backup_whose_file_has_vanished(backup_volume, media_tree):
    record = services.create_backup(BackupType.DAILY, BackupScope.MEDIA)
    Path(record.file_path).unlink()
    stats = services.backup_statistics()
    assert stats["missing_files"] == 1
    assert stats["health"] == services.HEALTH_WARNING
