import io
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .circuit import CircuitBreaker, CircuitOpen
from .loadtest import capacity_plan, run_load
from .quality_gate import evaluate_gate
from .telemetry import JsonFormatter, RequestTelemetryMiddleware, observe, request_context


class TelemetryTests(TestCase):
    def test_request_context_propagates_without_query_or_body(self):
        request = RequestFactory().get('/unknown?api_key=secret', HTTP_X_REQUEST_ID='test-request',
                                       HTTP_TRACEPARENT='00-' + 'a' * 32 + '-' + 'b' * 16 + '-01')
        with patch('apps.common.telemetry.event') as logged:
            response = RequestTelemetryMiddleware(lambda request: HttpResponse('ok'))(request)
        self.assertEqual(response['X-Request-ID'], 'test-request')
        self.assertEqual(response['X-Trace-ID'], 'a' * 32)
        self.assertNotIn('secret', str(logged.call_args))
        self.assertEqual(request_context.get(), {})

    def test_invalid_header_is_replaced_and_exception_context_is_reset(self):
        request = RequestFactory().get('/', HTTP_X_REQUEST_ID='bad\nheader')
        with self.assertRaises(RuntimeError):
            RequestTelemetryMiddleware(Mock(side_effect=RuntimeError('private')))(request)
        self.assertEqual(len(request.request_id), 32)
        self.assertEqual(request_context.get(), {})

    def test_metrics_failure_does_not_raise(self):
        observe(Mock(labels=Mock(side_effect=OSError('disk full'))), route='test')

    def test_json_log_is_machine_readable(self):
        record = logging.LogRecord('rrs.events', logging.INFO, '', 0, 'agent_started', (), None)
        record.event_fields = {'run_id': 'test'}
        self.assertEqual(json.loads(JsonFormatter().format(record))['run_id'], 'test')

    @override_settings(METRICS_TOKEN='test-token')
    def test_metrics_require_token_and_report_database_backlog(self):
        self.assertEqual(self.client.get('/metrics').status_code, 403)
        response = self.client.get('/metrics', HTTP_AUTHORIZATION='Bearer test-token')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'rrs_agent_active_runs', response.content)

    @patch('apps.common.health.Redis.from_url')
    def test_readiness_reports_broker_outage_but_liveness_survives(self, redis_client):
        redis_client.return_value.__enter__.return_value.ping.side_effect = ConnectionError('private connection string')
        response = self.client.get('/health/ready/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['dependencies']['broker'], 'unavailable')
        self.assertNotIn(b'private', response.content)
        self.assertEqual(self.client.get('/health/live/').status_code, 200)


class CircuitTests(SimpleTestCase):
    @patch('apps.common.circuit.monotonic')
    def test_open_short_circuits_then_allows_recovery_probe(self, now):
        now.return_value = 0
        breaker = CircuitBreaker()
        dependency = Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            breaker.call(dependency, threshold=1, cooldown=10)
        with self.assertRaises(CircuitOpen):
            breaker.call(dependency, threshold=1, cooldown=10)
        self.assertEqual(dependency.call_count, 1)
        now.return_value = 11
        self.assertEqual(breaker.call(lambda: 'recovered'), 'recovered')
        self.assertEqual(breaker.retry_at, 0)


class QualityGateTests(SimpleTestCase):
    def setUp(self):
        self.gate = {'checks': [{'match': {'kind': 'agent', 'source': 'human'}, 'metric': 'task_success', 'min': .9, 'min_samples': 2}]}
        self.group = {'kind': 'agent', 'source': 'human', 'metrics': {'task_success': {'mean': 1.0, 'n': 2, 'coverage': 1.0}}}

    def test_missing_labels_cannot_pass(self):
        self.group['metrics']['task_success']['coverage'] = .5
        report = evaluate_gate({'groups': [self.group]}, self.gate)
        self.assertFalse(report['passed'])
        self.assertIn('insufficient_coverage', report['checks'][0]['reasons'])

    def test_unmatched_group_fails_and_synthetic_requires_opt_in(self):
        self.assertFalse(evaluate_gate({'groups': []}, self.gate)['passed'])
        self.gate['checks'][0]['match']['source'] = 'synthetic'
        with self.assertRaises(ValueError):
            evaluate_gate({'groups': []}, self.gate)

    def test_each_slice_must_pass_independently(self):
        bad_group = {**self.group, 'slice': 'hard', 'metrics': {'task_success': {'mean': .5, 'n': 10, 'coverage': 1.0}}}
        self.assertFalse(evaluate_gate({'groups': [self.group, bad_group]}, self.gate)['passed'])

    def test_gate_command_exits_nonzero_and_writes_explanation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = root / 'cases.jsonl'
            cases.write_text(json.dumps({'id': 'one', 'kind': 'agent', 'source': 'human',
                                         'observation': {'status': 'success', 'stop_reason': 'completed', 'tool_trace': []}}), encoding='utf-8')
            gate = root / 'gate.json'
            gate.write_text(json.dumps(self.gate), encoding='utf-8')
            with self.assertRaises(CommandError):
                call_command('evaluate_quality', cases=str(cases), output=str(root / 'report'), gate=str(gate), stdout=io.StringIO())
            report = json.loads((root / 'report/gate.json').read_text(encoding='utf-8'))
            self.assertFalse(report['passed'])


class LoadTestTests(SimpleTestCase):
    def test_capacity_uses_service_time_and_utilization(self):
        self.assertEqual(capacity_plan(30, .1, .75)['minimum_worker_slots'], 4)
        with self.assertRaises(ValueError):
            capacity_plan(float('nan'), 1)

    @patch('apps.common.loadtest.request_json')
    def test_agent_load_waits_for_completion_and_tracks_execution(self, request):
        request.side_effect = [
            (201, {'id': 'conversation'}, 'r1'), (202, {'id': 'run'}, 'r2'),
            (200, {'status': 'processing'}, 'r3'),
            (200, {'status': 'success', 'stop_reason': 'completed', 'queue_wait_seconds': 2, 'execution_seconds': 30}, 'r4'),
        ]
        report = run_load('http://localhost', mode='agent', manuscript_id='fixture', prompt='test', requests=1,
                          poll_interval=.001, target_rps=.1)
        self.assertEqual(report['success_rate'], 1)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(report['capacity']['minimum_worker_slots'], 5)

    @patch('apps.common.loadtest.request_json')
    def test_budget_exhaustion_is_not_counted_as_task_success(self, request):
        request.side_effect = [
            (201, {'id': 'conversation'}, 'r1'), (202, {'id': 'run'}, 'r2'),
            (200, {'status': 'success', 'stop_reason': 'max_turns'}, 'r3'),
        ]
        report = run_load('http://localhost', mode='agent', manuscript_id='fixture', prompt='test', requests=1)
        self.assertEqual(report['success_rate'], 0)
        self.assertEqual(report['outcomes'], {'max_turns': 1})

    @patch('apps.common.loadtest.request_json', return_value=(429, {}, 'rejected'))
    def test_overload_counts_as_failure(self, request):
        report = run_load('http://localhost', requests=2)
        self.assertEqual(report['success_rate'], 0)
        self.assertEqual(report['outcomes'], {'http_429': 2})

    def test_http_load_against_real_local_http_server(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"degraded": true}')

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = run_load(f'http://127.0.0.1:{server.server_port}', requests=4, concurrency=2)
            self.assertEqual(report['success_rate'], 1)
            self.assertEqual(report['degraded_requests'], 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class MultiprocessMetricsTests(SimpleTestCase):
    def test_scrape_aggregates_independent_process_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, 'PROMETHEUS_MULTIPROC_DIR': directory, 'PYTHONPATH': str(settings.BASE_DIR)}
            for _ in range(2):
                subprocess.run([sys.executable, '-c',
                                "from apps.common.telemetry import AGENT_RUNS; AGENT_RUNS.labels(outcome='completed').inc()"],
                               env=environment, check=True, capture_output=True, timeout=15)
            scrape = subprocess.run([sys.executable, '-c',
                                     "from apps.common.telemetry import metrics_payload; print(metrics_payload().decode())"],
                                    env=environment, check=True, capture_output=True, timeout=15, text=True)
            self.assertIn('rrs_agent_executions_total{outcome="completed"} 2.0', scrape.stdout)
