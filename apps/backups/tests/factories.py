"""Factories for backup and restore records.

These build *rows*, not files. Anything that needs a real artefact on disk
should call :func:`apps.backups.services.create_backup`, so the test exercises
the code that actually writes bytes.
"""

from __future__ import annotations

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.backups.models import (
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreRecord,
    RestoreStatus,
)


class BackupRecordFactory(DjangoModelFactory):
    class Meta:
        model = BackupRecord
        django_get_or_create = ("backup_code",)

    backup_code = factory.Sequence(lambda n: f"BKP-20260101-{n + 1:03d}")
    backup_type = BackupType.MANUAL
    scope = BackupScope.DATABASE
    status = BackupStatus.COMPLETED
    file_path = ""
    file_size_bytes = 1024
    checksum_sha256 = "0" * 64
    database_engine = "django.db.backends.sqlite3"
    django_version = "5.2"
    app_version = "1.0.0"
    started_at = factory.LazyFunction(timezone.now)
    completed_at = factory.LazyFunction(timezone.now)
    duration_ms = 250
    notes = "Created by the test suite."
    is_verified = False


class RestoreRecordFactory(DjangoModelFactory):
    class Meta:
        model = RestoreRecord

    backup = factory.SubFactory(BackupRecordFactory)
    status = RestoreStatus.PENDING
    started_at = factory.LazyFunction(timezone.now)
    confirmation_text = ""
    pre_restore_checks = factory.LazyFunction(dict)
