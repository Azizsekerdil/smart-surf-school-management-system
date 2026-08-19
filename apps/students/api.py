"""REST API for students and skill assessments."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin

from . import selectors, services
from .models import SKILL_FIELDS, SkillAssessment, Student


class SkillAssessmentSerializer(serializers.ModelSerializer):
    average_score = serializers.FloatField(read_only=True)
    level_changed = serializers.BooleanField(read_only=True)
    instructor_name = serializers.SerializerMethodField()
    # Optional on write: omitting it means "the level did not change".
    level_after = serializers.ChoiceField(
        choices=SkillAssessment._meta.get_field("level_after").choices, required=False
    )

    class Meta:
        model = SkillAssessment
        fields = [
            "id",
            "public_id",
            "student",
            "instructor",
            "instructor_name",
            "assessed_on",
            "level_before",
            "level_after",
            "level_changed",
            "paddling",
            "popup",
            "positioning",
            "wave_reading",
            "safety",
            "average_score",
            "notes",
            "next_focus",
            "created_at",
        ]
        read_only_fields = ["id", "public_id", "student", "level_before", "created_at"]

    def get_instructor_name(self, obj) -> str:
        return str(obj.instructor) if obj.instructor_id else ""


class StudentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    is_minor = serializers.BooleanField(read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    surf_level_label = serializers.CharField(source="get_surf_level_display", read_only=True)
    recommended_board_volume = serializers.FloatField(read_only=True)
    recommended_board_type = serializers.CharField(read_only=True)
    has_medical_flags = serializers.BooleanField(read_only=True)
    latest_assessment = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            "id",
            "public_id",
            "student_code",
            "customer",
            "customer_code",
            "full_name",
            "age",
            "is_minor",
            "surf_level",
            "surf_level_label",
            "goals",
            "stance",
            "board_preference",
            "can_swim",
            "swim_distance_m",
            "medical_conditions",
            "medications",
            "allergies",
            "has_medical_flags",
            "weight_kg",
            "height_cm",
            "shoe_size",
            "wetsuit_size",
            "recommended_board_volume",
            "recommended_board_type",
            "preferred_instructor",
            "total_lessons",
            "total_hours",
            "last_lesson_date",
            "joined_at",
            "is_active",
            "latest_assessment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "student_code",
            "surf_level",
            "total_lessons",
            "total_hours",
            "last_lesson_date",
            "created_at",
            "updated_at",
        ]

    def get_latest_assessment(self, obj):
        assessment = obj.latest_assessment
        return SkillAssessmentSerializer(assessment).data if assessment else None


class StudentWriteSerializer(serializers.ModelSerializer):
    """Write serializer. ``surf_level`` is deliberately absent on update: a level
    only ever changes through an assessment."""

    surf_level = serializers.ChoiceField(
        choices=Student._meta.get_field("surf_level").choices, required=False
    )

    class Meta:
        model = Student
        fields = [
            "customer",
            "surf_level",
            "goals",
            "stance",
            "board_preference",
            "can_swim",
            "swim_distance_m",
            "medical_conditions",
            "medications",
            "allergies",
            "weight_kg",
            "height_cm",
            "shoe_size",
            "wetsuit_size",
            "preferred_instructor",
            "joined_at",
            "is_active",
        ]

    def create(self, validated_data):
        customer = validated_data.pop("customer")
        request = self.context.get("request")
        try:
            return services.create_student(
                customer,
                actor=getattr(request, "user", None),
                request=request,
                **validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc

    def update(self, instance, validated_data):
        validated_data.pop("customer", None)
        validated_data.pop("surf_level", None)
        request = self.context.get("request")
        try:
            return services.update_student(
                instance,
                actor=getattr(request, "user", None),
                request=request,
                **validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc


class StudentViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "students"
    capability_overrides = {
        "assess": "students.change",
        "progress": "students.view",
        "eligibility": "students.view",
    }
    queryset = Student.objects.select_related("customer", "preferred_instructor").order_by(
        "customer__last_name", "customer__first_name"
    )
    filterset_fields = ["surf_level", "is_active", "can_swim", "preferred_instructor"]
    search_fields = [
        "student_code",
        "customer__first_name",
        "customer__last_name",
        "customer__customer_code",
    ]
    ordering_fields = ["created_at", "last_lesson_date", "total_lessons", "surf_level"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return StudentWriteSerializer
        return StudentSerializer

    def perform_destroy(self, instance):
        services.set_active(instance, False, actor=self.request.user, request=self.request)
        instance.delete()  # soft delete

    @action(detail=True, methods=["post"])
    def assess(self, request, pk=None):
        """Record an assessment; the student's level follows from it."""
        student = self.get_object()
        serializer = SkillAssessmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            assessment = services.record_assessment(
                student,
                paddling=data["paddling"],
                popup=data["popup"],
                positioning=data["positioning"],
                wave_reading=data["wave_reading"],
                safety=data["safety"],
                instructor=data.get("instructor"),
                assessed_on=data.get("assessed_on"),
                level_after=data.get("level_after"),
                notes=data.get("notes", ""),
                next_focus=data.get("next_focus", ""),
                actor=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {
                    "error": {
                        "type": "validation_error",
                        "message": "; ".join(exc.messages),
                        "detail": getattr(exc, "message_dict", {}),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            SkillAssessmentSerializer(assessment).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        """Time series for the progress chart."""
        student = self.get_object()
        return Response(
            {
                "student": student.student_code,
                "skills": list(SKILL_FIELDS),
                "series": services.student_progress_series(student),
            }
        )

    @action(detail=True, methods=["get"])
    def eligibility(self, request, pk=None):
        """Whether this student may join ``?lesson=<id>``.

        Without a lesson id it reports the standing blockers (waiver, guardian,
        swimming ability, safety restrictions) so a booking screen can warn
        before a lesson has even been picked.
        """
        student = self.get_object()
        lesson = None
        lesson_id = request.query_params.get("lesson")
        if lesson_id:
            Lesson = selectors.optional_model("lessons", "Lesson")
            if Lesson is not None:
                manager = getattr(Lesson, "objects", None) or Lesson._default_manager
                lesson = manager.filter(pk=lesson_id).first()
        allowed, reason = student.can_join_lesson(lesson)
        return Response(
            {
                "student": student.student_code,
                "lesson": lesson.pk if lesson is not None else None,
                "allowed": allowed,
                "reason": reason,
                "has_valid_waiver": student.customer.has_valid_waiver(),
                "is_minor": student.is_minor,
                "can_swim": student.can_swim,
            }
        )


class SkillAssessmentViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Assessments are created through ``/students/{id}/assess/`` so the level
    change and the audit entry always happen together."""

    capability_prefix = "students"
    queryset = SkillAssessment.objects.select_related(
        "student", "student__customer", "instructor"
    )
    serializer_class = SkillAssessmentSerializer
    filterset_fields = ["student", "instructor", "level_after"]
    search_fields = ["student__student_code", "notes", "next_focus"]
    ordering_fields = ["assessed_on", "created_at"]
    ordering = ["-assessed_on"]


ROUTES = [
    ("students", StudentViewSet, "student"),
    ("skill-assessments", SkillAssessmentViewSet, "skillassessment"),
]
