"""What the wizard actually does when somebody presses Finish.

Everything up to that point is a draft stored on one ``OnboardingState`` row.
Finish is the moment the answers become real records:

* ``core.SystemSetting`` rows the rest of the product reads at runtime;
* the primary ``locations.SurfSpot``, because scheduling, surf scoring and half
  the safety logic need somewhere to fall back to.

Both are reached through ``django.apps.apps.get_model`` rather than an import.
This module must not create an import-time dependency on another app — the
wizard has to keep working on an installation where a module is disabled, and a
missing app degrades to "that part was skipped" instead of a crash on the
dashboard of every user.

Applying is **idempotent and non-destructive**: an existing primary spot is
never overwritten, and re-running Finish updates settings rather than
duplicating them.
"""

from __future__ import annotations

import logging

from django.apps import apps as django_apps
from django.conf import settings as django_settings
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import record_audit

from .models import OnboardingState

logger = logging.getLogger("apps.onboarding")

#: Session flag set when a user dismisses the dashboard banner.
BANNER_DISMISSED_SESSION_KEY = "onboarding.banner_dismissed"

#: ``SystemSetting`` keys written on Finish. Namespaced so another module can
#: read them without guessing.
SETTING_SCHOOL_NAME = "school.name"
SETTING_CURRENCY = "school.currency"
SETTING_TIMEZONE = "school.timezone"
SETTING_LANGUAGE = "school.default_language"
SETTING_LATITUDE = "school.latitude"
SETTING_LONGITUDE = "school.longitude"
SETTING_PRIMARY_SPOT = "school.primary_spot"
SETTING_AI_CONFIGURED = "ai.configured"
SETTING_BACKUP_CONFIGURED = "backup.configured"
SETTING_ONBOARDING_COMPLETED = "onboarding.completed"
SETTING_ONBOARDING_COMPLETED_AT = "onboarding.completed_at"


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------
def resolve_optional_url(url_name: str) -> str | None:
    """Reverse *url_name*, or return ``None`` if that module is not installed.

    The wizard links out to Locations, Users, the AI Control Center and Backups.
    On a deployment where one of those is switched off, the step still has to
    render — so a missing route hides a button instead of raising.
    """
    if not url_name:
        return None
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def _get_model(app_label: str, model_name: str):
    """Look a model up lazily, returning ``None`` when its app is absent."""
    try:
        return django_apps.get_model(app_label, model_name)
    except LookupError:
        logger.warning("Onboarding: %s.%s is not installed", app_label, model_name)
        return None


# ---------------------------------------------------------------------------
# Reading the environment the wizard is describing
# ---------------------------------------------------------------------------
def staff_overview() -> dict:
    """How many accounts exist, so the Staff step can say something true."""
    User = _get_model("accounts", "User")
    if User is None:
        return {"total": 0, "staff": 0, "external": 0}

    from apps.accounts.constants import EXTERNAL_ROLES  # noqa: PLC0415 - avoids a cycle

    total = User.objects.filter(is_active=True).count()
    external = User.objects.filter(is_active=True, role__in=list(EXTERNAL_ROLES)).count()
    return {"total": total, "staff": total - external, "external": external}


def existing_primary_spot():
    """The live default surf spot, if one has already been created."""
    SurfSpot = _get_model("locations", "SurfSpot")
    if SurfSpot is None:
        return None
    return SurfSpot.objects.filter(is_primary=True).first()


def spot_count() -> int:
    SurfSpot = _get_model("locations", "SurfSpot")
    if SurfSpot is None:
        return 0
    return SurfSpot.objects.count()


def defaults_from_settings() -> dict:
    """Values to pre-fill the wizard with, taken from the deployed settings."""
    school = getattr(django_settings, "SCHOOL", {}) or {}
    return {
        "school_name": school.get("NAME", ""),
        "currency": school.get("CURRENCY", ""),
        "timezone": getattr(django_settings, "TIME_ZONE", "") or "",
        "default_language": getattr(django_settings, "LANGUAGE_CODE", "") or "",
        "latitude": school.get("DEFAULT_LATITUDE"),
        "longitude": school.get("DEFAULT_LONGITUDE"),
        "primary_spot_name": school.get("DEFAULT_SPOT_NAME", ""),
    }


def prefill_state(state: OnboardingState) -> OnboardingState:
    """Fill blank fields from the deployment defaults, once, on first open."""
    defaults = defaults_from_settings()
    changed: list[str] = []
    for field, value in defaults.items():
        if value in (None, ""):
            continue
        if getattr(state, field) in (None, ""):
            setattr(state, field, value)
            changed.append(field)
    if changed:
        state.save(update_fields=[*changed, "updated_at"])
    return state


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _write_settings(state: OnboardingState) -> list[str]:
    """Write the answered values into ``core.SystemSetting``. Returns the keys."""
    SystemSetting = _get_model("core", "SystemSetting")
    if SystemSetting is None:
        return []

    written: list[str] = []

    def put(key: str, value, value_type: str, group: str = "school") -> None:
        SystemSetting.set(key, value, value_type=value_type, group=group)
        written.append(key)

    string_type = SystemSetting.ValueType.STRING
    decimal_type = SystemSetting.ValueType.DECIMAL
    boolean_type = SystemSetting.ValueType.BOOLEAN

    if state.school_name:
        put(SETTING_SCHOOL_NAME, state.school_name, string_type)
    if state.currency:
        put(SETTING_CURRENCY, state.currency, string_type)
    if state.timezone:
        put(SETTING_TIMEZONE, state.timezone, string_type)
    if state.default_language:
        put(SETTING_LANGUAGE, state.default_language, string_type)
    if state.latitude is not None:
        put(SETTING_LATITUDE, state.latitude, decimal_type, group="location")
    if state.longitude is not None:
        put(SETTING_LONGITUDE, state.longitude, decimal_type, group="location")
    if state.primary_spot_name:
        put(SETTING_PRIMARY_SPOT, state.primary_spot_name, string_type, group="location")

    put(SETTING_AI_CONFIGURED, state.ai_configured, boolean_type, group="ai")
    put(SETTING_BACKUP_CONFIGURED, state.backup_configured, boolean_type, group="backup")
    put(SETTING_ONBOARDING_COMPLETED, True, boolean_type, group="onboarding")
    put(
        SETTING_ONBOARDING_COMPLETED_AT,
        timezone.now().isoformat(timespec="seconds"),
        string_type,
        group="onboarding",
    )
    return written


def _create_primary_spot(state: OnboardingState, user=None):
    """Create the school's default surf spot, unless one already exists.

    Returns ``(spot, created)``. An existing primary spot is left alone: the
    wizard must never renumber a break the school has already been operating on,
    because every lesson, incident and score in the system points at that row.
    """
    SurfSpot = _get_model("locations", "SurfSpot")
    if SurfSpot is None or not state.can_create_spot:
        return None, False

    existing = SurfSpot.objects.filter(is_primary=True).first()
    if existing is not None:
        return existing, False

    same_name = SurfSpot.objects.filter(name__iexact=state.primary_spot_name).first()
    if same_name is not None:
        return same_name, False

    spot = SurfSpot(
        name=state.primary_spot_name,
        latitude=float(state.latitude),
        longitude=float(state.longitude),
        beach_facing_deg=float(state.beach_facing_deg),
        is_active=True,
        description=str(
            _("Created by the setup wizard. Check the hazards, access notes and "
              "emergency information before the first lesson.")
        ),
    )
    if user is not None and getattr(user, "is_authenticated", False):
        spot.created_by = user
        spot.updated_by = user

    # slug and code are assigned in SurfSpot.save(); validating them here would
    # reject the blank values the model fills in itself.
    spot.full_clean(exclude=["slug", "code"])
    spot.save()
    return spot, True


@transaction.atomic
def complete_onboarding(state: OnboardingState, *, request=None, user=None) -> dict:
    """Apply the wizard's answers and mark setup done.

    Safe to call twice: settings are updated in place and an existing primary
    spot is reused rather than duplicated.
    """
    spot, spot_created = _create_primary_spot(state, user=user)
    written_keys = _write_settings(state)

    state.is_completed = True
    state.completed_at = timezone.now()
    state.current_step = 9
    if user is not None and getattr(user, "is_authenticated", False) and state.started_by is None:
        state.started_by = user
    state.save()

    record_audit(
        request,
        action=AuditAction.SETTINGS_CHANGE,
        instance=state,
        user=user,
        description=_("First-run setup completed"),
        changes={"settings_written": [None, written_keys]} if written_keys else None,
    )

    return {
        "spot": spot,
        "spot_created": spot_created,
        "settings_written": written_keys,
    }


@transaction.atomic
def skip_onboarding(state: OnboardingState, *, request=None, user=None) -> OnboardingState:
    """Stop asking, without writing anything.

    Skipping is a legitimate answer — a school restoring a backup already has
    every one of these values. Nothing is applied, so no existing configuration
    is touched; the banner simply goes away.
    """
    state.is_completed = True
    state.completed_at = timezone.now()
    if user is not None and getattr(user, "is_authenticated", False) and state.started_by is None:
        state.started_by = user
    state.save()

    record_audit(
        request,
        action=AuditAction.SETTINGS_CHANGE,
        instance=state,
        user=user,
        description=_("First-run setup skipped"),
    )
    return state


@transaction.atomic
def restart_onboarding(state: OnboardingState, *, request=None, user=None) -> OnboardingState:
    """Reopen the wizard. Answers are kept so it can be reviewed, not retyped."""
    state.is_completed = False
    state.completed_at = None
    state.current_step = 1
    state.save(update_fields=["is_completed", "completed_at", "current_step", "updated_at"])

    record_audit(
        request,
        action=AuditAction.SETTINGS_CHANGE,
        instance=state,
        user=user,
        description=_("First-run setup reopened"),
    )
    return state


def record_step(state: OnboardingState, *, slug: str, number: int, answered: bool) -> None:
    """Remember where the operator got to and whether they answered."""
    if answered:
        state.mark_answered(slug)
    else:
        state.mark_unanswered(slug)
    state.current_step = max(state.current_step or 1, number)
    state.save(update_fields=["completed_steps", "current_step", "updated_at"])


# ---------------------------------------------------------------------------
# The dashboard banner
# ---------------------------------------------------------------------------
def should_show_banner(request) -> bool:
    """Should the dashboard nudge this user towards setup?

    Only for users who could actually do something about it, only while setup is
    unfinished, and not once they have dismissed it in this session.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    if not user.has_capability("onboarding.view"):
        return False

    session = getattr(request, "session", None)
    if session is not None and session.get(BANNER_DISMISSED_SESSION_KEY):
        return False

    return not OnboardingState.is_setup_complete()


def dismiss_banner(request) -> None:
    """Hide the banner for the rest of this session."""
    session = getattr(request, "session", None)
    if session is None:
        return
    session[BANNER_DISMISSED_SESSION_KEY] = True
    session.modified = True


def summary_rows(state: OnboardingState) -> list[dict]:
    """What Finish is about to apply, in the order the wizard asked for it."""
    labels = {
        "school_name": _("School name"),
        "timezone": _("Timezone"),
        "default_language": _("Default language"),
        "currency": _("Currency"),
        "primary_spot_name": _("Primary surf spot"),
        "coordinates": _("Coordinates"),
        "beach_facing_deg": _("Beach facing"),
        "ai_configured": _("AI assistant"),
        "backup_configured": _("Backups"),
    }
    not_set = _("Not set")

    coordinates = (
        f"{state.latitude:.5f}, {state.longitude:.5f}" if state.has_coordinates else None
    )
    facing = f"{state.beach_facing_deg:g}°" if state.beach_facing_deg is not None else None

    values = {
        "school_name": state.school_name,
        "timezone": state.timezone,
        "default_language": state.get_default_language_display() if state.default_language else "",
        "currency": state.get_currency_display() if state.currency else "",
        "primary_spot_name": state.primary_spot_name,
        "coordinates": coordinates,
        "beach_facing_deg": facing,
        "ai_configured": _("Configured") if state.ai_configured else _("Not configured"),
        "backup_configured": _("Configured") if state.backup_configured else _("Not configured"),
    }

    return [
        {
            "key": key,
            "label": labels[key],
            "value": values.get(key) or not_set,
            "is_set": bool(values.get(key)),
        }
        for key in labels
    ]
