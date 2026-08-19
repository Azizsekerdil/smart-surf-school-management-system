from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.constants import Role
from apps.training import services
from apps.training.models import TrainingProgress

from .factories import TrainingCourseFactory, TrainingStepFactory, build_course

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="learner", email="learner@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


def steps_of(course):
    return list(services.course_step_sequence(course))


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------
def test_start_course_creates_one_row_and_points_at_the_first_step(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    progress = services.start_course(learner, course)

    assert progress.status == TrainingProgress.Status.IN_PROGRESS
    assert progress.started_at is not None
    assert progress.step == steps_of(course)[0]
    assert TrainingProgress.objects.filter(user=learner, course=course).count() == 1


def test_start_course_is_idempotent(learner):
    course = build_course()
    first = services.start_course(learner, course)
    second = services.start_course(learner, course)
    assert first.pk == second.pk
    assert TrainingProgress.objects.filter(user=learner).count() == 1


def test_start_course_resumes_rather_than_resetting(learner):
    course = build_course(lessons=1, steps_per_lesson=3)
    sequence = steps_of(course)
    services.complete_step(learner, sequence[0])

    progress = services.start_course(learner, course)
    assert progress.completed_steps == [sequence[0].pk]
    assert progress.step == sequence[1]


def test_start_course_with_no_steps_does_not_crash(learner):
    course = TrainingCourseFactory()
    progress = services.start_course(learner, course)
    assert progress.step is None
    assert progress.percent_complete == 0


# ---------------------------------------------------------------------------
# Completing
# ---------------------------------------------------------------------------
def test_complete_step_advances_the_cursor(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    sequence = steps_of(course)

    progress = services.complete_step(learner, sequence[0])
    assert progress.completed_steps == [sequence[0].pk]
    assert progress.step == sequence[1]
    assert progress.status == TrainingProgress.Status.IN_PROGRESS


def test_completing_the_same_step_twice_counts_once(learner):
    course = build_course(lessons=1, steps_per_lesson=3)
    step = steps_of(course)[0]
    services.complete_step(learner, step)
    progress = services.complete_step(learner, step)
    assert progress.completed_steps == [step.pk]
    assert progress.percent_complete == 33


def test_completing_every_step_finishes_the_course(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    for step in steps_of(course):
        progress = services.complete_step(learner, step)

    assert progress.status == TrainingProgress.Status.COMPLETED
    assert progress.completed_at is not None
    assert progress.percent_complete == 100


def test_completing_out_of_order_keeps_the_cursor_on_the_first_gap(learner):
    course = build_course(lessons=1, steps_per_lesson=3)
    sequence = steps_of(course)
    progress = services.complete_step(learner, sequence[2])
    assert progress.step == sequence[0]


def test_a_new_step_reopens_a_completed_course(learner):
    """Adding a step must not leave people falsely marked as finished."""
    course = build_course(lessons=1, steps_per_lesson=2)
    for step in steps_of(course):
        services.complete_step(learner, step)

    lesson = course.lessons.first()
    TrainingStepFactory(lesson=lesson, order=99)

    summary = services.course_progress(learner, course)
    assert summary.is_completed is False
    assert summary.percent == 67


def test_removing_a_step_does_not_exceed_one_hundred_percent(learner):
    course = build_course(lessons=1, steps_per_lesson=3)
    sequence = steps_of(course)
    for step in sequence:
        services.complete_step(learner, step)

    sequence[0].delete()

    summary = services.course_progress(learner, course)
    assert summary.percent == 100
    assert summary.total_steps == 2


def test_uncomplete_step_reopens_the_course(learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    sequence = steps_of(course)
    for step in sequence:
        services.complete_step(learner, step)

    progress = services.uncomplete_step(learner, sequence[0])
    assert progress.status == TrainingProgress.Status.IN_PROGRESS
    assert progress.completed_at is None
    assert sequence[0].pk not in progress.completed_step_ids


def test_reset_course_clears_everything(learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    for step in steps_of(course):
        services.complete_step(learner, step)

    progress = services.reset_course(learner, course)
    assert progress.completed_steps == []
    assert progress.status == TrainingProgress.Status.NOT_STARTED
    assert progress.completed_at is None


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
def test_adjacent_steps_cross_lesson_boundaries(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    sequence = steps_of(course)

    previous_step, next_step = services.adjacent_steps(sequence[1])
    assert previous_step == sequence[0]
    assert next_step == sequence[2]
    assert next_step.lesson_id != sequence[1].lesson_id


def test_first_and_last_steps_have_no_neighbour_beyond_the_course():
    course = build_course(lessons=1, steps_per_lesson=2)
    sequence = steps_of(course)
    assert services.adjacent_steps(sequence[0])[0] is None
    assert services.adjacent_steps(sequence[-1])[1] is None


def test_first_incomplete_step_returns_the_last_step_when_finished(learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    sequence = steps_of(course)
    for step in sequence:
        services.complete_step(learner, step)
    progress = TrainingProgress.objects.get(user=learner, course=course)
    assert services.first_incomplete_step(course, progress) == sequence[-1]


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def test_course_progress_lists_every_visible_course(learner):
    build_course(code="a", sort_order=1)
    build_course(code="b", sort_order=2)
    summaries = services.course_progress(learner)
    assert [s.course.code for s in summaries] == ["a", "b"]
    assert all(s.status == TrainingProgress.Status.NOT_STARTED for s in summaries)


def test_course_progress_hides_courses_needing_a_missing_capability(learner):
    build_course(code="open", required_capability="")
    build_course(code="restricted", required_capability="backups.restore")
    summaries = services.course_progress(learner)
    assert [s.course.code for s in summaries] == ["open"]


def test_course_progress_hides_inactive_courses(learner):
    build_course(code="live")
    build_course(code="retired", is_active=False)
    assert [s.course.code for s in services.course_progress(learner)] == ["live"]


def test_overall_progress_aggregates(learner):
    first = build_course(code="a", lessons=1, steps_per_lesson=2, sort_order=1)
    build_course(code="b", lessons=1, steps_per_lesson=2, sort_order=2)
    for step in steps_of(first):
        services.complete_step(learner, step)

    overall = services.overall_progress(learner)
    assert overall["courses_total"] == 2
    assert overall["courses_completed"] == 1
    assert overall["steps_total"] == 4
    assert overall["steps_completed"] == 2
    assert overall["percent"] == 50


def test_overall_progress_is_safe_with_no_courses(learner):
    overall = services.overall_progress(learner)
    assert overall["percent"] == 0
    assert overall["courses_total"] == 0


def test_lesson_progress_reports_per_lesson_completion(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    lesson = course.lessons.order_by("order").first()
    for step in lesson.steps.all():
        services.complete_step(learner, step)

    progress = TrainingProgress.objects.get(user=learner, course=course)
    entry = services.lesson_progress(lesson, progress)
    assert entry["completed"] == 2
    assert entry["percent"] == 100
    assert entry["is_complete"] is True


def test_progress_is_per_user(learner):
    other = User.objects.create_user(
        username="other", email="other@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )
    course = build_course(lessons=1, steps_per_lesson=2)
    services.complete_step(learner, steps_of(course)[0])

    assert services.course_progress(other, course).completed_steps == 0
    assert services.course_progress(learner, course).completed_steps == 1
