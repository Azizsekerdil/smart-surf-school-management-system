"""Take a backup from the command line — no Celery, no Redis, no broker.

Windows Task Scheduler entry that gives a school a nightly backup with nothing
else installed::

    Program:   D:\\Surf_School\\.venv\\Scripts\\python.exe
    Arguments: manage.py backup --type daily --scope full --apply-retention
    Start in:  D:\\Surf_School

The exit code is what the scheduler reads: 0 means a verified backup exists, 1
means it does not. "Last run result" in Task Scheduler is then actually true.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.backups import services
from apps.backups.models import BackupScope, BackupStatus, BackupType


class Command(BaseCommand):
    help = "Create a backup of the database, the uploaded files, or both."

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            dest="backup_type",
            default=BackupType.MANUAL,
            choices=list(BackupType.values),
            help="Which retention bucket this backup belongs to (default: manual).",
        )
        parser.add_argument(
            "--scope",
            default=BackupScope.FULL,
            choices=list(BackupScope.values),
            help="How much to copy (default: full).",
        )
        parser.add_argument("--notes", default="", help="Free-text note stored with the record.")
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip the post-backup checksum and integrity check (not recommended).",
        )
        parser.add_argument(
            "--apply-retention",
            action="store_true",
            help="Prune old scheduled backups afterwards, using the retention policy.",
        )
        parser.add_argument(
            "--user",
            default="",
            help="Username to record as the author of the backup.",
        )

    def handle(self, *args, **options):
        user = None
        username = (options["user"] or "").strip()
        if username:
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.filter(username=username).first()
            if user is None:
                raise CommandError(f"No user named {username!r}.")

        notes = options["notes"] or f"Command line ({options['backup_type']})"
        self.stdout.write(
            f"Backing up ({options['scope']}) to {services.backup_root()} ..."
        )

        record = services.create_backup(
            options["backup_type"],
            options["scope"],
            user=user,
            notes=notes,
        )

        if record.status != BackupStatus.COMPLETED:
            self.stderr.write(
                self.style.ERROR(f"{record.backup_code} FAILED: {record.error_message}")
            )
            raise CommandError("The backup did not complete.")

        self.stdout.write(
            self.style.SUCCESS(
                f"  {record.backup_code}  {record.size_display}  "
                f"in {record.duration_display}  -> {record.file_path}"
            )
        )

        if not options["no_verify"]:
            verified, detail = services.verify_backup(record, user=user)
            if not verified:
                self.stderr.write(self.style.ERROR(f"  Verification FAILED: {detail}"))
                raise CommandError("The backup was written but did not verify.")
            self.stdout.write(self.style.SUCCESS(f"  Verified: {record.checksum_sha256}"))

        if options["apply_retention"]:
            summary = services.apply_retention_policy(user=user)
            self.stdout.write(
                f"  Retention: removed {summary['deleted']}, "
                f"reclaimed {summary['freed_display']}, kept {summary['kept']}."
            )
            for problem in summary["errors"]:
                self.stderr.write(self.style.WARNING(f"  {problem}"))

        statistics = services.backup_statistics()
        self.stdout.write(
            f"  Storage: {statistics['total_display']} used, "
            f"{statistics['free_display']} free on the backup volume."
        )
