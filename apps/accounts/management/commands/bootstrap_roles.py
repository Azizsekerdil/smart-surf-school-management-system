"""Synchronise the capability matrix into Django groups and permissions.

The application enforces access through
:meth:`apps.accounts.models.User.has_capability`, which reads the matrix in
:mod:`apps.accounts.constants` directly — so the app is already secure without
this command.

It exists because two other things also need to know about roles:

* the **Django admin**, which is permission-driven, and
* anything that later reads ``user.groups`` (reporting, third-party packages).

Running it creates one group per role and grants the model permissions implied
by that role's capabilities. It is idempotent and safe to run on every deploy.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.constants import MODULES, Role, capabilities_for

#: capability action -> Django permission codename prefix
ACTION_TO_PERMISSION = {
    "view": "view",
    "add": "add",
    "change": "change",
    "delete": "delete",
}

#: A capability module maps to one or more Django app labels.
MODULE_TO_APP_LABELS: dict[str, tuple[str, ...]] = {
    "accounts": ("accounts",),
    "customers": ("customers",),
    "students": ("students",),
    "instructors": ("instructors",),
    "crm": ("crm",),
    "locations": ("locations",),
    "lessons": ("lessons",),
    "bookings": ("bookings",),
    "surf_camps": ("surf_camps",),
    "equipment": ("equipment",),
    "rentals": ("rentals",),
    "maintenance": ("maintenance",),
    "surf_conditions": ("surf_conditions",),
    "safety": ("safety",),
    "finance": ("finance",),
    "pos": ("pos",),
    "analytics": ("analytics",),
    "reporting": ("reporting",),
    "notifications": ("notifications",),
    "backups": ("backups",),
    "audit": ("audit",),
    "ai": ("ai",),
    "ai_terminal": ("ai_terminal",),
    "help_center": ("help_center",),
    "training": ("training",),
    "onboarding": ("onboarding",),
    "settings": ("core",),
    "dashboard": (),
}


class Command(BaseCommand):
    help = "Create a Django group per role and grant the matching model permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reassign-users",
            action="store_true",
            help="Also put every existing user into the group matching their role.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would change without saving."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        installed_labels = {config.label for config in django_apps.get_app_configs()}

        created_groups = 0
        total_grants = 0

        for role_value, role_label in Role.choices:
            group, created = (
                Group.objects.get_or_create(name=str(role_value))
                if not dry_run
                else (Group(name=str(role_value)), not Group.objects.filter(name=role_value).exists())
            )
            if created:
                created_groups += 1

            capabilities = capabilities_for(role_value)
            permissions: set[Permission] = set()

            for capability in capabilities:
                module, _, action = capability.partition(".")
                permission_prefix = ACTION_TO_PERMISSION.get(action)
                if permission_prefix is None:
                    continue  # export/approve/manage have no Django equivalent

                for app_label in MODULE_TO_APP_LABELS.get(module, ()):
                    if app_label not in installed_labels:
                        continue
                    content_types = ContentType.objects.filter(app_label=app_label)
                    permissions |= set(
                        Permission.objects.filter(
                            content_type__in=content_types,
                            codename__startswith=f"{permission_prefix}_",
                        )
                    )

            if dry_run:
                self.stdout.write(
                    f"  {role_label}: {len(capabilities)} capabilities -> "
                    f"{len(permissions)} Django permissions"
                )
                continue

            group.permissions.set(permissions)
            total_grants += len(permissions)
            self.stdout.write(
                f"  {role_label:<22} {len(capabilities):>4} capabilities  "
                f"{len(permissions):>4} permissions"
            )

        if options["reassign_users"] and not dry_run:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            groups_by_name = {g.name: g for g in Group.objects.all()}
            moved = 0
            for user in User.objects.all().iterator():
                group = groups_by_name.get(user.role)
                if group is not None:
                    user.groups.set([group])
                    moved += 1
            self.stdout.write(self.style.SUCCESS(f"Reassigned {moved} user(s) to their role group."))

        # Sanity check: every module in the matrix should be mapped.
        unmapped = [m for m in MODULES if m not in MODULE_TO_APP_LABELS]
        if unmapped:
            self.stdout.write(
                self.style.WARNING(
                    "Modules with no app-label mapping (capability checks still work, "
                    f"but no Django permissions are granted): {', '.join(unmapped)}"
                )
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run - nothing was saved."))
            transaction.set_rollback(True)
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {len(Role.choices)} role group(s), {created_groups} newly created, "
                    f"{total_grants} permission grants."
                )
            )
