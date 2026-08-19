"""Training Center business rules.

Progress is deliberately forgiving: a user can tick a step off, untick it, jump
ahead, or come back to a course a month later on a different device. The rules
that must hold are only these —

* one progress row per user per course, ever;
* a step counts once, however many times the button is pressed;
* a course is complete when every *current* step is ticked, so adding a step to
  a course reopens it for everyone rather than pretending they finished it;
* removing steps from a course never pushes anybody above 100%.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from . import selectors
from .models import TrainingCourse, TrainingLesson, TrainingProgress, TrainingStep


@dataclass(frozen=True)
class CourseProgressSummary:
    """Everything a card or a list row needs about one course, for one user."""

    course: TrainingCourse
    progress: TrainingProgress | None
    status: str
    percent: int
    completed_steps: int
    total_steps: int
    next_step: TrainingStep | None

    @property
    def is_completed(self) -> bool:
        return self.status == TrainingProgress.Status.COMPLETED

    @property
    def is_started(self) -> bool:
        return self.status != TrainingProgress.Status.NOT_STARTED

    @property
    def remaining_steps(self) -> int:
        return max(0, self.total_steps - self.completed_steps)

    @property
    def status_label(self):
        return TrainingProgress.Status(self.status).label


# ---------------------------------------------------------------------------
# Sequencing
# ---------------------------------------------------------------------------
def course_step_sequence(course: TrainingCourse) -> list[TrainingStep]:
    """Every step of *course* in the order a learner walks them."""
    return list(selectors.steps_for(course))


def adjacent_steps(step: TrainingStep) -> tuple[TrainingStep | None, TrainingStep | None]:
    """The step before and the step after *step*, across lesson boundaries."""
    sequence = course_step_sequence(step.lesson.course)
    try:
        index = next(i for i, item in enumerate(sequence) if item.pk == step.pk)
    except StopIteration:
        return None, None
    previous_step = sequence[index - 1] if index > 0 else None
    next_step = sequence[index + 1] if index + 1 < len(sequence) else None
    return previous_step, next_step


def first_incomplete_step(
    course: TrainingCourse, progress: TrainingProgress | None
) -> TrainingStep | None:
    """Where the learner should be sent when they press *Continue*."""
    sequence = course_step_sequence(course)
    if not sequence:
        return None
    done = progress.completed_step_ids if progress else set()
    for step in sequence:
        if step.pk not in done:
            return step
    return sequence[-1]


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------
@transaction.atomic
def start_course(user, course: TrainingCourse) -> TrainingProgress:
    """Begin (or resume) *course* for *user*.

    Safe to call repeatedly: an already-completed course is not reset, and a
    course in progress simply has its activity clock stamped.
    """
    now = timezone.now()
    progress, created = TrainingProgress.objects.get_or_create(
        user=user,
        course=course,
        defaults={
            "status": TrainingProgress.Status.IN_PROGRESS,
            "completed_steps": [],
            "started_at": now,
            "last_activity_at": now,
        },
    )

    target = first_incomplete_step(course, progress)
    fields: list[str] = ["last_activity_at"]
    progress.last_activity_at = now

    if progress.status == TrainingProgress.Status.NOT_STARTED:
        progress.status = TrainingProgress.Status.IN_PROGRESS
        fields.append("status")
    if progress.started_at is None:
        progress.started_at = now
        fields.append("started_at")
    if target is not None and progress.step_id != target.pk:
        progress.step = target
        progress.lesson = target.lesson
        fields.extend(["step", "lesson"])

    if not created:
        progress.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
    else:
        progress.save()
    return progress


@transaction.atomic
def complete_step(user, step: TrainingStep) -> TrainingProgress:
    """Tick *step* off for *user* and move the cursor to the next one."""
    course = step.lesson.course
    progress = start_course(user, course)

    done = progress.completed_step_ids
    done.add(step.pk)

    sequence = course_step_sequence(course)
    valid_ids = {item.pk for item in sequence}
    # Steps deleted from the course must not linger in somebody's progress.
    progress.completed_steps = sorted(done & valid_ids)

    remaining = [item for item in sequence if item.pk not in progress.completed_steps]
    now = timezone.now()

    if remaining:
        cursor = remaining[0]
        progress.step = cursor
        progress.lesson = cursor.lesson
        progress.status = TrainingProgress.Status.IN_PROGRESS
        progress.completed_at = None
    else:
        progress.step = step
        progress.lesson = step.lesson
        progress.status = TrainingProgress.Status.COMPLETED
        progress.completed_at = progress.completed_at or now

    progress.last_activity_at = now
    progress.save()
    return progress


@transaction.atomic
def uncomplete_step(user, step: TrainingStep) -> TrainingProgress:
    """Untick *step*. Reopens the course if it had been finished."""
    course = step.lesson.course
    progress = start_course(user, course)

    done = progress.completed_step_ids
    done.discard(step.pk)
    progress.completed_steps = sorted(done)

    # Unticking anything reopens the course, including one that read "completed".
    progress.status = TrainingProgress.Status.IN_PROGRESS
    progress.completed_at = None
    progress.step = step
    progress.lesson = step.lesson
    progress.last_activity_at = timezone.now()
    progress.save()
    return progress


@transaction.atomic
def reset_course(user, course: TrainingCourse) -> TrainingProgress:
    """Clear all progress on *course* so it can be walked again from the top."""
    progress = start_course(user, course)
    progress.completed_steps = []
    progress.status = TrainingProgress.Status.NOT_STARTED
    progress.completed_at = None
    progress.started_at = None
    progress.last_activity_at = timezone.now()

    first = first_incomplete_step(course, None)
    progress.step = first
    progress.lesson = first.lesson if first else None
    progress.save()
    return progress


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------
def _summarise(
    course: TrainingCourse, progress: TrainingProgress | None, total_steps: int | None = None
) -> CourseProgressSummary:
    total = course.total_steps if total_steps is None else int(total_steps)
    if progress is not None:
        progress._total_steps = total
    done = min(progress.completed_count, total) if progress else 0
    stored = progress.status if progress else TrainingProgress.Status.NOT_STARTED

    # The stored status can lag behind the content: steps get added to a course
    # after somebody finished it, and removed after somebody stalled on them.
    # The displayed status is always derived from the steps that exist now.
    if not total:
        status = stored
    elif done >= total:
        status = TrainingProgress.Status.COMPLETED
    elif stored == TrainingProgress.Status.COMPLETED:
        status = TrainingProgress.Status.IN_PROGRESS
    else:
        status = stored

    percent = progress.percent_complete if progress else 0

    return CourseProgressSummary(
        course=course,
        progress=progress,
        status=status,
        percent=percent,
        completed_steps=done,
        total_steps=total,
        next_step=first_incomplete_step(course, progress),
    )


def course_progress(user, course: TrainingCourse | None = None):
    """Progress summaries for *user*.

    With *course*, one :class:`CourseProgressSummary`. Without it, a list
    covering every course the user is allowed to see, in course order — one
    query for the courses and one for the progress rows, whatever the count.
    """
    if course is not None:
        return _summarise(course, selectors.progress_for(user, course))

    records = selectors.progress_map(user)
    return [
        _summarise(item, records.get(item.pk), total_steps=getattr(item, "step_total", None))
        for item in selectors.courses_for(user)
    ]


def overall_progress(user) -> dict:
    """Headline numbers for the "my progress" screen."""
    summaries = course_progress(user)
    total_steps = sum(item.total_steps for item in summaries)
    done_steps = sum(item.completed_steps for item in summaries)
    completed_courses = sum(1 for item in summaries if item.is_completed)
    started_courses = sum(1 for item in summaries if item.is_started and not item.is_completed)
    remaining_minutes = sum(
        item.course.estimated_minutes for item in summaries if not item.is_completed
    )

    return {
        "courses_total": len(summaries),
        "courses_completed": completed_courses,
        "courses_in_progress": started_courses,
        "courses_not_started": len(summaries) - completed_courses - started_courses,
        "steps_total": total_steps,
        "steps_completed": done_steps,
        "percent": round(done_steps * 100 / total_steps) if total_steps else 0,
        "remaining_minutes": remaining_minutes,
        "summaries": summaries,
    }


def lesson_progress(lesson: TrainingLesson, progress: TrainingProgress | None) -> dict:
    """Completion of a single lesson, for the course outline."""
    steps = list(lesson.steps.all().order_by("order"))
    done_ids = progress.completed_step_ids if progress else set()
    done = sum(1 for step in steps if step.pk in done_ids)
    total = len(steps)
    return {
        "lesson": lesson,
        "steps": steps,
        "completed": done,
        "total": total,
        "percent": round(done * 100 / total) if total else 0,
        "is_complete": bool(total) and done == total,
    }
