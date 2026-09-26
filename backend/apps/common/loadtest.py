import json
import math
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def capacity_plan(service_seconds, arrival_rps, utilization=.7):
    if any(not math.isfinite(value) or value <= 0 for value in (service_seconds, arrival_rps, utilization)) or utilization >= 1:
        raise ValueError('Service time/rate must be positive and utilization must be in (0, 1).')
    return {
        'mean_service_seconds': service_seconds,
        'target_arrival_rps': arrival_rps,
        'target_utilization': utilization,
        'minimum_worker_slots': math.ceil(service_seconds * arrival_rps / utilization),
        'ideal_saturated_rps_per_slot': 1 / service_seconds,
        'assumptions': 'One execution occupies one worker slot; excludes provider quotas, memory and database limits. Verify by load test.',
    }


def request_json(url, timeout, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(url, data=data, headers={'Content-Type': 'application/json', 'X-Request-ID': f'load-{uuid.uuid4().hex}'})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            return response.status, body, response.headers.get('X-Request-ID')
    except HTTPError as exc:
        return exc.code, {}, exc.headers.get('X-Request-ID')


def run_sample(base_url, mode, path, manuscript_id, prompt, timeout, poll_interval):
    started = time.perf_counter()
    deadline = started + timeout
    result = {'outcome': 'client_error', 'success': False}

    def send(relative, payload=None):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('Sample deadline exceeded')
        return request_json(base_url.rstrip('/') + relative, min(remaining, 30), payload)

    try:
        if mode == 'http':
            status, body, request_id = send(path)
            result.update(status=status, request_id=request_id, degraded=body.get('degraded', False), success=200 <= status < 300)
            result['outcome'] = 'completed' if result['success'] else f'http_{status}'
        else:
            status, body, request_id = send('/api/agent/conversations/', {'manuscript_id': manuscript_id, 'title': 'Load test'})
            if status != 201:
                result.update(status=status, outcome=f'http_{status}', request_id=request_id)
                return result
            result['conversation_id'] = body['id']
            status, body, request_id = send(f'/api/agent/conversations/{body["id"]}/messages/', {'content': prompt})
            result.update(status=status, request_id=request_id)
            if status != 202:
                result['outcome'] = f'http_{status}'
                return result
            result['run_id'] = body['id']
            while True:
                status, body, _ = send(f'/api/agent/runs/{result["run_id"]}/')
                if status != 200:
                    result.update(status=status, outcome=f'poll_http_{status}')
                    break
                if body['status'] != 'processing':
                    success = body['status'] == 'success' and body.get('stop_reason') == 'completed'
                    result.update(success=success, outcome='completed' if success else body.get('stop_reason') or body['status'],
                                  queue_wait_seconds=body.get('queue_wait_seconds'), service_seconds=body.get('execution_seconds'))
                    break
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError('Sample deadline exceeded')
                time.sleep(min(poll_interval, remaining))
    except (TimeoutError, URLError) as exc:
        result['outcome'] = 'timeout' if isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError) else 'connection_error'
    except (ValueError, KeyError, TypeError):
        result['outcome'] = 'invalid_response'
    finally:
        result['latency_seconds'] = time.perf_counter() - started
    return result


def run_load(base_url, mode='http', path='/health/ready/', manuscript_id=None, prompt='', requests=10, concurrency=2,
             timeout=60, poll_interval=1, target_rps=None, utilization=.7):
    parsed = urlsplit(base_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError('base_url must be an HTTP(S) origin.')
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('path must start with a single slash.')
    if not 1 <= concurrency <= 100 or not 1 <= requests <= 10000 or timeout <= 0 or poll_interval <= 0:
        raise ValueError('Invalid request count, concurrency or timeout.')
    if mode not in {'http', 'agent'} or (mode == 'agent' and (not manuscript_id or not prompt.strip())):
        raise ValueError('Agent mode requires manuscript_id and prompt.')
    if target_rps is not None:
        capacity_plan(1, target_rps, utilization)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(run_sample, base_url, mode, path, manuscript_id, prompt, timeout, poll_interval) for _ in range(requests)]
        samples = [future.result() for future in futures]
    elapsed = time.perf_counter() - started
    successes = [sample for sample in samples if sample['success']]
    service_times = [sample['service_seconds'] for sample in successes if sample.get('service_seconds') is not None]
    report = {
        'mode': mode, 'load_model': 'closed_loop', 'concurrency': concurrency, 'requests': requests,
        'elapsed_seconds': elapsed, 'successful_requests': len(successes),
        'success_rate': len(successes) / requests, 'completed_throughput_rps': len(successes) / elapsed,
        'outcomes': dict(Counter(sample['outcome'] for sample in samples)),
        'all_latency_p50_seconds': percentile([sample['latency_seconds'] for sample in samples], .5),
        'all_latency_p95_seconds': percentile([sample['latency_seconds'] for sample in samples], .95),
        'all_latency_p99_seconds': percentile([sample['latency_seconds'] for sample in samples], .99),
        'success_latency_p95_seconds': percentile([sample['latency_seconds'] for sample in successes], .95),
        'success_latency_p99_seconds': percentile([sample['latency_seconds'] for sample in successes], .99),
        'percentile_note': 'Empirical percentiles from this closed-loop sample; small samples do not establish a stable P99 or production capacity.',
        'queue_wait_p95_seconds': percentile([sample['queue_wait_seconds'] for sample in samples if sample.get('queue_wait_seconds') is not None], .95),
        'degraded_requests': sum(bool(sample.get('degraded')) for sample in samples),
        'samples': samples,
    }
    if target_rps and service_times:
        report['capacity'] = capacity_plan(sum(service_times) / len(service_times), target_rps, utilization)
    return report
