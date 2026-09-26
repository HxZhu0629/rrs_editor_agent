from enum import StrEnum
from typing import Any, Dict, List, TypedDict

from langchain_core.messages import BaseMessage


class StopReason(StrEnum):
    COMPLETED = 'completed'
    MAX_TURNS = 'max_turns'
    TOOL_BUDGET_EXCEEDED = 'tool_budget_exceeded'
    APPROVAL_REQUIRED = 'approval_required'
    PROVIDER_ERROR = 'provider_error'
    TOOL_ERROR = 'tool_error'


class EditorAgentState(TypedDict, total=False):
    messages: List[BaseMessage]
    turn_count: int
    tool_call_count: int
    tool_trace: List[Dict[str, Any]]
    stop_reason: str
    answer: str
    plan: Dict[str, Any]
    recommendations: List[Dict[str, Any]]
    email_drafts: List[Dict[str, Any]]
    result: Dict[str, Any]
    pending_approval: Dict[str, Any]
    active_skills: List[str]
    session_summary: Dict[str, Any]
    system_context: Dict[str, Any]
    context_budget: Dict[str, Any]
    collapsed_segments: List[Dict[str, Any]]
    compaction_history: List[Dict[str, Any]]
    compaction_failure_count: int
    last_compaction_level: str
    context_revision: int
