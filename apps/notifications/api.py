"""REST API for notifications, templates and per-user preferences.

Scoping rule: every notification endpoint is filtered to ``request.user``
before the capability check is even relevant. ``notifications.view`` grants
access to *your* inbox, never to anybody else's — which is why the self-service
actions below override the default method-to-capability mapping (a customer has
``notifications.view`` but not ``notifications.change``, and must still be able
to mark their own reminder as read).
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.constants import STAFF_ROLES
from apps.accounts.permissions import CapabilityViewSetMixin
from apps.accounts.scoping import DENY, OWN, OwnerScopedQuerySetMixin

from . import selectors, services
from .models import (
    MAX_RENDERED_BODY,
    MAX_RENDERED_TITLE,
    Notification,
    NotificationCategory,
    NotificationLevel,
    NotificationPreference,
    NotificationTemplate,
)

#: Self-service actions: available to anyone who can see their own inbox.
SELF_SERVICE = "notifications.view"


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class NotificationSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    level_label = serializers.CharField(source="get_level_display", read_only=True)
    icon = serializers.CharField(source="icon_name", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "public_id",
            "category",
            "category_label",
            "level",
            "level_label",
            "icon",
            "title",
            "body",
            "link_url",
            "is_read",
            "read_at",
            "is_emailed",
            "emailed_at",
            "related_object_type",
            "related_object_id",
            "created_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    user_display = serializers.CharField(source="user.get_display_name", read_only=True)
    categories_muted = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationCategory.choices),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = NotificationPreference
        fields = [
            "id",
            "user",
            "user_display",
            "in_app_enabled",
            "email_enabled",
            "categories_muted",
            "quiet_hours_start",
            "quiet_hours_end",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "user_display", "updated_at"]

    def validate(self, attrs):
        start = attrs.get("quiet_hours_start", getattr(self.instance, "quiet_hours_start", None))
        end = attrs.get("quiet_hours_end", getattr(self.instance, "quiet_hours_end", None))
        if bool(start) != bool(end):
            raise serializers.ValidationError(
                _("Set both a start and an end time for quiet hours, or neither.")
            )
        if start and end and start == end:
            raise serializers.ValidationError(
                _("Quiet hours must start and end at different times.")
            )
        return attrs


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            "id",
            "code",
            "category",
            "level",
            "title_en",
            "title_tr",
            "body_en",
            "body_tr",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TemplatePreviewSerializer(serializers.Serializer):
    language = serializers.CharField(max_length=5, required=False, default="en")
    context = serializers.DictField(required=False, default=dict)


class BroadcastSerializer(serializers.Serializer):
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=sorted(STAFF_ROLES)), allow_empty=False
    )
    category = serializers.ChoiceField(
        choices=NotificationCategory.choices, default=NotificationCategory.SYSTEM
    )
    level = serializers.ChoiceField(
        choices=NotificationLevel.choices, default=NotificationLevel.INFO
    )
    title = serializers.CharField(max_length=MAX_RENDERED_TITLE)
    body = serializers.CharField(max_length=MAX_RENDERED_BODY, allow_blank=True, default="")
    link_url = serializers.CharField(max_length=500, allow_blank=True, default="")

    def validate_link_url(self, value: str) -> str:
        value = (value or "").strip()
        if value and (not value.startswith("/") or value.startswith("//") or "\\" in value):
            raise serializers.ValidationError(
                _("Enter a path inside this application, starting with a single “/”.")
            )
        return value


# ---------------------------------------------------------------------------
# Viewsets
# ---------------------------------------------------------------------------
class NotificationViewSet(
    OwnerScopedQuerySetMixin,
    CapabilityViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """The caller's own inbox.

    Notifications are never created through this endpoint — they are produced
    by :mod:`apps.notifications.services` from real business events. The one
    write path is ``broadcast``, which requires ``notifications.add``.
    """

    capability_prefix = "notifications"
    # ``get_queryset`` below is stricter than the shared engine: it filters to
    # the recipient for *everyone*, staff included. The declaration is kept so
    # the policy is visible where every other viewset states one.
    external_access = OWN
    owner_lookups = ("recipient",)
    capability_overrides = {
        "destroy": SELF_SERVICE,  # dismissing your own message is self-service
        "read": SELF_SERVICE,
        "unread": SELF_SERVICE,
        "read_all": SELF_SERVICE,
        "unread_count": SELF_SERVICE,
        "broadcast": "notifications.add",
    }
    serializer_class = NotificationSerializer
    queryset = Notification.objects.select_related("recipient")
    filterset_fields = ["category", "level", "is_read"]
    search_fields = ["title", "body"]
    ordering_fields = ["created_at", "category", "level"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(recipient=user).select_related("recipient")

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        services.mark_read(notification, request.user)
        return Response(self.get_serializer(notification).data)

    @action(detail=True, methods=["post"])
    def unread(self, request, pk=None):
        notification = self.get_object()
        services.mark_unread(notification, request.user)
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        category = request.data.get("category", "")
        if category not in NotificationCategory.values:
            category = ""
        updated = services.mark_all_read(request.user, category=category)
        return Response({"marked_read": updated, "unread": selectors.unread_count(request.user)})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": selectors.unread_count(request.user)})

    @action(detail=False, methods=["post"])
    def broadcast(self, request):
        """Send one message to every active user holding the given roles."""
        serializer = BroadcastSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        delivered = services.notify_role(
            data["roles"],
            data["category"],
            data["title"],
            data["body"],
            level=data["level"],
            link_url=data["link_url"],
            exclude_user=request.user,
        )
        return Response(
            {"delivered": len(delivered)},
            status=status.HTTP_201_CREATED if delivered else status.HTTP_200_OK,
        )


class NotificationPreferenceViewSet(
    OwnerScopedQuerySetMixin,
    CapabilityViewSetMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Read and edit **your own** delivery rules."""

    capability_prefix = "notifications"
    external_access = OWN
    owner_lookups = ("user",)
    capability_overrides = {
        "list": SELF_SERVICE,
        "retrieve": SELF_SERVICE,
        "update": SELF_SERVICE,
        "partial_update": SELF_SERVICE,
        "me": SELF_SERVICE,
    }
    serializer_class = NotificationPreferenceSerializer
    queryset = NotificationPreference.objects.select_related("user")

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return NotificationPreference.objects.none()
        return NotificationPreference.objects.filter(user=user).select_related("user")

    @action(detail=False, methods=["get", "put", "patch"])
    def me(self, request):
        preference = NotificationPreference.for_user(request.user, create=True)
        if request.method == "GET":
            return Response(self.get_serializer(preference).data)
        serializer = self.get_serializer(
            preference, data=request.data, partial=request.method == "PATCH"
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class NotificationTemplateViewSet(
    OwnerScopedQuerySetMixin, CapabilityViewSetMixin, viewsets.ModelViewSet
):
    """Bilingual message templates, editable without a deployment."""

    capability_prefix = "notifications"
    # Message templates are an operator tool, not user content.
    external_access = DENY
    capability_overrides = {"preview": SELF_SERVICE}
    queryset = NotificationTemplate.objects.all()
    serializer_class = NotificationTemplateSerializer
    filterset_fields = ["category", "level", "is_active"]
    search_fields = ["code", "title_en", "title_tr"]
    ordering_fields = ["code", "category", "updated_at"]
    ordering = ["category", "code"]
    lookup_field = "pk"

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        """Render the template in the sandbox so an editor can check it."""
        template = self.get_object()
        serializer = TemplatePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        title, body = template.render(
            serializer.validated_data["language"], serializer.validated_data["context"]
        )
        return Response({"title": title, "body": body})


ROUTES = [
    ("notifications", NotificationViewSet, "notification"),
    ("notification-preferences", NotificationPreferenceViewSet, "notification-preference"),
    ("notification-templates", NotificationTemplateViewSet, "notification-template"),
]
