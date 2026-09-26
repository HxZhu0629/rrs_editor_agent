from django.core.management.base import BaseCommand
from apps.agent.tasks import reconcile_agent_runs


class Command(BaseCommand):
    help = 'Mark expired queued/running agents failed without replaying side effects.'

    def handle(self, *args, **options):
        self.stdout.write(f'reconciled={reconcile_agent_runs()}')
