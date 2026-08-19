from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.constants import Role
from apps.training import services
from apps.training.models import TrainingProgress

from .factories import build_course

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="learner", email="learner@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )


@pytest.fixture
def super_admin(db):
    return User.objects.create_user(
        username="root", email="root@example.com", password="pw-test-12345",
        role=Role.SUPER_ADMIN,
    )


def steps_of(course):
    return list(services.course_step_sequence(course))


def test_course_list_requires_authentication(client):
    assert client.get(reverse("trainingcourse-list")).status_code in {401, 403}


def test_course_list_hides_courses_needing_a_missing_capability(client, learner):
    build_course(code="open")
    build_course(code="restricted", required_capability="backups.restore")
    client.force_login(learner)

    response = client.get(reverse("trainingcourse-list"))
    assert response.status_code == 200
    assert [row["code"] for row in response.json()["results"]] == ["open"]


def test_start_action_creates_progress(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    client.force_login(learner)

    response = client.post(reverse("trainingcourse-start", args=[course.pk]))
    assert response.status_code == 200
    assert response.json()["status"] == TrainingProgress.Status.IN_PROGRESS


def test_complete_action_ticks_a_step(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    step = steps_of(course)[0]
    client.force_login(learner)

    response = client.post(reverse("trainingstep-complete", args=[step.pk]))
    assert response.status_code == 200
    assert response.json()["percent_complete"] == 50


def test_progress_endpoint_summarises(client, learner):
    course = build_course(lessons=1, steps_per_lesson=2)
    services.complete_step(learner, steps_of(course)[0])
    client.force_login(learner)

    response = client.get(reverse("trainingcourse-progress"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["steps_total"] == 2
    assert payload["steps_completed"] == 1
    assert payload["percent"] == 50


def test_progress_rows_are_scoped_to_the_caller(client, learner):
    other = User.objects.create_user(
        username="other", email="other@example.com", password="pw-test-12345",
        role=Role.RECEPTION,
    )
    course = build_course(lessons=1, steps_per_lesson=1)
    services.complete_step(other, steps_of(course)[0])
    client.force_login(learner)

    response = client.get(reverse("trainingprogress-list"))
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_step_body_html_is_sanitised(client, learner):
    course = build_course(lessons=1, steps_per_lesson=1)
    step = steps_of(course)[0]
    step.body_en = "## Heading\n\n<script>alert(1)</script>"
    step.body_tr = "## Başlık\n\n<script>alert(1)</script>"
    step.save()
    client.force_login(learner)

    response = client.get(reverse("trainingstep-detail", args=[step.pk]))
    assert "<script" not in response.json()["body_html"]


def test_a_learner_cannot_create_a_course(client, learner):
    client.force_login(learner)
    response = client.post(
        reverse("trainingcourse-list"),
        {"code": "new-course", "title_en": "New", "title_tr": "Yeni"},
        content_type="application/json",
    )
    assert response.status_code == 403


def test_a_super_admin_can_create_a_course(client, super_admin):
    client.force_login(super_admin)
    response = client.post(
        reverse("trainingcourse-list"),
        {"code": "new-course", "title_en": "New", "title_tr": "Yeni"},
        content_type="application/json",
    )
    assert response.status_code == 201
