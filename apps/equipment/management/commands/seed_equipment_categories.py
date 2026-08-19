"""Create the standard equipment taxonomy.

Run once when a school is set up::

    .\\.venv\\Scripts\\python.exe manage.py seed_equipment_categories

Idempotent — existing categories are left exactly as they are, so a school that
renamed or re-parented a category keeps its edit.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.equipment.services import ensure_default_categories


class Command(BaseCommand):
    help = "Create any missing standard equipment category."

    def handle(self, *args, **options):
        created, untouched = ensure_default_categories()
        self.stdout.write(
            self.style.SUCCESS(
                f"Equipment categories: {created} created, {untouched} already present."
            )
        )
