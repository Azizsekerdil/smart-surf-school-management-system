from __future__ import annotations

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="list"),
    path("dropdown/", views.NotificationDropdownView.as_view(), name="dropdown"),
    path("preferences/", views.NotificationPreferenceUpdateView.as_view(), name="preferences"),
    path("broadcast/", views.NotificationBroadcastView.as_view(), name="broadcast"),
    path("mark-all-read/", views.NotificationMarkAllReadView.as_view(), name="mark_all_read"),
    path("<int:pk>/read/", views.NotificationMarkReadView.as_view(), name="mark_read"),
    path("<int:pk>/unread/", views.NotificationMarkUnreadView.as_view(), name="mark_unread"),
    path("<int:pk>/open/", views.NotificationOpenView.as_view(), name="open"),
]
