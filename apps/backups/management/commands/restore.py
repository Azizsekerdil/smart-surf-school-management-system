"""Restore a backup from the command line.

The console gets no shortcuts. The same five gates apply as on the web screen:
the artefact is verified, the backup code must be typed with ``--confirm``, the
named user must hold ``backups.restore``, a safety copy is taken first, and a
failure rolls back to it.

    .\\.venv\\Scripts\\python.exe manage.py restore BKP-20260815-001 ^
        --confirm BKP-20260815-001 --user alice

There is deliberately no ``--force`` and no ``--yes``: if the operator cannot
type the code, the operator has not read which backup they are restoring.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.backups import services
from apps.backups.models import BackupRecord, RestoreStatus


class Command(BaseCommand):
    help = "Restore a backup over the live system. Requires the backup code and a user."

    def add_arguments(self, parser):
        parser.add_argument("backup_code", help="The code of the backup to restore.")
        parser.add_argument(
            "--confirm",
            required=True,
            help="Type the backup code again to confirm. Must match exactly.",
        )
        parser.add_argument(
            "--user",
            required=True,
            help="Username of the person authorising the restore (needs backups.restore).",
        )
        parser.add_argument("--notes", default="", help="Why this restore is being performed.")

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        code = options["backup_code"].strip()
        record = BackupRecord.objects.filter(backup_code=code).first()
        if record is None:
            raise CommandError(f"No backup with code {code!r}.")

        user = get_user_model().objects.filter(username=options["user"].strip()).first()
        if user is None:
            raise CommandError(f"No user named {options['user']!r}.")
        if not user.has_capability("backups.restore"):
            raise CommandError(
                f"{user.username} does not hold backups.restore and may not restore anything."
            )

        self.stdout.write(
            self.style.WARNING(
                f"Restoring {record.backup_code} ({record.get_scope_display()}, "
                f"{record.size_display}, taken {record.age_days} day(s) ago).\n"
                "Everything created since that moment will be lost."
            )
        )

        restore = services.restore_backup(
            record,
            user,
            options["confirm"],
            notes=options["notes"] or "Command line restore",
        )

        for check in restore.check_rows:
            marker = "ok  " if check["passed"] else "FAIL"
            style = self.style.SUCCESS if check["passed"] else self.style.ERROR
            self.stdout.write(style(f"  [{marker}] {check['name']} {check['detail']}"))

        if restore.status == RestoreStatus.COMPLETED:
            safety = restore.safety_backup.backup_code if restore.safety_backup else "—"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Restore complete in {restore.duration_display}. "
                    f"Previous state saved as {safety}."
                )
            )
            return

        if restore.status == RestoreStatus.ROLLED_BACK:
            raise CommandError(
                f"The restore failed and was rolled back: {restore.error_message}"
            )
        raise CommandError(f"The restore did not run: {restore.error_message}")
