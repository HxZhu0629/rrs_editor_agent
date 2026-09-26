import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.common.quality_io import load_cases, write_jsonl


class Command(BaseCommand):
    help = 'Freeze dev/test splits by manuscript or explicit group_id, keeping related queries in one split.'

    def add_arguments(self, parser):
        parser.add_argument('--cases', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--test-fraction', type=float, default=.2)
        parser.add_argument('--seed', default='rrs-quality-v1')

    def handle(self, *args, **options):
        if not 0 < options['test_fraction'] < 1:
            raise CommandError('test-fraction must be between 0 and 1.')
        output = Path(options['output'])
        if output.exists():
            raise CommandError('Output already exists; choose a new file.')
        try:
            cases = load_cases(options['cases'])
            groups = {}
            for case in cases:
                group = case.get('group_id') or case.get('manuscript_id')
                if not isinstance(group, str) or not group:
                    raise ValueError(f'{case["id"]}: provide manuscript_id or group_id to avoid query leakage.')
                if case.get('split', 'unspecified') != 'unspecified':
                    raise ValueError('Dataset already has a split; preserve the frozen split instead of re-splitting.')
                digest = hashlib.sha256(f'{options["seed"]}:{group}'.encode('utf-8')).hexdigest()
                split = 'test' if int(digest[:16], 16) / 2 ** 64 < options['test_fraction'] else 'dev'
                groups[group] = split
                case.update(split=split, group_id=group)
            if len(set(groups.values())) < 2:
                raise ValueError('Too few independent groups for both splits; collect more manuscripts before freezing.')
            output.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(output, cases)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'cases={len(cases)} groups={len(groups)} test_groups={list(groups.values()).count("test")} output={output}')
