"""Fetch, store and score surf conditions — without needing Celery.

A surf school on Windows should not have to run a broker and a worker to get a
fresh forecast. Point Task Scheduler at::

    D:\\Surf_School\\.venv\\Scripts\\python.exe D:\\Surf_School\\manage.py refresh_conditions

every 30 minutes and the dashboards stay current. The command calls exactly the
same service function as the Celery task, so the two deployment styles cannot
drift apart.

Options::

    --spot alacati        only this spot (code, slug or name)
    --days 7              how many forecast days to store
    --no-forecast         current conditions only, skip the week
    --provider metno      override the configured provider for this run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.locations.models import SurfSpot
from apps.surf_conditions import services
from apps.surf_conditions.providers.registry import (
    PROVIDER_CLASSES,
    get_surf_provider,
)


class Command(BaseCommand):
    help = "Fetch and score the current surf conditions for every active spot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--spot",
            dest="spot",
            default="",
            help="Limit the run to one spot, by code, slug or name.",
        )
        parser.add_argument(
            "--days",
            dest="days",
            type=int,
            default=7,
            help="How many forecast days to store (1-16). Default 7.",
        )
        parser.add_argument(
            "--no-forecast",
            dest="forecast",
            action="store_false",
            default=True,
            help="Fetch the current reading only and skip the forecast.",
        )
        parser.add_argument(
            "--provider",
            dest="provider",
            default="",
            help=f"Override the configured provider. One of: {', '.join(PROVIDER_CLASSES)}.",
        )

    def handle(self, *args, **options):
        provider_name = (options.get("provider") or "").strip()
        if provider_name and provider_name not in PROVIDER_CLASSES:
            raise CommandError(
                f"Unknown provider {provider_name!r}. Choose one of: "
                f"{', '.join(PROVIDER_CLASSES)}."
            )
        provider = get_surf_provider(provider_name or None)

        spots = SurfSpot.objects.filter(is_active=True).order_by("-is_primary", "name")
        query = (options.get("spot") or "").strip()
        if query:
            spot = (
                spots.filter(code__iexact=query).first()
                or spots.filter(slug__iexact=query).first()
                or spots.filter(name__iexact=query).first()
                or spots.filter(name__icontains=query).first()
            )
            if spot is None:
                raise CommandError(f"No active surf spot matches {query!r}.")
            spots = [spot]
        else:
            spots = list(spots)

        if not spots:
            self.stdout.write(
                self.style.WARNING(
                    "No active surf spot is configured — nothing to refresh. "
                    "Add a spot under Locations first."
                )
            )
            return

        if not provider.provides_marine_data:
            self.stdout.write(
                self.style.WARNING(
                    f"{provider.label} has no wave model: wind and weather will be "
                    "stored, but no surf score can be computed."
                )
            )

        days = max(1, min(int(options.get("days") or 7), 16))
        include_forecast = bool(options.get("forecast", True))

        succeeded, failed = 0, []
        for spot in spots:
            condition = services.refresh_spot_conditions(
                spot, include_forecast=False, provider=provider
            )
            if condition is None:
                failed.append(spot.name)
                self.stdout.write(self.style.ERROR(f"  {spot.name}: no reading returned."))
                continue

            succeeded += 1
            detail = []
            if condition.wave_height_m is not None:
                detail.append(f"{condition.wave_height_m:.1f} m")
            if condition.wind_speed_kmh is not None:
                detail.append(f"wind {condition.wind_speed_kmh:.0f} km/h")
            if condition.water_temperature_c is not None:
                detail.append(f"water {condition.water_temperature_c:.1f} C")
            self.stdout.write(
                self.style.SUCCESS(f"  {spot.name}: " + (", ".join(detail) or "stored"))
            )

            if include_forecast:
                hours = services.refresh_spot_forecast(spot, days=days, provider=provider)
                self.stdout.write(f"    forecast: {hours} hour(s) stored.")

        summary = f"{succeeded} of {len(spots)} spot(s) refreshed via {provider.name}."
        if failed:
            self.stdout.write(self.style.WARNING(summary + f" Failed: {', '.join(failed)}."))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
