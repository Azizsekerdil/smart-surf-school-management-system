from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.training import services
from apps.training.models import TrainingProgress

from .factories import TrainingCourseFactory, build_course

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="learner", email="learner@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def other_learner(db):
    return User.objects.create_user(
        username="other", email="other@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def customer(db):
    """Customers hold no training capability at all."""
    return User.objects.create_user(
        username="guest", email="guest@example.com", password="pw-test-12345",
        role=Role.CUSTOMER,
    )


def steps_of(course):
    return list(services.course_step_sequence(course))


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_home_requires_authentication(client):
    response = client.get(reverse("training:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_a_customer_cannot_open_the_training_center(client, customer):
    client.force_login(customer)
    assert client.get(reverse("training:home")).status_code == 403


def test_a_course_needing_a_missing_capability_is_a_404(client, learner):
    course = build_course(required_capability="backups.restore")
    client.force_login(learner)
    assert client.get(reverse("training:course", args=[course.pk])).status_code == 404


def test_a_step_of_a_hidden_course_is_a_404(client, learner):
    course = build_course(required_capability="backups.restore")
    step = steps_of(course)[0]
    client.force_login(learner)
    assert client.get(reverse("training:step", args=[step.pk])).status_code == 404


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
def test_home_lists_courses_with_progress(client, learner):
    build_course(code="first-student", lessons=1, steps_per_lesson=2)
    client.force_login(learner)

    response = client.get(reverse("training:home"))
    assert response.status_code == 200
    assert len(response.context["summaries"]) == 1
    assert response.context["overall"]["steps_total"] == 2


def test_home_renders_an_empty_state_with_no_courses(client, learner):
    client.force_login(learner)
    response = client.get(reverse("training:home"))
    assert response.status_code == 200
    assert response.context["summaries"] == []


# ---------------------------------------------------------------------------
# Course detail
# ---------------------------------------------------------------------------
def test_course_detail_lists_lessons_and_steps(client, learner):
    course = build_course(lessons=2, steps_per_lesson=3)
    client.force_login(learner)

    response = client.get(reverse("training:course", args=[course.pk]))
    assert response.status_code == 200
    assert len(response.context["lessons"]) == 2
    assert response.context["lessons"][0]["total"] == 3


def test_course_detail_shows_completed_steps(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    first = steps_of(course)[0]
    services.complete_step(learner, first)

    client.force_login(learner)
    response = client.get(reverse("training:course", args=[course.pk]))
    assert first.pk in response.context["completed_ids"]


# ---------------------------------------------------------------------------
# Step view
# ---------------------------------------------------------------------------
def test_step_view_renders_navigation(client, learner):
    course = build_course(lessons=2, steps_per_lesson=2)
    sequence = steps_of(course)
    client.force_login(learner)

    response = client.get(reverse("training:step", args=[sequence[1].pk]))
    assert response.status_code == 200
    assert response.context["previous_step"] == sequence[0]
    assert response.context["next_step"] == sequence[2]
    assert response.context["step_position"] == 2
    assert response.context["step_total"] == 4


def test_opening_a_step_starts_the_course(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    client.force_login(learner)

    client.get(reverse("training:step", args=[steps_of(course)[0].pk]))
    progress = TrainingProgress.objects.get(user=learner, course=course)
    assert progress.status == TrainingProgress.Status.IN_PROGRESS


def test_step_body_is_sanitised(client, learner):
    course = build_course(lessons=1, steps_per_lesson=1)
    step = steps_of(course)[0]
    step.body_en = "<script>alert(1)</script>Do the thing."
    step.body_tr = "<script>alert(1)</script>İşi yapın."
    step.save()

    client.force_login(learner)
    response = client.get(reverse("training:step", args=[step.pk]))
    assert b"<script>alert(1)</script>" not in response.content


# ---------------------------------------------------------------------------
# Completing over HTMX
# ---------------------------------------------------------------------------
def test_mark_complete_over_htmx_returns_the_partial(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    step = steps_of(course)[0]
    client.force_login(learner)

    response = client.post(
        reverse("training:step_complete", args=[step.pk]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert "training/partials/step_status.html" in [t.name for t in response.templates]

    progress = TrainingProgress.objects.get(user=learner, course=course)
    assert step.pk in progress.completed_step_ids


def test_mark_complete_without_htmx_goes_to_the_next_step(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    sequence = steps_of(course)
    client.force_login(learner)

    response = client.post(reverse("training:step_complete", args=[sequence[0].pk]))
    assert response.status_code == 302
    assert response.url == reverse("training:step", args=[sequence[1].pk])


def test_completing_the_last_step_returns_to_the_course(client, learner):
    course = build_course(lessons=1, steps_per_lesson=1)
    step = steps_of(course)[0]
    client.force_login(learner)

    response = client.post(reverse("training:step_complete", args=[step.pk]))
    assert response.url == reverse("training:course", args=[course.pk])


def test_undo_unticks_the_step(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    step = steps_of(course)[0]
    services.complete_step(learner, step)
    client.force_login(learner)

    client.post(
        reverse("training:step_complete", args=[step.pk]), {"undo": "1"}, HTTP_HX_REQUEST="true"
    )
    progress = TrainingProgress.objects.get(user=learner, course=course)
    assert step.pk not in progress.completed_step_ids


def test_step_complete_rejects_get(client, learner):
    course = build_course(lessons=1, steps_per_lesson=1)
    step = steps_of(course)[0]
    client.force_login(learner)
    assert client.get(reverse("training:step_complete", args=[step.pk])).status_code == 405


def test_completion_is_recorded_against_the_signed_in_user_only(
    client, learner, other_learner
):
    course = build_course(lessons=1, steps_per_lesson=2)
    step = steps_of(course)[0]
    client.force_login(learner)
    client.post(reverse("training:step_complete", args=[step.pk]))

    assert TrainingProgress.objects.filter(user=learner, course=course).exists()
    assert not TrainingProgress.objects.filter(user=other_learner, course=course).exists()


# ---------------------------------------------------------------------------
# Start / reset
# ---------------------------------------------------------------------------
def test_start_redirects_to_the_first_incomplete_step(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    sequence = steps_of(course)
    services.complete_step(learner, sequence[0])
    client.force_login(learner)

    response = client.post(reverse("training:course_start", args=[course.pk]))
    assert response.url == reverse("training:step", args=[sequence[1].pk])


def test_start_on_an_empty_course_returns_to_the_course(client, learner):
    course = TrainingCourseFactory()
    client.force_login(learner)
    response = client.post(reverse("training:course_start", args=[course.pk]))
    assert response.url == reverse("training:course", args=[course.pk])


def test_reset_clears_progress(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    for step in steps_of(course):
        services.complete_step(learner, step)
    client.force_login(learner)

    client.post(reverse("training:course_reset", args=[course.pk]))
    progress = TrainingProgress.objects.get(user=learner, course=course)
    assert progress.completed_steps == []
    assert progress.status == TrainingProgress.Status.NOT_STARTED


# ---------------------------------------------------------------------------
# My progress
# ---------------------------------------------------------------------------
def test_progress_screen_groups_courses(client, learner):
    done = build_course(code="done", lessons=1, steps_per_lesson=1, sort_order=1)
    build_course(code="todo", lessons=1, steps_per_lesson=1, sort_order=2)
    services.complete_step(learner, steps_of(done)[0])

    client.force_login(learner)
    response = client.get(reverse("training:progress"))

    assert response.status_code == 200
    assert [s.course.code for s in response.context["completed"]] == ["done"]
    assert [s.course.code for s in response.context["not_started"]] == ["todo"]
