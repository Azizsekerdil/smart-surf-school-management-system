"""View behaviour: rendering, capability gates and state changes."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.instructors.models import AvailabilitySlot, Certification, Instructor, TimeOff

from .factories import (
    AvailabilitySlotFactory,
    CertificationFactory,
    InstructorFactory,
    TimeOffFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(client):
    user = UserFactory(username="manager", role=Role.MANAGER)
    client.force_login(user)
    return user


@pytest.fixture
def photographer(client):
    """A staff role with no instructors capability at all."""
    user = UserFactory(username="snapper", role=Role.PHOTOGRAPHER)
    client.force_login(user)
    return user


class TestPermissions:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(reverse("instructors:list"))
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_role_without_capability_is_denied(self, client, photographer):
        response = client.get(reverse("instructors:list"))
        assert response.status_code == 403

    def test_view_capability_does_not_grant_editing(self, client):
        user = UserFactory(username="desk", role=Role.RECEPTION)
        client.force_login(user)
        instructor = InstructorFactory()
        assert client.get(reverse("instructors:detail", args=[instructor.pk])).status_code == 200
        assert client.get(reverse("instructors:update", args=[instructor.pk])).status_code == 403


class TestListAndDetail:
    def test_list_renders(self, client, manager):
        InstructorFactory()
        response = client.get(reverse("instructors:list"))
        assert response.status_code == 200
        assert "instructors" in response.context

    def test_list_search_filters_by_name(self, client, manager):
        wanted = InstructorFactory(user=UserFactory(first_name="Elif", username="elif"))
        InstructorFactory(user=UserFactory(first_name="Baran", username="baran"))
        response = client.get(reverse("instructors:list"), {"q": "Elif"})
        assert list(response.context["instructors"]) == [wanted]

    def test_list_filters_by_certification_warning(self, client, manager):
        flagged = InstructorFactory()
        CertificationFactory(
            instructor=flagged, expires_on=timezone.localdate() + dt.timedelta(days=10)
        )
        InstructorFactory()
        response = client.get(reverse("instructors:list"), {"certs": "warning"})
        assert list(response.context["instructors"]) == [flagged]

    def test_htmx_request_renders_only_the_card_partial(self, client, manager):
        InstructorFactory()
        response = client.get(reverse("instructors:list"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response.templates[0].name == "instructors/partials/instructor_cards.html"

    def test_detail_renders_with_every_panel(self, client, manager):
        instructor = InstructorFactory()
        CertificationFactory(instructor=instructor)
        AvailabilitySlotFactory(instructor=instructor)
        response = client.get(reverse("instructors:detail", args=[instructor.pk]))
        assert response.status_code == 200
        assert len(response.context["week"]) == 7
        assert "performance" in response.context


class TestCreateAndUpdate:
    def test_create_assigns_a_code_and_audits(self, client, manager):
        user = UserFactory(username="newcoach", role=Role.SURF_INSTRUCTOR)
        response = client.post(
            reverse("instructors:create"),
            {
                "user": user.pk,
                "max_level_taught": "beginner",
                "max_students_per_lesson": 8,
                "hourly_rate": "400.00",
                "commission_percent": "12.00",
                "specialties": "kids, longboard",
                "is_active": "on",
                "is_available_for_booking": "on",
            },
        )
        assert response.status_code == 302
        instructor = Instructor.objects.get(user=user)
        assert instructor.instructor_code.startswith("INS")
        assert instructor.specialties == ["kids", "longboard"]

    def test_create_refuses_a_group_above_the_safety_ratio(self, client, manager):
        user = UserFactory(username="ratio", role=Role.SURF_INSTRUCTOR)
        response = client.post(
            reverse("instructors:create"),
            {
                "user": user.pk,
                "max_level_taught": "beginner",
                "max_students_per_lesson": 10,
                "hourly_rate": "400.00",
                "commission_percent": "0.00",
            },
        )
        assert response.status_code == 200
        assert "max_students_per_lesson" in response.context["form"].errors
        assert Instructor.objects.filter(user=user).exists() is False

    def test_update_changes_the_profile(self, client, manager):
        instructor = InstructorFactory()
        response = client.post(
            reverse("instructors:update", args=[instructor.pk]),
            {
                "user": instructor.user_id,
                "max_level_taught": "advanced",
                "max_students_per_lesson": 6,
                "hourly_rate": "500.00",
                "commission_percent": "15.00",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        instructor.refresh_from_db()
        assert instructor.max_level_taught == "advanced"
        assert instructor.hourly_rate == Decimal("500.00")
        assert instructor.is_available_for_booking is False

    def test_booking_toggle_requires_post(self, client, manager):
        instructor = InstructorFactory(is_available_for_booking=True)
        url = reverse("instructors:toggle_booking", args=[instructor.pk])
        assert client.get(url).status_code == 405
        client.post(url, {"available": "0"})
        instructor.refresh_from_db()
        assert instructor.is_available_for_booking is False

    def test_delete_soft_deletes(self, client, manager):
        instructor = InstructorFactory()
        response = client.post(reverse("instructors:delete", args=[instructor.pk]))
        assert response.status_code == 302
        assert Instructor.objects.filter(pk=instructor.pk).exists() is False
        assert Instructor.all_objects.filter(pk=instructor.pk).exists() is True


class TestCertificationViews:
    def test_create_certification(self, client, manager):
        instructor = InstructorFactory()
        today = timezone.localdate()
        response = client.post(
            reverse("instructors:certification_create", args=[instructor.pk]),
            {
                "kind": Certification.Kind.LIFEGUARD,
                "name": "Surf Lifeguard Award",
                "issuing_body": "ILS",
                "certificate_number": "SLA-1",
                "issued_on": (today - dt.timedelta(days=30)).isoformat(),
                "expires_on": (today + dt.timedelta(days=700)).isoformat(),
            },
        )
        assert response.status_code == 302
        certification = instructor.certifications.get()
        assert certification.is_verified is False

    def test_verification_requires_the_approve_capability(self, client):
        user = UserFactory(username="desk2", role=Role.RECEPTION)
        client.force_login(user)
        certification = CertificationFactory(is_verified=False)
        response = client.post(
            reverse("instructors:certification_verify", args=[certification.pk])
        )
        assert response.status_code == 403

    def test_verification_marks_the_record(self, client, manager):
        certification = CertificationFactory(is_verified=False)
        response = client.post(
            reverse("instructors:certification_verify", args=[certification.pk])
        )
        assert response.status_code == 302
        certification.refresh_from_db()
        assert certification.is_verified is True
        assert certification.verified_by == manager

    def test_expired_certification_cannot_be_verified(self, client, manager):
        certification = CertificationFactory(
            is_verified=False, expires_on=timezone.localdate() - dt.timedelta(days=1)
        )
        client.post(reverse("instructors:certification_verify", args=[certification.pk]))
        certification.refresh_from_db()
        assert certification.is_verified is False

    def test_editing_a_verified_certificate_resets_verification(self, client, manager):
        certification = CertificationFactory(is_verified=True)
        client.post(
            reverse("instructors:certification_update", args=[certification.pk]),
            {
                "kind": certification.kind,
                "name": "ISA Surf Level 1 Instructor (renewed)",
                "issuing_body": certification.issuing_body,
                "certificate_number": certification.certificate_number,
                "issued_on": certification.issued_on.isoformat(),
                "expires_on": certification.expires_on.isoformat(),
            },
        )
        certification.refresh_from_db()
        assert certification.is_verified is False


class TestAvailabilityViews:
    def test_editor_renders(self, client, manager):
        instructor = InstructorFactory()
        response = client.get(reverse("instructors:availability_editor", args=[instructor.pk]))
        assert response.status_code == 200
        assert len(response.context["week"]) == 7

    def test_htmx_slot_creation_returns_the_grid(self, client, manager):
        instructor = InstructorFactory()
        response = client.post(
            reverse("instructors:availability_slot_create", args=[instructor.pk]),
            {
                "weekday": 0,
                "start_time": "09:00",
                "end_time": "12:00",
                "is_active": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response.templates[0].name == "instructors/partials/availability_grid.html"
        assert instructor.availability_slots.count() == 1

    def test_overlapping_slot_is_reported_in_the_form(self, client, manager):
        instructor = InstructorFactory()
        AvailabilitySlotFactory(
            instructor=instructor, start_time=dt.time(9, 0), end_time=dt.time(12, 0)
        )
        response = client.post(
            reverse("instructors:availability_slot_create", args=[instructor.pk]),
            {"weekday": 0, "start_time": "10:00", "end_time": "14:00", "is_active": "on"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert instructor.availability_slots.count() == 1
        assert response.context["form"].errors

    def test_slot_toggle_and_delete(self, client, manager):
        slot = AvailabilitySlotFactory()
        client.post(reverse("instructors:availability_slot_toggle", args=[slot.pk]))
        slot.refresh_from_db()
        assert slot.is_active is False
        client.post(reverse("instructors:availability_slot_delete", args=[slot.pk]))
        assert AvailabilitySlot.objects.filter(pk=slot.pk).exists() is False

    def test_availability_board_search(self, client, manager):
        instructor = InstructorFactory()
        AvailabilitySlotFactory(
            instructor=instructor,
            weekday=timezone.localdate().weekday(),
            start_time=dt.time(8, 0),
            end_time=dt.time(18, 0),
        )
        response = client.get(
            reverse("instructors:availability_board"),
            {
                "date": timezone.localdate().isoformat(),
                "start_time": "10:00",
                "end_time": "12:00",
            },
        )
        assert response.status_code == 200
        assert response.context["searched"] is True
        assert [row["instructor"] for row in response.context["results"]] == [instructor]


class TestTimeOffViews:
    def test_request_and_approve(self, client, manager):
        instructor = InstructorFactory()
        today = timezone.localdate()
        response = client.post(
            reverse("instructors:timeoff_create", args=[instructor.pk]),
            {
                "start_date": (today + dt.timedelta(days=5)).isoformat(),
                "end_date": (today + dt.timedelta(days=8)).isoformat(),
                "reason": TimeOff.Reason.HOLIDAY,
                "note": "Family visit",
            },
        )
        assert response.status_code == 302
        time_off = instructor.time_off_periods.get()
        assert time_off.is_approved is False

        client.post(reverse("instructors:timeoff_approve", args=[time_off.pk]))
        time_off.refresh_from_db()
        assert time_off.is_approved is True
        assert time_off.approved_by == manager

    def test_overlapping_request_is_refused(self, client, manager):
        instructor = InstructorFactory()
        today = timezone.localdate()
        TimeOffFactory(
            instructor=instructor,
            start_date=today + dt.timedelta(days=5),
            end_date=today + dt.timedelta(days=10),
        )
        response = client.post(
            reverse("instructors:timeoff_create", args=[instructor.pk]),
            {
                "start_date": (today + dt.timedelta(days=7)).isoformat(),
                "end_date": (today + dt.timedelta(days=12)).isoformat(),
                "reason": TimeOff.Reason.SICK,
            },
        )
        assert response.status_code == 200
        assert instructor.time_off_periods.count() == 1

    def test_list_renders_and_filters(self, client, manager):
        TimeOffFactory(is_approved=False)
        TimeOffFactory(is_approved=True)
        response = client.get(reverse("instructors:timeoff_list"))
        assert response.status_code == 200
        assert len(response.context["time_off_periods"]) == 1

    def test_past_absence_cannot_be_withdrawn(self, client, manager):
        today = timezone.localdate()
        time_off = TimeOffFactory(
            start_date=today - dt.timedelta(days=10),
            end_date=today - dt.timedelta(days=5),
            is_approved=True,
        )
        client.post(reverse("instructors:timeoff_cancel", args=[time_off.pk]))
        assert TimeOff.objects.filter(pk=time_off.pk).exists() is True

    def test_open_redirect_in_next_is_ignored(self, client, manager):
        time_off = TimeOffFactory()
        response = client.post(
            reverse("instructors:timeoff_approve", args=[time_off.pk]),
            {"next": "https://evil.example.com/"},
        )
        assert response["Location"] == reverse("instructors:timeoff_list")


class TestReviewAndExport:
    def test_review_records_the_reviewer(self, client, manager):
        instructor = InstructorFactory()
        today = timezone.localdate()
        response = client.post(
            reverse("instructors:review_create", args=[instructor.pk]),
            {
                "period_start": (today - dt.timedelta(days=90)).isoformat(),
                "period_end": (today - dt.timedelta(days=1)).isoformat(),
                "teaching_quality": 5,
                "punctuality": 4,
                "safety": 5,
                "communication": 4,
                "teamwork": 4,
                "strengths": "Calm in the water.",
            },
        )
        assert response.status_code == 302
        review = instructor.performance_reviews.get()
        assert review.reviewer == manager
        assert review.overall_score == Decimal("4.40")

    def test_export_returns_csv_with_pay_for_a_manager(self, client, manager):
        InstructorFactory()
        response = client.get(reverse("instructors:export"))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment;" in response["Content-Disposition"]

    def test_export_is_denied_without_the_capability(self, client):
        user = UserFactory(username="desk3", role=Role.RECEPTION)
        client.force_login(user)
        assert client.get(reverse("instructors:export")).status_code == 403
