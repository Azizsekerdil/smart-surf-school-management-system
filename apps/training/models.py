"""Training Center: courses, lessons, steps and per-user progress.

Shape
-----
``TrainingCourse`` → ``TrainingLesson`` → ``TrainingStep``. A course is a job
("take your first booking"), a lesson is a stage of that job, and a step is one
thing you do. ``TrainingProgress`` is one row per user per course and is the only
table that grows with use.

Content is stored as ``*_en`` / ``*_tr`` column pairs and rendered through
:mod:`apps.help_center.content`, which sanitises the Markdown — the same policy
covers both guidance modules, and one sanitiser configuration is easier to audit
than two.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, TimeStampedModel
from apps.core.validators import slug_code_validator, validate_image_upload
from apps.help_center.content import RenderedContent, localized, render_content


class Difficulty(models.TextChoices):
    """How much surf-school context a course assumes."""

    BEGINNER = "beginner", _("Beginner")
    INTERMEDIATE = "intermediate", _("Intermediate")
    ADVANCED = "advanced", _("Advanced")


#: Badge colours for the difficulty pill, passed to ``{% status_badge %}``.
DIFFICULTY_COLORS: dict[str, str] = {
    Difficulty.BEGINNER: "emerald",
    Difficulty.INTERMEDIATE: "amber",
    Difficulty.ADVANCED: "rose",
}


def resolve_screen_url(target: str) -> str | None:
    """Turn a step's ``target_url`` into something a browser can follow.

    Accepts either a Django URL name (``"students:create"``) or an absolute
    path (``"/students/new/"``). A name that does not resolve — because that
    module is not installed in this deployment — returns ``None`` so the
    template simply omits the link instead of raising ``NoReverseMatch`` on a
    page whose whole job is to be reassuring.
    """
    value = (target or "").strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    try:
        return reverse(value)
    except NoReverseMatch:
        return None


class TrainingCourse(BaseModel):
    """One end-to-end task a member of staff needs to be able to perform."""

    code = models.SlugField(
        _("code"),
        max_length=60,
        unique=True,
        validators=[slug_code_validator],
        help_text=_("Stable identifier, e.g. first-student."),
    )
    title_en = models.CharField(_("title (EN)"), max_length=200)
    title_tr = models.CharField(_("title (TR)"), max_length=200)
    description_en = models.TextField(_("description (EN)"), blank=True)
    description_tr = models.TextField(_("description (TR)"), blank=True)
    icon = models.CharField(
        _("icon"),
        max_length=40,
        default="school",
        help_text=_("Name of a vendored Lucide icon, e.g. graduation-cap."),
    )
    estimated_minutes = models.PositiveIntegerField(
        _("estimated minutes"),
        default=10,
        validators=[MinValueValidator(1)],
        help_text=_("How long the whole course takes, including doing the work."),
    )
    difficulty = models.CharField(
        _("difficulty"),
        max_length=20,
        choices=Difficulty.choices,
        default=Difficulty.BEGINNER,
        db_index=True,
    )
    required_capability = models.CharField(
        _("required capability"),
        max_length=60,
        blank=True,
        help_text=_(
            "Only staff holding this capability see the course, e.g. bookings.add. "
            "Leave empty to show it to everyone."
        ),
    )
    sort_order = models.PositiveIntegerField(_("sort order"), default=100, db_index=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("training course")
        verbose_name_plural = _("training courses")
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="train_course_active_order"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return self.title_en or self.title_tr or self.code

    # -- language-aware content -------------------------------------------
    @property
    def title(self) -> str:
        return localized(self, "title", default=self.code)

    @property
    def description(self) -> str:
        return localized(self, "description")

    # -- derived values ----------------------------------------------------
    @property
    def lesson_count(self) -> int:
        """Number of lessons, using a prefetched list when one is available."""
        cache = getattr(self, "_prefetched_objects_cache", {})
        if "lessons" in cache:
            return len(cache["lessons"])
        return self.lessons.count()

    @property
    def total_steps(self) -> int:
        annotated = getattr(self, "step_total", None)
        if annotated is not None:
            return int(annotated)
        return TrainingStep.objects.filter(lesson__course=self).count()

    @property
    def difficulty_color(self) -> str:
        return DIFFICULTY_COLORS.get(self.difficulty, "slate")

    def get_absolute_url(self) -> str:
        return reverse("training:course", kwargs={"pk": self.pk})


class TrainingLesson(BaseModel):
    """A stage within a course."""

    course = models.ForeignKey(
        "training.TrainingCourse",
        verbose_name=_("course"),
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    order = models.PositiveIntegerField(
        _("order"), default=1, validators=[MinValueValidator(1)], db_index=True
    )
    title_en = models.CharField(_("title (EN)"), max_length=200)
    title_tr = models.CharField(_("title (TR)"), max_length=200)
    summary_en = models.TextField(_("summary (EN)"), blank=True)
    summary_tr = models.TextField(_("summary (TR)"), blank=True)
    estimated_minutes = models.PositiveIntegerField(
        _("estimated minutes"), default=5, validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = _("training lesson")
        verbose_name_plural = _("training lessons")
        ordering = ["course__sort_order", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"],
                condition=models.Q(is_deleted=False),
                name="train_lesson_unique_order",
            ),
        ]
        indexes = [
            models.Index(fields=["course", "order"], name="train_lesson_course_order"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return f"{self.order}. {self.title_en or self.title_tr}"

    @property
    def title(self) -> str:
        return localized(self, "title", default=str(self.order))

    @property
    def summary(self) -> str:
        return localized(self, "summary")

    @property
    def step_count(self) -> int:
        cache = getattr(self, "_prefetched_objects_cache", {})
        if "steps" in cache:
            return len(cache["steps"])
        return self.steps.count()

    @property
    def first_step(self):
        return self.steps.order_by("order").first()


class TrainingStep(BaseModel):
    """One instruction: what to do, where to do it, and what to expect."""

    lesson = models.ForeignKey(
        "training.TrainingLesson",
        verbose_name=_("lesson"),
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.PositiveIntegerField(
        _("order"), default=1, validators=[MinValueValidator(1)], db_index=True
    )
    title_en = models.CharField(_("title (EN)"), max_length=200)
    title_tr = models.CharField(_("title (TR)"), max_length=200)
    body_en = models.TextField(_("body (EN)"), blank=True, help_text=_("Markdown source."))
    body_tr = models.TextField(_("body (TR)"), blank=True, help_text=_("Markdown source."))
    target_url = models.CharField(
        _("target screen"),
        max_length=200,
        blank=True,
        help_text=_(
            "The screen this step is about: a URL name such as students:create, "
            "or an absolute path such as /students/new/."
        ),
    )
    action_hint_en = models.CharField(_("action hint (EN)"), max_length=200, blank=True)
    action_hint_tr = models.CharField(_("action hint (TR)"), max_length=200, blank=True)
    image = models.ImageField(
        _("screenshot"),
        upload_to="training/steps/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_image_upload],
    )

    class Meta:
        verbose_name = _("training step")
        verbose_name_plural = _("training steps")
        ordering = ["lesson__course__sort_order", "lesson__order", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "order"],
                condition=models.Q(is_deleted=False),
                name="train_step_unique_order",
            ),
        ]
        indexes = [
            models.Index(fields=["lesson", "order"], name="train_step_lesson_order"),
        ]
        base_manager_name = "all_objects"

    def __str__(self) -> str:
        return f"{self.lesson_id}.{self.order} {self.title_en or self.title_tr}"

    # -- language-aware content -------------------------------------------
    @property
    def title(self) -> str:
        return localized(self, "title", default=str(self.order))

    @property
    def body(self) -> str:
        return localized(self, "body")

    @property
    def action_hint(self) -> str:
        return localized(self, "action_hint")

    def _rendered(self) -> RenderedContent:
        source = self.body
        cached = getattr(self, "_render_cache", None)
        if cached is not None and cached[0] == source:
            return cached[1]
        result = render_content(source)
        self._render_cache = (source, result)
        return result

    def rendered_body(self) -> str:
        """Sanitised HTML for the active language — safe to output directly."""
        return self._rendered().html

    # -- navigation --------------------------------------------------------
    @property
    def target_link(self) -> str | None:
        """Resolved URL of the screen this step is about, or ``None``."""
        return resolve_screen_url(self.target_url)

    def get_absolute_url(self) -> str:
        return reverse("training:step", kwargs={"pk": self.pk})


class TrainingProgress(TimeStampedModel):
    """How far one user has got through one course."""

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", _("Not started")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        on_delete=models.CASCADE,
        related_name="training_progress",
    )
    course = models.ForeignKey(
        "training.TrainingCourse",
        verbose_name=_("course"),
        on_delete=models.CASCADE,
        related_name="progress_records",
    )
    lesson = models.ForeignKey(
        "training.TrainingLesson",
        verbose_name=_("current lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="progress_records",
    )
    step = models.ForeignKey(
        "training.TrainingStep",
        verbose_name=_("current step"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="progress_records",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.NOT_STARTED,
        db_index=True,
    )
    completed_steps = models.JSONField(
        _("completed steps"),
        default=list,
        blank=True,
        help_text=_("Primary keys of the steps this user has ticked off."),
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    last_activity_at = models.DateTimeField(_("last activity"), null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = _("training progress")
        verbose_name_plural = _("training progress")
        ordering = ["-last_activity_at", "course__sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["user", "course"], name="train_progress_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="train_progress_user_status"),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.course} · {self.get_status_display()}"

    # -- derived values ----------------------------------------------------
    @property
    def completed_step_ids(self) -> set[int]:
        """De-duplicated, integer-only view of the stored list."""
        values: set[int] = set()
        for raw in self.completed_steps or []:
            try:
                values.add(int(raw))
            except (TypeError, ValueError):
                continue
        return values

    @property
    def completed_count(self) -> int:
        return len(self.completed_step_ids)

    @property
    def total_steps(self) -> int:
        cached = getattr(self, "_total_steps", None)
        if cached is not None:
            return int(cached)
        return self.course.total_steps

    @property
    def percent_complete(self) -> int:
        """Whole-percent completion, clamped to 0–100.

        Steps can be removed from a course after somebody has ticked them off,
        so the numerator is capped at the current step count rather than being
        allowed to report 120%.
        """
        total = self.total_steps
        if not total:
            return 100 if self.status == self.Status.COMPLETED else 0
        done = min(self.completed_count, total)
        return round(done * 100 / total)

    @property
    def is_completed(self) -> bool:
        return self.status == self.Status.COMPLETED

    @property
    def is_started(self) -> bool:
        return self.status != self.Status.NOT_STARTED

    def touch(self) -> None:
        """Stamp the activity clock without saving."""
        self.last_activity_at = timezone.now()
