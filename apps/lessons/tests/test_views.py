"""HTML views: access control, filtering and the HTMX roster actions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.enums import LessonStatus

from ..models import Lesson, LessonAttendance
from ..services import add_student_to_lesson


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_requires_authentication(client):
    response = client.get(reverse("lessons:list"))
    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_list_is_denied_without_the_capability(client, unauthorised_user, lesson):
    client.force_login(unauthorised_user)
    assert client.get(reverse("lessons:list")).status_code == 403


@pytest.mark.django_db
def test_create_is_denied_without_lessons_add(client, instructor_user):
    client.force_login(instructor_user)
    assert client.get(reverse("lessons:create")).status_code == 403


@pytest.mark.django_db
def test_lesson_type_create_requires_manage(client, instructor_user):
    client.force_login(instructor_user)
    assert client.get(reverse("lessons:type_create")).status_code == 403


# ---------------------------------------------------------------------------
# Read screens
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_renders(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:list"))
    assert response.status_code == 200
    assert lesson.lesson_code.encode() in response.content


@pytest.mark.django_db
def test_list_filters_by_status(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:list"), {"status": LessonStatus.CANCELLED})
    assert response.status_code == 200
    assert lesson.lesson_code.encode() not in response.content


@pytest.mark.django_db
def test_list_htmx_returns_only_the_table(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:list"), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content.lower()
    assert b"lesson-table" in response.content


@pytest.mark.django_db
def test_detail_renders_with_the_roster(client, manager_user, lesson, student):
    add_student_to_lesson(lesson, student)
    client.force_login(manager_user)
    response = client.get(reverse("lessons:detail", args=[lesson.pk]))
    assert response.status_code == 200
    assert b"roster-panel" in response.content


@pytest.mark.django_db
def test_day_view_renders(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:day"), {"day": lesson.date.isoformat()})
    assert response.status_code == 200
    assert response.context["summary"]["lesson_count"] == 1
    assert len(response.context["week_days"]) == 7


@pytest.mark.django_db
def test_lesson_type_list_renders(client, manager_user, lesson_type):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:type_list"))
    assert response.status_code == 200
    assert lesson_type.code.encode() in response.content


# ---------------------------------------------------------------------------
# Create & update
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_form_renders(client, manager_user):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:create"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_create_rejects_a_double_booking(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:create"),
        {
            "lesson_type": lesson.lesson_type_id,
            "spot": lesson.spot_id,
            "date": lesson.date.isoformat(),
            "start_time": "10:30",
            "end_time": "12:30",
            "instructor": lesson.instructor_id,
            "capacity": 4,
            "status": LessonStatus.SCHEDULED,
            "notes": "",
            "internal_notes": "",
        },
    )
    assert response.status_code == 200
    assert Lesson.objects.count() == 1
    assert response.context["form"].non_field_errors()


@pytest.mark.django_db
def test_create_schedules_a_lesson(client, manager_user, lesson_type, spot, instructor):
    client.force_login(manager_user)
    target = timezone.localdate() + timedelta(days=4)
    response = client.post(
        reverse("lessons:create"),
        {
            "lesson_type": lesson_type.pk,
            "spot": spot.pk,
            "date": target.isoformat(),
            "start_time": "09:00",
            "end_time": "11:00",
            "instructor": instructor.pk,
            "capacity": 6,
            "status": LessonStatus.SCHEDULED,
            "notes": "",
            "internal_notes": "",
        },
    )
    assert response.status_code == 302
    created = Lesson.objects.get(date=target)
    assert created.capacity == 6
    assert created.lesson_code


@pytest.mark.django_db
def test_update_is_refused_for_a_completed_lesson(client, manager_user, past_lesson):
    past_lesson.status = LessonStatus.COMPLETED
    past_lesson.save()
    client.force_login(manager_user)
    response = client.get(reverse("lessons:update", args=[past_lesson.pk]))
    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Conflict endpoint
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_conflict_endpoint_reports_a_clash(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:check_conflicts"),
        {
            "lesson_type": lesson.lesson_type_id,
            "spot": lesson.spot_id,
            "date": lesson.date.isoformat(),
            "start_time": "10:30",
            "end_time": "12:30",
            "instructor": lesson.instructor_id,
            "capacity": 4,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert lesson.lesson_code.encode() in response.content


@pytest.mark.django_db
def test_conflict_endpoint_reports_a_clean_slot(client, manager_user, lesson_type, spot, instructor):
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:check_conflicts"),
        {
            "lesson_type": lesson_type.pk,
            "spot": spot.pk,
            "date": (timezone.localdate() + timedelta(days=9)).isoformat(),
            "start_time": "10:00",
            "end_time": "12:00",
            "instructor": instructor.pk,
            "capacity": 4,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response.context["conflicts"] == []
    assert response.context["suggested_capacity"]


# ---------------------------------------------------------------------------
# Roster actions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_add_student_via_htmx_returns_the_roster(client, manager_user, lesson, student):
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:attendance_add", args=[lesson.pk]),
        {"student": student.pk},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert b"roster-panel" in response.content
    assert LessonAttendance.objects.filter(lesson=lesson, student=student).exists()


@pytest.mark.django_db
def test_refused_roster_action_still_renders_the_panel(client, manager_user, lesson, student):
    attendance = add_student_to_lesson(lesson, student)
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:attendance_check_in", args=[lesson.pk, attendance.pk]),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response.context["roster_error"]
    attendance.refresh_from_db()
    assert attendance.status == LessonAttendance.Status.REGISTERED


@pytest.mark.django_db
def test_surf_instructor_may_run_the_roster(client, instructor_user, lesson, student):
    """A surf instructor holds lessons.change, so the roster is theirs to run."""
    client.force_login(instructor_user)
    response = client.post(
        reverse("lessons:attendance_add", args=[lesson.pk]),
        {"student": student.pk},
    )
    assert response.status_code == 302
    assert LessonAttendance.objects.filter(lesson=lesson, student=student).exists()


@pytest.mark.django_db
def test_roster_action_forbidden_for_outsiders(client, unauthorised_user, lesson, student):
    client.force_login(unauthorised_user)
    response = client.post(
        reverse("lessons:attendance_add", args=[lesson.pk]), {"student": student.pk}
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Lifecycle screens
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cancel_confirmation_renders(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.get(reverse("lessons:cancel", args=[lesson.pk]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_cancel_requires_a_reason(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.post(reverse("lessons:cancel", args=[lesson.pk]), {"reason": ""})
    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.SCHEDULED


@pytest.mark.django_db
def test_cancel_posts_through(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.post(
        reverse("lessons:cancel", args=[lesson.pk]), {"reason": "Double red flag"}
    )
    assert response.status_code == 302
    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.CANCELLED


@pytest.mark.django_db
def test_safety_check_records_the_person(client, manager_user, lesson):
    client.force_login(manager_user)
    response = client.post(reverse("lessons:safety_check", args=[lesson.pk]))
    assert response.status_code == 302
    lesson.refresh_from_db()
    assert lesson.safety_briefing_done is True
    assert lesson.safety_checked_by == manager_user


@pytest.mark.django_db
def test_complete_from_the_roster(client, manager_user, past_lesson, student):
    add_student_to_lesson(past_lesson, student)
    client.force_login(manager_user)
    response = client.post(reverse("lessons:complete", args=[past_lesson.pk]))
    assert response.status_code == 302
    past_lesson.refresh_from_db()
    assert past_lesson.status == LessonStatus.COMPLETED
