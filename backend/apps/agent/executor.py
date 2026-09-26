import json
from time import perf_counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.conf import settings
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from .policy import EditorToolPolicy
from .compaction import compact_json_result
from .state import StopReason
from .tools import EditorToolRegistry


@dataclass
class ToolBatchResult:
    messages: List[ToolMessage] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ''
    pending_approval: Dict[str, Any] = field(default_factory=dict)


class ToolResultError(RuntimeError):
    def __init__(self, status):
        super().__init__(f'Tool returned business status: {status}. No successful result is available.')
        self.code = 'unavailable' if status in {'disabled', 'unavailable', 'not_found'} else 'business_error'


def _json_result(value: Any, max_chars: int) -> str:
    return compact_json_result(value, max_chars)


class EditorToolExecutor:
    def __init__(self, registry: EditorToolRegistry, policy=None):
        self.registry = registry
        self.policy = policy or EditorToolPolicy()

    def execute_all(
        self,
        calls: List[Dict[str, Any]],
        already_used: int = 0,
        approval_decisions: Optional[Dict[str, bool]] = None,
    ) -> ToolBatchResult:
        batch = ToolBatchResult()
        approval_decisions = approval_decisions or {}
        max_calls = getattr(settings, 'AGENT_MAX_TOOL_CALLS', 12)
        for offset, call in enumerate(calls, start=1):
            call_id = str(call.get('id') or call.get('call_id') or f'tool_call_{already_used + offset}')
            name = str(call.get('name') or '')
            raw_arguments = call.get('args', {})
            invalid_arguments = False
            if isinstance(raw_arguments, str):
                try:
                    raw_arguments = json.loads(raw_arguments)
                except (TypeError, ValueError):
                    invalid_arguments = True
                    raw_arguments = {}
            invalid_arguments = invalid_arguments or not isinstance(raw_arguments, dict)
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            if already_used + offset > max_calls:
                error = {'ok': False, 'error': 'Tool-call budget exceeded.'}
                batch.messages.append(ToolMessage(
                    content=json.dumps(error), tool_call_id=call_id, name=name, status='error',
                ))
                batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'budget_exceeded'})
                batch.stop_reason = StopReason.TOOL_BUDGET_EXCEEDED
                continue

            if invalid_arguments:
                error = {'ok': False, 'error': 'Tool arguments must be a valid JSON object.', 'error_type': 'invalid_arguments'}
                batch.messages.append(ToolMessage(content=json.dumps(error), tool_call_id=call_id, name=name, status='error'))
                batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'error', **error})
                continue

            definition = self.registry.get(name)
            if definition is None:
                error = {'ok': False, 'error': f'Unknown tool: {name}'}
                batch.messages.append(ToolMessage(
                    content=json.dumps(error), tool_call_id=call_id, name=name, status='error',
                ))
                batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'error', 'error': error['error']})
                continue

            decision = self.policy.evaluate(definition.risk_level)
            if decision.requires_approval:
                if approval_decisions.get(call_id) is True:
                    decision = type(decision)(allowed=True)
                elif call_id in approval_decisions:
                    error = {'ok': False, 'error': 'Tool call denied by editor.'}
                    batch.messages.append(ToolMessage(
                        content=json.dumps(error), tool_call_id=call_id, name=name, status='error',
                    ))
                    batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'denied_by_editor'})
                    continue
            if decision.requires_approval:
                pending = {'tool': name, 'call_id': call_id, 'arguments': arguments, 'reason': decision.reason}
                batch.pending_approval = pending
                batch.stop_reason = StopReason.APPROVAL_REQUIRED
                batch.messages.append(ToolMessage(
                    content=json.dumps({'ok': False, 'approval_required': True, **pending}, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=name,
                    status='error',
                ))
                batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'approval_required'})
                continue
            if not decision.allowed:
                error = {'ok': False, 'error': decision.reason or 'Tool call denied.'}
                batch.messages.append(ToolMessage(
                    content=json.dumps(error, ensure_ascii=False), tool_call_id=call_id, name=name, status='error',
                ))
                batch.trace.append({'tool': name, 'call_id': call_id, 'status': 'denied'})
                continue

            started = perf_counter()
            try:
                result = definition.tool.invoke(arguments)
                result = result if isinstance(result, dict) else {'value': result}
                if result.get('status') in {'disabled', 'unavailable', 'not_found', 'error', 'failed'}:
                    raise ToolResultError(result['status'])
                batch.results.append({'name': name, 'call_id': call_id, 'arguments': arguments, 'result': result})
                limit = min(definition.max_result_chars, getattr(settings, 'AGENT_MAX_TOOL_RESULT_CHARS', 8000))
                batch.messages.append(ToolMessage(
                    content=_json_result({'ok': True, 'result': result}, limit),
                    tool_call_id=call_id,
                    name=name,
                ))
                batch.trace.append({
                    'tool': name,
                    'call_id': call_id,
                    'arguments': arguments,
                    'risk_level': definition.risk_level.value,
                    'tool_version': definition.version,
                    'source_protocol': definition.source_protocol,
                    **({'skill_version': result['skill']['version'], 'skill_sha256': result['skill']['instructions_sha256']}
                       if name == 'load_editor_skill' and result.get('status') == 'loaded' else {}),
                    'status': 'success',
                    'latency_ms': round((perf_counter() - started) * 1000, 3),
                })
            except Exception as exc:
                error = str(exc)
                error_type = getattr(exc, 'code', None) or (
                    'invalid_arguments' if isinstance(exc, (ValidationError, ValueError)) else
                    'timeout' if isinstance(exc, TimeoutError) else 'execution_error'
                )
                batch.messages.append(ToolMessage(
                    content=json.dumps({'ok': False, 'error': error, 'error_type': error_type}, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=name,
                    status='error',
                ))
                batch.trace.append({
                    'tool': name, 'call_id': call_id, 'arguments': arguments,
                    'status': 'error', 'error': error,
                    'error_type': error_type, 'tool_version': definition.version,
                    'source_protocol': definition.source_protocol,
                    'latency_ms': round((perf_counter() - started) * 1000, 3),
                })
        return batch
