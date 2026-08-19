"""Run every scheduled report that is due.

Intended for Windows Task Scheduler (or cron) every five minutes::

    .\\.venv\\Scripts\\python.exe manage.py run_scheduled_reports

``--dry-run`` lists what would be generated without writing files or sending
mail, which is how you check a cron expression before trusting it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.reporting import services
from apps.reporting.cron import describe_cron


class Command(BaseCommand):
    help = "Generate and e-mail scheduled reports that are due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--window",
            type=int,
            default=5,
            help="Minutes of schedule history to consider due (default: 5).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the definitions that would run, without generating anything.",
        )

    def handle(self, *args, **options):
        window = max(int(options["window"]), 1)
        moment = timezone.localtime()

        due = services.due_definitions(moment, window_minutes=window)
        if not due:
            self.stdout.write(f"Nothing due at {moment:%Y-%m-%d %H:%M}.")
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"{len(due)} definition(s) due:"))
            for definition in due:
                self.stdout.write(
                    f"  {definition.code}  {definition.report_key}  "
                    f"[{definition.schedule_cron}] -> {describe_cron(definition.schedule_cron)} "
                    f"-> {', '.join(definition.recipient_list) or 'no recipients'}"
                )
            return

        results = services.run_scheduled_reports(moment, window_minutes=window)
        for report in results:
            if report.is_downloadable:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ok    {report.report_key} ({report.row_count} rows, "
                        f"{report.file_size_display}, {report.duration_display})"
                    )
                )
            else:
                self.stderr.write(f"  fail  {report.report_key}: {report.error_message}")

        generated = sum(1 for report in results if report.is_downloadable)
        self.stdout.write(f"{generated}/{len(results)} scheduled report(s) generated.")
