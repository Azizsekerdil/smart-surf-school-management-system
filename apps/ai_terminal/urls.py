from __future__ import annotations

from django.urls import path

from . import views

app_name = "ai_terminal"

urlpatterns = [
    path("", views.ConsoleView.as_view(), name="console"),
    path("run/", views.run_command, name="run_command"),
    path("explain/", views.explain_command, name="explain_command"),
    path("commands/<int:pk>/approve/", views.approve_command, name="approve_command"),
    path("commands/<int:pk>/reject/", views.reject_command, name="reject_command"),
    path("agent/", views.run_agent, name="run_agent"),
    path("proposals/", views.ProposalListView.as_view(), name="proposal_list"),
    path("proposals/<int:pk>/", views.ProposalDetailView.as_view(), name="proposal_detail"),
    path("proposals/<int:pk>/approve/", views.approve_proposal, name="approve_proposal"),
    path("proposals/<int:pk>/reject/", views.reject_proposal, name="reject_proposal"),
    path("proposals/<int:pk>/apply/", views.apply_proposal, name="apply_proposal"),
    path("proposals/<int:pk>/revert/", views.revert_proposal, name="revert_proposal"),
    path("policy/", views.PolicyView.as_view(), name="policy"),
]
