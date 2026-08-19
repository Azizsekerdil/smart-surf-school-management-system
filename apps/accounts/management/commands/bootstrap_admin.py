"""Create the documented first-run administrator.

Usage::

    python manage.py bootstrap_admin

Refuses to do anything once any user account exists, so it can never be used to
push a live installation back to a known password. See
:mod:`apps.accounts.bootstrap` for the full contract.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.bootstrap import BOOTSTRAP_PASSWORD, BOOTSTRAP_USERNAME
from apps.accounts.constants import Role

User = get_user_model()


class Command(BaseCommand):
    help = "Create the single-use admin/admin first-run account (empty installs only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="admin@localhost",
            help="Contact address for the bootstrap account (never mailed).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.exists():
            raise CommandError(
                "This installation already has user accounts. The bootstrap "
                "account is only created on an empty database; use the Users & "
                "Roles screen, or manage.py createsuperuser, instead."
            )

        user = User(
            username=BOOTSTRAP_USERNAME,
            email=options["email"],
            role=Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True,
            must_change_password=True,
            is_bootstrap_account=True,
        )
        # set_password stores an Argon2id hash (see PASSWORD_HASHERS). The
        # plaintext is never written to the database, a log or an audit row.
        user.set_password(BOOTSTRAP_PASSWORD)
        user.save()

        self.stdout.write(
            self.style.WARNING(
                "\n"
                "  First-run account created.\n"
                "\n"
                "      username: admin\n"
                "      password: admin\n"
                "\n"
                "  This is a documented bootstrap credential, not a secret.\n"
                "  * it only works from this machine (loopback address);\n"
                "  * nothing in the product opens until you change it;\n"
                "  * admin / admin stops working the moment you do.\n"
            )
        )
