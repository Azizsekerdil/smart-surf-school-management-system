"""REST API contract: routes, capability gates and the availability action."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import Role
from apps.instructors.api import ROUTES
from apps.instructors.models import AvailabilitySlot, Certification

from .factories import (
    AvailabilitySlotFactory,
    CertificationFactory,
    InstructorFactory,
    TimeOffFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def manager_api(api):
    user = UserFactory(username="api-manager", role=Role.MANAGER)
    api.force_authenticate(user=user)
    return api


def next_weekday(weekday: int = 0) -> dt.date:
    today = timezone.localdate()
    return today + dt.timedelta(days=(weekday - today.weekday()) % 7)


class TestRoutes:
    def test_routes_are_declared(self):
        prefixes = {prefix for prefix, _viewset, _basename in ROUTES}
        assert "instructors" in prefixes
        assert len(ROUTES) == 5

    def test_basenames_are_unique(self):
        basenames = [basename for _prefix, _viewset, basename in ROUTES]
        assert len(basenames) == len(set(basenames))


class TestInstructorEndpoint:
    def test_anonymous_access_is_refused(self, api):
        response = api.get("/api/v1/instructors/")
        assert response.status_code in (401, 403)

    def test_role_without_capability_is_refused(self, api):
        api.force_authenticate(user=UserFactory(username="api-snapper", role=Role.PHOTOGRAPHER))
        response = api.get("/api/v1/instructors/")
        assert response.status_code == 403

    def test_list_returns_instructors(self, manager_api):
        InstructorFactory()
        response = manager_api.get("/api/v1/instructors/")
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_pay_is_hidden_without_the_commission_capability(self, api):
        InstructorFactory()
        api.force_authenticate(user=UserFactory(username="api-desk", role=Role.RECEPTION))
        response = api.get("/api/v1/instructors/")
        assert response.status_code == 200
        assert "hourly_rate" not in response.data["results"][0]

    def test_pay_is_visible_for_a_manager(self, manager_api):
        InstructorFactory()
        response = manager_api.get("/api/v1/instructors/")
        assert "hourly_rate" in response.data["results"][0]

    def test_available_action_requires_parameters(self, manager_api):
        response = manager_api.get("/api/v1/instructors/available/")
        assert response.status_code == 400
        assert response.data["error"]["type"] == "validation_error"

    def test_available_action_returns_free_instructors(self, manager_api):
        instructor = InstructorFactory()
        monday = next_weekday(0)
        AvailabilitySlotFactory(
            instructor=instructor,
            weekday=AvailabilitySlot.Weekday.MONDAY,
            start_time=dt.time(9, 0),
            end_time=dt.time(17, 0),
        )
        response = manager_api.get(
            "/api/v1/instructors/available/",
            {"date": monday.isoformat(), "start": "10:00", "end": "12:00"},
        )
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_performance_action_is_zero_safe(self, manager_api):
        instructor = InstructorFactory()
        response = manager_api.get(f"/api/v1/instructors/{instructor.pk}/performance/")
        assert response.status_code == 200
        assert response.data["lessons_taught"] == 0

    def test_create_refuses_a_group_above_the_safety_ratio(self, manager_api):
        user = UserFactory(username="api-coach", role=Role.SURF_INSTRUCTOR)
        response = manager_api.post(
            "/api/v1/instructors/",
            {
                "user": user.pk,
                "max_level_taught": "beginner",
                "max_students_per_lesson": 10,
                "hourly_rate": "300.00",
                "commission_percent": "5.00",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_refresh_statistics_action(self, manager_api):
        instructor = InstructorFactory()
        response = manager_api.post(
            f"/api/v1/instructors/{instructor.pk}/refresh_statistics/"
        )
        assert response.status_code == 200
        assert response.data["total_lessons_taught"] == 0


class TestCertificationEndpoint:
    def test_verify_requires_the_approve_capability(self, api):
        certification = CertificationFactory(is_verified=False)
        api.force_authenticate(user=UserFactory(username="api-desk2", role=Role.RECEPTION))
        response = api.post(f"/api/v1/instructor-certifications/{certification.pk}/verify/")
        assert response.status_code == 403

    def test_verify_marks_the_record(self, manager_api):
        certification = CertificationFactory(is_verified=False)
        response = manager_api.post(
            f"/api/v1/instructor-certifications/{certification.pk}/verify/"
        )
        assert response.status_code == 200
        certification.refresh_from_db()
        assert certification.is_verified is True

    def test_expired_certificate_cannot_be_verified(self, manager_api):
        certification = CertificationFactory(
            is_verified=False, expires_on=timezone.localdate() - dt.timedelta(days=2)
        )
        response = manager_api.post(
            f"/api/v1/instructor-certifications/{certification.pk}/verify/"
        )
        assert response.status_code == 400

    def test_expiring_report(self, manager_api):
        instructor = InstructorFactory()
        CertificationFactory(
            instructor=instructor, expires_on=timezone.localdate() + dt.timedelta(days=20)
        )
        response = manager_api.get("/api/v1/instructor-certifications/expiring/")
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_expiry_before_issue_is_refused(self, manager_api):
        instructor = InstructorFactory()
        today = timezone.localdate()
        response = manager_api.post(
            "/api/v1/instructor-certifications/",
            {
                "instructor": instructor.pk,
                "kind": Certification.Kind.FIRST_AID,
                "name": "EFAW",
                "issued_on": today.isoformat(),
                "expires_on": (today - dt.timedelta(days=1)).isoformat(),
            },
            format="json",
        )
        assert response.status_code == 400


class TestAvailabilityAndTimeOffEndpoints:
    def test_overlapping_slot_is_refused(self, manager_api):
        instructor = InstructorFactory()
        AvailabilitySlotFactory(
            instructor=instructor, start_time=dt.time(9, 0), end_time=dt.time(12, 0)
        )
        response = manager_api.post(
            "/api/v1/instructor-availability/",
            {
                "instructor": instructor.pk,
                "weekday": 0,
                "start_time": "11:00",
                "end_time": "14:00",
                "is_active": True,
            },
            format="json",
        )
        assert response.status_code == 400

    def test_time_off_approval_reports_affected_lessons(self, manager_api):
        time_off = TimeOffFactory()
        response = manager_api.post(f"/api/v1/instructor-time-off/{time_off.pk}/approve/")
        assert response.status_code == 200
        assert response.data["is_approved"] is True
        assert response.data["lessons_to_reassign"] == 0

    def test_overlapping_time_off_is_refused(self, manager_api):
        instructor = InstructorFactory()
        today = timezone.localdate()
        TimeOffFactory(
            instructor=instructor,
            start_date=today,
            end_date=today + dt.timedelta(days=5),
        )
        response = manager_api.post(
            "/api/v1/instructor-time-off/",
            {
                "instructor": instructor.pk,
                "start_date": (today + dt.timedelta(days=2)).isoformat(),
                "end_date": (today + dt.timedelta(days=6)).isoformat(),
                "reason": "sick",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_review_sets_the_reviewer_from_the_request(self, manager_api):
        instructor = InstructorFactory()
        today = timezone.localdate()
        response = manager_api.post(
            "/api/v1/instructor-reviews/",
            {
                "instructor": instructor.pk,
                "period_start": (today - dt.timedelta(days=30)).isoformat(),
                "period_end": today.isoformat(),
                "teaching_quality": 5,
                "punctuality": 5,
                "safety": 5,
                "communication": 5,
                "teamwork": 5,
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["overall_score"] == "5.00"
