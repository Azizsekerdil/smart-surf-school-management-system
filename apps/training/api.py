"""REST API for the Training Center.

Read endpoints let a kiosk or an onboarding checklist show what a member of
staff still has to work through. The write endpoints only ever touch the
authenticated user's own progress — there is no payload that lets one account
tick a course off for another.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import OWN, SHARED, OwnerScopedQuerySetMixin

from . import selectors, services
from .models import TrainingCourse, TrainingLesson, TrainingProgress, TrainingStep


class TrainingStepSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    body = serializers.CharField(read_only=True)
    body_html = serializers.SerializerMethodField()
    action_hint = serializers.CharField(read_only=True)
    target_link = serializers.CharField(read_only=True)
    course = serializers.IntegerField(source="lesson.course_id", read_only=True)

    class Meta:
        model = TrainingStep
        fields = [
            "id",
            "public_id",
            "lesson",
            "course",
            "order",
            "title",
            "title_en",
            "title_tr",
            "body",
            "body_en",
            "body_tr",
            "body_html",
            "target_url",
            "target_link",
            "action_hint",
            "action_hint_en",
            "action_hint_tr",
            "image",
        ]
        read_only_fields = ["id", "public_id"]

    def get_body_html(self, obj) -> str:
        """Sanitised HTML for the active language."""
        return str(obj.rendered_body())


class TrainingLessonSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    summary = serializers.CharField(read_only=True)
    step_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TrainingLesson
        fields = [
            "id",
            "public_id",
            "course",
            "order",
            "title",
            "title_en",
            "title_tr",
            "summary",
            "summary_en",
            "summary_tr",
            "estimated_minutes",
            "step_count",
        ]
        read_only_fields = ["id", "public_id"]


class TrainingCourseSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    difficulty_label = serializers.CharField(source="get_difficulty_display", read_only=True)
    lesson_count = serializers.IntegerField(read_only=True)
    total_steps = serializers.IntegerField(read_only=True)

    class Meta:
        model = TrainingCourse
        fields = [
            "id",
            "public_id",
            "code",
            "title",
            "title_en",
            "title_tr",
            "description",
            "description_en",
            "description_tr",
            "icon",
            "estimated_minutes",
            "difficulty",
            "difficulty_label",
            "required_capability",
            "lesson_count",
            "total_steps",
            "sort_order",
            "is_active",
        ]
        read_only_fields = ["id", "public_id"]


class TrainingProgressSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    percent_complete = serializers.IntegerField(read_only=True)

    class Meta:
        model = TrainingProgress
        fields = [
            "id",
            "user",
            "course",
            "course_code",
            "course_title",
            "lesson",
            "step",
            "status",
            "status_label",
            "completed_steps",
            "percent_complete",
            "started_at",
            "completed_at",
            "last_activity_at",
        ]
        read_only_fields = fields


class TrainingCourseViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Courses, filtered to what the caller is allowed to see."""

    capability_prefix = "training"
    # Course material; ``selectors.courses_for`` already narrows the catalogue
    # to the audience of each course.
    external_access = SHARED
    capability_overrides = {"start": "training.view", "progress": "training.view"}
    queryset = TrainingCourse.objects.all()
    serializer_class = TrainingCourseSerializer
    filterset_fields = ["difficulty", "is_active"]
    search_fields = ["code", "title_en", "title_tr", "description_en", "description_tr"]
    ordering_fields = ["sort_order", "estimated_minutes", "difficulty"]
    ordering = ["sort_order"]

    def get_queryset(self):
        if self.action in {"list", "retrieve", "start", "progress"}:
            return selectors.courses_for(self.request.user)
        return super().get_queryset()

    @extend_schema(responses=TrainingProgressSerializer)
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Begin or resume this course for the authenticated user."""
        course = self.get_object()
        progress = services.start_course(request.user, course)
        return Response(TrainingProgressSerializer(progress).data)

    @extend_schema(responses={200: serializers.Serializer})
    @action(detail=False, methods=["get"])
    def progress(self, request):
        """Overall training progress for the authenticated user."""
        overall = services.overall_progress(request.user)
        return Response(
            {
                "courses_total": overall["courses_total"],
                "courses_completed": overall["courses_completed"],
                "courses_in_progress": overall["courses_in_progress"],
                "courses_not_started": overall["courses_not_started"],
                "steps_total": overall["steps_total"],
                "steps_completed": overall["steps_completed"],
                "percent": overall["percent"],
                "remaining_minutes": overall["remaining_minutes"],
                "courses": [
                    {
                        "id": item.course.pk,
                        "code": item.course.code,
                        "title": item.course.title,
                        "status": item.status,
                        "percent": item.percent,
                        "completed_steps": item.completed_steps,
                        "total_steps": item.total_steps,
                        "next_step": item.next_step.pk if item.next_step else None,
                    }
                    for item in overall["summaries"]
                ],
            }
        )


class TrainingLessonViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "training"
    external_access = SHARED
    queryset = TrainingLesson.objects.select_related("course").order_by("course__sort_order", "order")
    serializer_class = TrainingLessonSerializer
    filterset_fields = ["course"]
    search_fields = ["title_en", "title_tr"]
    ordering_fields = ["order"]


class TrainingStepViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    capability_prefix = "training"
    external_access = SHARED
    capability_overrides = {"complete": "training.view", "uncomplete": "training.view"}
    queryset = TrainingStep.objects.select_related("lesson", "lesson__course").order_by(
        "lesson__order", "order"
    )
    serializer_class = TrainingStepSerializer
    filterset_fields = ["lesson", "lesson__course"]
    search_fields = ["title_en", "title_tr", "body_en", "body_tr"]
    ordering_fields = ["order"]

    @extend_schema(responses=TrainingProgressSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Tick this step off for the authenticated user."""
        step = self.get_object()
        progress = services.complete_step(request.user, step)
        return Response(TrainingProgressSerializer(progress).data)

    @extend_schema(responses=TrainingProgressSerializer)
    @action(detail=True, methods=["post"])
    def uncomplete(self, request, pk=None):
        """Untick this step for the authenticated user."""
        step = self.get_object()
        progress = services.uncomplete_step(request.user, step)
        return Response(TrainingProgressSerializer(progress).data)


class TrainingProgressViewSet(
    OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet
):
    """Progress rows. A user sees their own; ``training.manage`` sees everyone's."""

    capability_prefix = "training"
    external_access = OWN
    owner_lookups = ("user",)
    queryset = TrainingProgress.objects.select_related("course", "user")
    serializer_class = TrainingProgressSerializer
    filterset_fields = ["course", "status"]
    ordering_fields = ["last_activity_at", "completed_at"]
    ordering = ["-last_activity_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.has_capability("training.manage"):
            return queryset
        return queryset.filter(user=user)

    @extend_schema(responses=TrainingProgressSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="mine")
    def mine(self, request):
        """The authenticated user's own progress rows, whatever their role."""
        records = TrainingProgress.objects.filter(user=request.user).select_related("course")
        return Response(TrainingProgressSerializer(records, many=True).data)


ROUTES = [
    ("training-courses", TrainingCourseViewSet, "trainingcourse"),
    ("training-lessons", TrainingLessonViewSet, "traininglesson"),
    ("training-steps", TrainingStepViewSet, "trainingstep"),
    ("training-progress", TrainingProgressViewSet, "trainingprogress"),
]
