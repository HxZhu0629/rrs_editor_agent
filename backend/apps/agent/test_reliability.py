from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.manuscripts.models import Manuscript
from .models import AgentAdmission, AgentConversation, AgentMessage, AgentRun
from .tasks import execute_agent_run, reconcile_agent_runs


class AgentReliabilityTests(TestCase):
    def setUp(self):
        self.conversation = AgentConversation.objects.create(manuscript=Manuscript.objects.create(title='Reliability fixture'))
        self.message = AgentMessage.objects.create(conversation=self.conversation, role='user', content='test')

    def new_run(self, **values):
        return AgentRun.objects.create(conversation=self.conversation, user_input='test',
                                       runtime_context={'input_message_id': str(self.message.id)}, **values)

    @override_settings(AGENT_MAX_PENDING_RUNS=1)
    @patch('apps.agent.views.execute_agent_run.delay')
    def test_admission_rejects_without_creating_or_publishing(self, delay):
        other = AgentConversation.objects.create(manuscript=self.conversation.manuscript)
        AgentRun.objects.create(conversation=other, user_input='busy')
        response = self.client.post(reverse('agent-message', args=[self.conversation.id]), {'content': 'new'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '30')
        self.assertEqual(AgentRun.objects.count(), 1)
        delay.assert_not_called()

    @patch('apps.agent.tasks.run_editor_agent')
    def test_live_lease_suppresses_duplicate_execution(self, graph):
        run = self.new_run(lease_token='owned', lease_until=timezone.now() + timedelta(seconds=120))
        result = execute_agent_run.run(str(run.id))
        self.assertEqual(result['status'], 'duplicate_suppressed')
        graph.assert_not_called()

    @patch('apps.agent.tasks.run_editor_agent', side_effect=TimeoutError('secret-provider-url'))
    def test_failure_is_persisted_without_leaking_provider_exception(self, graph):
        run = self.new_run(request_id='request-1', trace_id='a' * 32)
        with self.assertRaises(TimeoutError):
            execute_agent_run.run(str(run.id))
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.lease_token, '')
        self.assertIsNotNone(run.execution_seconds)
        self.assertNotIn('secret', run.error_message)

    @patch('apps.agent.tasks.run_editor_agent')
    def test_expired_queue_is_not_executed(self, graph):
        run = self.new_run(queued_at=timezone.now() - timedelta(hours=1))
        execute_agent_run.run(str(run.id))
        run.refresh_from_db()
        self.assertEqual(run.stop_reason, 'queue_timeout')
        graph.assert_not_called()

    def test_reconciliation_expires_abandoned_work_and_preserves_approval(self):
        now = timezone.now()
        expired = self.new_run(lease_token='lost-worker', lease_until=now - timedelta(seconds=1))
        waiting = self.new_run(status='waiting_approval', queued_at=now - timedelta(days=1))
        self.assertEqual(reconcile_agent_runs(), 1)
        self.assertEqual(reconcile_agent_runs(), 0)
        expired.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(expired.stop_reason, 'execution_timeout')
        self.assertEqual(waiting.status, 'waiting_approval')

    @patch('apps.agent.tasks.run_editor_agent')
    def test_late_duplicate_cannot_resume_another_approval(self, graph):
        run = self.new_run(status='waiting_approval')
        execute_agent_run.run(str(run.id), resume={'approved': True})
        graph.assert_not_called()

    @patch('apps.agent.tasks.run_editor_agent', return_value={'stop_reason': 'completed', 'answer': 'ok'})
    def test_durable_approval_payload_survives_redelivery_without_arguments(self, graph):
        run = self.new_run()
        run.runtime_context['resume_payload'] = {'approved': False, 'approved_call_ids': ['call-1']}
        run.save(update_fields=['runtime_context'])
        execute_agent_run.run(str(run.id))
        self.assertEqual(graph.call_args.kwargs['resume'], {'approved': False, 'approved_call_ids': ['call-1']})
        run.refresh_from_db()
        self.assertNotIn('resume_payload', run.runtime_context)

    def test_evaluation_snapshot_reports_measured_segment_latency(self):
        from apps.common.quality_collection import snapshot_agent
        run = self.new_run(execution_seconds=2, queue_wait_seconds=1)
        snapshot = snapshot_agent(run)
        self.assertEqual(snapshot['latency_ms'], 3000)
        self.assertEqual(snapshot['timing_scope'], 'latest_execution_segment_including_queue')


class DispatchTransactionTests(TransactionTestCase):
    @patch('apps.agent.views.execute_agent_run.delay', side_effect=ConnectionError('broker-secret'))
    def test_dispatch_failure_returns_503_and_leaves_no_processing_run(self, delay):
        conversation = AgentConversation.objects.create(manuscript=Manuscript.objects.create(title='Dispatch fixture'))
        response = self.client.post(reverse('agent-message', args=[conversation.id]), {'content': 'test'}, HTTP_X_REQUEST_ID='submit-1')
        self.assertEqual(response.status_code, 503)
        run = AgentRun.objects.get()
        self.assertEqual(run.stop_reason, 'dispatch_failed')
        self.assertEqual(run.request_id, 'submit-1')
        self.assertEqual(AgentRun.objects.filter(status='processing').count(), 0)
        self.assertNotIn('secret', response.content.decode())


@skipUnlessDBFeature('has_select_for_update')
class AdmissionConcurrencyTests(TransactionTestCase):
    @override_settings(AGENT_MAX_PENDING_RUNS=1)
    @patch('apps.agent.views.execute_agent_run.delay')
    def test_parallel_submissions_cannot_exceed_global_capacity(self, delay):
        manuscript = Manuscript.objects.create(title='Concurrency fixture')
        conversation_ids = [AgentConversation.objects.create(manuscript=manuscript).id for _ in range(2)]
        AgentAdmission.objects.create(pk=1)
        barrier = Barrier(2)

        def submit(conversation_id):
            try:
                barrier.wait(timeout=10)
                return Client().post(reverse('agent-message', args=[conversation_id]), {'content': 'test'}).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, conversation_ids))
        self.assertEqual(sorted(statuses), [202, 429])
        self.assertEqual(AgentRun.objects.count(), 1)
        delay.assert_called_once()

    @patch('apps.agent.tasks.run_editor_agent')
    def test_parallel_deliveries_execute_graph_once(self, graph):
        conversation = AgentConversation.objects.create(manuscript=Manuscript.objects.create(title='Lease fixture'))
        message = AgentMessage.objects.create(conversation=conversation, role='user', content='test')
        run = AgentRun.objects.create(conversation=conversation, user_input='test', runtime_context={'input_message_id': str(message.id)})
        entered = Event()
        release = Event()

        def graph_result(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError('Test synchronization failed')
            return {'answer': 'ok', 'stop_reason': 'completed'}

        def execute():
            try:
                return execute_agent_run.run(str(run.id))
            finally:
                connections.close_all()

        graph.side_effect = graph_result
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(execute)
            try:
                self.assertTrue(entered.wait(timeout=10))
                second = pool.submit(execute)
                self.assertEqual(second.result(timeout=10)['status'], 'duplicate_suppressed')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)['status'], 'success')
        graph.assert_called_once()
