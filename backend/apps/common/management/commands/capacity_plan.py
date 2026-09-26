import json
from django.core.management.base import BaseCommand, CommandError
from apps.common.loadtest import capacity_plan


class Command(BaseCommand):
    help = 'Estimate worker slots from measured mean execution duration and target arrival rate.'

    def add_arguments(self, parser):
        parser.add_argument('--service-seconds', type=float, required=True)
        parser.add_argument('--arrival-rps', type=float, required=True)
        parser.add_argument('--utilization', type=float, default=.7)

    def handle(self, *args, **options):
        try:
            report = capacity_plan(options['service_seconds'], options['arrival_rps'], options['utilization'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2))
