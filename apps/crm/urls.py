from __future__ import annotations

from django.urls import path

from . import views

app_name = "crm"

urlpatterns = [
    path("", views.CrmDashboardView.as_view(), name="dashboard"),
    # --- leads ------------------------------------------------------------
    path("leads/", views.LeadListView.as_view(), name="lead_list"),
    path("leads/board/", views.LeadBoardView.as_view(), name="lead_board"),
    path("leads/new/", views.LeadCreateView.as_view(), name="lead_create"),
    path("leads/<int:pk>/", views.LeadDetailView.as_view(), name="lead_detail"),
    path("leads/<int:pk>/edit/", views.LeadUpdateView.as_view(), name="lead_update"),
    path("leads/<int:pk>/status/", views.LeadStatusView.as_view(), name="lead_status"),
    path("leads/<int:pk>/convert/", views.LeadConvertView.as_view(), name="lead_convert"),
    path(
        "leads/<int:pk>/interactions/",
        views.LeadInteractionsView.as_view(),
        name="lead_interactions",
    ),
    # --- interactions ------------------------------------------------------
    path("interactions/", views.InteractionListView.as_view(), name="interaction_list"),
    path(
        "interactions/new/",
        views.InteractionCreateView.as_view(),
        name="interaction_create",
    ),
    path(
        "interactions/<int:pk>/follow-up-done/",
        views.FollowUpCompleteView.as_view(),
        name="follow_up_complete",
    ),
    # --- campaigns ---------------------------------------------------------
    path("campaigns/", views.CampaignListView.as_view(), name="campaign_list"),
    path("campaigns/new/", views.CampaignCreateView.as_view(), name="campaign_create"),
    path("campaigns/<int:pk>/", views.CampaignDetailView.as_view(), name="campaign_detail"),
    path(
        "campaigns/<int:pk>/edit/",
        views.CampaignUpdateView.as_view(),
        name="campaign_update",
    ),
    path(
        "campaigns/<int:pk>/status/",
        views.CampaignStatusView.as_view(),
        name="campaign_status",
    ),
    # --- segments ----------------------------------------------------------
    path("segments/", views.SegmentListView.as_view(), name="segment_list"),
    path("segments/new/", views.SegmentCreateView.as_view(), name="segment_create"),
    path("segments/preview/", views.SegmentPreviewView.as_view(), name="segment_preview"),
    path("segments/<int:pk>/", views.SegmentDetailView.as_view(), name="segment_detail"),
    path("segments/<int:pk>/edit/", views.SegmentUpdateView.as_view(), name="segment_update"),
    path(
        "segments/<int:pk>/refresh/",
        views.SegmentRefreshView.as_view(),
        name="segment_refresh",
    ),
]
