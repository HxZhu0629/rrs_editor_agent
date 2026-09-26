import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.common.loadtest import run_load


class Command(BaseCommand):
    help = 'Bounded HTTP/Agent closed-loop load test. Agent mode creates data and calls configured providers.'

    def add_arguments(self, parser):
        parser.add_argument('--base-url', required=True)
        parser.add_argument('--mode', choices=['http', 'agent'], default='http')
        parser.add_argument('--path', default='/health/ready/')
        parser.add_argument('--manuscript-id')
        parser.add_argument('--prompt', default='')
        parser.add_argument('--requests', type=int, default=10)
        parser.add_argument('--concurrency', type=int, default=2)
        parser.add_argument('--timeout', type=float, default=60)
        parser.add_argument('--poll-interval', type=float, default=1)
        parser.add_argument('--target-rps', type=float)
        parser.add_argument('--utilization', type=float, default=.7)
        parser.add_argument('--min-success-rate', type=float, default=.99)
        parser.add_argument('--max-p95-seconds', type=float)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        path = Path(options['output'])
        if path.exists():
            raise CommandError('Output exists; choose a new path.')
        if not 0 <= options['min_success_rate'] <= 1:
            raise CommandError('min-success-rate must be in [0, 1].')
        try:
            report = run_load(**{key: options[key] for key in (
                'base_url', 'mode', 'path', 'manuscript_id', 'prompt', 'requests', 'concurrency',
                'timeout', 'poll_interval', 'target_rps', 'utilization',
            )})
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as output:
                json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'success_rate={report["success_rate"]:.3f} throughput={report["completed_throughput_rps"]:.3f}/s output={path}')
        if report['success_rate'] < options['min_success_rate'] or (
            options['max_p95_seconds'] is not None and report['all_latency_p95_seconds'] > options['max_p95_seconds']
        ):
            raise CommandError('Load test gate failed; inspect outcome counts and samples.')
