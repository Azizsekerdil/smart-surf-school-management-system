"""User model and role-based access control."""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.validators import phone_validator, validate_image_upload

from .constants import (
    BASE_CAPABILITIES,
    EXTERNAL_ROLES,
    STAFF_ROLES,
    Role,
    capabilities_for,
)


class UserManager(DjangoUserManager):
    """Manager that normalises e-mail and keeps role/staff flags consistent."""

    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.RECEPTION)
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return super().create_superuser(username, email, password, **extra_fields)

    def staff_members(self):
        return self.get_queryset().filter(role__in=STAFF_ROLES, is_active=True)


class User(AbstractUser):
    """Application user.

    A user has exactly one primary :class:`~apps.accounts.constants.Role`.
    Fine-grained exceptions are expressed with ``extra_capabilities`` (grants)
    and ``denied_capabilities`` (revocations), which are applied on top of the
    role matrix. Denials always win.
    """

    class Language(models.TextChoices):
        TURKISH = "tr", _("Türkçe")
        ENGLISH = "en", _("English")

    email = models.EmailField(_("e-mail address"), unique=True)
    role = models.CharField(
        _("role"),
        max_length=32,
        choices=Role.choices,
        default=Role.RECEPTION,
        db_index=True,
    )
    phone = models.CharField(_("phone"), max_length=25, blank=True, validators=[phone_validator])
    photo = models.ImageField(
        _("photo"),
        upload_to="users/photos/%Y/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    employee_id = models.CharField(_("employee ID"), max_length=30, blank=True)
    language = models.CharField(
        _("interface language"), max_length=5, choices=Language.choices, default=Language.TURKISH
    )
    job_title = models.CharField(_("job title"), max_length=100, blank=True)

    #: Capabilities granted in addition to the role matrix.
    extra_capabilities = models.JSONField(_("extra capabilities"), default=list, blank=True)
    #: Capabilities revoked even though the role would grant them.
    denied_capabilities = models.JSONField(_("denied capabilities"), default=list, blank=True)

    must_change_password = models.BooleanField(_("must change password"), default=False)
    #: True only while this account still holds the documented first-run
    #: password. Cleared permanently the first time the password changes; see
    #: :mod:`apps.accounts.bootstrap`.
    is_bootstrap_account = models.BooleanField(
        _("first-run bootstrap account"), default=False
    )
    last_seen_at = models.DateTimeField(_("last seen"), null=True, blank=True)
    notes = models.TextField(_("internal notes"), blank=True)

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["first_name", "last_name", "username"]
        indexes = [
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.get_display_name()

    # -- naming ------------------------------------------------------------
    def get_display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.username

    @property
    def initials(self) -> str:
        first = (self.first_name or self.username or "?")[:1]
        last = (self.last_name or "")[:1]
        return (first + last).upper()

    # -- role helpers ------------------------------------------------------
    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN or self.is_superuser

    @property
    def is_staff_member(self) -> bool:
        """True for school personnel (distinct from Django's ``is_staff``)."""
        return self.role in STAFF_ROLES

    @property
    def is_external(self) -> bool:
        return self.role in EXTERNAL_ROLES

    @property
    def role_label(self) -> str:
        return self.get_role_display()

    # -- capabilities ------------------------------------------------------
    def get_capabilities(self) -> frozenset[str]:
        """Effective capability set: role matrix + grants − denials."""
        if self.is_super_admin:
            from .constants import all_capabilities

            return all_capabilities()

        caps = set(BASE_CAPABILITIES) | set(capabilities_for(self.role))
        caps |= {str(c) for c in (self.extra_capabilities or [])}
        caps -= {str(c) for c in (self.denied_capabilities or [])}
        return frozenset(caps)

    def has_capability(self, capability: str) -> bool:
        """Return True when the user holds *capability* (e.g. ``"bookings.add"``)."""
        if not self.is_active:
            return False
        return capability in self.get_capabilities()

    def has_any_capability(self, *capabilities: str) -> bool:
        if not self.is_active:
            return False
        owned = self.get_capabilities()
        return any(c in owned for c in capabilities)

    def has_all_capabilities(self, *capabilities: str) -> bool:
        if not self.is_active:
            return False
        owned = self.get_capabilities()
        return all(c in owned for c in capabilities)

    def can_view_module(self, module: str) -> bool:
        return self.has_capability(f"{module}.view")

    # -- misc --------------------------------------------------------------
    def touch_last_seen(self) -> None:
        User.objects.filter(pk=self.pk).update(last_seen_at=timezone.now())

    def clear_bootstrap_state(self) -> None:
        """Retire the first-run credential. Idempotent.

        Called from every path that sets a new password: the change-password
        screen, the password-reset confirmation and the admin-side reset. Once
        this has run, ``admin`` / ``admin`` is gone for good — the validator
        in :mod:`apps.accounts.validators` refuses to put it back.
        """
        if self.must_change_password or self.is_bootstrap_account:
            self.must_change_password = False
            self.is_bootstrap_account = False
            type(self).objects.filter(pk=self.pk).update(
                must_change_password=False, is_bootstrap_account=False
            )

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        # Keep Django's admin flag aligned with the operational role.
        if self.role == Role.SUPER_ADMIN:
            self.is_staff = True
            self.is_superuser = True
        super().save(*args, **kwargs)


class UserSession(TimeStampedModel):
    """Login history, used by the security screen and the audit log."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sessions", verbose_name=_("user")
    )
    session_key = models.CharField(_("session key"), max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(_("user agent"), max_length=400, blank=True)
    login_at = models.DateTimeField(_("login at"), default=timezone.now, db_index=True)
    logout_at = models.DateTimeField(_("logout at"), null=True, blank=True)
    was_successful = models.BooleanField(_("successful"), default=True)

    class Meta:
        verbose_name = _("user session")
        verbose_name_plural = _("user sessions")
        ordering = ["-login_at"]

    def __str__(self) -> str:
        return f"{self.user} @ {self.login_at:%Y-%m-%d %H:%M}"

    @property
    def is_active_session(self) -> bool:
        return self.logout_at is None


class PasswordResetRequest(TimeStampedModel):
    """Auditable record of password reset requests."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_resets", verbose_name=_("user")
    )
    requested_ip = models.GenericIPAddressField(_("requested from"), null=True, blank=True)
    used_at = models.DateTimeField(_("used at"), null=True, blank=True)

    class Meta:
        verbose_name = _("password reset request")
        verbose_name_plural = _("password reset requests")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"reset({self.user})"
