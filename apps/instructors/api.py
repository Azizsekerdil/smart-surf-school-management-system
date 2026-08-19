"""REST API for instructors, certifications, availability and absence.

The ``available`` action is the endpoint the booking screens call before they
offer an instructor, so it returns the same verdict as the HTML flow — one
implementation in :mod:`services`, two front ends.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import services
from .models import (
    AvailabilitySlot,
    Certification,
    Instructor,
    PerformanceReview,
    TimeOff,
)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class CertificationSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status = serializers.CharField(read_only=True)
    status_label = serializers.CharField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    days_until_expiry = serializers.IntegerField(read_only=True)

    class Meta:
        model = Certification
        fields = [
            "id",
            "public_id",
            "instructor",
            "kind",
            "kind_label",
            "name",
            "issuing_body",
            "certificate_number",
            "issued_on",
            "expires_on",
            "document",
            "is_verified",
            "verified_by",
            "verified_at",
            "status",
            "status_label",
            "is_expired",
            "days_until_expiry",
        ]
        read_only_fields = ["id", "public_id", "is_verified", "verified_by", "verified_at"]

    def validate(self, attrs):
        issued_on = attrs.get("issued_on") or getattr(self.instance, "issued_on", None)
        expires_on = attrs.get("expires_on") or getattr(self.instance, "expires_on", None)
        if issued_on and issued_on > timezone.localdate():
            raise serializers.ValidationError(
                {"issued_on": _("The issue date cannot be in the future.")}
            )
        if issued_on and expires_on and expires_on <= issued_on:
            raise serializers.ValidationError(
                {"expires_on": _("The expiry date must be after the issue date.")}
            )
        return attrs


class AvailabilitySlotSerializer(serializers.ModelSerializer):
    weekday_label = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = AvailabilitySlot
        fields = [
            "id",
            "instructor",
            "weekday",
            "weekday_label",
            "start_time",
            "end_time",
            "is_active",
            "valid_from",
            "valid_until",
        ]

    def validate(self, attrs):
        instance = self.instance
        start = attrs.get("start_time") or getattr(instance, "start_time", None)
        end = attrs.get("end_time") or getattr(instance, "end_time", None)
        if start and end and end <= start:
            raise serializers.ValidationError(
                {"end_time": _("The end time must be after the start time.")}
            )
        candidate = AvailabilitySlot(
            pk=getattr(instance, "pk", None),
            instructor=attrs.get("instructor") or getattr(instance, "instructor", None),
            weekday=attrs.get("weekday", getattr(instance, "weekday", None)),
            start_time=start,
            end_time=end,
            valid_from=attrs.get("valid_from", getattr(instance, "valid_from", None)),
            valid_until=attrs.get("valid_until", getattr(instance, "valid_until", None)),
        )
        if candidate.instructor_id:
            services.validate_availability_slot(candidate)
        return attrs


class TimeOffSerializer(serializers.ModelSerializer):
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    total_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = TimeOff
        fields = [
            "id",
            "public_id",
            "instructor",
            "start_date",
            "end_date",
            "reason",
            "reason_label",
            "note",
            "is_approved",
            "approved_by",
            "approved_at",
            "total_days",
        ]
        read_only_fields = ["id", "public_id", "is_approved", "approved_by", "approved_at"]

    def validate(self, attrs):
        start = attrs.get("start_date") or getattr(self.instance, "start_date", None)
        end = attrs.get("end_date") or getattr(self.instance, "end_date", None)
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": _("The end date must not be before the start date.")}
            )
        instructor = attrs.get("instructor") or getattr(self.instance, "instructor", None)
        if instructor and start and end:
            clash = services.overlapping_time_off(
                instructor, start, end, exclude_pk=getattr(self.instance, "pk", None)
            ).first()
            if clash is not None:
                raise serializers.ValidationError(
                    {"start_date": _("This overlaps an absence already recorded.")}
                )
        return attrs


class PerformanceReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source="reviewer.get_display_name", read_only=True)

    class Meta:
        model = PerformanceReview
        fields = [
            "id",
            "public_id",
            "instructor",
            "period_start",
            "period_end",
            "reviewer",
            "reviewer_name",
            "teaching_quality",
            "punctuality",
            "safety",
            "communication",
            "teamwork",
            "strengths",
            "improvements",
            "goals",
            "overall_score",
        ]
        read_only_fields = ["id", "public_id", "overall_score", "reviewer"]

    def validate(self, attrs):
        start = attrs.get("period_start") or getattr(self.instance, "period_start", None)
        end = attrs.get("period_end") or getattr(self.instance, "period_end", None)
        if start and end and end < start:
            raise serializers.ValidationError(
                {"period_end": _("The end of the period must not be before its start.")}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            validated_data["reviewer"] = request.user
            validated_data.setdefault("created_by", request.user)
        return super().create(validated_data)


class InstructorSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    level_label = serializers.CharField(source="get_max_level_taught_display", read_only=True)
    has_valid_certifications = serializers.BooleanField(read_only=True)
    certifications = CertificationSerializer(many=True, read_only=True)
    expiring_certification_count = serializers.SerializerMethodField()

    class Meta:
        model = Instructor
        fields = [
            "id",
            "public_id",
            "instructor_code",
            "user",
            "full_name",
            "bio",
            "photo",
            "specialties",
            "languages",
            "max_level_taught",
            "level_label",
            "max_students_per_lesson",
            "hourly_rate",
            "commission_percent",
            "hire_date",
            "is_active",
            "is_available_for_booking",
            "rating_average",
            "rating_count",
            "total_lessons_taught",
            "emergency_contact_name",
            "emergency_contact_phone",
            "has_valid_certifications",
            "expiring_certification_count",
            "certifications",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "instructor_code",
            "rating_average",
            "rating_count",
            "total_lessons_taught",
        ]

    def get_expiring_certification_count(self, obj) -> int:
        return obj.expiring_certifications.count()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        # Pay data is a separate capability: not every role that may see the
        # roster may see what each coach earns.
        if not (user and user.is_authenticated and user.has_capability("instructors.view_commission")):
            data.pop("hourly_rate", None)
            data.pop("commission_percent", None)
        return data


class InstructorWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Instructor
        fields = [
            "user",
            "bio",
            "photo",
            "specialties",
            "languages",
            "max_level_taught",
            "max_students_per_lesson",
            "hourly_rate",
            "commission_percent",
            "hire_date",
            "is_active",
            "is_available_for_booking",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]

    def validate(self, attrs):
        level = attrs.get("max_level_taught") or getattr(
            self.instance, "max_level_taught", None
        )
        maximum = attrs.get("max_students_per_lesson") or getattr(
            self.instance, "max_students_per_lesson", None
        )
        if level and maximum:
            ceiling = services.ratio_ceiling(level)
            if maximum > ceiling:
                raise serializers.ValidationError(
                    {
                        "max_students_per_lesson": _(
                            "The safety ratio allows at most %(max)s students per instructor "
                            "at this level."
                        )
                        % {"max": ceiling}
                    }
                )
        return attrs


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
def _parse_date(value, default=None):
    try:
        return dt.date.fromisoformat(value) if value else default
    except (TypeError, ValueError):
        return default


def _parse_time(value, default=None):
    try:
        return dt.time.fromisoformat(value) if value else default
    except (TypeError, ValueError):
        return default


class InstructorViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD plus the availability and performance questions booking screens ask."""

    capability_prefix = "instructors"
    capability_overrides = {
        "available": "instructors.view",
        "performance": "instructors.view",
        "refresh_statistics": "instructors.change",
    }
    queryset = (
        Instructor.objects.select_related("user")
        .prefetch_related("certifications", "availability_slots")
        .all()
    )
    filterset_fields = ["is_active", "is_available_for_booking", "max_level_taught"]
    search_fields = [
        "instructor_code",
        "user__first_name",
        "user__last_name",
        "user__email",
    ]
    ordering_fields = ["instructor_code", "rating_average", "total_lessons_taught", "hire_date"]
    ordering = ["user__first_name", "user__last_name"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return InstructorWriteSerializer
        return InstructorSerializer

    @action(detail=False, methods=["get"])
    def available(self, request):
        """``?date=&start=&end=&level=`` — instructors free for that window."""
        on_date = _parse_date(request.query_params.get("date"))
        start_time = _parse_time(request.query_params.get("start"))
        end_time = _parse_time(request.query_params.get("end"))
        if not (on_date and start_time and end_time):
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": _("date, start and end are required."),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        level = request.query_params.get("level") or None
        queryset = services.available_instructors(on_date, start_time, end_time, level=level)
        serializer = InstructorSerializer(queryset, many=True, context={"request": request})
        return Response({"count": queryset.count(), "results": serializer.data})

    @action(detail=True, methods=["get"])
    def performance(self, request, pk=None):
        """``?start=&end=`` — delivery and earnings for one instructor."""
        instructor = self.get_object()
        today = timezone.localdate()
        start = _parse_date(request.query_params.get("start"), today - dt.timedelta(days=89))
        end = _parse_date(request.query_params.get("end"), today)
        summary = services.instructor_performance(instructor, start, end)
        if not request.user.has_capability("instructors.view_commission"):
            summary.pop("commission_earned", None)
            summary.pop("commission_percent", None)
            summary.pop("revenue_generated", None)
        return Response(summary)

    @action(detail=True, methods=["post"])
    def refresh_statistics(self, request, pk=None):
        """Rebuild the denormalised rating and lesson counters."""
        instructor = self.get_object()
        return Response(services.refresh_instructor_statistics(instructor))


class CertificationViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "instructors"
    capability_overrides = {"verify": "instructors.approve", "expiring": "instructors.view"}
    queryset = Certification.objects.select_related("instructor", "instructor__user")
    serializer_class = CertificationSerializer
    filterset_fields = ["instructor", "kind", "is_verified"]
    search_fields = ["name", "issuing_body", "certificate_number"]
    ordering_fields = ["expires_on", "issued_on", "kind"]

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        certification = self.get_object()
        if certification.is_expired:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": _("An expired certification cannot be verified."),
                        "detail": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        services.verify_certification(certification, request.user, request=request)
        return Response(self.get_serializer(certification).data)

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Instructors whose paperwork needs attention within ``?days=``."""
        try:
            days = int(request.query_params.get("days", 60))
        except (TypeError, ValueError):
            days = 60
        payload = [
            {
                "instructor_id": entry["instructor"].pk,
                "instructor": entry["instructor"].full_name,
                "instructor_code": entry["instructor"].instructor_code,
                "soonest_expiry": entry["soonest_expiry"],
                "days_until_expiry": entry["days_until_expiry"],
                "has_expired": entry["has_expired"],
                "certifications": CertificationSerializer(
                    entry["certifications"], many=True, context={"request": request}
                ).data,
            }
            for entry in services.check_certification_expiry(days)
        ]
        return Response({"count": len(payload), "results": payload})


class AvailabilitySlotViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "instructors"
    queryset = AvailabilitySlot.objects.select_related("instructor", "instructor__user")
    serializer_class = AvailabilitySlotSerializer
    filterset_fields = ["instructor", "weekday", "is_active"]
    ordering_fields = ["weekday", "start_time"]
    ordering = ["weekday", "start_time"]


class TimeOffViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "instructors"
    capability_overrides = {"approve": "instructors.approve"}
    queryset = TimeOff.objects.select_related("instructor", "instructor__user", "approved_by")
    serializer_class = TimeOffSerializer
    filterset_fields = ["instructor", "reason", "is_approved"]
    ordering_fields = ["start_date", "end_date"]
    ordering = ["-start_date"]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        time_off = self.get_object()
        _period, affected = services.approve_time_off(time_off, request.user, request=request)
        data = self.get_serializer(time_off).data
        data["lessons_to_reassign"] = affected
        return Response(data)


class PerformanceReviewViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "instructors"
    queryset = PerformanceReview.objects.select_related(
        "instructor", "instructor__user", "reviewer"
    )
    serializer_class = PerformanceReviewSerializer
    filterset_fields = ["instructor", "reviewer"]
    ordering_fields = ["period_end", "overall_score"]
    ordering = ["-period_end"]


ROUTES = [
    ("instructors", InstructorViewSet, "instructor"),
    ("instructor-certifications", CertificationViewSet, "instructor-certification"),
    ("instructor-availability", AvailabilitySlotViewSet, "instructor-availability"),
    ("instructor-time-off", TimeOffViewSet, "instructor-time-off"),
    ("instructor-reviews", PerformanceReviewViewSet, "instructor-review"),
]
