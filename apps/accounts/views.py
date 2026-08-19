"""Authentication and user-administration views."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import DetailView, ListView, TemplateView, UpdateView

from apps.audit.services import record_audit

from .bootstrap import BOOTSTRAP_WARNING
from .constants import Role
from .forms import (
    CapabilityOverrideForm,
    LoginForm,
    ProfileForm,
    SurfPasswordChangeForm,
    SurfSetPasswordForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import User, UserSession
from .permissions import CapabilityRequiredMixin


class SurfLoginView(LoginView):
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # expire at browser close
        else:
            self.request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["school_name"] = settings.SCHOOL["NAME"]
        context["has_any_user"] = User.objects.exists()
        # Drives the first-run notice on the sign-in screen. True only while
        # an untouched bootstrap account exists, so the banner disappears by
        # itself as soon as the password is changed.
        context["bootstrap_pending"] = User.objects.filter(
            is_bootstrap_account=True
        ).exists()
        context["bootstrap_warning"] = BOOTSTRAP_WARNING
        return context


class SurfLogoutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


class SurfPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class SurfPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class SurfPasswordResetConfirmView(PasswordResetConfirmView):
    """Finish a reset — and make sure it cannot resurrect the default.

    A reset chooses a *new* password, which the validators refuse to let be
    the documented first-run one; clearing the bootstrap state here means the
    account also stops being local-device-only once a real password exists.
    """

    template_name = "accounts/password_reset_confirm.html"
    form_class = SurfSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = getattr(form, "user", None)
        if user is not None:
            user.clear_bootstrap_state()
        return response


class SurfPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class LockoutView(TemplateView):
    template_name = "accounts/lockout.html"


# ---------------------------------------------------------------------------
# User administration
# ---------------------------------------------------------------------------
class UserListView(CapabilityRequiredMixin, ListView):
    capability = "accounts.view"
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        queryset = User.objects.all().order_by("first_name", "last_name")
        search = self.request.GET.get("q", "").strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(employee_id__icontains=search)
            )
        role = self.request.GET.get("role", "").strip()
        if role:
            queryset = queryset.filter(role=role)
        status = self.request.GET.get("status", "").strip()
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["roles"] = Role.choices
        context["current_role"] = self.request.GET.get("role", "")
        context["current_status"] = self.request.GET.get("status", "")
        context["search"] = self.request.GET.get("q", "")
        return context

    def get_template_names(self):
        if self.request.htmx:
            return ["accounts/partials/user_table.html"]
        return [self.template_name]


class UserDetailView(CapabilityRequiredMixin, DetailView):
    capability = "accounts.view"
    model = User
    template_name = "accounts/user_detail.html"
    context_object_name = "user_obj"
    slug_field = "public_id"

    def get_object(self, queryset=None):
        return get_object_or_404(User, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_obj = context["user_obj"]
        context["capabilities"] = sorted(user_obj.get_capabilities())
        context["recent_sessions"] = user_obj.sessions.all()[:15]
        return context


class UserCreateView(CapabilityRequiredMixin, TemplateView):
    capability = "accounts.add"
    template_name = "accounts/user_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", UserCreateForm())
        context["title"] = _("New user")
        return context

    def post(self, request, *args, **kwargs):
        form = UserCreateForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            record_audit(
                request,
                action="create",
                instance=user,
                description=_("User %(name)s created with role %(role)s")
                % {"name": user.get_display_name(), "role": user.get_role_display()},
            )
            messages.success(request, _("User “%(name)s” was created.") % {"name": user})
            return redirect("accounts:user_detail", pk=user.pk)
        return self.render_to_response(self.get_context_data(form=form))


class UserUpdateView(CapabilityRequiredMixin, UpdateView):
    capability = "accounts.change"
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    context_object_name = "user_obj"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["editor"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit user")
        return context

    def form_valid(self, form):
        before = User.objects.get(pk=self.object.pk)
        response = super().form_valid(form)
        record_audit(
            self.request,
            action="update",
            instance=self.object,
            changes={
                field: [getattr(before, field, None), getattr(self.object, field, None)]
                for field in form.changed_data
            },
        )
        messages.success(self.request, _("User updated."))
        return response

    def get_success_url(self):
        return reverse_lazy("accounts:user_detail", kwargs={"pk": self.object.pk})


class UserCapabilityView(CapabilityRequiredMixin, TemplateView):
    capability = "accounts.manage"
    template_name = "accounts/user_capabilities.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_obj = get_object_or_404(User, pk=self.kwargs["pk"])
        context["user_obj"] = user_obj
        context.setdefault("form", CapabilityOverrideForm(user=user_obj))
        context["role_capabilities"] = sorted(user_obj.get_capabilities())
        return context

    def post(self, request, *args, **kwargs):
        user_obj = get_object_or_404(User, pk=self.kwargs["pk"])
        form = CapabilityOverrideForm(request.POST, user=user_obj)
        if form.is_valid():
            form.save()
            record_audit(
                request,
                action="permission_change",
                instance=user_obj,
                description=_("Capability overrides updated for %(name)s")
                % {"name": user_obj.get_display_name()},
                changes={
                    "extra_capabilities": [None, user_obj.extra_capabilities],
                    "denied_capabilities": [None, user_obj.denied_capabilities],
                },
            )
            messages.success(request, _("Permissions updated."))
            return redirect("accounts:user_detail", pk=user_obj.pk)
        return self.render_to_response(self.get_context_data(form=form))


class RoleMatrixView(CapabilityRequiredMixin, TemplateView):
    """Read-only overview of which role grants which capability."""

    capability = "accounts.view"
    template_name = "accounts/role_matrix.html"

    def get_context_data(self, **kwargs):
        from .constants import MODULE_LABELS, MODULES, ROLE_CAPABILITIES

        context = super().get_context_data(**kwargs)
        rows = []
        for module in MODULES:
            row = {"module": module, "label": MODULE_LABELS.get(module, module), "roles": []}
            for role_value, role_label in Role.choices:
                caps = ROLE_CAPABILITIES.get(role_value, frozenset())
                module_caps = sorted(c.split(".", 1)[1] for c in caps if c.startswith(f"{module}."))
                row["roles"].append(
                    {"role": role_value, "label": role_label, "actions": module_caps}
                )
            rows.append(row)
        context["rows"] = rows
        context["roles"] = Role.choices
        return context


# ---------------------------------------------------------------------------
# Self service
# ---------------------------------------------------------------------------
@login_required
def profile_view(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            user = form.save()
            request.session["_language"] = user.language
            messages.success(request, _("Your profile was updated."))
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)

    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "capabilities": sorted(request.user.get_capabilities()),
            "sessions": UserSession.objects.filter(user=request.user)[:10],
        },
    )


@login_required
def change_password_view(request):
    if request.method == "POST":
        form = SurfPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            # Retires must_change_password *and* the bootstrap flag, so the
            # documented admin/admin credential dies here and cannot be
            # restored by a later reset.
            user.clear_bootstrap_state()
            record_audit(request, action="password_change", instance=user)
            messages.success(request, _("Your password has been changed."))
            return redirect("accounts:profile")
    else:
        form = SurfPasswordChangeForm(request.user)

    return render(request, "accounts/change_password.html", {"form": form})
