from __future__ import annotations

from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    # --- assistant --------------------------------------------------------
    path("", views.ChatView.as_view(), name="chat"),
    path("send/", views.chat_send, name="chat_send"),
    path("conversations/<int:pk>/delete/", views.conversation_delete, name="conversation_delete"),
    # --- control center ---------------------------------------------------
    path("control-center/", views.ControlCenterView.as_view(), name="control_center"),
    path("control-center/test/<str:name>/", views.test_provider, name="test_provider"),
    path("control-center/probe/<str:name>/", views.probe_models, name="probe_models"),
    path("control-center/<str:name>/settings/", views.ProviderConfigView.as_view(), name="provider_config"),
    path("routing-mode/", views.set_routing_mode, name="set_routing_mode"),
    # --- usage ------------------------------------------------------------
    path("usage/", views.UsageView.as_view(), name="usage"),
    # --- knowledge base ---------------------------------------------------
    path("knowledge/", views.KnowledgeListView.as_view(), name="knowledge_list"),
    path("knowledge/new/", views.KnowledgeCreateView.as_view(), name="knowledge_create"),
    path("knowledge/<int:pk>/edit/", views.KnowledgeUpdateView.as_view(), name="knowledge_update"),
    path("knowledge/<int:pk>/delete/", views.KnowledgeDeleteView.as_view(), name="knowledge_delete"),
    path("knowledge/reindex/", views.reindex_knowledge, name="knowledge_reindex"),
    path("knowledge/search/", views.search_knowledge, name="knowledge_search"),
]
