import json
import logging
import os
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter

from prometheus_client import CollectorRegistry, Counter, Histogram, REGISTRY, generate_latest, multiprocess


request_context = ContextVar('request_context', default={})
logger = logging.getLogger('rrs.events')
HTTP_REQUESTS = Counter('rrs_http_requests_total', 'HTTP requests', ['route', 'method', 'status'])
HTTP_LATENCY = Histogram('rrs_http_duration_seconds', 'HTTP duration', ['route'], buckets=(.05, .1, .3, 1, 3, 10, 30, 120))
AGENT_RUNS = Counter('rrs_agent_executions_total', 'Agent execution segments', ['outcome'])
AGENT_LATENCY = Histogram('rrs_agent_duration_seconds', 'Agent execution segment duration', buckets=(1, 5, 15, 30, 60, 120, 300, 600, 900))
QUEUE_LATENCY = Histogram('rrs_agent_queue_seconds', 'Agent queue wait', buckets=(1, 5, 15, 30, 60, 120, 300, 600))
DEGRADATIONS = Counter('rrs_degradations_total', 'Dependency degradation events', ['component', 'reason'])
RETRIEVAL_LATENCY = Histogram('rrs_retrieval_duration_seconds', 'Document hybrid retrieval duration', buckets=(.1, .5, 1, 3, 10, 30, 120))
SPAN_LATENCY = Histogram('rrs_agent_span_seconds', 'Measured model/tool spans', ['kind', 'status'], buckets=(.1, .5, 1, 5, 15, 30, 60, 120))
MODEL_TOKENS = Counter('rrs_agent_known_tokens_total', 'Reported model/finalize tokens; excludes unreported and tool-internal usage', ['direction'])


def observe(metric, value=1, **labels):
    try:
        child = metric.labels(**labels) if labels else metric
        if hasattr(child, 'observe'):
            child.observe(value)
        else:
            child.inc(value)
    except Exception:
        pass


def event(name, **fields):
    try:
        logger.info(name, extra={'event_fields': fields})
    except Exception:
        pass


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'event': record.getMessage(),
            **request_context.get(),
            **getattr(record, 'event_fields', {}),
        }
        if record.exc_info:
            payload['error_type'] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def metrics_payload():
    registry = REGISTRY
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry)


class RequestTelemetryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get('X-Request-ID', '')
        request_id = supplied if re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', supplied) else uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        traceparent = request.headers.get('traceparent', '')
        match = re.fullmatch(r'00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})', traceparent)
        if match and int(match[1], 16) and int(match[2], 16):
            trace_id = match[1]
        request.request_id = request_id
        request.trace_id = trace_id
        token = request_context.set({'request_id': request_id, 'trace_id': trace_id})
        started = perf_counter()
        response_status = 500
        try:
            response = self.get_response(request)
            response_status = response.status_code
            response['X-Request-ID'] = request_id
            response['X-Trace-ID'] = trace_id
            return response
        finally:
            duration = perf_counter() - started
            match = getattr(request, 'resolver_match', None)
            route = str(match.route) if match else 'unmatched'
            method = request.method if request.method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'} else 'OTHER'
            if not request.path.startswith(('/health/', '/metrics')):
                observe(HTTP_REQUESTS, route=route, method=method, status=str(response_status))
                observe(HTTP_LATENCY, duration, route=route)
                event('http_request', route=route, method=method, status=response_status, duration_ms=round(duration * 1000, 2))
            request_context.reset(token)
