import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from django.conf import settings
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


REFERENCE_KEYS = {
    'id', 'chunk_id', 'evidence_id', 'paper_id', 'reviewer_id',
    'doi', 'orcid', 'openalex_id',
}

NO_TOOLS_COMPACTION_PROMPT = """CRITICAL: Respond with JSON text only. Do not call tools.
Tool calls are rejected for this one-turn context-compaction task.
Summarize the editorial-agent conversation without inventing facts. Preserve these nine fields:
1. main_task
2. editor_requirements
3. manuscript_concepts
4. tools_and_key_results
5. errors_and_fixes
6. editorial_decisions
7. pending_approvals
8. current_work_and_next_step
9. recent_user_quotes
The recent_user_quotes field must contain short verbatim quotes from the latest user messages.
Return exactly one JSON object. Do not include markdown fences or hidden reasoning."""


@dataclass(frozen=True)
class MessageBlock:
    messages: Tuple[BaseMessage, ...]


def _content_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(message.content, ensure_ascii=False, default=str)


def _message_payload(message: BaseMessage) -> Dict[str, Any]:
    payload: Dict[str, Any] = {'type': message.type, 'content': _content_text(message)}
    if isinstance(message, AIMessage) and message.tool_calls:
        payload['tool_calls'] = message.tool_calls
    if isinstance(message, ToolMessage):
        payload.update({
            'name': message.name,
            'tool_call_id': message.tool_call_id,
            'status': getattr(message, 'status', None),
        })
    return payload


def message_blocks(messages: Sequence[BaseMessage]) -> List[MessageBlock]:
    """Group an AI tool request and its contiguous tool results as one indivisible block."""
    blocks: List[MessageBlock] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            call_ids = {
                str(call.get('id') or call.get('call_id') or '')
                for call in message.tool_calls
            }
            grouped = [message]
            next_index = index + 1
            while next_index < len(messages):
                candidate = messages[next_index]
                if not isinstance(candidate, ToolMessage):
                    break
                if str(candidate.tool_call_id or '') not in call_ids:
                    break
                grouped.append(candidate)
                next_index += 1
            blocks.append(MessageBlock(tuple(grouped)))
            index = next_index
            continue
        blocks.append(MessageBlock((message,)))
        index += 1
    return blocks


def _flatten(blocks: Iterable[MessageBlock]) -> List[BaseMessage]:
    return [message for block in blocks for message in block.messages]


def _references(value: Any) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in REFERENCE_KEYS and nested not in (None, '', [], {}):
                    serialized = str(nested)
                    marker = (key, serialized)
                    if marker not in seen:
                        seen.add(marker)
                        found.append({'type': key, 'value': serialized[:300]})
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return found[:20]


def _decode_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def _fit_json(payloads: Sequence[Dict[str, Any]], max_chars: int) -> str:
    for payload in payloads:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str)
        if len(encoded) <= max_chars:
            return encoded
    return '{}' if max_chars >= 2 else ''


def compact_tool_result_content(
    content: str,
    *,
    max_chars: int,
    tool: str = '',
    call_id: str = '',
) -> str:
    if len(content) <= max_chars:
        return content
    parsed = _decode_json(content)
    full = {
        'compacted': 'tool_result_budget',
        'truncated': True,
        'tool': tool,
        'call_id': call_id,
        'original_chars': len(content),
        'references': _references(parsed),
    }
    return _fit_json([
        full,
        {key: value for key, value in full.items() if key != 'references'},
        {'compacted': 'tool_result', 'truncated': True},
        {'truncated': True},
    ], max_chars)


def compact_json_result(value: Any, max_chars: int, *, tool: str = '', call_id: str = '') -> str:
    content = json.dumps(value, ensure_ascii=False, default=str)
    return compact_tool_result_content(content, max_chars=max_chars, tool=tool, call_id=call_id)


def apply_tool_result_budget(
    messages: Sequence[BaseMessage],
    max_chars: int,
) -> Tuple[List[BaseMessage], List[Dict[str, Any]]]:
    prepared: List[BaseMessage] = []
    replacements: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            prepared.append(message)
            continue
        content = _content_text(message)
        bounded = compact_tool_result_content(
            content,
            max_chars=max_chars,
            tool=str(message.name or ''),
            call_id=str(message.tool_call_id or ''),
        )
        if bounded != content:
            replacements.append({
                'tool': str(message.name or ''),
                'call_id': str(message.tool_call_id or ''),
                'original_chars': len(content),
                'compacted_chars': len(bounded),
            })
            message = message.model_copy(update={'content': bounded})
        prepared.append(message)
    return prepared, replacements


def _message_chars(messages: Sequence[BaseMessage]) -> int:
    return sum(
        len(json.dumps(_message_payload(message), ensure_ascii=False, default=str))
        for message in messages
    )


def _estimated_tokens(messages: Sequence[BaseMessage]) -> int:
    byte_count = sum(
        len(json.dumps(_message_payload(message), ensure_ascii=False, default=str).encode('utf-8'))
        for message in messages
    )
    return max(1, math.ceil(byte_count / 4)) if messages else 0


def context_budget(messages: Sequence[BaseMessage]) -> Dict[str, Any]:
    char_count = _message_chars(messages)
    estimated_tokens = _estimated_tokens(messages)
    max_chars = max(1, getattr(settings, 'AGENT_MAX_CONTEXT_CHARS', 40000))
    max_tokens = max(1, getattr(settings, 'AGENT_MAX_CONTEXT_TOKENS', 12000))
    return {
        'char_count': char_count,
        'estimated_tokens': estimated_tokens,
        'max_chars': max_chars,
        'max_tokens': max_tokens,
        'usage_ratio': max(char_count / max_chars, estimated_tokens / max_tokens),
    }


def select_recent_blocks(messages: Sequence[BaseMessage], max_chars: int) -> List[BaseMessage]:
    selected: List[MessageBlock] = []
    used = 0
    for block in reversed(message_blocks(messages)):
        block_chars = _message_chars(block.messages)
        if selected and used + block_chars > max_chars:
            break
        selected.append(block)
        used += block_chars
    return _flatten(reversed(selected))


def _clip(text: str, limit: int, marker: str) -> str:
    if len(text) <= limit:
        return text
    return f'{text[:max(0, limit - len(marker))]}{marker}'[:limit]


def _tool_signature(block: MessageBlock) -> str:
    first = block.messages[0]
    if not isinstance(first, AIMessage) or not first.tool_calls:
        return ''
    return json.dumps([
        {'name': call.get('name'), 'args': call.get('args', {})}
        for call in first.tool_calls
    ], ensure_ascii=False, sort_keys=True, default=str)


def _snip(messages: Sequence[BaseMessage], recent_blocks: int) -> List[BaseMessage]:
    blocks = message_blocks(messages)
    boundary = max(0, len(blocks) - recent_blocks)
    prepared: List[MessageBlock] = []
    for index, block in enumerate(blocks):
        if index >= boundary:
            prepared.append(block)
            continue
        first = block.messages[0]
        if isinstance(first, AIMessage) and not first.tool_calls and not _content_text(first).strip():
            continue
        if isinstance(first, AIMessage) and first.tool_calls:
            compacted = [first]
            for tool_message in block.messages[1:]:
                if not isinstance(tool_message, ToolMessage):
                    compacted.append(tool_message)
                    continue
                record = {
                    'compacted': 'snip',
                    'tool': tool_message.name,
                    'call_id': tool_message.tool_call_id,
                    'status': getattr(tool_message, 'status', 'success'),
                    'references': _references(_decode_json(_content_text(tool_message))),
                }
                compacted.append(tool_message.model_copy(update={
                    'content': json.dumps(record, ensure_ascii=False, separators=(',', ':'), default=str),
                }))
            prepared.append(MessageBlock(tuple(compacted)))
            continue
        prepared.append(block)
    return _flatten(prepared)


def _microcompact(messages: Sequence[BaseMessage], recent_blocks: int) -> List[BaseMessage]:
    blocks = message_blocks(messages)
    boundary = max(0, len(blocks) - recent_blocks)
    duplicate_tool_signatures = set()
    duplicate_system_messages = set()
    prepared_reversed: List[MessageBlock] = []
    for reverse_index, block in enumerate(reversed(blocks)):
        original_index = len(blocks) - reverse_index - 1
        if original_index >= boundary:
            prepared_reversed.append(block)
            continue
        first = block.messages[0]
        signature = _tool_signature(block)
        if signature:
            if signature in duplicate_tool_signatures:
                continue
            duplicate_tool_signatures.add(signature)
        if isinstance(first, SystemMessage) and not first.additional_kwargs.get('compaction'):
            content = _content_text(first)
            if content in duplicate_system_messages:
                continue
            duplicate_system_messages.add(content)
        compacted = []
        for message in block.messages:
            if isinstance(message, AIMessage):
                limit = 240 if message.tool_calls else 600
                content = _clip(_content_text(message), limit, '\n[Microcompacted.]')
                message = message.model_copy(update={'content': content})
            compacted.append(message)
        prepared_reversed.append(MessageBlock(tuple(compacted)))
    return _flatten(reversed(prepared_reversed))


def _deterministic_summary(messages: Sequence[BaseMessage]) -> Dict[str, Any]:
    user_requests = []
    assistant_outcomes = []
    tool_executions = []
    references: List[Dict[str, str]] = []
    for message in messages:
        content = _content_text(message)
        if isinstance(message, HumanMessage):
            user_requests.append(_clip(content, 500, '…'))
        elif isinstance(message, AIMessage) and content.strip():
            assistant_outcomes.append(_clip(content, 350, '…'))
        elif isinstance(message, ToolMessage):
            parsed = _decode_json(content)
            references.extend(_references(parsed))
            tool_executions.append({
                'tool': message.name,
                'call_id': message.tool_call_id,
                'status': getattr(message, 'status', 'success'),
            })
    unique_references = []
    seen = set()
    for reference in references:
        marker = (reference['type'], reference['value'])
        if marker not in seen:
            seen.add(marker)
            unique_references.append(reference)
    return {
        'user_requests': user_requests[-8:],
        'assistant_outcomes': assistant_outcomes[-5:],
        'tool_executions': tool_executions[-12:],
        'references': unique_references[:20],
    }


def _collapse(
    messages: Sequence[BaseMessage],
    recent_blocks: int,
) -> Tuple[List[BaseMessage], Dict[str, Any]]:
    blocks = message_blocks(messages)
    boundary = max(0, len(blocks) - recent_blocks)
    old_blocks = blocks[:boundary]
    if len(old_blocks) < 2:
        return list(messages), {}
    old_messages = _flatten(old_blocks)
    serialized = json.dumps(
        [_message_payload(message) for message in old_messages],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    segment_id = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]
    summary = _deterministic_summary(old_messages)
    summary_message = SystemMessage(
        content=json.dumps({
            'compacted': 'context_collapse',
            'segment_id': segment_id,
            'summary': summary,
        }, ensure_ascii=False, separators=(',', ':'), default=str),
        additional_kwargs={'compaction': 'context_collapse', 'segment_id': segment_id},
    )
    segment = {
        'id': segment_id,
        'message_count': len(old_messages),
        'summary': summary,
        'references': summary['references'],
    }
    return [summary_message, *_flatten(blocks[boundary:])], segment


def _strip_analysis(text: str) -> str:
    text = re.sub(r'<analysis>.*?</analysis>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    start = text.find('{')
    end = text.rfind('}')
    return text[start:end + 1] if start >= 0 and end > start else text


def _summary_source(messages: Sequence[BaseMessage], max_chars: int) -> str:
    serialized = json.dumps(
        [_message_payload(message) for message in messages],
        ensure_ascii=False,
        default=str,
    )
    if len(serialized) <= max_chars:
        return serialized
    head_chars = max_chars // 4
    tail_chars = max_chars - head_chars
    return f'{serialized[:head_chars]}\n[older context omitted]\n{serialized[-tail_chars:]}'


def _limit_summary(summary: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    per_item = max(120, max_chars // 18)

    def limit(value: Any) -> Any:
        if isinstance(value, str):
            return _clip(value, per_item, '…')
        if isinstance(value, list):
            return [limit(item) for item in value[:8]]
        if isinstance(value, dict):
            return {str(key): limit(item) for key, item in value.items()}
        return value

    return {str(key): limit(value) for key, value in summary.items()}


class ContextCompactionManager:
    def __init__(self, summary_model=None):
        self.summary_model = summary_model

    def _auto_compact(self, messages: Sequence[BaseMessage]) -> Tuple[List[BaseMessage], Dict[str, Any]]:
        if self.summary_model is None:
            raise RuntimeError('No summary model is configured.')
        max_input_chars = max(2000, getattr(settings, 'AGENT_MAX_CONTEXT_CHARS', 40000))
        response = self.summary_model.invoke([
            SystemMessage(content=NO_TOOLS_COMPACTION_PROMPT),
            HumanMessage(content=_summary_source(messages, max_input_chars)),
        ])
        if getattr(response, 'tool_calls', None):
            raise ValueError('The compaction model attempted a tool call.')
        content = str(getattr(response, 'content', response) or '')
        summary = json.loads(_strip_analysis(content))
        if not isinstance(summary, dict):
            raise ValueError('The compaction response must be a JSON object.')
        required = {
            'main_task', 'editor_requirements', 'manuscript_concepts',
            'tools_and_key_results', 'errors_and_fixes', 'editorial_decisions',
            'pending_approvals', 'current_work_and_next_step', 'recent_user_quotes',
        }
        if not required.issubset(summary):
            raise ValueError('The compaction response is missing required fields.')
        summary = _limit_summary(
            summary,
            max(1000, getattr(settings, 'AGENT_COMPACTION_SUMMARY_CHARS', 4000)),
        )
        blocks = message_blocks(messages)
        keep_count = min(2, len(blocks))
        recent = _flatten(blocks[-keep_count:]) if keep_count else []
        summary_message = SystemMessage(
            content=json.dumps({
                'compacted': 'auto_compact',
                'summary': summary,
            }, ensure_ascii=False, separators=(',', ':'), default=str),
            additional_kwargs={'compaction': 'auto_compact'},
        )
        return [summary_message, *recent], summary

    @staticmethod
    def _event(level: str, before: Dict[str, Any], after: Dict[str, Any], **extra) -> Dict[str, Any]:
        return {
            'level': level,
            'before_chars': before['char_count'],
            'after_chars': after['char_count'],
            'tokens_freed': max(0, before['estimated_tokens'] - after['estimated_tokens']),
            **extra,
        }

    def compact_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        original_messages = list(state.get('messages', []))
        messages, replacements = apply_tool_result_budget(
            original_messages,
            getattr(settings, 'AGENT_MAX_TOOL_RESULT_CHARS', 8000),
        )
        history = list(state.get('compaction_history', []))
        collapsed_segments = list(state.get('collapsed_segments', []))
        failure_count = int(state.get('compaction_failure_count', 0))
        session_summary = dict(state.get('session_summary', {}))
        last_level = str(state.get('last_compaction_level', 'none'))

        if replacements:
            before = context_budget(original_messages)
            after = context_budget(messages)
            history.append(self._event('tool_result_budget', before, after, replacements=replacements))
            last_level = 'tool_result_budget'

        recent_blocks = max(1, getattr(settings, 'AGENT_CONTEXT_RECENT_BLOCKS', 6))
        stages = (
            ('snip', getattr(settings, 'AGENT_CONTEXT_SNIP_RATIO', 0.55), _snip),
            ('microcompact', getattr(settings, 'AGENT_CONTEXT_MICRO_RATIO', 0.70), _microcompact),
        )
        for level, threshold, operation in stages:
            before = context_budget(messages)
            if before['usage_ratio'] < threshold:
                continue
            compacted = operation(messages, recent_blocks)
            after = context_budget(compacted)
            if compacted != messages:
                messages = compacted
                history.append(self._event(level, before, after))
                last_level = level

        before = context_budget(messages)
        if before['usage_ratio'] >= getattr(settings, 'AGENT_CONTEXT_COLLAPSE_RATIO', 0.82):
            compacted, segment = _collapse(messages, recent_blocks)
            after = context_budget(compacted)
            if segment:
                messages = compacted
                collapsed_segments.append(segment)
                history.append(self._event('context_collapse', before, after, segment_id=segment['id']))
                last_level = 'context_collapse'

        before = context_budget(messages)
        auto_threshold = getattr(settings, 'AGENT_CONTEXT_AUTO_RATIO', 0.90)
        max_failures = max(1, getattr(settings, 'AGENT_COMPACTION_MAX_FAILURES', 3))
        if before['usage_ratio'] >= auto_threshold:
            if failure_count >= max_failures:
                last_level = 'auto_compact_circuit_open'
            else:
                try:
                    compacted, session_summary = self._auto_compact(messages)
                except Exception as exc:
                    failure_count += 1
                    history.append(self._event(
                        'auto_compact_failed', before, before,
                        failure_count=failure_count,
                        error=str(exc)[:500],
                    ))
                    last_level = (
                        'auto_compact_circuit_open'
                        if failure_count >= max_failures else 'auto_compact_failed'
                    )
                else:
                    messages = compacted
                    after = context_budget(messages)
                    failure_count = 0
                    history.append(self._event('auto_compact', before, after))
                    last_level = 'auto_compact'

        changed = messages != original_messages
        return {
            'messages': messages,
            'context_budget': context_budget(messages),
            'collapsed_segments': collapsed_segments[-50:],
            'compaction_history': history[-50:],
            'compaction_failure_count': failure_count,
            'last_compaction_level': last_level,
            'session_summary': session_summary,
            'context_revision': int(state.get('context_revision', 0)) + (1 if changed else 0),
        }
