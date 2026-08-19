"""REST API for users and roles."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .constants import MODULE_LABELS, MODULES, ROLE_CAPABILITIES, Role
from .permissions import CapabilityViewSetMixin

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(source="get_display_name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "role",
            "role_label",
            "phone",
            "job_title",
            "employee_id",
            "language",
            "is_active",
            "last_login",
            "last_seen_at",
            "capabilities",
        ]
        read_only_fields = ["id", "last_login", "last_seen_at"]

    def get_capabilities(self, obj) -> list[str]:
        return sorted(obj.get_capabilities())


class UserWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=10)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "phone",
            "job_title",
            "employee_id",
            "language",
            "is_active",
            "password",
            "extra_capabilities",
            "denied_capabilities",
        ]

    def validate_role(self, value):
        request = self.context.get("request")
        if (
            value == Role.SUPER_ADMIN
            and request is not None
            and not getattr(request.user, "is_super_admin", False)
        ):
            raise serializers.ValidationError(
                "Only a Super Admin may assign the Super Admin role."
            )
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            # An operator creating an account through the API gets the same
            # password rules as a user changing their own: length, common-password
            # and similarity checks, plus the refusal of the documented
            # first-run default. Skipping them here would have made the API the
            # easy way around every one of them.
            validate_password(password, user)
            user.set_password(password)
        else:
            user.set_unusable_password()
            user.must_change_password = True
            # An operator-issued temporary password never reinstates the
            # first-run bootstrap state.
            user.is_bootstrap_account = False
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            validate_password(password, instance)
            instance.set_password(password)
        instance.save()
        return instance


class UserViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD for application users."""

    capability_prefix = "accounts"
    capability_overrides = {"me": "dashboard.view", "capabilities": "dashboard.view"}
    queryset = User.objects.all().order_by("first_name", "last_name")
    filterset_fields = ["role", "is_active", "language"]
    search_fields = ["username", "email", "first_name", "last_name", "employee_id"]
    ordering_fields = ["first_name", "last_name", "date_joined", "last_login"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return UserWriteSerializer
        return UserSerializer

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Current user with the effective capability set."""
        return Response(UserSerializer(request.user).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def capabilities(self, request):
        """The full role/capability matrix — used by the role matrix screen."""
        return Response(
            {
                "modules": [
                    {"key": m, "label": str(MODULE_LABELS.get(m, m))} for m in MODULES
                ],
                "roles": [
                    {
                        "key": value,
                        "label": str(label),
                        "capabilities": sorted(ROLE_CAPABILITIES.get(value, frozenset())),
                    }
                    for value, label in Role.choices
                ],
            }
        )

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response(
                {"error": {"type": "validation_error", "message": "You cannot delete yourself.", "detail": {}}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Deactivate rather than destroy: user rows are referenced by audit history.
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


ROUTES = [
    ("users", UserViewSet, "user"),
]
