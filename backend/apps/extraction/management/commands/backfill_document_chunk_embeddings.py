from django.core.management.base import BaseCommand

from apps.extraction.models import ExtractionRun
from apps.extraction.vector_search import embed_document_chunks


class Command(BaseCommand):
    help = 'Backfill child document chunk embeddings with the configured Ollama model'

    def add_arguments(self, parser):
        parser.add_argument('--run-id', default='')
        parser.add_argument('--batch-size', type=int, default=32)
        parser.add_argument('--only-missing', action='store_true')

    def handle(self, *args, **options):
        queryset = ExtractionRun.objects.filter(status=ExtractionRun.Status.SUCCESS).order_by('created_at')
        if options['run_id']:
            queryset = queryset.filter(id=options['run_id'])
        total = 0
        for run in queryset:
            count = embed_document_chunks(
                run,
                batch_size=options['batch_size'],
                only_missing=options['only_missing'],
            )
            total += count
            self.stdout.write(f'{run.id}: embedded {count} child chunks')
        self.stdout.write(self.style.SUCCESS(f'updated={total}'))
