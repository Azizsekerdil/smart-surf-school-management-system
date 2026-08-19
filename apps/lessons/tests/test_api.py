"""REST API: capability enforcement, scheduling rules and the custom actions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.enums import LessonStatus

from ..models import Lesson, LessonAttendance
from ..services import add_student_to_lesson


@pytest.fixture
def api(manager_user):
    client = APIClient()
    client.force_authenticate(manager_user)
    return client


@pytest.mark.django_db
def test_anonymous_access_is_rejected():
    response = APIClient().get(reverse("lesson-list"))
    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_capability_is_enforced(unauthorised_user):
    client = APIClient()
    client.force_authenticate(unauthorised_user)
    assert client.get(reverse("lesson-list")).status_code == 403


@pytest.mark.django_db
def test_list_lessons(api, lesson):
    response = api.get(reverse("lesson-list"))
    assert response.status_code == 200
    payload = response.json()
    results = payload["results"] if isinstance(payload, dict) else payload
    assert results[0]["lesson_code"] == lesson.lesson_code


@pytest.mark.django_db
def test_detail_exposes_derived_values(api, lesson, student):
    add_student_to_lesson(lesson, student)
    response = api.get(reverse("lesson-detail", args=[lesson.pk]))
    assert response.status_code == 200
    data = response.json()
    assert data["booked_count"] == 1
    assert data["available_seats"] == lesson.capacity - 1
    assert data["required_ratio_ok"] is True


@pytest.mark.django_db
def test_create_rejects_a_conflicting_slot(api, lesson):
    response = api.post(
        reverse("lesson-list"),
        {
            "lesson_type": lesson.lesson_type_id,
            "spot": lesson.spot_id,
            "date": lesson.date.isoformat(),
            "start_time": "10:30:00",
            "end_time": "12:30:00",
            "instructor": lesson.instructor_id,
            "capacity": 4,
            "status": LessonStatus.SCHEDULED,
        },
        format="json",
    )
    assert response.status_code == 400
    assert Lesson.objects.count() == 1


@pytest.mark.django_db
def test_create_accepts_a_free_slot(api, lesson_type, spot, instructor):
    target = timezone.localdate() + timedelta(days=6)
    response = api.post(
        reverse("lesson-list"),
        {
            "lesson_type": lesson_type.pk,
            "spot": spot.pk,
            "date": target.isoformat(),
            "start_time": "14:00:00",
            "end_time": "16:00:00",
            "instructor": instructor.pk,
            "capacity": 5,
            "status": LessonStatus.SCHEDULED,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert Lesson.objects.filter(date=target).exists()


@pytest.mark.django_db
def test_cancel_action(api, lesson, student):
    add_student_to_lesson(lesson, student)
    response = api.post(
        reverse("lesson-cancel", args=[lesson.pk]),
        {"reason": "Offshore gale"},
        format="json",
    )
    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.CANCELLED
    assert (
        LessonAttendance.objects.get(lesson=lesson, student=student).status
        == LessonAttendance.Status.CANCELLED
    )


@pytest.mark.django_db
def test_cancel_action_requires_a_reason(api, lesson):
    response = api.post(reverse("lesson-cancel", args=[lesson.pk]), {"reason": ""}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_complete_action_refuses_a_future_lesson(api, lesson):
    response = api.post(reverse("lesson-complete", args=[lesson.pk]), {}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_add_student_action(api, lesson, student):
    response = api.post(
        reverse("lesson-add-student", args=[lesson.pk]),
        {"student": student.pk},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert LessonAttendance.objects.filter(lesson=lesson, student=student).exists()


@pytest.mark.django_db
def test_add_student_action_refuses_to_overbook(api, lesson, student, another_student):
    lesson.capacity = 1
    lesson.save()
    add_student_to_lesson(lesson, student)
    response = api.post(
        reverse("lesson-add-student", args=[lesson.pk]),
        {"student": another_student.pk},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_calendar_action(api, lesson):
    response = api.get(
        reverse("lesson-calendar"),
        {"start": lesson.date.isoformat(), "end": lesson.date.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["events"][0]["code"] == lesson.lesson_code


@pytest.mark.django_db
def test_conflicts_action(api, lesson):
    response = api.get(reverse("lesson-conflicts", args=[lesson.pk]))
    assert response.status_code == 200
    assert "conflicts" in response.json()


@pytest.mark.django_db
def test_safety_check_action(api, lesson, manager_user):
    response = api.post(reverse("lesson-safety-check", args=[lesson.pk]), {}, format="json")
    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.safety_briefing_done is True
    assert lesson.safety_checked_by == manager_user


@pytest.mark.django_db
def test_lesson_type_capacity_action(api, lesson_type):
    response = api.get(
        reverse("lessontype-capacity", args=[lesson_type.pk]), {"instructors": 2, "minors": "true"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_minors"] is True
    assert data["suggested_capacity"] <= lesson_type.max_students


@pytest.mark.django_db
def test_lesson_type_create_requires_manage(instructor_user, lesson_type):
    client = APIClient()
    client.force_authenticate(instructor_user)
    response = client.post(
        reverse("lessontype-list"),
        {
            "code": "NEW1",
            "name": "New product",
            "category": "group",
            "duration_minutes": 120,
            "min_students": 1,
            "max_students": 6,
            "base_price": "500.00",
            "price_per_extra_student": "0.00",
            "colour": "#0ea5e9",
        },
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_attendance_check_in_requires_the_briefing(api, lesson, student):
    attendance = add_student_to_lesson(lesson, student)
    response = api.post(
        reverse("lessonattendance-check-in", args=[attendance.pk]), {}, format="json"
    )
    assert response.status_code == 400
