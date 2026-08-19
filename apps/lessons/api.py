"""REST API for the lesson catalogue, the timetable and attendance."""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import OWN, SHARED, OwnerScopedQuerySetMixin

from .models import Lesson, LessonAttendance, LessonType
from .selectors import BOOKED_ANNOTATION
from .services import (
    add_student_to_lesson,
    assign_equipment_to_attendance,
    cancel_lesson,
    capture_conditions_snapshot,
    check_in_student,
    check_lesson_conflicts,
    check_lesson_warnings,
    complete_lesson,
    lessons_for_calendar,
    mark_no_show,
    mark_safety_check,
    remove_student_from_lesson,
    suggest_capacity,
)


def _as_drf_error(exc: DjangoValidationError) -> serializers.ValidationError:
    """Translate a service-layer refusal into a DRF 400 with the same wording."""
    return serializers.ValidationError({"detail": list(exc.messages)})


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class LessonTypeSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    allowed_levels = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_private = serializers.BooleanField(read_only=True)
    ratio_per_instructor = serializers.IntegerField(read_only=True)

    class Meta:
        model = LessonType
        fields = [
            "id",
            "public_id",
            "code",
            "name",
            "description",
            "category",
            "category_label",
            "min_level",
            "max_level",
            "allowed_levels",
            "min_age",
            "max_age",
            "duration_minutes",
            "min_students",
            "max_students",
            "base_price",
            "price_per_extra_student",
            "requires_board",
            "requires_wetsuit",
            "requires_leash",
            "colour",
            "is_active",
            "sort_order",
            "is_private",
            "ratio_per_instructor",
        ]
        read_only_fields = ["id", "public_id"]


class LessonAttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.__str__", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    lesson_code = serializers.CharField(source="lesson.lesson_code", read_only=True)
    equipment_ready = serializers.BooleanField(read_only=True)

    class Meta:
        model = LessonAttendance
        fields = [
            "id",
            "public_id",
            "lesson",
            "lesson_code",
            "student",
            "student_name",
            "booking",
            "status",
            "status_label",
            "checked_in_at",
            "rating",
            "student_feedback",
            "instructor_notes",
            "assigned_board",
            "assigned_wetsuit",
            "equipment_ready",
        ]
        read_only_fields = ["id", "public_id", "checked_in_at", "status"]


class LessonSerializer(serializers.ModelSerializer):
    lesson_type_name = serializers.CharField(source="lesson_type.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    spot_name = serializers.CharField(source="spot.__str__", read_only=True)
    instructor_name = serializers.CharField(source="instructor.__str__", read_only=True)
    booked_count = serializers.IntegerField(read_only=True)
    available_seats = serializers.IntegerField(read_only=True)
    is_full = serializers.BooleanField(read_only=True)
    required_ratio_ok = serializers.BooleanField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "public_id",
            "lesson_code",
            "lesson_type",
            "lesson_type_name",
            "spot",
            "spot_name",
            "date",
            "start_time",
            "end_time",
            "duration_minutes",
            "instructor",
            "instructor_name",
            "assistant_instructors",
            "capacity",
            "status",
            "status_label",
            "price_override",
            "price",
            "total_price",
            "notes",
            "internal_notes",
            "conditions_snapshot",
            "safety_briefing_done",
            "safety_checked_by",
            "safety_checked_at",
            "cancellation_reason",
            "cancelled_at",
            "booked_count",
            "available_seats",
            "is_full",
            "required_ratio_ok",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "lesson_code",
            "conditions_snapshot",
            "safety_briefing_done",
            "safety_checked_by",
            "safety_checked_at",
            "cancellation_reason",
            "cancelled_at",
        ]

    def validate(self, attrs):
        """Apply exactly the same scheduling rules as the HTML form."""
        instance = self.instance
        proposal = {
            "lesson_type": attrs.get("lesson_type", getattr(instance, "lesson_type", None)),
            "spot": attrs.get("spot", getattr(instance, "spot", None)),
            "date": attrs.get("date", getattr(instance, "date", None)),
            "start_time": attrs.get("start_time", getattr(instance, "start_time", None)),
            "end_time": attrs.get("end_time", getattr(instance, "end_time", None)),
            "instructor": attrs.get("instructor", getattr(instance, "instructor", None)),
            "assistant_instructors": list(
                attrs.get("assistant_instructors")
                or (list(instance.assistant_instructors.all()) if instance else [])
            ),
            "capacity": attrs.get("capacity", getattr(instance, "capacity", 0)),
            "status": attrs.get("status", getattr(instance, "status", None)),
        }
        conflicts = check_lesson_conflicts(
            proposal, exclude_pk=instance.pk if instance else None
        )
        if conflicts:
            raise serializers.ValidationError({"detail": conflicts})
        return attrs


class LessonCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=1000)


class AddStudentSerializer(serializers.Serializer):
    student = serializers.IntegerField()
    booking = serializers.IntegerField(required=False, allow_null=True)


class AssignEquipmentSerializer(serializers.Serializer):
    board = serializers.IntegerField(required=False, allow_null=True)
    wetsuit = serializers.IntegerField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class LessonTypeViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """The catalogue of teaching products."""

    capability_prefix = "lessons"
    # A product catalogue carries no personal data.
    external_access = SHARED
    capability_overrides = {"create": "lessons.manage", "update": "lessons.manage",
                            "partial_update": "lessons.manage", "destroy": "lessons.manage"}
    queryset = LessonType.objects.all()
    serializer_class = LessonTypeSerializer
    filterset_fields = ["category", "is_active", "min_level", "max_level"]
    search_fields = ["code", "name", "description"]
    ordering_fields = ["sort_order", "name", "base_price", "duration_minutes"]
    ordering = ["sort_order", "name"]

    @action(detail=True, methods=["get"])
    def capacity(self, request, pk=None):
        """Safe group size for this product at a given staffing level."""
        lesson_type = self.get_object()
        instructors = max(1, int(request.query_params.get("instructors", 1) or 1))
        has_minors = str(request.query_params.get("minors", "")).lower() in {"1", "true", "yes"}
        return Response(
            {
                "lesson_type": lesson_type.code,
                "instructors": instructors,
                "has_minors": has_minors,
                "suggested_capacity": suggest_capacity(
                    lesson_type, instructor_count=instructors, has_minors=has_minors
                ),
                "max_students": lesson_type.max_students,
            }
        )


class LessonViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Scheduled lessons, plus the operational actions that run them."""

    capability_prefix = "lessons"
    # A customer or student sees a lesson only if they are on its register.
    # Without this a ``lessons.view`` holder could read every roster in the
    # school, which for a surf school means the names of other people's
    # children.
    external_access = OWN
    owner_lookups = ("attendances__student__customer__user",)
    capability_overrides = {
        "cancel": "lessons.change",
        "complete": "lessons.change",
        "add_student": "lessons.change",
        "remove_student": "lessons.change",
        "safety_check": "lessons.change",
        "capture_conditions": "lessons.change",
        "calendar": "lessons.view",
        "conflicts": "lessons.view",
    }
    queryset = (
        Lesson.objects.select_related("lesson_type", "spot", "instructor")
        .prefetch_related("assistant_instructors", "attendances")
        .annotate(booked=BOOKED_ANNOTATION)
    )
    serializer_class = LessonSerializer
    filterset_fields = ["status", "lesson_type", "spot", "instructor", "date"]
    search_fields = ["lesson_code", "notes", "lesson_type__name"]
    ordering_fields = ["date", "start_time", "created_at"]
    ordering = ["-date", "start_time"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    # -- read-only helpers ------------------------------------------------
    @action(detail=False, methods=["get"])
    def calendar(self, request):
        """Events between ``?start=`` and ``?end=`` for a calendar UI."""
        today = timezone.localdate()
        start = serializers.DateField().to_internal_value(
            request.query_params.get("start") or today.isoformat()
        )
        end = serializers.DateField().to_internal_value(
            request.query_params.get("end") or (today + timedelta(days=13)).isoformat()
        )
        if end < start:
            start, end = end, start
        if (end - start).days > 92:
            end = start + timedelta(days=92)
        return Response(
            {"events": lessons_for_calendar(start, end, viewer=request.user)}
        )

    @action(detail=True, methods=["get"])
    def conflicts(self, request, pk=None):
        """Blocking conflicts and advisory warnings for one lesson."""
        lesson = self.get_object()
        return Response(
            {
                "conflicts": [str(item) for item in check_lesson_conflicts(lesson)],
                "warnings": [str(item) for item in check_lesson_warnings(lesson)],
            }
        )

    # -- lifecycle ---------------------------------------------------------
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        lesson = self.get_object()
        payload = LessonCancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            cancel_lesson(
                lesson, payload.validated_data["reason"], user=request.user, request=request
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(lesson).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        lesson = self.get_object()
        mark_no_shows = str(request.data.get("mark_unchecked_as_no_show", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            complete_lesson(
                lesson,
                mark_unchecked_as_no_show=mark_no_shows,
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(lesson).data)

    @action(detail=True, methods=["post"], url_path="safety-check")
    def safety_check(self, request, pk=None):
        lesson = self.get_object()
        try:
            mark_safety_check(lesson, request.user, request=request)
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(lesson).data)

    @action(detail=True, methods=["post"], url_path="capture-conditions")
    def capture_conditions(self, request, pk=None):
        lesson = self.get_object()
        snapshot = capture_conditions_snapshot(lesson, user=request.user, request=request)
        if not snapshot:
            return Response(
                {"detail": ["No surf condition reading is available for this spot."]},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(snapshot)

    # -- roster ------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="add-student")
    def add_student(self, request, pk=None):
        lesson = self.get_object()
        payload = AddStudentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        student = self._resolve("students", "Student", payload.validated_data["student"])
        booking = (
            self._resolve("bookings", "Booking", payload.validated_data.get("booking"))
            if payload.validated_data.get("booking")
            else None
        )
        try:
            attendance = add_student_to_lesson(
                lesson, student, booking=booking, user=request.user, request=request
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(
            LessonAttendanceSerializer(attendance).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="remove-student")
    def remove_student(self, request, pk=None):
        lesson = self.get_object()
        payload = AddStudentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        student = self._resolve("students", "Student", payload.validated_data["student"])
        try:
            attendance = remove_student_from_lesson(
                lesson,
                student,
                reason=str(request.data.get("reason", "")),
                user=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(LessonAttendanceSerializer(attendance).data)

    @staticmethod
    def _resolve(app_label: str, model_name: str, pk):
        from django.apps import apps as django_apps
        from django.shortcuts import get_object_or_404

        model = django_apps.get_model(app_label, model_name)
        return get_object_or_404(model, pk=pk)


class LessonAttendanceViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Seats on lessons: check-in, no-show, equipment and feedback."""

    capability_prefix = "lessons"
    # An attendance row names a student — often a minor — and carries the
    # instructor's notes about them. Strictly own-row for external accounts.
    external_access = OWN
    owner_lookups = ("student__customer__user",)
    capability_overrides = {
        "check_in": "lessons.change",
        "no_show": "lessons.change",
        "assign_equipment": "lessons.change",
    }
    queryset = LessonAttendance.objects.select_related(
        "lesson", "student", "booking", "assigned_board", "assigned_wetsuit"
    )
    serializer_class = LessonAttendanceSerializer
    filterset_fields = ["lesson", "student", "status", "booking"]
    search_fields = ["lesson__lesson_code", "instructor_notes"]
    ordering_fields = ["lesson__date", "status"]
    ordering = ["-lesson__date"]

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        attendance = self.get_object()
        try:
            check_in_student(attendance, user=request.user, request=request)
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(attendance).data)

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, pk=None):
        attendance = self.get_object()
        try:
            mark_no_show(attendance, user=request.user, request=request)
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(attendance).data)

    @action(detail=True, methods=["post"], url_path="assign-equipment")
    def assign_equipment(self, request, pk=None):
        attendance = self.get_object()
        payload = AssignEquipmentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        board = self._equipment(payload.validated_data.get("board"))
        wetsuit = self._equipment(payload.validated_data.get("wetsuit"))
        try:
            assign_equipment_to_attendance(
                attendance, board=board, wetsuit=wetsuit, user=request.user, request=request
            )
        except DjangoValidationError as exc:
            raise _as_drf_error(exc) from exc
        return Response(self.get_serializer(attendance).data)

    @staticmethod
    def _equipment(pk):
        if not pk:
            return None
        from django.apps import apps as django_apps
        from django.shortcuts import get_object_or_404

        return get_object_or_404(django_apps.get_model("equipment", "Equipment"), pk=pk)


ROUTES = [
    ("lesson-types", LessonTypeViewSet, "lessontype"),
    ("lessons", LessonViewSet, "lesson"),
    ("lesson-attendances", LessonAttendanceViewSet, "lessonattendance"),
]
