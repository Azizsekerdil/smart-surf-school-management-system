"""Everything that actually touches the disk.

Design notes that matter
------------------------
* **SQLite is copied with the online backup API**, never ``shutil.copy``. A file
  copy of a live SQLite database (which this project runs in WAL mode) can be
  torn: the ``.sqlite3`` file and its ``-wal`` sidecar are only consistent
  together. ``sqlite3.Connection.backup()`` walks the pages under a read lock and
  produces a single self-contained, consistent file while the school keeps
  working.
* **PostgreSQL is dumped with ``pg_dump`` in custom format**, invoked with an
  argument *list* — never ``shell=True``, so no path containing a space or an
  ampersand can turn into a command. The password is handed over through the
  ``PGPASSWORD`` environment variable of the child process, because anything on
  the command line is visible to every other user in the process list.
* **Nothing here trusts a database row.** Every function re-checks the file.
* :func:`create_backup` never raises. A backup run that explodes must leave a
  FAILED row explaining why, not a traceback in a Celery log nobody reads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess  # noqa: S404 - used only with argument lists, never shell=True  # nosec B404
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

import django
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connections, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit
from apps.core.models import SystemSetting

from .models import (
    RESTORABLE_SCOPES,
    BackupRecord,
    BackupScope,
    BackupStatus,
    BackupType,
    RestoreRecord,
    RestoreStatus,
    human_size,
)

logger = logging.getLogger("apps.backups")

#: Read buffer for hashing. 1 MiB keeps a multi-gigabyte dump off the heap.
_HASH_CHUNK = 1024 * 1024

#: Lock wait for the stdlib SQLite connections used for backup and restore.
SQLITE_TIMEOUT_SECONDS = 60

#: Hard ceiling on a pg_dump / pg_restore child process.
PG_TIMEOUT_SECONDS = 3600

#: SystemSetting group and keys used by the retention settings screen.
RETENTION_GROUP = "backups"
RETENTION_KEYS = {
    "daily": "backups.retention_daily",
    "weekly": "backups.retention_weekly",
    "monthly": "backups.retention_monthly",
}

#: Directory inside MEDIA_ROOT that is never backed up: it holds identity
#: documents and medical notes that must not be copied onto a backup volume.
PRIVATE_MEDIA_DIRNAME = "private"

#: Names inside a FULL archive.
ARCHIVE_DATABASE_DIR = "database/"
ARCHIVE_MEDIA_DIR = "media/"
ARCHIVE_MANIFEST_NAME = "config_manifest.json"


class BackupError(Exception):
    """A backup or restore could not be completed. Message is operator-facing."""


# ---------------------------------------------------------------------------
# Paths, codes and hashes
# ---------------------------------------------------------------------------
def backup_root() -> Path:
    """The directory backups are written to, created if it is missing."""
    root = Path(settings.BACKUP["ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _database_settings() -> dict:
    return settings.DATABASES["default"]


def _database_engine() -> str:
    return _database_settings().get("ENGINE", "")


def database_engine() -> str:
    """The engine the site is running on right now (public read for templates)."""
    return _database_engine()


def database_name() -> str:
    """The database a restore would overwrite."""
    return str(_database_settings().get("NAME", ""))


def is_sqlite() -> bool:
    return _database_engine().endswith("sqlite3")


def is_postgresql() -> bool:
    return "postgresql" in _database_engine()


def _next_backup_code(when: date | None = None) -> str:
    """Return the next ``BKP-YYYYMMDD-NNN`` code for the given day.

    The maximum is computed numerically, not lexicographically, so the sequence
    stays correct past the 999th backup of a single day.
    """
    day = when or timezone.localdate()
    prefix = f"BKP-{day:%Y%m%d}-"
    used = BackupRecord.all_objects.filter(backup_code__startswith=prefix).values_list(
        "backup_code", flat=True
    )
    highest = 0
    for code in used:
        suffix = str(code)[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:03d}"


def sha256_of(path: Path) -> str:
    """Stream a file through SHA-256 without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_quietly(path: Path | None) -> int:
    """Delete a file if present; return the bytes reclaimed."""
    if path is None:
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    try:
        path.unlink()
    except OSError as error:
        logger.warning("Could not remove backup artefact %s: %s", path, error)
        return 0
    return size


# ---------------------------------------------------------------------------
# Retention policy (settings.BACKUP defaults, overridable from the UI)
# ---------------------------------------------------------------------------
def retention_policy() -> dict[str, int]:
    """Effective retention counts: SystemSetting override, else ``settings.BACKUP``."""
    defaults = {
        "daily": int(settings.BACKUP.get("RETENTION_DAILY", 7)),
        "weekly": int(settings.BACKUP.get("RETENTION_WEEKLY", 4)),
        "monthly": int(settings.BACKUP.get("RETENTION_MONTHLY", 12)),
    }
    policy: dict[str, int] = {}
    for name, default in defaults.items():
        stored = SystemSetting.get(RETENTION_KEYS[name], None)
        try:
            value = int(stored) if stored is not None else default
        except (TypeError, ValueError):
            value = default
        # At least one copy of each cadence must survive, or a scheduled sweep
        # would quietly leave the school with nothing.
        policy[name] = max(1, min(value, 365))
    return policy


def save_retention_policy(policy: dict[str, int], *, user=None, request=None) -> dict[str, int]:
    """Persist retention counts and audit the change."""
    current = retention_policy()
    changes: dict[str, list] = {}
    for name, key in RETENTION_KEYS.items():
        if name not in policy:
            continue
        value = max(1, min(int(policy[name]), 365))
        if value != current.get(name):
            changes[key] = [current.get(name), value]
        SystemSetting.set(key, value, SystemSetting.ValueType.INTEGER, group=RETENTION_GROUP)
    if changes:
        record_audit(
            request,
            action=AuditAction.SETTINGS_CHANGE,
            user=user,
            description=_("Backup retention policy updated"),
            changes=changes,
        )
    return retention_policy()


# ---------------------------------------------------------------------------
# Engine-level helpers
# ---------------------------------------------------------------------------
def _connect_live_sqlite() -> sqlite3.Connection:
    """Open a **separate** connection to the database this site runs on.

    Deliberately not Django's own handle: that connection may be sitting inside
    a transaction, and SQLite's backup API cannot copy a database through a
    connection that is mid-write — it would spin on ``SQLITE_BUSY`` forever.
    Django's test runner names its in-memory database with a shared-cache URI,
    which is why the URI form is handled here as well.
    """
    name = str(_database_settings().get("NAME", "") or "")
    if not name:
        raise BackupError(_("No SQLite database is configured."))
    if name.startswith("file:"):
        return sqlite3.connect(name, uri=True, timeout=SQLITE_TIMEOUT_SECONDS)
    if name == ":memory:":
        raise BackupError(
            _(
                "A private in-memory database cannot be backed up — nothing "
                "outside the process that created it can read it."
            )
        )
    if not Path(name).is_file():
        raise BackupError(
            _("The SQLite database file %(path)s was not found.") % {"path": name}
        )
    return sqlite3.connect(name, timeout=SQLITE_TIMEOUT_SECONDS)


def _copy_sqlite_database(
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    *,
    deadline_seconds: int = SQLITE_TIMEOUT_SECONDS,
) -> None:
    """Run the backup API in chunks, with a wall-clock deadline.

    ``Connection.backup`` retries a locked database **forever**. A scheduled job
    that hangs instead of failing is worse than one that fails, so the progress
    callback raises once the deadline passes — CPython propagates that out of the
    loop, which is the only reliable way to interrupt it.
    """
    deadline = time.monotonic() + deadline_seconds

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise BackupError(
                _(
                    "The database stayed locked for %(seconds)s seconds, so the "
                    "copy was abandoned rather than left to hang."
                )
                % {"seconds": deadline_seconds}
            )

    with destination:
        source.backup(destination, pages=512, progress=progress, sleep=0.1)


def _sqlite_online_backup(target_path: Path) -> None:
    """Copy the live SQLite database consistently, using the backup API."""
    destination = sqlite3.connect(str(target_path), timeout=SQLITE_TIMEOUT_SECONDS)
    try:
        source = _connect_live_sqlite()
        try:
            _copy_sqlite_database(source, destination)
        finally:
            source.close()
        # Leave the artefact as one self-contained file: a WAL sidecar next to a
        # backup is half a copy waiting to be lost.
        destination.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        destination.close()


def _sqlite_integrity_check(path: Path) -> tuple[bool, str]:
    """Run ``PRAGMA integrity_check`` against a backup file.

    This is the SQLite engine inspecting one of our own artefacts through the
    stdlib driver — not an ORM query, and never touching user input.
    """
    try:
        connection = sqlite3.connect(str(path), timeout=SQLITE_TIMEOUT_SECONDS)
    except sqlite3.Error as error:
        return False, str(error)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as error:
        return False, str(error)
    finally:
        connection.close()
    results = [str(row[0]) for row in rows]
    if results == ["ok"]:
        return True, ""
    return False, "; ".join(results[:5])


def _postgres_executable(setting_key: str, program: str) -> str:
    """Locate ``pg_dump``/``pg_restore``, or explain exactly how to fix it."""
    configured = (settings.BACKUP.get(setting_key) or "").strip()
    if configured:
        if Path(configured).is_file():
            return configured
        raise BackupError(
            _(
                "%(setting)s points at %(path)s, which does not exist. Correct the "
                "path in .env so it names the %(program)s executable."
            )
            % {"setting": setting_key, "path": configured, "program": program}
        )
    found = shutil.which(program)
    if found:
        return found
    raise BackupError(
        _(
            "%(program)s was not found. Install the PostgreSQL client tools, then "
            "either add their bin directory to PATH or set %(setting)s in .env to "
            "the full path of the executable "
            "(for example C:\\Program Files\\PostgreSQL\\16\\bin\\%(program)s.exe)."
        )
        % {"program": program, "setting": setting_key}
    )


def _postgres_connection_arguments() -> tuple[list[str], dict[str, str]]:
    """Build shared connection flags and a child environment carrying the password."""
    config = _database_settings()
    arguments: list[str] = []
    if config.get("HOST"):
        arguments += ["--host", str(config["HOST"])]
    if config.get("PORT"):
        arguments += ["--port", str(config["PORT"])]
    if config.get("USER"):
        arguments += ["--username", str(config["USER"])]
    arguments.append("--no-password")

    child_env = os.environ.copy()
    password = config.get("PASSWORD") or ""
    if password:
        # Never on the command line: argv is world-readable in the process list.
        child_env["PGPASSWORD"] = str(password)
    return arguments, child_env


def _run_postgres_tool(arguments: list[str], child_env: dict[str, str], program: str) -> None:
    """Run a PostgreSQL client tool with an argument list (never a shell)."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv list, shell=False
            arguments,
            env=child_env,
            capture_output=True,
            shell=False,  # nosec B603
            timeout=PG_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise BackupError(
            _("%(program)s could not be started: %(error)s")
            % {"program": program, "error": error}
        ) from error
    except subprocess.TimeoutExpired as error:
        raise BackupError(
            _("%(program)s did not finish within %(seconds)s seconds and was stopped.")
            % {"program": program, "seconds": PG_TIMEOUT_SECONDS}
        ) from error

    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupError(
            _("%(program)s failed (exit code %(code)s): %(detail)s")
            % {
                "program": program,
                "code": completed.returncode,
                "detail": detail[:2000] or _("no output"),
            }
        )


def _postgres_dump(target_path: Path) -> None:
    executable = _postgres_executable("PG_DUMP_PATH", "pg_dump")
    connection_arguments, child_env = _postgres_connection_arguments()
    arguments = [
        executable,
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(target_path),
        *connection_arguments,
        "--dbname",
        str(_database_settings().get("NAME", "")),
    ]
    _run_postgres_tool(arguments, child_env, "pg_dump")


def _postgres_restore(source_path: Path) -> None:
    executable = _postgres_executable("PG_RESTORE_PATH", "pg_restore")
    connection_arguments, child_env = _postgres_connection_arguments()
    arguments = [
        executable,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--single-transaction",
        *connection_arguments,
        "--dbname",
        str(_database_settings().get("NAME", "")),
        str(source_path),
    ]
    _run_postgres_tool(arguments, child_env, "pg_restore")


def _dump_database(target_path: Path) -> str:
    """Write a consistent database dump to *target_path*; return a short note."""
    if is_sqlite():
        _sqlite_online_backup(target_path)
        return _("SQLite online backup (page-level, consistent)")
    if is_postgresql():
        _postgres_dump(target_path)
        return _("pg_dump, custom format")
    raise BackupError(
        _("Database engine %(engine)s is not supported by the backup module.")
        % {"engine": _database_engine()}
    )


def _database_artifact_suffix() -> str:
    return ".sqlite3" if is_sqlite() else ".dump"


# ---------------------------------------------------------------------------
# Media and configuration
# ---------------------------------------------------------------------------
def _media_files() -> list[Path]:
    """Every file under MEDIA_ROOT that belongs in a backup.

    ``media/private`` is skipped by design — identity documents and medical
    notes must not be copied onto a backup volume that may leave the building.
    The backup directory itself is skipped too, so a backup can never contain
    the previous backups.
    """
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.is_dir():
        return []
    try:
        private_dir = (media_root / PRIVATE_MEDIA_DIRNAME).resolve()
        backup_dir = backup_root().resolve()
    except OSError:
        private_dir = media_root / PRIVATE_MEDIA_DIRNAME
        backup_dir = Path(settings.BACKUP["ROOT"])

    files: list[Path] = []
    for candidate in sorted(media_root.rglob("*")):
        try:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == private_dir or private_dir in resolved.parents:
            continue
        if resolved == backup_dir or backup_dir in resolved.parents:
            continue
        files.append(candidate)
    return files


def _write_media_into(archive: zipfile.ZipFile, prefix: str) -> tuple[int, int]:
    """Add MEDIA_ROOT to an open archive. Returns ``(files, skipped)``."""
    media_root = Path(settings.MEDIA_ROOT)
    written = 0
    skipped = 0
    for path in _media_files():
        try:
            relative = path.relative_to(media_root).as_posix()
            archive.write(path, arcname=f"{prefix}{relative}")
            written += 1
        except (OSError, ValueError) as error:
            skipped += 1
            logger.warning("Skipping unreadable media file %s: %s", path, error)
    return written, skipped


def config_manifest() -> dict:
    """A description of how this installation is configured — names only.

    The ``.env`` file is read for its **keys**. Values are discarded on the line
    they are parsed, and never stored, logged or returned. Knowing that
    ``STRIPE_SECRET_KEY`` is expected is what makes a rebuild possible; knowing
    what it contains is what makes a leaked backup a breach.
    """
    env_file = Path(settings.BASE_DIR) / ".env"
    keys: set[str] = set()
    if env_file.is_file():
        try:
            for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key.lower().startswith("export "):
                    key = key[7:].strip()
                if key:
                    keys.add(key)
        except OSError as error:
            logger.warning("Could not read .env for the config manifest: %s", error)

    return {
        "generated_at": timezone.now().isoformat(),
        "app_version": getattr(settings, "APP_VERSION", ""),
        "django_version": django.get_version(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "database_engine": _database_engine(),
        "database_name": Path(str(_database_settings().get("NAME", ""))).name,
        "time_zone": settings.TIME_ZONE,
        "language_code": settings.LANGUAGE_CODE,
        "installed_apps": [app for app in settings.INSTALLED_APPS if app.startswith("apps.")],
        "media_root_name": Path(settings.MEDIA_ROOT).name,
        "env_file_present": env_file.is_file(),
        "env_keys": sorted(keys),
        "note": (
            "Environment variable NAMES only. Values are deliberately excluded "
            "and are never written to a backup."
        ),
    }


# ---------------------------------------------------------------------------
# Writers, one per scope
# ---------------------------------------------------------------------------
def _write_database_backup(target: Path) -> dict:
    note = _dump_database(target)
    return {"method": str(note)}


def _write_media_backup(target: Path) -> dict:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        written, skipped = _write_media_into(archive, "")
    return {"files": written, "unreadable_files": skipped}


def _write_config_backup(target: Path) -> dict:
    manifest = config_manifest()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr(ARCHIVE_MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"env_keys": len(manifest["env_keys"])}


def _write_full_backup(target: Path) -> dict:
    """Database + media + manifest in a single archive.

    The database is dumped to a temporary file first so the archive only ever
    receives a finished, consistent artefact.
    """
    detail: dict = {}
    with tempfile.TemporaryDirectory(prefix="surf-backup-") as staging:
        dump_path = Path(staging) / f"database{_database_artifact_suffix()}"
        detail["method"] = str(_dump_database(dump_path))
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.write(dump_path, arcname=f"{ARCHIVE_DATABASE_DIR}{dump_path.name}")
            written, skipped = _write_media_into(archive, ARCHIVE_MEDIA_DIR)
            manifest = config_manifest()
            archive.writestr(
                ARCHIVE_MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False)
            )
        detail["files"] = written
        detail["unreadable_files"] = skipped
    return detail


_WRITERS = {
    BackupScope.DATABASE: (_write_database_backup, None),
    BackupScope.MEDIA: (_write_media_backup, ".zip"),
    BackupScope.CONFIG: (_write_config_backup, ".zip"),
    BackupScope.FULL: (_write_full_backup, ".zip"),
}


def _artifact_name(code: str, scope: str) -> str:
    suffix = _WRITERS[scope][1] or _database_artifact_suffix()
    return f"{code}_{scope}{suffix}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def _reserve_record(backup_type: str, scope: str, user, notes: str) -> BackupRecord:
    """Insert the PENDING row, retrying if two runs race for the same code."""
    author = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    last_error: Exception | None = None
    for _attempt in range(6):
        code = _next_backup_code()
        try:
            with transaction.atomic():
                return BackupRecord.objects.create(
                    backup_code=code,
                    backup_type=backup_type,
                    scope=scope,
                    status=BackupStatus.PENDING,
                    notes=notes or "",
                    database_engine=_database_engine(),
                    django_version=django.get_version(),
                    app_version=getattr(settings, "APP_VERSION", ""),
                    created_by=author,
                    updated_by=author,
                )
        except IntegrityError as error:  # another run took the code first
            last_error = error
    raise BackupError(
        _("Could not allocate a unique backup code: %(error)s") % {"error": last_error}
    )


def create_backup(
    backup_type: str = BackupType.MANUAL,
    scope: str = BackupScope.FULL,
    user=None,
    notes: str = "",
    *,
    request=None,
) -> BackupRecord:
    """Produce one backup artefact and the row that describes it.

    Never raises. A run that fails returns a ``FAILED`` record carrying an
    operator-readable ``error_message``, because the caller is usually a
    scheduled task with nobody watching its traceback.
    """
    if backup_type not in BackupType.values:
        backup_type = BackupType.MANUAL
    if scope not in BackupScope.values:
        scope = BackupScope.FULL

    try:
        record = _reserve_record(backup_type, scope, user, notes)
    except BackupError as error:
        logger.exception("Could not reserve a backup record")
        record_audit(
            request,
            action=AuditAction.BACKUP_CREATE,
            user=user,
            description=_("Backup could not be started: %(error)s") % {"error": error},
        )
        raise  # nothing was created; the caller has no record to inspect

    record.status = BackupStatus.RUNNING
    record.started_at = timezone.now()
    record.save(update_fields=["status", "started_at", "updated_at"])

    target = backup_root() / _artifact_name(record.backup_code, scope)
    clock_started = time.monotonic()
    try:
        writer = _WRITERS[scope][0]
        detail = writer(target)
        if not target.is_file():
            raise BackupError(_("The backup finished but no file was produced."))

        record.file_path = str(target)
        record.file_size_bytes = target.stat().st_size
        record.checksum_sha256 = sha256_of(target)
        record.status = BackupStatus.COMPLETED
        record.completed_at = timezone.now()
        record.duration_ms = int((time.monotonic() - clock_started) * 1000)
        record.error_message = ""
        record.save(
            update_fields=[
                "file_path",
                "file_size_bytes",
                "checksum_sha256",
                "status",
                "completed_at",
                "duration_ms",
                "error_message",
                "updated_at",
            ]
        )
        record_audit(
            request,
            action=AuditAction.BACKUP_CREATE,
            instance=record,
            user=user,
            description=_("Backup %(code)s created (%(scope)s, %(size)s)")
            % {
                "code": record.backup_code,
                "scope": record.get_scope_display(),
                "size": record.size_display,
            },
            changes={"detail": [None, json.dumps(detail, ensure_ascii=False)]},
        )
        logger.info(
            "Backup %s completed: %s in %s ms",
            record.backup_code,
            record.size_display,
            record.duration_ms,
        )
        return record

    except Exception as error:  # noqa: BLE001 - a backup run must never propagate
        # A half-written artefact is worse than none: it looks restorable.
        _remove_quietly(target)
        record.status = BackupStatus.FAILED
        record.file_path = ""
        record.file_size_bytes = 0
        record.checksum_sha256 = ""
        record.completed_at = timezone.now()
        record.duration_ms = int((time.monotonic() - clock_started) * 1000)
        record.error_message = str(error)[:5000]
        record.save(
            update_fields=[
                "status",
                "file_path",
                "file_size_bytes",
                "checksum_sha256",
                "completed_at",
                "duration_ms",
                "error_message",
                "updated_at",
            ]
        )
        record_audit(
            request,
            action=AuditAction.BACKUP_CREATE,
            instance=record,
            user=user,
            description=_("Backup %(code)s FAILED: %(error)s")
            % {"code": record.backup_code, "error": record.error_message},
        )
        logger.exception("Backup %s failed", record.backup_code)
        return record


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
def _mark_corrupt(record: BackupRecord, message: str) -> None:
    record.status = BackupStatus.CORRUPT
    record.is_verified = False
    record.error_message = message[:5000]
    record.save(update_fields=["status", "is_verified", "error_message", "updated_at"])


def verify_backup(record: BackupRecord, *, user=None, request=None) -> tuple[bool, str]:
    """Prove the artefact is still the one that was written, and still readable.

    Three independent questions, in the only order that makes sense: does the
    file exist, does its SHA-256 still match, and does the storage engine itself
    consider the contents sound. A failure marks the record ``CORRUPT`` so it can
    never be chosen for a restore.
    """
    if record.status == BackupStatus.FAILED:
        return False, _("This backup run failed; there is nothing to verify.")

    path = record.path
    if path is None:
        return False, _("No file path is recorded for this backup.")

    if not record.exists_on_disk:
        message = _("The file is missing from the backup volume: %(path)s") % {"path": path}
        _mark_corrupt(record, str(message))
        return False, str(message)

    if not record.checksum_sha256:
        message = _("No checksum was stored, so this backup cannot be proven intact.")
        _mark_corrupt(record, str(message))
        return False, str(message)

    try:
        actual = sha256_of(path)
    except OSError as error:
        message = _("The file could not be read: %(error)s") % {"error": error}
        _mark_corrupt(record, str(message))
        return False, str(message)

    if actual != record.checksum_sha256:
        message = _(
            "Checksum mismatch — the file has changed since it was written. "
            "Expected %(expected)s, found %(actual)s."
        ) % {"expected": record.checksum_sha256[:16], "actual": actual[:16]}
        _mark_corrupt(record, str(message))
        record_audit(
            request,
            action=AuditAction.BACKUP_CREATE,
            instance=record,
            user=user,
            description=_("Backup %(code)s failed verification: checksum mismatch")
            % {"code": record.backup_code},
        )
        return False, str(message)

    suffix = path.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                broken = archive.testzip()
        except (zipfile.BadZipFile, OSError) as error:
            message = _("The archive is not readable: %(error)s") % {"error": error}
            _mark_corrupt(record, str(message))
            return False, str(message)
        if broken is not None:
            message = _("The archive contains a damaged entry: %(name)s") % {"name": broken}
            _mark_corrupt(record, str(message))
            return False, str(message)
    elif suffix == ".sqlite3":
        sound, detail = _sqlite_integrity_check(path)
        if not sound:
            message = _("SQLite integrity check failed: %(detail)s") % {"detail": detail}
            _mark_corrupt(record, str(message))
            return False, str(message)
    elif suffix == ".dump":
        # A pg_dump custom archive starts with the "PGDMP" magic. That is as far
        # as we can check without a PostgreSQL server to replay it into.
        try:
            with path.open("rb") as handle:
                header = handle.read(5)
        except OSError as error:
            message = _("The dump could not be read: %(error)s") % {"error": error}
            _mark_corrupt(record, str(message))
            return False, str(message)
        if header != b"PGDMP":
            message = _("This file is not a PostgreSQL custom-format dump.")
            _mark_corrupt(record, str(message))
            return False, str(message)

    size = record.size_on_disk
    record.status = BackupStatus.COMPLETED
    record.is_verified = True
    record.verified_at = timezone.now()
    record.error_message = ""
    if size is not None:
        record.file_size_bytes = size
    record.save(
        update_fields=[
            "status",
            "is_verified",
            "verified_at",
            "error_message",
            "file_size_bytes",
            "updated_at",
        ]
    )
    return True, str(_("Verified: checksum matches and the contents are readable."))


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _safe_members(archive: zipfile.ZipFile, prefix: str, destination: Path):
    """Yield ``(member, absolute_target)`` for entries under *prefix*.

    Refuses any entry that would land outside *destination* — an archive is
    untrusted input the moment it comes back off a shared drive.
    """
    root = destination.resolve()
    for member in archive.infolist():
        name = member.filename
        if member.is_dir():
            continue
        if prefix:
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix) :]
        else:
            relative = name
        if not relative:
            continue
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise BackupError(
                _("The archive contains an entry that escapes the target directory: %(name)s")
                % {"name": name}
            )
        yield member, candidate


def _extract_media(archive_path: Path, prefix: str) -> int:
    """Write the media entries of an archive back into MEDIA_ROOT."""
    destination = Path(settings.MEDIA_ROOT)
    destination.mkdir(parents=True, exist_ok=True)
    restored = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member, target in _safe_members(archive, prefix, destination):
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            restored += 1
    return restored


def _restore_database_file(dump_path: Path) -> None:
    """Write a database dump back over the live database."""
    if is_sqlite():
        source = sqlite3.connect(str(dump_path), timeout=SQLITE_TIMEOUT_SECONDS)
        try:
            # Take our own handle on the live database *before* dropping
            # Django's: with a shared in-memory database, closing the last
            # connection would delete the thing we are about to write into.
            destination = _connect_live_sqlite()
            try:
                # Django's pooled connections must let go before the live
                # database is rewritten. The backup API replaces the destination
                # wholesale, which is what makes this work on Windows, where an
                # open file cannot simply be overwritten.
                connections.close_all()
                _copy_sqlite_database(source, destination)
            finally:
                destination.close()
        finally:
            source.close()
            connections.close_all()
        return

    # Django's own pooled connections must let go before the server-side
    # database is torn down and rebuilt.
    connections.close_all()
    try:
        if is_postgresql():
            _postgres_restore(dump_path)
        else:
            raise BackupError(
                _("Database engine %(engine)s is not supported by the restore module.")
                % {"engine": _database_engine()}
            )
    finally:
        connections.close_all()


def _apply_backup(record: BackupRecord) -> dict:
    """Write *record*'s artefact back over the live system."""
    path = record.path
    if path is None or not path.is_file():
        raise BackupError(
            _("The artefact for %(code)s is not on disk.") % {"code": record.backup_code}
        )

    applied: dict = {}
    if record.scope == BackupScope.DATABASE:
        _restore_database_file(path)
        applied["database"] = True
    elif record.scope == BackupScope.MEDIA:
        applied["media_files"] = _extract_media(path, "")
    elif record.scope == BackupScope.FULL:
        with tempfile.TemporaryDirectory(prefix="surf-restore-") as staging:
            staging_dir = Path(staging)
            with zipfile.ZipFile(path) as archive:
                dump_name = next(
                    (
                        member.filename
                        for member in archive.infolist()
                        if member.filename.startswith(ARCHIVE_DATABASE_DIR) and not member.is_dir()
                    ),
                    None,
                )
                if dump_name is None:
                    raise BackupError(
                        _("The archive holds no database dump, so it cannot be restored.")
                    )
                dump_path = staging_dir / Path(dump_name).name
                with archive.open(dump_name) as source, dump_path.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
            _restore_database_file(dump_path)
            applied["database"] = True
        applied["media_files"] = _extract_media(path, ARCHIVE_MEDIA_DIR)
    else:
        raise BackupError(
            _("A %(scope)s backup contains no data to restore.")
            % {"scope": record.get_scope_display()}
        )
    return applied


def _row_values(instance) -> dict:
    values = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key:
            continue
        values[field.attname] = getattr(instance, field.attname)
    return values


def _reinstate_records(restore: RestoreRecord, source: BackupRecord, safety: BackupRecord | None):
    """Put this restore's own paperwork back after the database was replaced.

    Restoring the database rewinds the whole system, including the rows that
    describe the restore. Two distinct things go wrong without this:

    * the safety copy and the restore itself simply **disappear** — they were
      created after the artefact was written, so the restored database has never
      heard of them; and
    * the backup that was just restored comes back **stale** — a full archive
      contains the row describing its own run, captured while that run was still
      ``RUNNING`` with no file path or checksum yet. Left alone, the school's
      newest good backup would look like an abandoned job and the retention
      sweep would happily discard it while its file sat orphaned on disk.

    So an existing row is brought up to date and a missing one is recreated,
    original timestamps and all. Returns the :class:`RestoreRecord` to keep
    working with.
    """
    connections.close_all()

    from django.contrib.auth import get_user_model

    User = get_user_model()

    def user_or_none(user_id):
        """Drop a reference to a user the restored database does not have."""
        if not user_id:
            return None
        return user_id if User.objects.filter(pk=user_id).exists() else None

    def values_for(instance) -> dict:
        values = _row_values(instance)
        values["created_by_id"] = user_or_none(values.get("created_by_id"))
        values["updated_by_id"] = user_or_none(values.get("updated_by_id"))
        return values

    def write_back(model, instance, values, existing):
        if existing is not None:
            for field, value in values.items():
                setattr(existing, field, value)
            existing.save()
            return existing
        created = model.all_objects.create(**values)
        # ``created_at`` is auto_now_add, so the row would otherwise claim to
        # have been made during the restore rather than when it really was.
        model.all_objects.filter(pk=created.pk).update(created_at=instance.created_at)
        created.created_at = instance.created_at
        return created

    def reinstate_backup(record: BackupRecord | None) -> BackupRecord | None:
        if record is None:
            return None
        values = values_for(record)
        existing = BackupRecord.all_objects.filter(public_id=record.public_id).first()
        if existing is None and BackupRecord.all_objects.filter(
            backup_code=values["backup_code"]
        ).exists():
            # The older database reused this number for a different backup.
            values["backup_code"] = _next_backup_code()
        return write_back(BackupRecord, record, values, existing)

    new_source = reinstate_backup(source)
    new_safety = reinstate_backup(safety)

    values = values_for(restore)
    values["confirmed_by_id"] = user_or_none(values.get("confirmed_by_id"))
    values["backup_id"] = new_source.pk if new_source is not None else None
    values["safety_backup_id"] = new_safety.pk if new_safety is not None else None
    if values["backup_id"] is None:
        # PROTECT forbids a null target; without the source row there is nothing
        # sane to attach the restore to, so leave the audit log as the record.
        return restore

    existing_restore = RestoreRecord.all_objects.filter(public_id=restore.public_id).first()
    return write_back(RestoreRecord, restore, values, existing_restore)


def _fail_restore(
    restore: RestoreRecord,
    checks: dict,
    message: str,
    *,
    status: str = RestoreStatus.FAILED,
    clock_started: float | None = None,
    user=None,
    request=None,
) -> RestoreRecord:
    restore.status = status
    restore.error_message = str(message)[:5000]
    restore.pre_restore_checks = checks
    restore.completed_at = timezone.now()
    if clock_started is not None:
        restore.duration_ms = int((time.monotonic() - clock_started) * 1000)
    restore.save(
        update_fields=[
            "status",
            "error_message",
            "pre_restore_checks",
            "completed_at",
            "duration_ms",
            "updated_at",
        ]
    )
    record_audit(
        request,
        action=AuditAction.BACKUP_RESTORE,
        instance=restore,
        user=user,
        description=_("Restore of %(code)s stopped: %(error)s")
        % {"code": restore.backup.backup_code, "error": restore.error_message},
    )
    logger.warning("Restore of %s stopped: %s", restore.backup.backup_code, message)
    return restore


def restore_backup(
    record: BackupRecord,
    user,
    confirmation_text: str,
    *,
    request=None,
    notes: str = "",
) -> RestoreRecord:
    """Put a backup back over the live system, through five gates in order.

    1. the artefact is verified (a checksum mismatch stops everything),
    2. ``confirmation_text`` must equal the backup code character for character,
    3. the operator must hold ``backups.restore``,
    4. a PRE_RESTORE safety copy is taken **first** and linked, and only then
    5. the artefact is applied.

    If step 5 throws, the safety copy is applied back and the attempt is recorded
    as ``ROLLED_BACK``. Every stage writes a ``BACKUP_RESTORE`` audit entry, and
    the paperwork is reinstated afterwards because restoring the database also
    deletes the rows describing the restore.

    Returns the :class:`RestoreRecord` in every case — including refusals — so
    the caller always has something to show and to keep.
    """
    author = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    clock_started = time.monotonic()
    restore = RestoreRecord.objects.create(
        backup=record,
        status=RestoreStatus.VERIFYING,
        started_at=timezone.now(),
        confirmed_by=author,
        confirmation_text=(confirmation_text or "")[:64],
        notes=notes or "",
        created_by=author,
        updated_by=author,
        pre_restore_checks={},
    )
    record_audit(
        request,
        action=AuditAction.BACKUP_RESTORE,
        instance=restore,
        user=user,
        description=_("Restore of %(code)s requested") % {"code": record.backup_code},
    )

    checks: dict = {}

    # --- 1. the artefact must prove itself --------------------------------
    verified, verify_message = verify_backup(record, user=user, request=request)
    checks["integrity_verified"] = {"passed": verified, "detail": str(verify_message)}
    if not verified:
        return _fail_restore(
            restore, checks, verify_message, clock_started=clock_started, user=user, request=request
        )

    if record.scope not in RESTORABLE_SCOPES:
        message = _("A %(scope)s backup holds no data to restore.") % {
            "scope": record.get_scope_display()
        }
        checks["scope_restorable"] = {"passed": False, "detail": str(message)}
        return _fail_restore(
            restore, checks, message, clock_started=clock_started, user=user, request=request
        )
    checks["scope_restorable"] = {"passed": True, "detail": str(record.get_scope_display())}

    if record.covers_database and record.database_engine and not record.engine_matches_current:
        message = _(
            "This backup was taken on %(was)s but the site now runs on %(now)s. "
            "A dump cannot be moved between database engines."
        ) % {"was": record.database_engine, "now": _database_engine()}
        checks["engine_matches"] = {"passed": False, "detail": str(message)}
        return _fail_restore(
            restore, checks, message, clock_started=clock_started, user=user, request=request
        )
    checks["engine_matches"] = {"passed": True, "detail": _database_engine()}

    # --- 2. the operator must have typed the code -------------------------
    typed = (confirmation_text or "").strip()
    matches = typed == record.backup_code
    checks["confirmation_typed"] = {
        "passed": matches,
        "detail": str(_("Backup code typed exactly")) if matches else str(_("Does not match")),
    }
    if not matches:
        return _fail_restore(
            restore,
            checks,
            _("The typed confirmation does not match the backup code %(code)s.")
            % {"code": record.backup_code},
            clock_started=clock_started,
            user=user,
            request=request,
        )

    # --- 3. the operator must hold the capability -------------------------
    allowed = bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.has_capability("backups.restore")
    )
    checks["capability_backups_restore"] = {
        "passed": allowed,
        "detail": getattr(user, "username", "") or str(_("anonymous")),
    }
    if not allowed:
        return _fail_restore(
            restore,
            checks,
            _("Restoring a backup requires the backups.restore permission."),
            clock_started=clock_started,
            user=user,
            request=request,
        )

    # --- 4. safety copy FIRST --------------------------------------------
    safety = create_backup(
        BackupType.PRE_RESTORE,
        record.scope,
        user=user,
        notes=str(
            _("Automatic safety copy taken before restoring %(code)s")
            % {"code": record.backup_code}
        ),
        request=request,
    )
    safe = safety.status == BackupStatus.COMPLETED
    checks["safety_backup_taken"] = {
        "passed": safe,
        "detail": safety.backup_code if safe else (safety.error_message or ""),
    }
    restore.safety_backup = safety
    restore.pre_restore_checks = checks
    restore.save(update_fields=["safety_backup", "pre_restore_checks", "updated_at"])
    if not safe:
        return _fail_restore(
            restore,
            checks,
            _("The safety copy failed, so the restore was not started: %(error)s")
            % {"error": safety.error_message},
            clock_started=clock_started,
            user=user,
            request=request,
        )
    record_audit(
        request,
        action=AuditAction.BACKUP_RESTORE,
        instance=restore,
        user=user,
        description=_("Safety copy %(safety)s taken before restoring %(code)s")
        % {"safety": safety.backup_code, "code": record.backup_code},
    )

    # --- 5. apply, and 6. roll back if it goes wrong ----------------------
    restore.status = RestoreStatus.RUNNING
    restore.save(update_fields=["status", "updated_at"])

    try:
        applied = _apply_backup(record)
    except Exception as error:  # noqa: BLE001 - every failure must be recovered from
        logger.exception("Restore of %s failed; rolling back", record.backup_code)
        checks["restore_applied"] = {"passed": False, "detail": str(error)[:500]}
        rollback_error = ""
        try:
            _apply_backup(safety)
            rolled_back = True
        except Exception as rollback_failure:  # noqa: BLE001
            rolled_back = False
            rollback_error = str(rollback_failure)
            logger.exception("Rollback from %s failed", safety.backup_code)
        checks["rolled_back"] = {"passed": rolled_back, "detail": rollback_error}

        if rolled_back and record.covers_database:
            restore = _reinstate_records(restore, record, safety)
        message = (
            _("The restore failed and the system was returned to the safety copy %(safety)s: %(error)s")
            % {"safety": safety.backup_code, "error": error}
            if rolled_back
            else _(
                "The restore failed AND the rollback failed. Restore %(safety)s by hand "
                "immediately. Restore error: %(error)s. Rollback error: %(rollback)s"
            )
            % {"safety": safety.backup_code, "error": error, "rollback": rollback_error}
        )
        return _fail_restore(
            restore,
            checks,
            message,
            status=RestoreStatus.ROLLED_BACK if rolled_back else RestoreStatus.FAILED,
            clock_started=clock_started,
            user=user,
            request=request,
        )

    checks["restore_applied"] = {
        "passed": True,
        "detail": json.dumps(applied, ensure_ascii=False),
    }

    if record.covers_database:
        try:
            restore = _reinstate_records(restore, record, safety)
        except Exception:  # noqa: BLE001 - the data is back; paperwork is best effort
            logger.exception("Could not reinstate restore paperwork after %s", record.backup_code)

    restore.status = RestoreStatus.COMPLETED
    restore.completed_at = timezone.now()
    restore.duration_ms = int((time.monotonic() - clock_started) * 1000)
    restore.error_message = ""
    restore.pre_restore_checks = checks
    restore.save(
        update_fields=[
            "status",
            "completed_at",
            "duration_ms",
            "error_message",
            "pre_restore_checks",
            "updated_at",
        ]
    )
    record_audit(
        request,
        action=AuditAction.BACKUP_RESTORE,
        instance=restore,
        user=user,
        description=_("Backup %(code)s restored over the live system (safety copy %(safety)s)")
        % {"code": record.backup_code, "safety": safety.backup_code},
    )
    logger.warning("Backup %s restored by %s", record.backup_code, getattr(user, "username", "?"))
    return restore


# ---------------------------------------------------------------------------
# Delete & retention
# ---------------------------------------------------------------------------
def latest_successful_backup() -> BackupRecord | None:
    """The newest completed backup — the one the school would actually reach for."""
    # ``nulls_last`` is explicit because SQLite and PostgreSQL disagree about
    # where NULLs sort in a descending order; a row with no completion time must
    # never be mistaken for the freshest copy.
    return (
        BackupRecord.objects.filter(status=BackupStatus.COMPLETED)
        .order_by(F("completed_at").desc(nulls_last=True), "-created_at", "-id")
        .first()
    )


def can_delete_backup(record: BackupRecord) -> tuple[bool, str]:
    """May this backup be removed? Returns ``(allowed, reason)``."""
    latest = latest_successful_backup()
    if latest is not None and latest.pk == record.pk:
        return False, str(
            _(
                "This is the most recent successful backup. Create a newer one "
                "before deleting it, so the school is never left without a copy."
            )
        )
    blocking = record.protected_restores.exclude(
        status__in=[RestoreStatus.COMPLETED, RestoreStatus.ROLLED_BACK, RestoreStatus.FAILED]
    ).first()
    if blocking is not None:
        return False, str(
            _("This is the safety copy for a restore that has not finished yet.")
        )
    return True, ""


def delete_backup(record: BackupRecord, user, *, request=None) -> int:
    """Remove a backup artefact and archive its record. Returns bytes reclaimed.

    Requires ``backups.delete``; raises :class:`~django.core.exceptions.PermissionDenied`
    otherwise, and :class:`~django.core.exceptions.ValidationError` when deleting
    would leave the school without its most recent copy.
    """
    if not (
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.has_capability("backups.delete")
    ):
        raise PermissionDenied(_("Deleting a backup requires the backups.delete permission."))

    allowed, reason = can_delete_backup(record)
    if not allowed:
        raise ValidationError(reason)

    freed = _remove_quietly(record.path)
    code = record.backup_code
    record.is_verified = False
    record.file_path = ""
    record.save(update_fields=["is_verified", "file_path", "updated_at"])
    record.delete()  # soft delete: the history of the backup survives its file

    record_audit(
        request,
        action=AuditAction.BACKUP_DELETE,
        instance=record,
        user=user,
        description=_("Backup %(code)s deleted (%(size)s reclaimed)")
        % {"code": code, "size": human_size(freed)},
    )
    return freed


def apply_retention_policy(*, user=None, request=None) -> dict:
    """Prune scheduled backups down to the configured retention counts.

    Manual and pre-restore backups are never touched — a person asked for the
    first, and the second is somebody's undo button. The most recent successful
    backup is never touched either, whatever its type.
    """
    policy = retention_policy()
    keeper = latest_successful_backup()
    keeper_pk = keeper.pk if keeper is not None else None

    type_limits = {
        BackupType.DAILY: policy["daily"],
        BackupType.WEEKLY: policy["weekly"],
        BackupType.MONTHLY: policy["monthly"],
    }

    deleted = 0
    freed = 0
    kept = 0
    errors: list[str] = []

    for backup_type, limit in type_limits.items():
        rows = list(
            BackupRecord.objects.filter(backup_type=backup_type).order_by(
                F("completed_at").desc(nulls_last=True), "-created_at", "-id"
            )
        )
        survivors: list[int] = []
        for row in rows:
            if row.status == BackupStatus.COMPLETED and len(survivors) < limit:
                survivors.append(row.pk)
        kept += len(survivors)

        for row in rows:
            if row.pk in survivors or row.pk == keeper_pk:
                continue
            try:
                freed += _remove_quietly(row.path)
                row.file_path = ""
                row.is_verified = False
                row.save(update_fields=["file_path", "is_verified", "updated_at"])
                row.delete()
                deleted += 1
            except Exception as error:  # noqa: BLE001 - one bad row must not stop the sweep
                errors.append(f"{row.backup_code}: {error}")
                logger.exception("Retention sweep could not remove %s", row.backup_code)

    summary = {
        "deleted": deleted,
        "kept": kept,
        "freed_bytes": freed,
        "freed_display": human_size(freed),
        "policy": policy,
        "errors": errors,
    }
    if deleted or errors:
        record_audit(
            request,
            action=AuditAction.BACKUP_DELETE,
            user=user,
            description=_("Retention sweep removed %(count)s backup(s), reclaiming %(size)s")
            % {"count": deleted, "size": human_size(freed)},
            changes={"policy": [None, json.dumps(policy)]},
        )
    return summary


def retention_preview() -> list[BackupRecord]:
    """Which backups the next retention sweep would remove, without removing them."""
    policy = retention_policy()
    keeper = latest_successful_backup()
    keeper_pk = keeper.pk if keeper is not None else None
    type_limits = {
        BackupType.DAILY: policy["daily"],
        BackupType.WEEKLY: policy["weekly"],
        BackupType.MONTHLY: policy["monthly"],
    }
    doomed: list[BackupRecord] = []
    for backup_type, limit in type_limits.items():
        rows = list(
            BackupRecord.objects.filter(backup_type=backup_type).order_by(
                F("completed_at").desc(nulls_last=True), "-created_at", "-id"
            )
        )
        survivors: list[int] = []
        for row in rows:
            if row.status == BackupStatus.COMPLETED and len(survivors) < limit:
                survivors.append(row.pk)
        doomed += [r for r in rows if r.pk not in survivors and r.pk != keeper_pk]
    return sorted(doomed, key=lambda r: r.created_at)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
#: Days after which a school with no fresh backup is in real trouble.
HEALTH_WARNING_DAYS = 2
HEALTH_CRITICAL_DAYS = 7

HEALTH_HEALTHY = "healthy"
HEALTH_WARNING = "warning"
HEALTH_CRITICAL = "critical"


def backup_statistics() -> dict:
    """Everything the list screen needs to answer "are we actually protected?"."""
    queryset = BackupRecord.objects.all()
    completed = queryset.filter(status=BackupStatus.COMPLETED)

    total = queryset.count()
    by_status = {
        status: queryset.filter(status=status).count() for status, _label in BackupStatus.choices
    }
    by_type = {
        value: queryset.filter(backup_type=value).count() for value, _label in BackupType.choices
    }

    total_bytes = 0
    by_scope: dict[str, dict] = {
        value: {"label": str(label), "bytes": 0, "count": 0}
        for value, label in BackupScope.choices
    }
    missing_files = 0
    for row in completed.only("scope", "file_size_bytes", "file_path"):
        total_bytes += row.file_size_bytes or 0
        bucket = by_scope.get(row.scope)
        if bucket is not None:
            bucket["bytes"] += row.file_size_bytes or 0
            bucket["count"] += 1
        if not row.exists_on_disk:
            missing_files += 1

    latest = latest_successful_backup()
    oldest = queryset.order_by("created_at", "id").first()
    corrupt_count = by_status.get(BackupStatus.CORRUPT, 0)
    failed_count = by_status.get(BackupStatus.FAILED, 0)

    if latest is None:
        health = HEALTH_CRITICAL
        health_message = _("No successful backup exists. The school is not protected.")
    elif latest.age_days >= HEALTH_CRITICAL_DAYS:
        health = HEALTH_CRITICAL
        health_message = _("The most recent backup is %(days)s days old.") % {
            "days": latest.age_days
        }
    elif corrupt_count or missing_files:
        health = HEALTH_WARNING
        health_message = _(
            "%(corrupt)s backup(s) are corrupt and %(missing)s file(s) are missing from disk."
        ) % {"corrupt": corrupt_count, "missing": missing_files}
    elif latest.age_days >= HEALTH_WARNING_DAYS:
        health = HEALTH_WARNING
        health_message = _("The most recent backup is %(days)s days old.") % {
            "days": latest.age_days
        }
    else:
        health = HEALTH_HEALTHY
        health_message = _("A recent, verified copy of the system is available.")

    root = Path(settings.BACKUP["ROOT"])
    free_bytes: int | None = None
    try:
        if root.exists():
            free_bytes = shutil.disk_usage(root).free
    except OSError:
        free_bytes = None

    return {
        "total": total,
        "completed": completed.count(),
        "failed": failed_count,
        "corrupt": corrupt_count,
        "verified": queryset.filter(is_verified=True).count(),
        "missing_files": missing_files,
        "by_status": by_status,
        "by_type": by_type,
        "by_scope": by_scope,
        "total_bytes": total_bytes,
        "total_display": human_size(total_bytes),
        "free_bytes": free_bytes,
        "free_display": human_size(free_bytes) if free_bytes is not None else "—",
        "last_successful": latest,
        "last_successful_age_days": latest.age_days if latest else None,
        "oldest": oldest,
        "health": health,
        "health_message": str(health_message),
        "backup_root": str(root),
        "restores": RestoreRecord.objects.count(),
        "engine": _database_engine(),
    }
