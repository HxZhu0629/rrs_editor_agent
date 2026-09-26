from rest_framework import serializers

from .models import AgentConversation, AgentMessage, AgentRun


class AgentMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMessage
        fields = ['id', 'role', 'content', 'metadata', 'created_at']


class AgentRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRun
        fields = [
            'id', 'status', 'user_input', 'plan', 'tool_trace', 'answer', 'error_message',
            'stop_reason', 'turn_count', 'tool_call_count', 'pending_approval',
            'created_at', 'updated_at',
            'request_id', 'trace_id', 'queued_at', 'started_at', 'finished_at',
            'queue_wait_seconds', 'execution_seconds',
        ]


class AgentConversationSerializer(serializers.ModelSerializer):
    messages = AgentMessageSerializer(many=True, read_only=True)
    latest_run = serializers.SerializerMethodField()

    def get_latest_run(self, conversation):
        runs = conversation.runs.order_by('-created_at', '-updated_at', '-id')
        run = runs.filter(status__in=['processing', 'waiting_approval']).first() or runs.first()
        return AgentRunSerializer(run).data if run else None

    class Meta:
        model = AgentConversation
        fields = ['id', 'manuscript', 'title', 'status', 'context', 'messages', 'latest_run', 'created_at', 'updated_at']


class AgentConversationCreateSerializer(serializers.Serializer):
    manuscript_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)


class AgentMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000, allow_blank=False)


class AgentApprovalSerializer(serializers.Serializer):
    approved = serializers.BooleanField(required=False, default=False)
    approved_call_ids = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        default=list,
    )
