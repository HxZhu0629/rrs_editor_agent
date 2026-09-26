from time import perf_counter
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from apps.common.llm import build_chat_model
from apps.manuscripts.models import Manuscript

from .context import AgentContextManager
from .compaction import ContextCompactionManager
from .executor import EditorToolExecutor
from .observability import build_langfuse_callback
from .state import EditorAgentState, StopReason
from .tools import build_editor_tool_registry


def _model_metrics(response, started):
    usage = getattr(response, 'usage_metadata', None)
    return {
        'latency_ms': round((perf_counter() - started) * 1000, 3),
        'usage': usage if isinstance(usage, dict) else None,
        'model': getattr(settings, 'DEEPSEEK_CHAT_MODEL', ''),
    }


def _history_messages(history: Iterable[Dict[str, Any]]) -> List[BaseMessage]:
    messages = []
    for item in history:
        role = item.get('role')
        content = str(item.get('content') or '')
        if role == 'user':
            messages.append(HumanMessage(content=content))
        elif role == 'assistant':
            messages.append(AIMessage(content=content))
    return messages


def _collect_business_results(state: EditorAgentState, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    recommendations = list(state.get('recommendations', []))
    email_drafts = list(state.get('email_drafts', []))
    plan = dict(state.get('plan', {}))
    active_skills = list(state.get('active_skills', []))
    for execution in tool_results:
        result = execution['result']
        if execution['name'] == 'load_editor_skill' and result.get('status') == 'loaded':
            skill_name = result.get('skill', {}).get('name')
            if skill_name and skill_name not in active_skills:
                active_skills.append(skill_name)
        elif execution['name'] == 'recommend_reviewers':
            recommendations = result.get('recommendations', [])
            plan = execution['arguments']
        elif execution['name'] == 'explain_recommendation' and result.get('recommendations'):
            recommendations = result['recommendations']
        elif execution['name'] == 'draft_reviewer_emails':
            email_drafts = result.get('email_drafts', [])
    return {
        'recommendations': recommendations,
        'email_drafts': email_drafts,
        'plan': plan,
        'active_skills': active_skills,
    }


def build_editor_agent_graph(
    manuscript: Manuscript,
    previous_recommendations: Optional[List[Dict[str, Any]]] = None,
    editor_context: Optional[Dict[str, Any]] = None,
    model=None,
    checkpointer=None,
):
    registry = build_editor_tool_registry(manuscript, previous_recommendations or [])
    chat_model = model or build_chat_model()
    bound_model = chat_model.bind_tools(registry.langchain_tools())
    context_manager = AgentContextManager(manuscript, editor_context, registry.skills.catalog())
    compaction_manager = ContextCompactionManager(summary_model=chat_model)
    executor = EditorToolExecutor(registry)

    def manage_context(state: EditorAgentState) -> Dict[str, Any]:
        result = compaction_manager.compact_state(state)
        result['system_context'] = state.get('system_context') or context_manager.system_snapshot
        return result

    def call_model(state: EditorAgentState) -> Dict[str, Any]:
        turn_count = state.get('turn_count', 0) + 1
        started = perf_counter()
        try:
            runtime_context = {
                'active_skills': state.get('active_skills', []),
                'context_budget': state.get('context_budget', {}),
                'session_summary': state.get('session_summary', {}),
                'last_compaction_level': state.get('last_compaction_level', 'none'),
            }
            response = bound_model.invoke(
                context_manager.prepare(
                    state.get('messages', []),
                    runtime_context=runtime_context,
                    system_context=state.get('system_context', {}),
                )
            )
        except Exception as exc:
            trace = [*state.get('tool_trace', []), {
                'event': 'model_error', 'turn': turn_count, 'status': 'error', 'error': str(exc),
                **_model_metrics(None, started),
            }]
            return {'turn_count': turn_count, 'tool_trace': trace, 'stop_reason': StopReason.PROVIDER_ERROR, 'answer': ''}
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, 'content', response)))
        tool_calls = list(response.tool_calls or [])
        trace = [*state.get('tool_trace', []), {
            'event': 'model', 'turn': turn_count, 'status': 'success',
            'tool_calls': [call.get('name', '') for call in tool_calls],
            **_model_metrics(response, started),
        }]
        result = {'messages': [*state.get('messages', []), response], 'turn_count': turn_count, 'tool_trace': trace}
        if not tool_calls:
            result.update({'stop_reason': StopReason.COMPLETED, 'answer': str(response.content or '').strip()})
        elif turn_count >= getattr(settings, 'AGENT_MAX_TURNS', 6):
            result.update({'stop_reason': StopReason.MAX_TURNS, 'answer': str(response.content or '').strip()})
        else:
            result['stop_reason'] = ''
        return result

    def finalize(state: EditorAgentState) -> Dict[str, Any]:
        started = perf_counter()
        messages = list(state.get('messages', []))
        if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
            messages.pop()
        messages.append(HumanMessage(content=(
            'Execution budget is exhausted. Do not call tools. Give the editor the best final answer now '
            'using only the verified evidence and tool results already present. Clearly state any '
            'unfinished verification or uncertainty, and answer in the user\'s language.'
        )))
        try:
            response = chat_model.invoke(context_manager.prepare(
                messages,
                runtime_context={
                    'active_skills': state.get('active_skills', []),
                    'context_budget': state.get('context_budget', {}),
                    'session_summary': state.get('session_summary', {}),
                    'last_compaction_level': state.get('last_compaction_level', 'none'),
                    'finalization_only': True,
                },
                system_context=state.get('system_context', {}),
            ))
            answer = str(getattr(response, 'content', response) or '').strip()
            if not answer:
                raise ValueError('Finalization model returned an empty answer.')
            trace = [*state.get('tool_trace', []), {
                'event': 'finalize', 'status': 'success', 'reason': state.get('stop_reason', ''),
                **_model_metrics(response, started),
            }]
            return {'answer': answer, 'tool_trace': trace}
        except Exception as exc:
            trace = [*state.get('tool_trace', []), {
                'event': 'finalize', 'status': 'error', 'reason': state.get('stop_reason', ''),
                'error': str(exc),
                **_model_metrics(None, started),
            }]
            return {
                'answer': '本次 Agent 已达到执行预算，检索结果已保留，但最终结论生成失败。请继续对话以完成整理。',
                'tool_trace': trace,
            }

    def execute_tools(state: EditorAgentState) -> Dict[str, Any]:
        response = state['messages'][-1]
        calls = list(getattr(response, 'tool_calls', []) or [])
        approval_requests = []
        for call in calls:
            definition = registry.get(str(call.get('name') or ''))
            if definition is None:
                continue
            decision = executor.policy.evaluate(definition.risk_level)
            if decision.requires_approval:
                approval_requests.append({
                    'tool': call.get('name'),
                    'call_id': str(call.get('id') or call.get('call_id') or ''),
                    'arguments': call.get('args', {}),
                    'reason': decision.reason,
                })
        approval_decisions = {}
        if approval_requests and checkpointer is not None:
            response_value = interrupt({'type': 'tool_approval', 'requests': approval_requests})
            response_value = response_value if isinstance(response_value, dict) else {}
            approved_ids = set(response_value.get('approved_call_ids') or [])
            approve_all = response_value.get('approved') is True
            approval_decisions = {
                request['call_id']: approve_all or request['call_id'] in approved_ids
                for request in approval_requests
            }
        batch = executor.execute_all(
            calls,
            already_used=state.get('tool_call_count', 0),
            approval_decisions=approval_decisions,
        )
        business = _collect_business_results(state, batch.results)
        return {
            'messages': [*state.get('messages', []), *batch.messages],
            'tool_call_count': state.get('tool_call_count', 0) + len(calls),
            'tool_trace': [*state.get('tool_trace', []), *batch.trace],
            'stop_reason': batch.stop_reason,
            'pending_approval': batch.pending_approval,
            **business,
        }

    def route_after_model(state: EditorAgentState) -> str:
        if state.get('stop_reason') == StopReason.MAX_TURNS:
            return 'finalize'
        return END if state.get('stop_reason') else 'tools'

    def route_after_tools(state: EditorAgentState) -> str:
        if state.get('stop_reason') == StopReason.TOOL_BUDGET_EXCEEDED:
            return 'finalize'
        return END if state.get('stop_reason') else 'model'

    workflow = StateGraph(EditorAgentState)
    workflow.add_node('context', manage_context)
    workflow.add_node('model', call_model)
    workflow.add_node('tools', execute_tools)
    workflow.add_node('finalize', finalize)
    workflow.set_entry_point('context')
    workflow.add_edge('context', 'model')
    workflow.add_conditional_edges('model', route_after_model, {'tools': 'tools', 'finalize': 'finalize', END: END})
    workflow.add_conditional_edges('tools', route_after_tools, {
        'model': 'context', 'finalize': 'finalize', END: END,
    })
    workflow.add_edge('finalize', END)
    return workflow.compile(checkpointer=checkpointer)


def run_editor_agent(
    content: str,
    manuscript: Manuscript,
    previous_recommendations=None,
    history=None,
    editor_context=None,
    model=None,
    thread_id=None,
    checkpointer=None,
    resume=None,
    continue_existing=False,
    session_id=None,
) -> EditorAgentState:
    messages = _history_messages(history or [])
    messages.append(HumanMessage(content=content))
    initial_state: EditorAgentState = {
        'messages': messages, 'turn_count': 0, 'tool_call_count': 0, 'tool_trace': [],
        'stop_reason': '', 'answer': '', 'plan': {},
        'recommendations': list(previous_recommendations or []), 'email_drafts': [], 'pending_approval': {},
        'active_skills': [],
        'session_summary': {}, 'system_context': {}, 'context_budget': {}, 'collapsed_segments': [],
        'compaction_history': [], 'compaction_failure_count': 0,
        'last_compaction_level': 'none', 'context_revision': 0,
    }
    graph = build_editor_agent_graph(
        manuscript,
        previous_recommendations=previous_recommendations,
        editor_context=editor_context,
        model=model,
        checkpointer=checkpointer,
    )
    config = {'configurable': {'thread_id': str(thread_id)}} if thread_id else {}
    callback = build_langfuse_callback(str(thread_id) if thread_id else None)
    if callback is not None:
        config.update({
            'callbacks': [callback],
            'run_name': 'editor-agent',
            'metadata': {
                'agent_run_id': str(thread_id or ''),
                'manuscript_id': str(manuscript.id),
                'langfuse_session_id': str(session_id or ''),
            },
            'tags': ['editor-agent', manuscript.status],
        })
    if resume is not None:
        graph_input = Command(resume=resume)
    elif continue_existing:
        graph_input = None
    else:
        graph_input = initial_state
    state = graph.invoke(graph_input, config=config or None)
    if thread_id:
        snapshot = graph.get_state(config)
        interrupts = tuple(
            item
            for task in getattr(snapshot, 'tasks', ())
            for item in getattr(task, 'interrupts', ())
        )
        if interrupts:
            state['__interrupt__'] = interrupts
    state['result'] = {
        'recommendations': state.get('recommendations', []),
        'email_drafts': state.get('email_drafts', []),
        'active_skills': state.get('active_skills', []),
    }
    return state
