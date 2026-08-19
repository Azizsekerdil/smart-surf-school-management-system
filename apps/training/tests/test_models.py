from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.utils import translation

from apps.accounts.constants import Role
from apps.training.models import TrainingProgress, resolve_screen_url

from .factories import (
    TrainingCourseFactory,
    TrainingLessonFactory,
    TrainingProgressFactory,
    TrainingStepFactory,
    build_course,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="learner", email="learner@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------
def test_course_title_follows_the_active_language():
    course = TrainingCourseFactory(title_en="First booking", title_tr="İlk rezervasyon")
    with translation.override("tr"):
        assert course.title == "İlk rezervasyon"
    with translation.override("en"):
        assert course.title == "First booking"


def test_course_counts_lessons_and_steps():
    course = build_course(lessons=3, steps_per_lesson=4)
    assert course.lesson_count == 3
    assert course.total_steps == 12


def test_course_str_uses_the_english_title():
    course = TrainingCourseFactory(title_en="First booking")
    assert str(course) == "First booking"


def test_difficulty_colour_is_mapped():
    from apps.training.models import Difficulty

    assert TrainingCourseFactory(difficulty=Difficulty.BEGINNER).difficulty_color == "emerald"
    assert TrainingCourseFactory(difficulty=Difficulty.ADVANCED).difficulty_color == "rose"


# ---------------------------------------------------------------------------
# Ordering constraints
# ---------------------------------------------------------------------------
def test_two_lessons_cannot_share_an_order_in_one_course():
    course = TrainingCourseFactory()
    TrainingLessonFactory(course=course, order=1)
    with pytest.raises(IntegrityError):
        TrainingLessonFactory(course=course, order=1)


def test_two_steps_cannot_share_an_order_in_one_lesson():
    lesson = TrainingLessonFactory(order=1)
    TrainingStepFactory(lesson=lesson, order=1)
    with pytest.raises(IntegrityError):
        TrainingStepFactory(lesson=lesson, order=1)


def test_the_same_order_is_fine_in_different_courses():
    first = TrainingLessonFactory(order=1)
    second = TrainingLessonFactory(order=1)  # different course via the SubFactory
    assert first.course_id != second.course_id


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def test_step_body_is_rendered_and_sanitised():
    step = TrainingStepFactory(body_en="**Bold** and <script>alert(1)</script>")
    with translation.override("en"):
        html = str(step.rendered_body())
    assert "<strong>Bold</strong>" in html
    assert "<script" not in html


def test_target_link_resolves_a_url_name():
    step = TrainingStepFactory(target_url="training:home")
    assert step.target_link is not None
    assert step.target_link.endswith("/training/")


def test_target_link_accepts_an_absolute_path():
    assert TrainingStepFactory(target_url="/students/new/").target_link == "/students/new/"


def test_target_link_is_none_for_an_unknown_screen():
    """A module that is not installed in this deployment must not raise."""
    assert TrainingStepFactory(target_url="nonexistent:screen").target_link is None


def test_target_link_is_none_when_empty():
    assert TrainingStepFactory(target_url="").target_link is None
    assert resolve_screen_url("   ") is None


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def test_percent_complete_is_zero_without_steps_done(learner):
    course = build_course(lessons=2, steps_per_lesson=3)
    progress = TrainingProgressFactory(user=learner, course=course)
    assert progress.percent_complete == 0


def test_percent_complete_counts_unique_steps(learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    step_ids = [s.pk for s in course.lessons.first().steps.all()]
    progress = TrainingProgressFactory(
        user=learner, course=course, completed_steps=[*step_ids, step_ids[0]]
    )
    assert progress.percent_complete == 50


def test_percent_complete_never_exceeds_one_hundred(learner):
    """Steps removed from a course must not push somebody above 100%."""
    course = build_course(lessons=1, steps_per_lesson=2)
    progress = TrainingProgressFactory(
        user=learner, course=course, completed_steps=[1, 2, 3, 4, 5, 6, 7]
    )
    assert progress.percent_complete == 100


def test_percent_complete_ignores_junk_entries(learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    steps = list(course.lessons.first().steps.all())
    progress = TrainingProgressFactory(
        user=learner, course=course, completed_steps=[steps[0].pk, None, "abc"]
    )
    assert progress.completed_count == 1
    assert progress.percent_complete == 50


def test_a_user_has_at_most_one_progress_row_per_course(learner):
    course = TrainingCourseFactory()
    TrainingProgressFactory(user=learner, course=course)
    with pytest.raises(IntegrityError):
        TrainingProgressFactory(user=learner, course=course)


def test_progress_str_is_readable(learner):
    course = TrainingCourseFactory(title_en="First booking")
    progress = TrainingProgressFactory(user=learner, course=course)
    assert "First booking" in str(progress)


def test_status_flags(learner):
    course = build_course()
    progress = TrainingProgressFactory(
        user=learner, course=course, status=TrainingProgress.Status.COMPLETED
    )
    assert progress.is_completed is True
    assert progress.is_started is True
