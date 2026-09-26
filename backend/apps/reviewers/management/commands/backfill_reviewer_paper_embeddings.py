from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.recommendations.embeddings import compose_reviewer_paper_embedding_text, embed_texts
from apps.reviewers.models import ReviewerPaper


class Command(BaseCommand):
    help = 'Backfill reviewer paper embeddings with the configured Ollama embedding model'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=32)
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--only-missing', action='store_true')

    def handle(self, *args, **options):
        batch_size = max(1, int(options['batch_size']))
        limit = max(0, int(options['limit']))
        only_missing = bool(options['only_missing'])

        queryset = ReviewerPaper.objects.select_related('reviewer').order_by('reviewer_id', '-publication_year')
        if only_missing:
            queryset = queryset.filter(embedding_vector=[])
        if limit:
            queryset = queryset[:limit]

        papers = list(queryset)
        if not papers:
            self.stdout.write(self.style.WARNING('no reviewer papers to embed'))
            return

        updated_count = 0
        for start in range(0, len(papers), batch_size):
            batch = papers[start : start + batch_size]
            vectors = embed_texts([compose_reviewer_paper_embedding_text(paper) for paper in batch])
            now = timezone.now()
            for paper, vector in zip(batch, vectors):
                paper.embedding_vector = vector
                paper.embedding = vector if len(vector) == settings.OLLAMA_EMBED_DIMENSION else None
                paper.embedding_model = settings.OLLAMA_EMBED_MODEL
                paper.embedding_updated_at = now
                paper.updated_at = now
            ReviewerPaper.objects.bulk_update(
                batch,
                ['embedding_vector', 'embedding', 'embedding_model', 'embedding_updated_at', 'updated_at'],
            )
            updated_count += len(batch)
            self.stdout.write(f'embedded {updated_count}/{len(papers)} reviewer papers')

        self.stdout.write(self.style.SUCCESS(
            f'embedding_model={settings.OLLAMA_EMBED_MODEL} updated={updated_count}'
        ))
