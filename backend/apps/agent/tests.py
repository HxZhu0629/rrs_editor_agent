from types import SimpleNamespace
import tempfile
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from apps.manuscripts.models import Manuscript

from .checkpointer import DjangoCheckpointSaver
from .compaction import ContextCompactionManager, apply_tool_result_budget, message_blocks
from .context import AgentContextManager
from .executor import EditorToolExecutor
from .graph import run_editor_agent
from .memory import LayeredMemoryLoader
from .mcp_client import _decode_call_result
from .models import AgentCheckpoint, AgentConversation, AgentMessage, AgentRun
from .observability import _trace_id, build_langfuse_callback
from .policy import RiskLevel
from .tasks import execute_agent_run
from .tools import EditorTool, EditorToolRegistry, build_editor_tool_registry


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return next(self.responses)


class ActionInput(BaseModel):
    value: str = ''


class ApprovalState(TypedDict, total=False):
    request: str
    approved: bool


def build_approval_graph():
    def request_approval(state: ApprovalState):
        decision = interrupt({'type': 'approval', 'request': state['request']})
        return {'approved': bool(decision.get('approved'))}

    workflow = StateGraph(ApprovalState)
    workflow.add_node('approval', request_approval)
    workflow.set_entry_point('approval')
    workflow.add_edge('approval', END)
    return workflow.compile(checkpointer=DjangoCheckpointSaver())


class AgentCheckpointTests(TransactionTestCase):
    def test_compaction_state_is_saved_in_checkpoint(self):
        manuscript = Manuscript.objects.create(title='Checkpoint context', status=Manuscript.Status.CONFIRMED)
        model = ScriptedModel([AIMessage(content='Done.')])
        thread_id = 'checkpoint-compaction-state-test'
        with patch('apps.agent.graph.build_editor_tool_registry') as build_registry:
            build_registry.return_value = EditorToolRegistry([])
            state = run_editor_agent(
                'Continue.', manuscript, model=model, thread_id=thread_id,
                checkpointer=DjangoCheckpointSaver(),
            )
        self.assertIn('context_budget', state)
        snapshot = DjangoCheckpointSaver().get_tuple(
            {'configurable': {'thread_id': thread_id}}
        )
        self.assertIsNotNone(snapshot)
        checkpoint_state = snapshot.checkpoint.get('channel_values', {})
        self.assertIn('context_budget', checkpoint_state)
        self.assertIn('system_context', checkpoint_state)

    def test_checkpoint_survives_graph_rebuild_and_resumes_interrupt(self):
        thread_id = 'checkpoint-resume-test'
        config = {'configurable': {'thread_id': thread_id}}
        graph = build_approval_graph()
        graph.invoke({'request': 'send invitation'}, config=config)
        snapshot = graph.get_state(config)

        self.assertTrue(snapshot.tasks[0].interrupts)
        self.assertTrue(AgentCheckpoint.objects.filter(thread_id=thread_id).exists())

        resumed = build_approval_graph().invoke(Command(resume={'approved': True}), config=config)
        self.assertTrue(resumed['approved'])

    def test_editor_agent_resumes_same_action_after_approval(self):
        manuscript = Manuscript.objects.create(
            title='Checkpointed action',
            abstract='Approval flow.',
            keywords=['agent'],
            status=Manuscript.Status.CONFIRMED,
        )
        executed = []
        action = StructuredTool.from_function(
            func=lambda value='': executed.append(value) or {'value': value},
            name='send_email',
            description='Send an email',
            args_schema=ActionInput,
        )
        registry = EditorToolRegistry([EditorTool(action, risk_level=RiskLevel.ACTION)])
        model = ScriptedModel([
            AIMessage(content='', tool_calls=[{
                'name': 'send_email',
                'args': {'value': 'invitation'},
                'id': 'call-1',
                'type': 'tool_call',
            }]),
            AIMessage(content='Invitation sent.'),
        ])
        thread_id = 'editor-action-resume-test'

        with patch('apps.agent.graph.build_editor_tool_registry', return_value=registry):
            interrupted = run_editor_agent(
                'Send the invitation.',
                manuscript,
                model=model,
                thread_id=thread_id,
                checkpointer=DjangoCheckpointSaver(),
            )
            paused_again = run_editor_agent(
                'Send the invitation.',
                manuscript,
                model=model,
                thread_id=thread_id,
                checkpointer=DjangoCheckpointSaver(),
                continue_existing=True,
            )
            self.assertIn('__interrupt__', paused_again)
            self.assertEqual(executed, [])
            resumed = run_editor_agent(
                'Send the invitation.',
                manuscript,
                model=model,
                thread_id=thread_id,
                checkpointer=DjangoCheckpointSaver(),
                resume={'approved': True, 'approved_call_ids': ['call-1']},
            )

        self.assertIn('__interrupt__', interrupted)
        self.assertEqual(executed, ['invitation'])
        self.assertEqual(resumed['answer'], 'Invitation sent.')


class AgentRuntimeTests(APITestCase):
    def setUp(self):
        self.manuscript = Manuscript.objects.create(
            title='GraphRAG Reviewer Recommendation',
            abstract='Graph-based retrieval augmented generation.',
            keywords=['GraphRAG', 'RAG'],
            status=Manuscript.Status.CONFIRMED,
        )

    def test_registry_exposes_langchain_schemas(self):
        registry = build_editor_tool_registry(self.manuscript, [])
        self.assertEqual(
            [schema['name'] for schema in registry.schemas()],
            [
                'load_editor_skill',
                'search_manuscript_evidence',
                'search_external_papers',
                'verify_paper_metadata',
                'lookup_author_identity',
                'check_retraction_status',
                'check_submission_completeness',
                'recommend_reviewers',
                'explain_recommendation',
                'draft_reviewer_emails',
            ],
        )
        schema = registry.get('recommend_reviewers').tool.args_schema.model_json_schema()
        self.assertEqual(schema['properties']['top_k']['maximum'], 50)

    @override_settings(LANGFUSE_TRACING_ENABLED=False)
    def test_langfuse_is_optional_and_uses_stable_trace_ids(self):
        run_id = '1020940d-5f49-4892-8194-16fda0cfb106'
        self.assertIsNone(build_langfuse_callback(run_id))
        self.assertEqual(_trace_id(run_id), '1020940d5f494892819416fda0cfb106')
        self.assertIsNone(_trace_id('not-a-uuid'))

    def test_mcp_result_wrapper_is_decoded(self):
        result = SimpleNamespace(
            isError=False,
            structuredContent={
                'result': {
                    'provider': 'OpenAlex',
                    'result_count': 1,
                    'results': [{'title': 'External Evidence'}],
                },
            },
        )
        decoded = _decode_call_result(result)
        self.assertEqual(decoded['provider'], 'OpenAlex')
        self.assertEqual(decoded['result_count'], 1)

    @patch('apps.agent.tools.AcademicMCPClient.search_papers')
    def test_external_paper_search_is_exposed_through_mcp_adapter(self, mock_search):
        mock_search.return_value = {
            'status': 'success',
            'source_protocol': 'mcp',
            'provider': 'OpenAlex',
            'results': [{'title': 'External Evidence', 'doi': 'https://doi.org/10.1/example'}],
        }
        registry = build_editor_tool_registry(self.manuscript, [])
        result = registry.get('search_external_papers').tool.invoke({
            'query': 'retrieval augmented generation',
            'top_k': 3,
            'from_year': 2022,
        })
        self.assertEqual(result['source_protocol'], 'mcp')
        self.assertEqual(result['provider'], 'OpenAlex')
        mock_search.assert_called_once_with(
            query='retrieval augmented generation',
            top_k=3,
            from_year=2022,
            to_year=0,
            open_access_only=False,
        )

    def test_external_paper_search_rejects_inverted_year_range(self):
        registry = build_editor_tool_registry(self.manuscript, [])
        with self.assertRaises(ValueError):
            registry.get('search_external_papers').tool.invoke({
                'query': 'RAG',
                'from_year': 2025,
                'to_year': 2020,
            })

    @patch('apps.agent.tools.AcademicMCPClient.verify_metadata')
    def test_paper_metadata_verification_is_exposed_through_mcp_adapter(self, mock_verify):
        mock_verify.return_value = {
            'status': 'success',
            'source_protocol': 'mcp',
            'provider': 'Crossref',
            'overall_match': True,
        }
        result = build_editor_tool_registry(self.manuscript, []).get(
            'verify_paper_metadata'
        ).tool.invoke({
            'doi': '10.1/example',
            'expected_title': 'Example',
            'expected_year': 2024,
            'expected_authors': ['Author One'],
        })
        self.assertTrue(result['overall_match'])
        mock_verify.assert_called_once_with(
            doi='10.1/example',
            expected_title='Example',
            expected_year=2024,
            expected_authors=['Author One'],
        )

    @patch('apps.agent.tools.AcademicMCPClient.lookup_author')
    def test_orcid_identity_lookup_is_exposed_through_mcp_adapter(self, mock_lookup):
        mock_lookup.return_value = {
            'status': 'success',
            'source_protocol': 'mcp',
            'provider': 'ORCID',
            'requires_editor_confirmation': True,
            'results': [{'orcid': '0000-0001-2345-6789'}],
        }
        result = build_editor_tool_registry(self.manuscript, []).get(
            'lookup_author_identity'
        ).tool.invoke({'name': 'Author One', 'affiliation': 'Example University'})
        self.assertTrue(result['requires_editor_confirmation'])
        mock_lookup.assert_called_once_with(
            name='Author One',
            affiliation='Example University',
            top_k=5,
        )

    @patch('apps.agent.tools.AcademicMCPClient.check_retraction')
    def test_retraction_check_is_exposed_through_mcp_adapter(self, mock_check):
        mock_check.return_value = {
            'status': 'success',
            'source_protocol': 'mcp',
            'providers': ['Crossref', 'OpenAlex'],
            'retraction_status': 'retracted',
            'is_retracted': True,
        }
        result = build_editor_tool_registry(self.manuscript, []).get(
            'check_retraction_status'
        ).tool.invoke({'doi': '10.1/retracted'})
        self.assertTrue(result['is_retracted'])
        mock_check.assert_called_once_with(doi='10.1/retracted')

    def test_skill_registry_exposes_compact_catalog_and_full_instructions_on_demand(self):
        registry = build_editor_tool_registry(self.manuscript, [])
        catalog = registry.skills.catalog()
        self.assertIn('reviewer_selection', catalog)
        self.assertNotIn('Treat conflict filtering as mandatory', catalog)

        result = registry.get('load_editor_skill').tool.invoke({'skill_name': 'reviewer_selection'})
        self.assertEqual(result['status'], 'loaded')
        self.assertIn('recommend_reviewers', result['skill']['recommended_tools'])
        self.assertTrue(any(
            'Treat conflict filtering as mandatory' in instruction
            for instruction in result['skill']['instructions']
        ))

    def test_loaded_skill_is_recorded_in_agent_state(self):
        model = ScriptedModel([
            AIMessage(content='', tool_calls=[{
                'name': 'load_editor_skill',
                'args': {'skill_name': 'submission_preflight'},
                'id': 'skill-1',
                'type': 'tool_call',
            }]),
            AIMessage(content='Preflight complete.'),
        ])
        state = run_editor_agent('Run the submission preflight.', self.manuscript, model=model)
        self.assertEqual(state['active_skills'], ['submission_preflight'])
        self.assertIn('submission_preflight', model.calls[0][0].content)
        self.assertIsInstance(model.calls[1][-1], ToolMessage)

    @patch('apps.agent.tools._recommend_reviewers')
    def test_new_recommendations_are_available_to_later_tools_in_same_run(self, mock_recommend):
        mock_recommend.return_value = {
            'candidate_count': 1,
            'returned_count': 1,
            'recommendations': [{
                'rank': 1,
                'reviewer_id': 'reviewer-1',
                'reviewer_name': 'Reviewer One',
                'reviewer_email': 'reviewer@example.com',
                'rationale': {},
            }],
        }
        registry = build_editor_tool_registry(self.manuscript, [])
        registry.get('recommend_reviewers').tool.invoke({'top_k': 1})
        result = registry.get('draft_reviewer_emails').tool.invoke({'count': 1})
        self.assertEqual(result['email_drafts'][0]['reviewer_name'], 'Reviewer One')

    def test_check_submission_tool_returns_editor_facing_result(self):
        result = build_editor_tool_registry(self.manuscript, []).get(
            'check_submission_completeness'
        ).tool.invoke({})
        self.assertFalse(result['complete'])
        self.assertIn('authors', result['missing_fields'])
        self.assertEqual(result['manuscript']['title'], self.manuscript.title)

    def test_email_tool_only_creates_drafts(self):
        previous = [{
            'rank': 1,
            'reviewer_id': 'reviewer-1',
            'reviewer_name': 'Reviewer One',
            'reviewer_email': 'reviewer@example.com',
            'rationale': {'representative_papers': [{'title': 'GraphRAG Evidence'}]},
        }]
        result = build_editor_tool_registry(self.manuscript, previous).get(
            'draft_reviewer_emails'
        ).tool.invoke({'count': 1})
        self.assertTrue(result['requires_human_approval'])
        self.assertIn('GraphRAG Evidence', result['email_drafts'][0]['body'])

    def test_native_tool_call_round_trip_returns_tool_message_to_model(self):
        model = ScriptedModel([
            AIMessage(content='', tool_calls=[{
                'name': 'check_submission_completeness',
                'args': {},
                'id': 'call-1',
                'type': 'tool_call',
            }]),
            AIMessage(content='The submission is missing required metadata.'),
        ])
        state = run_editor_agent('Check the submission.', self.manuscript, model=model)
        self.assertEqual(state['stop_reason'], 'completed')
        self.assertEqual(state['answer'], 'The submission is missing required metadata.')
        self.assertEqual(state['tool_call_count'], 1)
        self.assertIsInstance(model.calls[1][-1], ToolMessage)
        self.assertEqual(model.calls[1][-1].tool_call_id, 'call-1')

    def test_history_is_injected_into_model_context(self):
        model = ScriptedModel([AIMessage(content='Done.')])
        state = run_editor_agent(
            'Continue.',
            self.manuscript,
            history=[{'role': 'user', 'content': 'Earlier request'}],
            model=model,
        )
        self.assertEqual(state['answer'], 'Done.')
        contents = [message.content for message in model.calls[0]]
        self.assertIn('Earlier request', contents)

    @override_settings(AGENT_MAX_TURNS=1)
    def test_max_turns_stops_before_tool_execution_and_finalizes(self):
        model = ScriptedModel([
            AIMessage(content='', tool_calls=[{
                'name': 'check_submission_completeness', 'args': {}, 'id': 'call-1', 'type': 'tool_call',
            }]),
            AIMessage(content='I could not complete the check within this run.'),
        ])
        state = run_editor_agent('Check it.', self.manuscript, model=model)
        self.assertEqual(state['stop_reason'], 'max_turns')
        self.assertEqual(state['tool_call_count'], 0)
        self.assertEqual(state['answer'], 'I could not complete the check within this run.')
        self.assertEqual(state['tool_trace'][-1]['event'], 'finalize')

    @override_settings(AGENT_MAX_TOOL_CALLS=0)
    def test_tool_budget_is_enforced_and_finalizes(self):
        model = ScriptedModel([
            AIMessage(content='', tool_calls=[{
                'name': 'check_submission_completeness', 'args': {}, 'id': 'call-1', 'type': 'tool_call',
            }]),
            AIMessage(content='The check could not run because the tool budget was exhausted.'),
        ])
        state = run_editor_agent('Check it.', self.manuscript, model=model)
        self.assertEqual(state['stop_reason'], 'tool_budget_exceeded')
        self.assertEqual(state['tool_call_count'], 1)
        self.assertEqual(
            state['answer'],
            'The check could not run because the tool budget was exhausted.',
        )
        self.assertEqual(state['tool_trace'][-1]['event'], 'finalize')

    def test_action_tools_fail_closed_until_approval(self):
        action = StructuredTool.from_function(
            func=lambda value='': {'value': value},
            name='send_email',
            description='Send an email',
            args_schema=ActionInput,
        )
        registry = EditorToolRegistry([EditorTool(action, risk_level=RiskLevel.ACTION)])
        result = EditorToolExecutor(registry).execute_all([{
            'name': 'send_email', 'id': 'call-1', 'args': {'value': 'hello'},
        }])
        self.assertEqual(result.stop_reason, 'approval_required')
        self.assertEqual(result.pending_approval['tool'], 'send_email')

    def test_action_tool_executes_only_after_explicit_approval(self):
        action = StructuredTool.from_function(
            func=lambda value='': {'value': value},
            name='send_email',
            description='Send an email',
            args_schema=ActionInput,
        )
        registry = EditorToolRegistry([EditorTool(action, risk_level=RiskLevel.ACTION)])
        call = {'name': 'send_email', 'id': 'call-1', 'args': {'value': 'hello'}}

        approved = EditorToolExecutor(registry).execute_all(
            [call], approval_decisions={'call-1': True},
        )
        denied = EditorToolExecutor(registry).execute_all(
            [call], approval_decisions={'call-1': False},
        )

        self.assertEqual(approved.results[0]['result'], {'value': 'hello'})
        self.assertEqual(approved.stop_reason, '')
        self.assertEqual(denied.trace[0]['status'], 'denied_by_editor')

    @override_settings(AGENT_MAX_TOOL_RESULT_CHARS=80)
    def test_tool_result_is_bounded(self):
        read_tool = StructuredTool.from_function(
            func=lambda: {'payload': 'x' * 500},
            name='read_data',
            description='Read data',
            args_schema=type('EmptyInput', (BaseModel,), {}),
        )
        registry = EditorToolRegistry([EditorTool(read_tool, risk_level=RiskLevel.READ)])
        result = EditorToolExecutor(registry).execute_all([{
            'name': 'read_data', 'id': 'call-1', 'args': {},
        }])
        self.assertLessEqual(len(result.messages[0].content), 120)
        self.assertIn('truncated', result.messages[0].content)

    @override_settings(AGENT_MAX_CONTEXT_CHARS=100, AGENT_MAX_TOOL_RESULT_CHARS=50)
    def test_context_manager_truncates_tool_results_and_old_context(self):
        manager = AgentContextManager(self.manuscript)
        messages = [
            HumanMessage(content='old ' * 100),
            ToolMessage(content='new ' * 100, tool_call_id='call-1', name='tool'),
        ]
        prepared = manager.prepare(messages)
        self.assertLessEqual(sum(len(str(message.content)) for message in prepared[1:]), 140)
        self.assertIn('truncated', prepared[-1].content)

    @override_settings(AGENT_MAX_TOOL_RESULT_CHARS=240)
    def test_tool_result_budget_keeps_structured_references(self):
        messages, replacements = apply_tool_result_budget([
            ToolMessage(
                content='{"result":{"chunk_id":"chunk-7","doi":"10.1000/example","text":"' + 'x' * 500 + '"}}',
                tool_call_id='call-7',
                name='search_manuscript_evidence',
            ),
        ], 240)
        self.assertEqual(len(replacements), 1)
        payload = __import__('json').loads(messages[0].content)
        self.assertEqual(payload['compacted'], 'tool_result_budget')
        self.assertEqual({item['type'] for item in payload['references']}, {'chunk_id', 'doi'})

    def test_message_blocks_keep_tool_call_and_results_together(self):
        messages = [
            HumanMessage(content='Find evidence.'),
            AIMessage(content='', tool_calls=[{
                'name': 'search_manuscript_evidence', 'args': {}, 'id': 'call-1', 'type': 'tool_call',
            }]),
            ToolMessage(content='{"ok":true}', tool_call_id='call-1', name='search_manuscript_evidence'),
            HumanMessage(content='Now explain.'),
        ]
        blocks = message_blocks(messages)
        self.assertEqual([len(block.messages) for block in blocks], [1, 2, 1])

    @override_settings(
        AGENT_MAX_CONTEXT_CHARS=100,
        AGENT_MAX_CONTEXT_TOKENS=20,
        AGENT_CONTEXT_SNIP_RATIO=0,
        AGENT_CONTEXT_MICRO_RATIO=0,
        AGENT_CONTEXT_COLLAPSE_RATIO=0,
        AGENT_CONTEXT_AUTO_RATIO=1,
        AGENT_CONTEXT_RECENT_BLOCKS=1,
    )
    def test_context_collapse_creates_stable_segment(self):
        manager = ContextCompactionManager()
        state = {
            'messages': [
                HumanMessage(content='A' * 100),
                AIMessage(content='B' * 100),
                HumanMessage(content='latest'),
            ],
            'collapsed_segments': [],
            'compaction_history': [],
            'context_revision': 0,
        }
        first = manager.compact_state(state)
        second = manager.compact_state(state)
        self.assertTrue(first['collapsed_segments'])
        self.assertEqual(
            first['collapsed_segments'][0]['id'],
            second['collapsed_segments'][0]['id'],
        )
        self.assertEqual(first['messages'][0].additional_kwargs['compaction'], 'context_collapse')

    def test_auto_compact_requires_json_and_does_not_use_tools(self):
        class SummaryModel:
            def __init__(self):
                self.calls = []

            def invoke(self, messages):
                self.calls.append(messages)
                return AIMessage(content='''<analysis>draft</analysis>{"main_task":"recommend","editor_requirements":[],"manuscript_concepts":[],"tools_and_key_results":[],"errors_and_fixes":[],"editorial_decisions":[],"pending_approvals":[],"current_work_and_next_step":"done","recent_user_quotes":["recommend"]}''')

        model = SummaryModel()
        manager = ContextCompactionManager(model)
        with override_settings(
            AGENT_MAX_CONTEXT_CHARS=10,
            AGENT_MAX_CONTEXT_TOKENS=1,
            AGENT_CONTEXT_SNIP_RATIO=2,
            AGENT_CONTEXT_MICRO_RATIO=2,
            AGENT_CONTEXT_COLLAPSE_RATIO=2,
            AGENT_CONTEXT_AUTO_RATIO=0,
        ):
            result = manager.compact_state({'messages': [HumanMessage(content='recommend')], 'context_revision': 0})
        self.assertEqual(result['last_compaction_level'], 'auto_compact')
        self.assertEqual(result['session_summary']['main_task'], 'recommend')
        self.assertFalse(getattr(model.calls[0][-1], 'tool_calls', None))

    def test_auto_compact_circuit_opens_after_three_failures(self):
        class FailingModel:
            def __init__(self):
                self.calls = 0

            def invoke(self, messages):
                self.calls += 1
                raise RuntimeError('summary unavailable')

        model = FailingModel()
        manager = ContextCompactionManager(model)
        state = {'messages': [HumanMessage(content='x' * 100)], 'context_revision': 0}
        with override_settings(
            AGENT_MAX_CONTEXT_CHARS=10,
            AGENT_MAX_CONTEXT_TOKENS=1,
            AGENT_CONTEXT_SNIP_RATIO=2,
            AGENT_CONTEXT_MICRO_RATIO=2,
            AGENT_CONTEXT_COLLAPSE_RATIO=2,
            AGENT_CONTEXT_AUTO_RATIO=0,
            AGENT_COMPACTION_MAX_FAILURES=3,
        ):
            current = state
            for _ in range(4):
                current = {**current, **manager.compact_state(current)}
        self.assertEqual(model.calls, 3)
        self.assertEqual(current['last_compaction_level'], 'auto_compact_circuit_open')
        self.assertEqual(current['compaction_failure_count'], 3)

    def test_layered_memory_loads_in_priority_order_and_expands_includes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            managed = root / 'managed.md'
            user = root / 'user.md'
            project = root / 'project.md'
            included = root / 'included.md'
            managed.write_text('managed', encoding='utf-8')
            user.write_text('@included.md\nuser', encoding='utf-8')
            included.write_text('included', encoding='utf-8')
            project.write_text('---\npaths: *.py\n---\npython-only', encoding='utf-8')
            with override_settings(
                AGENT_MANAGED_MEMORY_FILE=str(managed),
                AGENT_USER_MEMORY_FILE=str(user),
                AGENT_PROJECT_MEMORY_FILE=str(project),
                AGENT_LOCAL_MEMORY_FILE='',
                AGENT_TEAM_MEMORY_FILE='',
                AGENT_MEMORY_MAX_CHARS=10000,
            ):
                loader = LayeredMemoryLoader(auto_memory=['session preference'], target_paths=['main.py'])
                rendered = loader.render()
        self.assertLess(rendered.index('Managed memory'), rendered.index('User memory'))
        self.assertIn('included', rendered)
        self.assertIn('python-only', rendered)
        self.assertIn('Auto memory', rendered)

    def test_conditional_memory_is_not_injected_for_non_matching_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / 'project.md'
            project.write_text('---\npaths: *.py\n---\npython-only', encoding='utf-8')
            with override_settings(AGENT_PROJECT_MEMORY_FILE=str(project)):
                rendered = LayeredMemoryLoader(target_paths=['main.ts']).render()
        self.assertNotIn('python-only', rendered)

    def test_context_manager_compiles_stable_and_dynamic_context(self):
        manager = AgentContextManager(
            self.manuscript,
            editor_context={'auto_memory': ['Prefer concise evidence tables.']},
            skill_catalog='- reviewer_selection: Select reviewers.',
        )
        prepared = manager.prepare(
            [HumanMessage(content='Continue.')],
            runtime_context={'active_skills': ['reviewer_selection'], 'last_compaction_level': 'snip'},
            system_context={'date': '2026-09-08'},
        )
        system_prompt = prepared[0].content
        self.assertIn(self.manuscript.title, system_prompt)
        self.assertIn('Prefer concise evidence tables.', system_prompt)
        self.assertIn('reviewer_selection', system_prompt)
        self.assertIn('2026-09-08', system_prompt)
        self.assertIn('snip', system_prompt)

    @patch('apps.agent.views.execute_agent_run.delay')
    def test_message_api_creates_async_run(self, mock_delay):
        conversation = self.client.post(
            reverse('agent-conversation-create'),
            {'manuscript_id': str(self.manuscript.id)},
            format='json',
        ).data
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('agent-message', kwargs={'conversation_id': conversation['id']}),
                {'content': 'Recommend a reviewer.'},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], AgentRun.Status.PROCESSING)
        mock_delay.assert_called_once_with(response.data['id'])
        detail = self.client.get(reverse('agent-run-detail', kwargs={'run_id': response.data['id']}))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

    @patch('apps.agent.tasks.run_editor_agent')
    def test_celery_task_persists_interrupt_and_terminal_message(self, mock_run):
        conversation = AgentConversation.objects.create(manuscript=self.manuscript)
        message = AgentMessage.objects.create(
            conversation=conversation,
            role=AgentMessage.Role.USER,
            content='Send the invitation.',
        )
        run = AgentRun.objects.create(
            conversation=conversation,
            user_input=message.content,
            runtime_context={'input_message_id': str(message.id)},
        )
        mock_run.return_value = {
            '__interrupt__': (SimpleNamespace(value={
                'type': 'tool_approval',
                'requests': [{'tool': 'send_email', 'call_id': 'call-1'}],
            }),),
            'plan': {},
            'tool_trace': [],
            'answer': '',
            'stop_reason': '',
            'turn_count': 1,
            'tool_call_count': 1,
        }

        execute_agent_run.run(str(run.id))
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.Status.WAITING_APPROVAL)
        self.assertEqual(run.pending_approval['interrupts'][0]['type'], 'tool_approval')

        mock_run.return_value = {
            'plan': {},
            'tool_trace': [],
            'answer': 'Invitation sent after approval.',
            'stop_reason': 'completed',
            'turn_count': 2,
            'tool_call_count': 1,
            'result': {},
        }
        run.status = AgentRun.Status.PROCESSING
        run.save(update_fields=['status'])
        execute_agent_run.run(str(run.id), resume={'approved': True, 'approved_call_ids': []})
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRun.Status.SUCCESS)
        self.assertEqual(conversation.messages.filter(role=AgentMessage.Role.ASSISTANT).count(), 1)

    @patch('apps.agent.tasks.run_editor_agent')
    @patch('apps.agent.tasks.DjangoCheckpointSaver')
    def test_celery_redelivery_continues_existing_checkpoint(self, mock_saver_class, mock_run):
        conversation = AgentConversation.objects.create(manuscript=self.manuscript)
        message = AgentMessage.objects.create(
            conversation=conversation,
            role=AgentMessage.Role.USER,
            content='Continue after worker restart.',
        )
        run = AgentRun.objects.create(
            conversation=conversation,
            user_input=message.content,
            runtime_context={'input_message_id': str(message.id)},
        )
        saver = mock_saver_class.return_value
        saver.get_tuple.return_value = object()
        mock_run.return_value = {
            'plan': {},
            'tool_trace': [],
            'answer': 'Recovered.',
            'stop_reason': 'completed',
            'turn_count': 2,
            'tool_call_count': 0,
            'result': {},
        }

        execute_agent_run.run(str(run.id))

        self.assertTrue(mock_run.call_args.kwargs['continue_existing'])
        self.assertIs(mock_run.call_args.kwargs['checkpointer'], saver)

    @patch('apps.agent.views.execute_agent_run.delay')
    def test_approval_api_queues_resume_command(self, mock_delay):
        conversation = AgentConversation.objects.create(manuscript=self.manuscript)
        run = AgentRun.objects.create(
            conversation=conversation,
            user_input='Send invitation.',
            status=AgentRun.Status.WAITING_APPROVAL,
            pending_approval={'interrupts': [{'type': 'tool_approval'}]},
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('agent-run-approval', kwargs={'run_id': run.id}),
                {'approved': False, 'approved_call_ids': ['call-1']},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['status'], AgentRun.Status.PROCESSING)
        mock_delay.assert_called_once_with(
            str(run.id),
            resume={'approved': False, 'approved_call_ids': ['call-1']},
        )
