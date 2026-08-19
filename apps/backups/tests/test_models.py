from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.backups.models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreStatus,
    human_size,
)

from .factories import BackupRecordFactory, RestoreRecordFactory

pytestmark = pytest.mark.django_db


def test_str_names_the_code_and_scope():
    backup = BackupRecordFactory(backup_code="BKP-20260101-777", scope=BackupScope.FULL)
    assert "BKP-20260101-777" in str(backup)
    assert str(BackupScope.FULL.label) in str(backup)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        (-1, "—"),
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024 * 1024 * 1024 * 3, "3.0 GB"),
    ],
)
def test_human_size(value, expected):
    assert human_size(value) == expected


def test_size_display_uses_the_recorded_byte_count():
    backup = BackupRecordFactory(file_size_bytes=2 * 1024 * 1024)
    assert backup.size_display == "2.0 MB"


def test_exists_on_disk_is_false_without_a_path():
    assert BackupRecordFactory(file_path="").exists_on_disk is False


def test_exists_on_disk_asks_the_filesystem(tmp_path):
    artefact = tmp_path / "copy.sqlite3"
    artefact.write_bytes(b"x" * 10)
    backup = BackupRecordFactory(file_path=str(artefact))
    assert backup.exists_on_disk is True
    assert backup.size_on_disk == 10

    artefact.unlink()
    # The row is unchanged, but the property must tell the truth about the disk.
    assert backup.exists_on_disk is False
    assert backup.size_on_disk is None


def test_age_days_counts_from_completion():
    backup = BackupRecordFactory(completed_at=timezone.now() - timedelta(days=4, hours=1))
    assert backup.age_days == 4


def test_age_days_is_never_negative():
    backup = BackupRecordFactory(completed_at=timezone.now() + timedelta(hours=2))
    assert backup.age_days == 0


def test_is_restorable_requires_completed_restorable_scope_and_a_file(tmp_path):
    artefact = tmp_path / "copy.zip"
    artefact.write_bytes(b"zip")

    ok = BackupRecordFactory(scope=BackupScope.MEDIA, file_path=str(artefact))
    assert ok.is_restorable is True

    # A config manifest holds nothing to write back.
    config = BackupRecordFactory(scope=BackupScope.CONFIG, file_path=str(artefact))
    assert config.is_restorable is False

    failed = BackupRecordFactory(
        scope=BackupScope.MEDIA, status=BackupStatus.FAILED, file_path=str(artefact)
    )
    assert failed.is_restorable is False

    gone = BackupRecordFactory(scope=BackupScope.MEDIA, file_path=str(tmp_path / "nope.zip"))
    assert gone.is_restorable is False


def test_is_stalled_only_for_long_running_runs():
    fresh = BackupRecordFactory(status=BackupStatus.RUNNING, started_at=timezone.now())
    assert fresh.is_stalled is False

    abandoned = BackupRecordFactory(
        status=BackupStatus.RUNNING, started_at=timezone.now() - timedelta(hours=6)
    )
    assert abandoned.is_stalled is True

    finished = BackupRecordFactory(
        status=BackupStatus.COMPLETED, started_at=timezone.now() - timedelta(hours=6)
    )
    assert finished.is_stalled is False


def test_protected_types_are_manual_and_pre_restore():
    assert BackupRecordFactory(backup_type=BackupType.MANUAL).is_protected_from_retention
    assert BackupRecordFactory(backup_type=BackupType.PRE_RESTORE).is_protected_from_retention
    assert not BackupRecordFactory(backup_type=BackupType.DAILY).is_protected_from_retention


def test_scope_coverage_flags():
    assert BackupRecordFactory(scope=BackupScope.FULL).covers_database
    assert BackupRecordFactory(scope=BackupScope.FULL).covers_media
    assert BackupRecordFactory(scope=BackupScope.DATABASE).covers_database
    assert not BackupRecordFactory(scope=BackupScope.DATABASE).covers_media
    assert not BackupRecordFactory(scope=BackupScope.CONFIG).covers_database


def test_check_rows_flattens_the_stored_gate_results():
    restore = RestoreRecordFactory(
        pre_restore_checks={
            "integrity_verified": {"passed": True, "detail": "ok"},
            "confirmation_typed": {"passed": False, "detail": "no match"},
            "legacy_boolean": True,
        }
    )
    rows = restore.check_rows
    assert [row["name"] for row in rows] == [
        "integrity_verified",
        "confirmation_typed",
        "legacy_boolean",
    ]
    assert rows[0]["passed"] is True
    assert rows[1]["passed"] is False
    assert rows[2]["passed"] is True


def test_soft_delete_hides_the_row_but_keeps_the_history():
    backup = BackupRecordFactory()
    backup.delete()
    assert not BackupRecord.objects.filter(pk=backup.pk).exists()
    assert BackupRecord.all_objects.filter(pk=backup.pk).exists()


def test_restore_status_choices_include_rolled_back():
    assert RestoreStatus.ROLLED_BACK in RestoreStatus.values
