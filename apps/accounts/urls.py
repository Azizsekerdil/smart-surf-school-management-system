from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # --- authentication ---------------------------------------------------
    path("login/", views.SurfLoginView.as_view(), name="login"),
    path("logout/", views.SurfLogoutView.as_view(), name="logout"),
    path("locked/", views.LockoutView.as_view(), name="lockout"),
    path("password-reset/", views.SurfPasswordResetView.as_view(), name="password_reset"),
    path(
        "password-reset/sent/",
        views.SurfPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        views.SurfPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        views.SurfPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # --- self service -----------------------------------------------------
    path("profile/", views.profile_view, name="profile"),
    path("profile/password/", views.change_password_view, name="change_password"),
    # --- administration ---------------------------------------------------
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_update"),
    path(
        "users/<int:pk>/permissions/",
        views.UserCapabilityView.as_view(),
        name="user_capabilities",
    ),
    path("roles/", views.RoleMatrixView.as_view(), name="role_matrix"),
]
