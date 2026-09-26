from django.db import models
from django.utils import timezone

from apps.common.models import UUIDTimestampModel
from apps.manuscripts.models import Manuscript


class AgentConversation(UUIDTimestampModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CLOSED = 'closed', 'Closed'

    manuscript = models.ForeignKey(
        Manuscript,
        on_delete=models.CASCADE,
        related_name='agent_conversations',
        blank=True,
        null=True,
        default=None,
    )
    title = models.CharField(max_length=255, blank=True, default='编辑助手')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'agent_conversations'
        ordering = ['-created_at']


class AgentMessage(UUIDTimestampModel):
    class Role(models.TextChoices):
        USER = 'user', 'User'
        ASSISTANT = 'assistant', 'Assistant'
        TOOL = 'tool', 'Tool'
        SYSTEM = 'system', 'System'

    conversation = models.ForeignKey(AgentConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'agent_messages'
        ordering = ['created_at']


class AgentRun(UUIDTimestampModel):
    class Status(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        WAITING_APPROVAL = 'waiting_approval', 'Waiting approval'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    conversation = models.ForeignKey(AgentConversation, on_delete=models.CASCADE, related_name='runs')
    user_input = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    plan = models.JSONField(default=dict, blank=True)
    tool_trace = models.JSONField(default=list, blank=True)
    answer = models.TextField(blank=True, default='')
    stop_reason = models.CharField(max_length=32, blank=True, default='')
    turn_count = models.PositiveSmallIntegerField(default=0)
    tool_call_count = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    pending_approval = models.JSONField(default=dict, blank=True)
    runtime_context = models.JSONField(default=dict, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True, default='')
    request_id = models.CharField(max_length=64, blank=True, default='')
    trace_id = models.CharField(max_length=32, blank=True, default='')
    queued_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=32, blank=True, default='')
    queue_wait_seconds = models.FloatField(null=True, blank=True)
    execution_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'agent_runs'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'queued_at'], name='agent_status_queue_idx')]


class AgentAdmission(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)


class AgentCheckpoint(models.Model):
    thread_id = models.CharField(max_length=64, db_index=True)
    checkpoint_ns = models.CharField(max_length=255, blank=True, default='')
    checkpoint_id = models.CharField(max_length=128)
    parent_checkpoint_id = models.CharField(max_length=128, blank=True, default='')
    checkpoint_type = models.CharField(max_length=64)
    checkpoint_data = models.BinaryField()
    metadata_type = models.CharField(max_length=64)
    metadata_data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agent_checkpoints'
        constraints = [
            models.UniqueConstraint(
                fields=['thread_id', 'checkpoint_ns', 'checkpoint_id'],
                name='unique_agent_checkpoint',
            ),
        ]
        ordering = ['-checkpoint_id']


class AgentCheckpointWrite(models.Model):
    thread_id = models.CharField(max_length=64, db_index=True)
    checkpoint_ns = models.CharField(max_length=255, blank=True, default='')
    checkpoint_id = models.CharField(max_length=128)
    task_id = models.CharField(max_length=255)
    write_index = models.IntegerField()
    channel = models.CharField(max_length=255)
    value_type = models.CharField(max_length=64)
    value_data = models.BinaryField()
    task_path = models.CharField(max_length=512, blank=True, default='')

    class Meta:
        db_table = 'agent_checkpoint_writes'
        constraints = [
            models.UniqueConstraint(
                fields=['thread_id', 'checkpoint_ns', 'checkpoint_id', 'task_id', 'write_index'],
                name='unique_agent_checkpoint_write',
            ),
        ]
