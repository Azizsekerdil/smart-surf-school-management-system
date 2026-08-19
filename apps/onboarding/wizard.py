"""The wizard's shape: which steps exist, in what order, and what each edits.

Keeping this in one table means the progress indicator, the URL router, the
"next"/"back" links and the finish summary all read from the same source — a
step cannot appear in the sidebar and be unreachable in the router.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from . import forms


@dataclass(frozen=True)
class WizardStep:
    number: int
    slug: str
    label: object
    icon: str
    headline: object
    form_class: type | None = None
    #: URL name of the screen this step points at, if any. Resolved defensively:
    #: a module missing from this deployment must not break setup.
    target_url: str = ""

    @property
    def has_form(self) -> bool:
        return self.form_class is not None


STEPS: tuple[WizardStep, ...] = (
    WizardStep(
        number=1,
        slug="welcome",
        label=_("Welcome"),
        icon="waves",
        headline=_("Let's set this up for your school"),
    ),
    WizardStep(
        number=2,
        slug="business",
        label=_("Business information"),
        icon="building-2",
        headline=_("What is the school called, and where in the world is it?"),
        form_class=forms.BusinessInfoForm,
    ),
    WizardStep(
        number=3,
        slug="language",
        label=_("Language"),
        icon="languages",
        headline=_("Which language should the system default to?"),
        form_class=forms.LanguageForm,
    ),
    WizardStep(
        number=4,
        slug="currency",
        label=_("Currency"),
        icon="banknote",
        headline=_("What currency does the school take money in?"),
        form_class=forms.CurrencyForm,
    ),
    WizardStep(
        number=5,
        slug="location",
        label=_("Location and surf spots"),
        icon="map-pin",
        headline=_("Where do you put people in the water?"),
        form_class=forms.LocationForm,
        target_url="locations:list",
    ),
    WizardStep(
        number=6,
        slug="staff",
        label=_("Staff"),
        icon="users",
        headline=_("Who else needs an account?"),
        target_url="accounts:user_list",
    ),
    WizardStep(
        number=7,
        slug="ai",
        label=_("AI setup"),
        icon="sparkles",
        headline=_("Optional: connect an AI provider"),
        form_class=forms.AISetupForm,
        target_url="ai:control_center",
    ),
    WizardStep(
        number=8,
        slug="backup",
        label=_("Backup setup"),
        icon="database-backup",
        headline=_("The ten minutes a week that saves the season"),
        form_class=forms.BackupSetupForm,
        target_url="backups:list",
    ),
    WizardStep(
        number=9,
        slug="finish",
        label=_("Finish"),
        icon="circle-check",
        headline=_("Ready to apply"),
    ),
)

STEPS_BY_SLUG: dict[str, WizardStep] = {step.slug: step for step in STEPS}
STEPS_BY_NUMBER: dict[int, WizardStep] = {step.number: step for step in STEPS}

FIRST_STEP: WizardStep = STEPS[0]
LAST_STEP: WizardStep = STEPS[-1]
TOTAL_STEPS: int = len(STEPS)


def get_step(slug: str) -> WizardStep | None:
    return STEPS_BY_SLUG.get(slug)


def step_for_number(number: int) -> WizardStep:
    """Clamp *number* into the wizard's range and return that step."""
    try:
        value = int(number)
    except (TypeError, ValueError):
        value = FIRST_STEP.number
    value = max(FIRST_STEP.number, min(value, LAST_STEP.number))
    return STEPS_BY_NUMBER[value]


def neighbours(step: WizardStep) -> tuple[WizardStep | None, WizardStep | None]:
    """The step before and the step after *step*."""
    previous_step = STEPS_BY_NUMBER.get(step.number - 1)
    next_step = STEPS_BY_NUMBER.get(step.number + 1)
    return previous_step, next_step
