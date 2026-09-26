import math
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.common.telemetry import AGENT_LATENCY, AGENT_RUNS, QUEUE_LATENCY, SPAN_LATENCY, MODEL_TOKENS, event, observe, request_context

from .checkpointer import DjangoCheckpointSaver
from .graph import run_editor_agent
from .models import AgentMessage, AgentRun
from .observability import flush_langfuse


def _interrupt_payload(state) -> dict:
    values = []
    for item in state.get('__interrupt__', ()):
        value = getattr(item, 'value', item)
        values.append(value if isinstance(value, dict) else {'value': value})
    return {'interrupts': values} if values else {}


def _persist_terminal_message(run: AgentRun, state) -> None:
    context = dict(run.runtime_context or {})
    if context.get('assistant_message_id'):
        return
    result = state.get('result', {})
    message = AgentMessage.objects.create(
        conversation=run.conversation,
        role=AgentMessage.Role.ASSISTANT,
        content=run.answer,
        metadata={
            'run_id': str(run.id),
            'plan': run.plan,
            'tool_trace': run.tool_trace,
            'recommendations': result.get('recommendations', state.get('recommendations', [])),
            'email_drafts': result.get('email_drafts', state.get('email_drafts', [])),
            'active_skills': state.get('active_skills', []),
            'stop_reason': run.stop_reason,
        },
    )
    context['assistant_message_id'] = str(message.id)
    run.runtime_context = context


@shared_task(bind=True)
def execute_agent_run(self, run_id: str, resume: dict = None):
    now = timezone.now()
    lease_token = uuid4().hex
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().get(id=run_id)
        if run.status in {AgentRun.Status.SUCCESS, AgentRun.Status.FAILED, AgentRun.Status.WAITING_APPROVAL}:
            return {'run_id': run_id, 'status': run.status}
        if run.lease_token:
            return {'run_id': run_id, 'status': 'duplicate_suppressed'}
        if (now - run.queued_at).total_seconds() > settings.AGENT_QUEUE_TIMEOUT:
            run.status = AgentRun.Status.FAILED
            run.stop_reason = 'queue_timeout'
            run.finished_at = now
            run.save(update_fields=['status', 'stop_reason', 'finished_at', 'updated_at'])
            observe(AGENT_RUNS, outcome='queue_timeout')
            return {'run_id': run_id, 'status': run.status}
        run.status = AgentRun.Status.PROCESSING
        run.celery_task_id = str(self.request.id or '')
        run.error_message = ''
        run.started_at = now
        run.lease_token = lease_token
        run.lease_until = now + timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT + 30)
        run.queue_wait_seconds = max(0, (now - run.queued_at).total_seconds())
        run.save(update_fields=['status', 'celery_task_id', 'error_message', 'started_at', 'lease_token', 'lease_until', 'queue_wait_seconds', 'updated_at'])
    token = request_context.set({'request_id': run.request_id, 'trace_id': run.trace_id, 'run_id': str(run.id), 'task_id': run.celery_task_id})
    observe(QUEUE_LATENCY, run.queue_wait_seconds)
    event('agent_started', queue_wait_seconds=run.queue_wait_seconds, langfuse_trace_id=run.id.hex)
    started = perf_counter()
    previous_trace_length = len(run.tool_trace)
    outcome = 'exception'
    try:
        context = run.runtime_context or {}
        resume = context.get('resume_payload', resume)
        input_message = AgentMessage.objects.get(id=context['input_message_id'])
        history = list(
            run.conversation.messages.filter(created_at__lt=input_message.created_at)
            .order_by('created_at')
            .values('role', 'content')
        )
        checkpointer = DjangoCheckpointSaver()
        checkpoint_config = {'configurable': {'thread_id': str(run.id)}}
        continue_existing = resume is None and checkpointer.get_tuple(checkpoint_config) is not None
        state = run_editor_agent(
            run.user_input,
            run.conversation.manuscript,
            previous_recommendations=context.get('previous_recommendations', []),
            history=history,
            editor_context=run.conversation.context,
            thread_id=str(run.id),
            checkpointer=checkpointer,
            resume=resume,
            continue_existing=continue_existing,
            session_id=str(run.conversation_id),
        )
        pending = _interrupt_payload(state)
        with transaction.atomic():
            run = AgentRun.objects.select_for_update().get(id=run_id)
            if run.status in {AgentRun.Status.SUCCESS, AgentRun.Status.FAILED} or run.lease_token != lease_token:
                outcome = 'superseded'
                return {'run_id': run_id, 'status': run.status}
            run.plan = state.get('plan', {})
            run.tool_trace = state.get('tool_trace', [])
            run.answer = state.get('answer', '')
            run.stop_reason = str(state.get('stop_reason') or '')
            run.turn_count = state.get('turn_count', 0)
            run.tool_call_count = state.get('tool_call_count', 0)
            run.pending_approval = pending
            run.runtime_context.pop('resume_payload', None)
            if pending:
                run.status = AgentRun.Status.WAITING_APPROVAL
            elif run.stop_reason in {'provider_error', 'tool_error'}:
                run.status = AgentRun.Status.FAILED
            else:
                run.status = AgentRun.Status.SUCCESS
                _persist_terminal_message(run, state)
            outcome = 'waiting_approval' if pending else ('completed' if run.stop_reason == 'completed' else 'incomplete')
            run.finished_at = timezone.now()
            run.execution_seconds = perf_counter() - started
            run.lease_token = ''
            run.lease_until = None
            run.save(update_fields=[
                'status', 'plan', 'tool_trace', 'answer', 'stop_reason', 'turn_count',
                'tool_call_count', 'pending_approval', 'runtime_context', 'updated_at',
                'finished_at', 'execution_seconds', 'lease_token', 'lease_until',
            ])
        for index, span in enumerate(run.tool_trace[previous_trace_length:], start=previous_trace_length):
            event('agent_span', span_id=str(index), parent_span_id=str(run.id),
                  stage=span.get('event', 'tool'), tool=span.get('tool'), status=span.get('status'), duration_ms=span.get('latency_ms'))
            kind = 'tool' if span.get('tool') else 'model'
            span_status = 'success' if span.get('status') == 'success' else 'error'
            if isinstance(span.get('latency_ms'), (float, int)) and math.isfinite(span['latency_ms']) and span['latency_ms'] >= 0:
                observe(SPAN_LATENCY, span['latency_ms'] / 1000, kind=kind, status=span_status)
            usage = span['usage'] if isinstance(span.get('usage'), dict) else {}
            for direction in ('input', 'output'):
                count = usage.get(f'{direction}_tokens')
                if isinstance(count, (int, float)) and math.isfinite(count) and count >= 0:
                    observe(MODEL_TOKENS, count, direction=direction)
        return {'run_id': run_id, 'status': run.status}
    except Exception as exc:
        with transaction.atomic():
            run = AgentRun.objects.select_for_update().get(id=run_id)
            if run.status not in {AgentRun.Status.SUCCESS, AgentRun.Status.FAILED} and run.lease_token == lease_token:
                run.status = AgentRun.Status.FAILED
                run.error_message = f'{type(exc).__name__}: execution failed; use request_id/run_id to investigate.'
                run.stop_reason = 'execution_error'
                run.finished_at = timezone.now()
                run.execution_seconds = perf_counter() - started
                run.lease_token = ''
                run.lease_until = None
                run.save(update_fields=['status', 'error_message', 'stop_reason', 'finished_at', 'execution_seconds', 'lease_token', 'lease_until', 'updated_at'])
        event('agent_failed', error_type=type(exc).__name__)
        raise
    finally:
        observe(AGENT_LATENCY, perf_counter() - started)
        observe(AGENT_RUNS, outcome=outcome)
        event('agent_finished', outcome=outcome, duration_ms=round((perf_counter() - started) * 1000, 2))
        flush_langfuse()
        request_context.reset(token)


@shared_task
def reconcile_agent_runs():
    now = timezone.now()
    stale = AgentRun.objects.filter(status=AgentRun.Status.PROCESSING).filter(
        Q(lease_token='', queued_at__lt=now - timedelta(seconds=settings.AGENT_QUEUE_TIMEOUT))
        | Q(lease_until__lt=now),
    )
    count = 0
    for run_id in stale.values_list('id', flat=True):
        with transaction.atomic():
            run = AgentRun.objects.select_for_update().get(pk=run_id)
            expired = run.lease_until and run.lease_until < now
            queued = not run.lease_token and run.queued_at < now - timedelta(seconds=settings.AGENT_QUEUE_TIMEOUT)
            if run.status != AgentRun.Status.PROCESSING or not (expired or queued):
                continue
            run.status = AgentRun.Status.FAILED
            run.stop_reason = 'execution_timeout' if expired else 'queue_timeout'
            run.error_message = 'Execution deadline exceeded; inspect checkpoints and side effects before retrying.'
            run.finished_at = now
            run.lease_token = ''
            run.lease_until = None
            run.save(update_fields=['status', 'stop_reason', 'error_message', 'finished_at', 'lease_token', 'lease_until', 'updated_at'])
            count += 1
            observe(AGENT_RUNS, outcome=run.stop_reason)
            event('agent_reconciled', run_id=str(run.id), request_id=run.request_id, trace_id=run.trace_id, outcome=run.stop_reason)
    return count
