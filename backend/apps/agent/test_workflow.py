from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from langchain_core.messages import AIMessage

from apps.manuscripts.models import Manuscript

from .models import AgentConversation, AgentMessage, AgentRun
from .tasks import execute_agent_run
from .tests import ScriptedModel


class EditorialWorkflowTests(TransactionTestCase):
    @override_settings(LANGFUSE_TRACING_ENABLED=False, AGENT_MAX_TURNS=8)
    @patch('apps.agent.views.execute_agent_run.delay')
    @patch('apps.agent.graph.build_chat_model')
    @patch('apps.agent.tools._recommend_reviewers')
    def test_submit_poll_restore_and_evidence_draft_delivery(self, recommend, build_model, delay):
        manuscript = Manuscript.objects.create(title='RAG editorial fixture', keywords=['retrieval'])
        conversation = AgentConversation.objects.create(manuscript=manuscript)
        recommend.return_value = {'recommendations': [{
            'rank': 1, 'reviewer_id': 'reviewer-fixture', 'reviewer_name': 'Fixture Reviewer',
            'reviewer_email': 'fixture@example.com', 'reviewer_affiliation': 'Fixture Institute',
            'rationale': {'representative_papers': [{'title': 'Evidence Fixture', 'doi': '10.0000/fixture'}]},
        }]}
        build_model.return_value = ScriptedModel([
            AIMessage(content='', tool_calls=[{'id': 'skill', 'name': 'load_editor_skill', 'args': {'skill_name': 'reviewer_selection'}}]),
            AIMessage(content='', tool_calls=[{'id': 'recommend', 'name': 'recommend_reviewers', 'args': {'top_k': 1}}]),
            AIMessage(content='', tool_calls=[{'id': 'draft', 'name': 'draft_reviewer_emails', 'args': {'count': 1}}]),
            AIMessage(content='候选名单及邀请草稿已准备，需人工复核。'),
        ])
        submitted = self.client.post(reverse('agent-message', args=[conversation.id]), {'content': '推荐一位审稿人并准备邀请草稿'})
        self.assertEqual(submitted.status_code, 202)
        run_id = submitted.json()['id']
        self.assertEqual(submitted.json()['status'], 'processing')
        delay.assert_called_once_with(run_id)
        execute_agent_run.run(run_id)
        polled = self.client.get(reverse('agent-run-detail', args=[run_id])).json()
        self.assertEqual(polled['stop_reason'], 'completed')
        self.assertIsNotNone(polled['execution_seconds'])
        restored = self.client.get(reverse('agent-conversation-detail', args=[conversation.id])).json()
        self.assertEqual(restored['latest_run']['id'], run_id)
        result = restored['messages'][-1]['metadata']
        self.assertEqual(result['run_id'], run_id)
        self.assertEqual(result['recommendations'][0]['rationale']['representative_papers'][0]['title'], 'Evidence Fixture')
        self.assertTrue(result['email_drafts'][0]['requires_human_approval'])
        self.assertIn('Evidence Fixture', result['email_drafts'][0]['body'])
        execute_agent_run.run(run_id)
        self.assertEqual(AgentMessage.objects.filter(conversation=conversation, role='assistant').count(), 1)

    def test_restoring_waiting_run_keeps_approval_details_separate_from_old_answer(self):
        conversation = AgentConversation.objects.create(manuscript=Manuscript.objects.create(title='Approval fixture'))
        AgentRun.objects.create(conversation=conversation, user_input='previous', status='success', answer='Old answer')
        pending = {'interrupts': [{'requests': [{'tool': 'fixture_action', 'call_id': 'call-1', 'arguments': {'value': 'review'}}]}]}
        waiting = AgentRun.objects.create(conversation=conversation, user_input='new action', status='waiting_approval', pending_approval=pending)
        restored = self.client.get(reverse('agent-conversation-detail', args=[conversation.id])).json()
        self.assertEqual(restored['latest_run']['id'], str(waiting.id))
        self.assertEqual(restored['latest_run']['pending_approval'], pending)
        self.assertEqual(restored['latest_run']['answer'], '')
