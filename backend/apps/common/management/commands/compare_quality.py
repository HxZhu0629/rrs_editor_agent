from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.common.quality_comparison import compare_cases, write_comparison
from apps.common.quality_io import load_cases


class Command(BaseCommand):
    help = 'Compare frozen evaluation case snapshots by ID, preserving missing evidence and bad-case changes.'

    def add_arguments(self, parser):
        parser.add_argument('--baseline', required=True, help='Baseline report cases.jsonl')
        parser.add_argument('--candidate', required=True, help='Candidate report cases.jsonl')
        parser.add_argument('--output', required=True)
        parser.add_argument('--fail-on-regression', action='store_true', help='Fail on new flags or missing evidence; use evaluate_quality --gate for metric thresholds.')

    def handle(self, *args, **options):
        output = Path(options['output'])
        if output.exists():
            raise CommandError('Output already exists; choose a new directory.')
        try:
            result = compare_cases(load_cases(options['baseline']), load_cases(options['candidate']))
            output.mkdir(parents=True, exist_ok=False)
            write_comparison(result, output)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'cases={result["cases"]} outcomes={result["outcomes"]} report={output / "comparison.md"}')
        if options['fail_on_regression'] and any(result['outcomes'].get(status) for status in ('regressed', 'inconclusive')):
            raise CommandError('Regression or missing evidence detected; see comparison.json.')
