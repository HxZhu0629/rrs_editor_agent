from django.urls import path

from .views import (
    AgentConversationCreateView,
    AgentConversationDetailView,
    AgentMessageView,
    AgentRunApprovalView,
    AgentRunDetailView,
)


urlpatterns = [
    path('conversations/', AgentConversationCreateView.as_view(), name='agent-conversation-create'),
    path('conversations/<uuid:conversation_id>/', AgentConversationDetailView.as_view(), name='agent-conversation-detail'),
    path('conversations/<uuid:conversation_id>/messages/', AgentMessageView.as_view(), name='agent-message'),
    path('runs/<uuid:run_id>/', AgentRunDetailView.as_view(), name='agent-run-detail'),
    path('runs/<uuid:run_id>/approval/', AgentRunApprovalView.as_view(), name='agent-run-approval'),
]
