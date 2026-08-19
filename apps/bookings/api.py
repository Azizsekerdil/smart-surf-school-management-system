"""REST API for bookings and the waiting list.

The write path deliberately goes through :mod:`apps.bookings.services`, so an
API client is held to exactly the same safety rules as the receptionist at the
desk — a booking made over HTTP cannot skip the ratio check.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import OWN, OwnerScopedQuerySetMixin, StaffOnlyActionsMixin
from apps.core.enums import BookingStatus

from . import selectors, services
from .models import Booking, WaitlistEntry


def related_queryset(field_name: str):
    """Default queryset of the model a Booking FK points at.

    Avoids importing sibling apps at module scope while still giving DRF a real
    queryset to validate primary keys against.
    """
    return Booking._meta.get_field(field_name).remote_field.model._default_manager.all()


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class BookingSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.__str__", read_only=True)
    student_name = serializers.SerializerMethodField()
    activity = serializers.CharField(source="activity_label", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    payment_status_label = serializers.CharField(
        source="get_payment_status_display", read_only=True
    )
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_paid = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    is_cancellable_free = serializers.BooleanField(read_only=True)
    scheduled_start = serializers.DateTimeField(read_only=True)
    scheduled_end = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "public_id",
            "booking_code",
            "booking_type",
            "customer",
            "customer_name",
            "student",
            "student_name",
            "lesson",
            "surf_camp",
            "activity",
            "status",
            "status_label",
            "payment_status",
            "payment_status_label",
            "participants",
            "unit_price",
            "discount_amount",
            "total_amount",
            "paid_amount",
            "balance_due",
            "is_paid",
            "is_active",
            "can_cancel",
            "is_cancellable_free",
            "scheduled_start",
            "scheduled_end",
            "source",
            "booked_at",
            "confirmed_at",
            "cancelled_at",
            "cancellation_reason",
            "cancellation_fee",
            "special_requests",
            "internal_notes",
            "reminder_sent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "booking_code",
            "status",
            "payment_status",
            "total_amount",
            "paid_amount",
            "confirmed_at",
            "cancelled_at",
            "cancellation_fee",
            "created_at",
            "updated_at",
        ]

    def get_student_name(self, obj) -> str:
        return str(obj.student) if obj.student_id else ""


class BookingCreateSerializer(serializers.Serializer):
    """Input for creating a booking through the service layer."""

    booking_type = serializers.ChoiceField(
        choices=Booking.BookingType.choices, default=Booking.BookingType.LESSON
    )
    customer = serializers.PrimaryKeyRelatedField(queryset=related_queryset("customer"))
    student = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("student"), required=False, allow_null=True
    )
    lesson = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("lesson"), required=False, allow_null=True
    )
    surf_camp = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("surf_camp"), required=False, allow_null=True
    )
    participants = serializers.IntegerField(min_value=1, max_value=30, default=1)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0.00")
    )
    source = serializers.CharField(required=False, default="walk_in")
    special_requests = serializers.CharField(required=False, allow_blank=True, default="")
    internal_notes = serializers.CharField(required=False, allow_blank=True, default="")
    confirm = serializers.BooleanField(required=False, default=False)


class ConflictCheckSerializer(serializers.Serializer):
    booking_type = serializers.ChoiceField(
        choices=Booking.BookingType.choices, default=Booking.BookingType.LESSON
    )
    lesson = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("lesson"), required=False, allow_null=True
    )
    surf_camp = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("surf_camp"), required=False, allow_null=True
    )
    student = serializers.PrimaryKeyRelatedField(
        queryset=related_queryset("student"), required=False, allow_null=True
    )
    participants = serializers.IntegerField(min_value=1, max_value=30, default=1)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)
    fee = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )


class PaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )


class WaitlistEntrySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.__str__", read_only=True)
    target = serializers.SerializerMethodField()

    class Meta:
        model = WaitlistEntry
        fields = [
            "id",
            "public_id",
            "lesson",
            "surf_camp",
            "target",
            "customer",
            "customer_name",
            "student",
            "participants",
            "position",
            "requested_at",
            "is_notified",
            "notified_at",
            "is_converted",
            "converted_booking",
            "note",
        ]
        read_only_fields = [
            "id",
            "public_id",
            "position",
            "is_notified",
            "notified_at",
            "is_converted",
            "converted_booking",
        ]

    def get_target(self, obj) -> str:
        return str(obj.lesson or obj.surf_camp or "")


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class BookingViewSet(
    StaffOnlyActionsMixin, OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet
):
    """Bookings. Creation and every transition run through the service layer."""

    capability_prefix = "bookings"
    external_access = OWN
    # ``student__customer__user`` covers the case where a parent's login is
    # attached to the child's customer record rather than the payer's.
    owner_lookups = ("customer__user", "student__customer__user")
    # ``calendar`` and ``schedule`` are whole-school run sheets that name every
    # customer booked that day; there is no own-rows projection of them.
    staff_only_actions = ("calendar", "schedule")
    capability_overrides = {
        "confirm": "bookings.change",
        "check_in": "bookings.change",
        "complete": "bookings.change",
        "cancel": "bookings.change",
        "no_show": "bookings.change",
        "payment": "bookings.change",
        "conflicts": "bookings.add",
        "calendar": "bookings.view",
        "schedule": "bookings.view",
    }
    queryset = Booking.objects.select_related(
        "customer", "student", "lesson", "surf_camp"
    ).order_by("-booked_at", "-id")
    serializer_class = BookingSerializer
    filterset_fields = ["status", "payment_status", "booking_type", "source", "customer"]
    search_fields = ["booking_code", "customer__first_name", "customer__last_name"]
    ordering_fields = ["booked_at", "total_amount", "status"]
    ordering = ["-booked_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return BookingCreateSerializer
        return BookingSerializer

    def _error(self, errors, code=status.HTTP_409_CONFLICT):
        return Response(
            {"error": {"type": "booking_conflict", "message": errors[0], "detail": {"conflicts": errors}}},
            status=code,
        )

    def create(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            booking = services.create_booking(
                data["customer"],
                data["booking_type"],
                lesson=data.get("lesson"),
                camp=data.get("surf_camp"),
                student=data.get("student"),
                participants=data["participants"],
                source=data.get("source") or "walk_in",
                user=request.user,
                request=request,
                unit_price=data.get("unit_price"),
                discount_amount=data.get("discount_amount") or 0,
                special_requests=data.get("special_requests", ""),
                internal_notes=data.get("internal_notes", ""),
                status=BookingStatus.CONFIRMED
                if data.get("confirm")
                else BookingStatus.PENDING,
            )
        except services.BookingConflictError as error:
            return self._error(error.errors)
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        booking = serializer.save(updated_by=self.request.user)
        booking.recalculate_totals(commit=True)

    def destroy(self, request, *args, **kwargs):
        """Bookings are never hard-deleted: cancel them so history survives."""
        booking = self.get_object()
        if not booking.can_cancel:
            booking.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {
                "error": {
                    "type": "validation_error",
                    "message": "Active bookings must be cancelled, not deleted.",
                    "detail": {"cancel_endpoint": f"/api/v1/bookings/{booking.pk}/cancel/"},
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # -- transitions --------------------------------------------------------
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        booking = self.get_object()
        try:
            services.confirm_booking(booking, user=request.user, request=request)
        except services.BookingConflictError as error:
            return self._error(error.errors)
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        booking = self.get_object()
        force = bool(request.data.get("force"))
        try:
            services.check_in_booking(
                booking, user=request.user, request=request, force=force
            )
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        booking = self.get_object()
        try:
            services.complete_booking(booking, user=request.user, request=request)
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        booking = self.get_object()
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.cancel_booking(
                booking,
                serializer.validated_data["reason"],
                user=request.user,
                fee=serializer.validated_data.get("fee"),
                request=request,
            )
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, pk=None):
        booking = self.get_object()
        try:
            services.mark_no_show(booking, user=request.user, request=request)
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"])
    def payment(self, request, pk=None):
        booking = self.get_object()
        serializer = PaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.register_payment(
                booking, serializer.validated_data["amount"], user=request.user, request=request
            )
        except services.BookingError as error:
            return self._error([error.message], status.HTTP_400_BAD_REQUEST)
        return Response(BookingSerializer(booking).data)

    # -- read-only helpers --------------------------------------------------
    @action(detail=False, methods=["post"])
    def conflicts(self, request):
        """Dry-run the rules without creating anything."""
        serializer = ConflictCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        problems = services.check_booking_conflicts(
            booking_type=data["booking_type"],
            lesson=data.get("lesson"),
            camp=data.get("surf_camp"),
            student=data.get("student"),
            participants=data["participants"],
        )
        return Response({"ok": not problems, "conflicts": problems})

    @action(detail=False, methods=["get"])
    def calendar(self, request):
        """Calendar events between ``?start=`` and ``?end=`` (ISO dates)."""
        start = selectors.parse_date(request.query_params.get("start", ""))
        end = selectors.parse_date(request.query_params.get("end", ""))
        today = timezone.localdate()
        start = start or today
        end = end or (start + timedelta(days=30))
        events = services.booking_calendar_events(
            services.as_aware(start), services.as_end_of_day(end)
        )
        payload = [
            {
                key: value
                for key, value in event.items()
                if key not in {"object", "bookings"}
            }
            for event in events
        ]
        return Response({"start": start, "end": end, "events": payload})

    @action(detail=False, methods=["get"])
    def schedule(self, request):
        """Structured run sheet for a single day."""
        day = selectors.parse_date(request.query_params.get("date", "")) or timezone.localdate()
        data = services.daily_schedule(day)
        return Response(
            {
                "date": data["date"],
                "alerts": [str(alert) for alert in data["alerts"]],
                "totals": data["totals"],
                "sessions": [
                    {
                        "id": event["id"],
                        "title": event["title"],
                        "start": event["start"],
                        "end": event["end"],
                        "capacity_label": event["capacity_label"],
                        "status": event["status"],
                        "instructor": event["instructor"],
                        "bookings": BookingSerializer(event["bookings"], many=True).data,
                    }
                    for event in data["events"]
                ],
            }
        )


class WaitlistViewSet(OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """The waiting list for sold-out lessons and camps."""

    capability_prefix = "bookings"
    external_access = OWN
    owner_lookups = ("customer__user", "student__customer__user")
    capability_overrides = {"promote": "bookings.change"}
    queryset = WaitlistEntry.objects.select_related(
        "customer", "student", "lesson", "surf_camp", "converted_booking"
    ).order_by("position", "requested_at")
    serializer_class = WaitlistEntrySerializer
    filterset_fields = ["lesson", "surf_camp", "is_converted", "customer"]
    search_fields = ["customer__first_name", "customer__last_name", "note"]
    ordering_fields = ["position", "requested_at"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            entry = services.add_to_waitlist(
                data["customer"],
                lesson=data.get("lesson"),
                camp=data.get("surf_camp"),
                student=data.get("student"),
                participants=data.get("participants") or 1,
                note=data.get("note", ""),
                user=request.user,
                request=request,
            )
        except services.BookingError as error:
            return Response(
                {"error": {"type": "validation_error", "message": error.message, "detail": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            WaitlistEntrySerializer(entry).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        entry = self.get_object()
        booking = services.promote_from_waitlist(
            lesson=entry.lesson, camp=entry.surf_camp, user=request.user, request=request
        )
        if booking is None:
            return Response(
                {
                    "error": {
                        "type": "booking_conflict",
                        "message": "No waiting entry could be promoted; no seat is free.",
                        "detail": {},
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)


ROUTES = [
    ("bookings", BookingViewSet, "booking"),
    ("waitlist", WaitlistViewSet, "waitlist"),
]
