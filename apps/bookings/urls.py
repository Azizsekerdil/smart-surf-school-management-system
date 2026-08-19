from __future__ import annotations

from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    # --- calendar (the module's landing screen, linked from the sidebar) ----
    path("", views.BookingCalendarView.as_view(), name="calendar"),
    path("schedule/", views.DailyScheduleView.as_view(), name="schedule"),
    # --- bookings ----------------------------------------------------------
    path("list/", views.BookingListView.as_view(), name="list"),
    path("new/", views.BookingCreateView.as_view(), name="create"),
    path("check/", views.BookingConflictCheckView.as_view(), name="check"),
    # --- HTMX pickers used by the create screen ----------------------------
    path("pick/customers/", views.CustomerSearchView.as_view(), name="pick_customers"),
    path("pick/students/", views.StudentOptionsView.as_view(), name="pick_students"),
    path("pick/lessons/", views.LessonPickerView.as_view(), name="pick_lessons"),
    # --- waiting list ------------------------------------------------------
    path("waitlist/", views.WaitlistListView.as_view(), name="waitlist"),
    path("waitlist/add/", views.WaitlistCreateView.as_view(), name="waitlist_add"),
    path(
        "waitlist/<int:pk>/promote/",
        views.WaitlistPromoteView.as_view(),
        name="waitlist_promote",
    ),
    path(
        "waitlist/<int:pk>/remove/",
        views.WaitlistRemoveView.as_view(),
        name="waitlist_remove",
    ),
    # --- a single booking --------------------------------------------------
    path("<int:pk>/", views.BookingDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.BookingUpdateView.as_view(), name="update"),
    path("<int:pk>/confirm/", views.BookingConfirmView.as_view(), name="confirm"),
    path("<int:pk>/check-in/", views.BookingCheckInView.as_view(), name="check_in"),
    path("<int:pk>/complete/", views.BookingCompleteView.as_view(), name="complete"),
    path("<int:pk>/no-show/", views.BookingNoShowView.as_view(), name="no_show"),
    path("<int:pk>/payment/", views.BookingPaymentView.as_view(), name="payment"),
    path("<int:pk>/cancel/", views.BookingCancelView.as_view(), name="cancel"),
]
