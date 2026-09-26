from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.manuscripts.models import Manuscript

from .models import AgentAdmission, AgentConversation, AgentMessage, AgentRun
from apps.common.telemetry import DEGRADATIONS, event, observe
from .serializers import (
    AgentApprovalSerializer,
    AgentConversationCreateSerializer,
    AgentConversationSerializer,
    AgentMessageCreateSerializer,
    AgentRunSerializer,
)
from .tasks import execute_agent_run


def admission_full():
    AgentAdmission.objects.get_or_create(pk=1)
    AgentAdmission.objects.select_for_update().get(pk=1)
    return AgentRun.objects.filter(status=AgentRun.Status.PROCESSING).count() >= settings.AGENT_MAX_PENDING_RUNS


def overloaded():
    observe(DEGRADATIONS, component='agent_admission', reason='overloaded')
    return Response({'detail': 'Agent capacity is full. Retry later.'}, status=429, headers={'Retry-After': '30'})


def dispatch_run(run, resume=None):
    try:
        if resume is None:
            execute_agent_run.delay(str(run.id))
        else:
            execute_agent_run.delay(str(run.id), resume=resume)
    except Exception as exc:
        now = timezone.now()
        changed = AgentRun.objects.filter(pk=run.pk, status=AgentRun.Status.PROCESSING, lease_token='').update(
            status=AgentRun.Status.FAILED, stop_reason='dispatch_failed',
            error_message='Task dispatch was not confirmed. Inspect this run before submitting again.',
            finished_at=now, updated_at=now,
        )
        if changed:
            run.refresh_from_db()
        observe(DEGRADATIONS, component='broker', reason='dispatch_failed')
        event('agent_dispatch_failed', run_id=str(run.id), error_type=type(exc).__name__)


def run_response(run):
    failed = run.stop_reason == 'dispatch_failed'
    return Response(AgentRunSerializer(run).data, status=503 if failed else 202)


class AgentConversationCreateView(APIView):
    def post(self, request):
        serializer = AgentConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manuscript = None
        manuscript_id = serializer.validated_data.get('manuscript_id')
        if manuscript_id:
            manuscript = get_object_or_404(Manuscript, id=manuscript_id)
        conversation = AgentConversation.objects.create(
            manuscript=manuscript,
            title=serializer.validated_data.get('title') or '编辑助手',
        )
        return Response(AgentConversationSerializer(conversation).data, status=status.HTTP_201_CREATED)


class AgentConversationDetailView(APIView):
    def get(self, request, conversation_id):
        conversation = get_object_or_404(
            AgentConversation.objects.prefetch_related('messages'),
            id=conversation_id,
        )
        return Response(AgentConversationSerializer(conversation).data)


class AgentMessageView(APIView):
    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            AgentConversation.objects.select_related('manuscript'),
            id=conversation_id,
        )
        if not conversation.manuscript:
            return Response(
                {'detail': 'A manuscript must be selected before chatting.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if conversation.runs.filter(
            status__in=[AgentRun.Status.PROCESSING, AgentRun.Status.WAITING_APPROVAL],
        ).exists():
            return Response(
                {'detail': 'This conversation already has an active agent run.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = AgentMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data['content'].strip()
        previous_message = conversation.messages.filter(
            role=AgentMessage.Role.ASSISTANT,
        ).order_by('-created_at').first()
        previous_recommendations = (
            previous_message.metadata.get('recommendations', [])
            if previous_message and isinstance(previous_message.metadata, dict)
            else []
        )
        with transaction.atomic():
            if admission_full():
                return overloaded()
            if conversation.runs.filter(status__in=[AgentRun.Status.PROCESSING, AgentRun.Status.WAITING_APPROVAL]).exists():
                return Response({'detail': 'This conversation already has an active agent run.'}, status=409)
            message = AgentMessage.objects.create(
                conversation=conversation,
                role=AgentMessage.Role.USER,
                content=content,
            )
            run = AgentRun.objects.create(
                conversation=conversation,
                user_input=content,
                status=AgentRun.Status.PROCESSING,
                request_id=getattr(request, 'request_id', ''),
                trace_id=getattr(request, 'trace_id', ''),
                runtime_context={
                    'input_message_id': str(message.id),
                    'previous_recommendations': previous_recommendations,
                },
            )
            transaction.on_commit(lambda: dispatch_run(run))
        return run_response(run)


class AgentRunDetailView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(AgentRun, id=run_id)
        return Response(AgentRunSerializer(run).data)


class AgentRunApprovalView(APIView):
    def post(self, request, run_id):
        serializer = AgentApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            if admission_full():
                return overloaded()
            run = get_object_or_404(
                AgentRun.objects.select_for_update(),
                id=run_id,
                status=AgentRun.Status.WAITING_APPROVAL,
            )
            values = serializer.validated_data
            resume = {
                'approved': values['approved'],
                'approved_call_ids': values['approved_call_ids'],
            }
            run.status = AgentRun.Status.PROCESSING
            run.pending_approval = {}
            run.queued_at = timezone.now()
            run.started_at = None
            run.finished_at = None
            run.execution_seconds = None
            run.queue_wait_seconds = None
            run.runtime_context = {**run.runtime_context, 'resume_payload': resume}
            run.save(update_fields=['status', 'pending_approval', 'queued_at', 'started_at', 'finished_at', 'execution_seconds', 'queue_wait_seconds', 'runtime_context', 'updated_at'])
            transaction.on_commit(lambda: dispatch_run(run, resume=resume))
        return run_response(run)
