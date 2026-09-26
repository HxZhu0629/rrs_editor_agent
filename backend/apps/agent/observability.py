import logging
import os
from typing import Optional
from uuid import UUID

from django.conf import settings


logger = logging.getLogger(__name__)


def _trace_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return UUID(str(value)).hex
    except (TypeError, ValueError):
        return None


def build_langfuse_callback(run_id: Optional[str] = None):
    if not getattr(settings, 'LANGFUSE_TRACING_ENABLED', False):
        return None
    if not os.getenv('LANGFUSE_PUBLIC_KEY') or not os.getenv('LANGFUSE_SECRET_KEY'):
        return None
    try:
        from langfuse.langchain import CallbackHandler

        trace_id = _trace_id(run_id)
        trace_context = {'trace_id': trace_id} if trace_id else None
        return CallbackHandler(trace_context=trace_context)
    except Exception:
        logger.exception('Langfuse callback initialization failed; continuing without tracing.')
        return None


def flush_langfuse() -> None:
    if not getattr(settings, 'LANGFUSE_TRACING_ENABLED', False):
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.exception('Langfuse flush failed; agent result remains available in AgentRun.')
