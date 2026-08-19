"""REST API for surf camps, participants, days and activities."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import (
    OWN,
    SHARED,
    OwnerScopedQuerySetMixin,
    StaffOnlyActionsMixin,
)

from . import services
from .models import CampActivity, CampDay, CampParticipant, SurfCamp
from .selectors import camps_with_occupancy, participants_for

ZERO = Decimal("0.00")


def _as_drf_error(error: DjangoValidationError) -> serializers.ValidationError:
    """Translate a model/service validation error into a DRF one."""
    if hasattr(error, "message_dict"):
        return serializers.ValidationError(error.message_dict)
    return serializers.ValidationError({"detail": error.messages})


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class CampActivitySerializer(serializers.ModelSerializer):
    activity_type_label = serializers.CharField(source="get_activity_type_display", read_only=True)
    instructor_name = serializers.SerializerMethodField()
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = CampActivity
        fields = [
            "id",
            "public_id",
            "camp_day",
            "start_time",
            "end_time",
            "title",
            "activity_type",
            "activity_type_label",
            "instructor",
            "instructor_name",
            "lesson",
            "location",
            "notes",
            "duration_minutes",
        ]
        read_only_fields = ["id", "public_id"]

    def get_instructor_name(self, obj) -> str:
        return str(obj.instructor) if obj.instructor_id else ""

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError(
                {"end_time": "The end time must be after the start time."}
            )
        return attrs


class CampDaySerializer(serializers.ModelSerializer):
    activities = CampActivitySerializer(many=True, read_only=True)
    spot_name = serializers.SerializerMethodField()

    class Meta:
        model = CampDay
        fields = [
            "id",
            "public_id",
            "camp",
            "date",
            "day_number",
            "title",
            "description",
            "weather_note",
            "spot",
            "spot_name",
            "activities",
        ]
        read_only_fields = ["id", "public_id"]

    def get_spot_name(self, obj) -> str:
        spot = obj.effective_spot
        return str(spot) if spot else ""


class CampParticipantSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    camp_code = serializers.CharField(source="camp.code", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    room_type_label = serializers.CharField(source="get_room_type_display", read_only=True)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    payment_state = serializers.CharField(read_only=True)

    class Meta:
        model = CampParticipant
        fields = [
            "id",
            "public_id",
            "camp",
            "camp_code",
            "student",
            "student_name",
            "booking",
            "room_number",
            "room_type",
            "room_type_label",
            "roommate_preference",
            "arrival_datetime",
            "departure_datetime",
            "arrival_flight",
            "departure_flight",
            "needs_transfer",
            "dietary_requirements",
            "medical_notes",
            "t_shirt_size",
            "amount_paid",
            "deposit_paid",
            "status",
            "status_label",
            "cancellation_reason",
            "total_price",
            "balance_due",
            "payment_state",
        ]
        read_only_fields = ["id", "public_id", "status", "cancellation_reason"]

    def get_student_name(self, obj) -> str:
        return str(obj.student) if obj.student_id else ""

    def create(self, validated_data):
        """Registration always goes through the service, so capacity and level
        checks can never be bypassed by using the API."""
        camp = validated_data.pop("camp")
        student = validated_data.pop("student")
        booking = validated_data.pop("booking", None)
        request = self.context.get("request")
        try:
            return services.add_participant(
                camp, student, booking=booking, request=request, **validated_data
            )
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error


class SurfCampSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    spot_name = serializers.SerializerMethodField()
    lead_instructor_name = serializers.SerializerMethodField()
    duration_days = serializers.IntegerField(read_only=True)
    is_upcoming = serializers.BooleanField(read_only=True)
    is_running = serializers.BooleanField(read_only=True)
    # Occupancy is read from the queryset annotations so a camp list stays one
    # query instead of four per row.
    participant_count = serializers.SerializerMethodField()
    available_places = serializers.SerializerMethodField()
    is_full = serializers.SerializerMethodField()
    total_revenue = serializers.SerializerMethodField()

    class Meta:
        model = SurfCamp
        fields = [
            "id",
            "public_id",
            "name",
            "code",
            "description",
            "photo",
            "start_date",
            "end_date",
            "spot",
            "spot_name",
            "capacity",
            "min_participants",
            "min_level",
            "max_level",
            "price",
            "deposit_amount",
            "single_room_supplement",
            "includes_accommodation",
            "includes_meals",
            "includes_transfer",
            "includes_equipment",
            "includes_insurance",
            "accommodation_name",
            "accommodation_address",
            "meal_plan",
            "transfer_pickup_point",
            "transfer_notes",
            "status",
            "status_label",
            "lead_instructor",
            "lead_instructor_name",
            "instructors",
            "is_active",
            "duration_days",
            "participant_count",
            "available_places",
            "is_full",
            "is_upcoming",
            "is_running",
            "total_revenue",
        ]
        read_only_fields = ["id", "public_id"]
        extra_kwargs = {"code": {"required": False}}

    def get_spot_name(self, obj) -> str:
        return str(obj.spot) if obj.spot_id else ""

    def get_lead_instructor_name(self, obj) -> str:
        return str(obj.lead_instructor) if obj.lead_instructor_id else ""

    def get_participant_count(self, obj) -> int:
        booked = getattr(obj, "booked_count", None)
        return obj.participant_count if booked is None else booked

    def get_available_places(self, obj) -> int:
        left = getattr(obj, "places_left", None)
        return obj.available_places if left is None else left

    def get_is_full(self, obj) -> bool:
        booked = getattr(obj, "booked_count", None)
        return obj.is_full if booked is None else booked >= (obj.capacity or 0)

    def get_total_revenue(self, obj) -> Decimal | None:
        # Camp takings are a revenue figure, not product information. Anyone
        # without finance.revenue — including every customer and student —
        # gets null instead.
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated and user.has_capability("finance.revenue")):
            return None
        booked = getattr(obj, "booked_count", None)
        singles = getattr(obj, "single_room_count", None)
        if booked is None or singles is None:
            return obj.total_revenue
        return ((obj.price or ZERO) * booked) + ((obj.single_room_supplement or ZERO) * singles)

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "The end date cannot be before the start date."}
            )
        return attrs

    def create(self, validated_data):
        instructors = validated_data.pop("instructors", [])
        camp = SurfCamp(**validated_data)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            camp.created_by = user
            camp.updated_by = user
        try:
            camp.full_clean(exclude=["code", "created_by", "updated_by"])
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        camp.save()
        if instructors:
            camp.instructors.set(instructors)
        services.create_camp_with_days(camp)
        return camp


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class SurfCampViewSet(
    StaffOnlyActionsMixin, OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet
):
    """Camps, with the operational read-only endpoints the mobile desk needs."""

    capability_prefix = "surf_camps"
    # The camp itself is a product listing — customers are meant to browse it.
    external_access = SHARED
    # These four are operational overviews of *other people*: the participant
    # list, the daily register (which counts minors and prints dietary and
    # allergy notes), and the camp's takings. There is no own-rows projection
    # of them, so external accounts are refused outright.
    staff_only_actions = ("participants", "roster", "finance", "add_participant")
    capability_overrides = {
        "publish": "surf_camps.approve",
        "cancel": "surf_camps.approve",
        "add_participant": "surf_camps.change",
        "generate_programme": "surf_camps.change",
        "participants": "surf_camps.view",
        "programme": "surf_camps.view",
        "roster": "surf_camps.view",
        "finance": "surf_camps.view",
    }
    serializer_class = SurfCampSerializer
    queryset = SurfCamp.objects.select_related("spot", "lead_instructor").prefetch_related(
        "instructors"
    )
    filterset_fields = ["status", "is_active", "spot", "min_level", "max_level"]
    search_fields = ["name", "code", "accommodation_name"]
    ordering_fields = ["start_date", "end_date", "price", "name", "created_at"]
    ordering = ["-start_date"]

    def get_queryset(self):
        return self.scope(camps_with_occupancy())

    @action(detail=True, methods=["get"])
    def participants(self, request, pk=None):
        camp = self.get_object()
        include_cancelled = request.query_params.get("include_cancelled") == "1"
        queryset = participants_for(camp, include_cancelled=include_cancelled)
        return Response(CampParticipantSerializer(queryset, many=True).data)

    @action(detail=True, methods=["post"], url_path="add-participant")
    def add_participant(self, request, pk=None):
        camp = self.get_object()
        serializer = CampParticipantSerializer(
            data={**request.data, "camp": camp.pk}, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def programme(self, request, pk=None):
        camp = self.get_object()
        days = (
            camp.days.select_related("spot", "camp__spot")
            .prefetch_related("activities__instructor")
            .order_by("date")
        )
        return Response(CampDaySerializer(days, many=True).data)

    @action(detail=True, methods=["post"], url_path="generate-programme")
    def generate_programme(self, request, pk=None):
        camp = self.get_object()
        replace = str(request.data.get("replace", "")).lower() in {"1", "true", "yes"}
        try:
            created = services.generate_default_programme(camp, replace=replace, request=request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        return Response({"activities_created": created})

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        camp = self.get_object()
        raw = request.query_params.get("date")
        try:
            on_date = date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            raise serializers.ValidationError({"date": "Use the YYYY-MM-DD format."}) from None

        data = services.camp_daily_roster(camp, on_date)
        return Response(
            {
                "date": on_date,
                "spot": str(data["spot"]) if data["spot"] else "",
                "present_count": data["present_count"],
                "minors": len(data["minors"]),
                "staffing": data["staffing"],
                "activities": CampActivitySerializer(data["activities"], many=True).data,
                "participants": CampParticipantSerializer(data["participants"], many=True).data,
                "arrivals": CampParticipantSerializer(data["arrivals"], many=True).data,
                "departures": CampParticipantSerializer(data["departures"], many=True).data,
                "dietary": [
                    {"student": str(p.student), "requirement": p.dietary_requirements}
                    for p in data["dietary"]
                ],
            }
        )

    @action(detail=True, methods=["get"])
    def finance(self, request, pk=None):
        return Response(services.camp_financial_summary(self.get_object()))

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        camp = self.get_object()
        try:
            services.publish_camp(camp, request=request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        return Response(self.get_serializer(camp).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        camp = self.get_object()
        try:
            services.cancel_camp(camp, reason=request.data.get("reason", ""), request=request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        return Response(self.get_serializer(camp).data)


class CampParticipantViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """A person's place in a camp.

    These rows carry room numbers, flight details, dietary requirements and —
    because surf camps run junior weeks — the whereabouts of children. Both
    external roles hold ``surf_camps.view``, so the ownership rule below is the
    only thing standing between a customer and every family's itinerary.
    """

    capability_prefix = "surf_camps"
    external_access = OWN
    owner_lookups = ("student__customer__user", "booking__customer__user")
    capability_overrides = {
        "check_in": "surf_camps.change",
        "check_out": "surf_camps.change",
        "confirm": "surf_camps.change",
    }
    serializer_class = CampParticipantSerializer
    queryset = CampParticipant.objects.select_related("camp", "student", "booking")
    filterset_fields = ["camp", "status", "room_type", "needs_transfer", "deposit_paid"]
    search_fields = ["room_number", "dietary_requirements", "arrival_flight", "departure_flight"]
    ordering_fields = ["created_at", "room_number", "status"]
    ordering = ["camp", "room_number"]

    def perform_destroy(self, instance):
        """Cancelling frees the place; the row survives because money hangs off it."""
        try:
            services.remove_participant(instance, request=self.request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error

    def _transition(self, request, target: str):
        participant = self.get_object()
        try:
            services.set_participant_status(participant, target, request=request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        return Response(self.get_serializer(participant).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return self._transition(request, CampParticipant.Status.CONFIRMED)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        return self._transition(request, CampParticipant.Status.ARRIVED)

    @action(detail=True, methods=["post"], url_path="check-out")
    def check_out(self, request, pk=None):
        return self._transition(request, CampParticipant.Status.DEPARTED)


class CampDayViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """A day of a camp programme — timetable data, no personal records."""

    capability_prefix = "surf_camps"
    external_access = SHARED
    serializer_class = CampDaySerializer
    queryset = (
        CampDay.objects.select_related("camp", "spot")
        .prefetch_related("activities__instructor")
        .order_by("camp", "date")
    )
    filterset_fields = ["camp", "date"]
    search_fields = ["title", "description", "weather_note"]
    ordering_fields = ["date", "day_number"]
    ordering = ["date"]


class CampActivityViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """A scheduled item inside a camp day — timetable data."""

    capability_prefix = "surf_camps"
    external_access = SHARED
    serializer_class = CampActivitySerializer
    queryset = CampActivity.objects.select_related(
        "camp_day", "camp_day__camp", "instructor", "lesson"
    )
    filterset_fields = ["camp_day", "activity_type", "instructor"]
    search_fields = ["title", "location", "notes"]
    ordering_fields = ["start_time", "activity_type"]
    ordering = ["start_time"]

    def perform_create(self, serializer):
        activity = CampActivity(**serializer.validated_data)
        user = self.request.user
        if user.is_authenticated:
            activity.created_by = user
            activity.updated_by = user
        try:
            services.save_activity(activity, request=self.request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        serializer.instance = activity

    def perform_update(self, serializer):
        instance = serializer.instance
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        if self.request.user.is_authenticated:
            instance.updated_by = self.request.user
        try:
            services.save_activity(instance, request=self.request)
        except DjangoValidationError as error:
            raise _as_drf_error(error) from error
        serializer.instance = instance


ROUTES = [
    ("surf-camps", SurfCampViewSet, "surfcamp"),
    ("camp-participants", CampParticipantViewSet, "campparticipant"),
    ("camp-days", CampDayViewSet, "campday"),
    ("camp-activities", CampActivityViewSet, "campactivity"),
]
