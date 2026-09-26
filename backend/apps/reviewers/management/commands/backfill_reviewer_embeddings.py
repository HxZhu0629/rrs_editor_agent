from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.recommendations.embeddings import compose_reviewer_embedding_text, embed_texts
from apps.reviewers.models import ReviewerProfile


class Command(BaseCommand):
    help = "Backfill reviewer profile embeddings with the configured Ollama embedding model"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=32)
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--only-missing", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        batch_size = max(1, int(options["batch_size"]))
        limit = max(0, int(options["limit"]))
        only_missing = bool(options["only_missing"])

        queryset = ReviewerProfile.objects.select_related("affiliation").filter(is_active=True).order_by("name", "email")
        if only_missing:
            queryset = queryset.filter(embedding_vector=[])
        if limit:
            queryset = queryset[:limit]

        reviewers = list(queryset)
        if not reviewers:
            self.stdout.write(self.style.WARNING("no reviewers to embed"))
            return

        updated_count = 0
        for start in range(0, len(reviewers), batch_size):
            batch = reviewers[start : start + batch_size]
            texts = [compose_reviewer_embedding_text(reviewer) for reviewer in batch]
            vectors = embed_texts(texts)
            now = timezone.now()

            for reviewer, vector in zip(batch, vectors):
                reviewer.embedding_vector = vector
                reviewer.embedding_model = settings.OLLAMA_EMBED_MODEL
                reviewer.embedding_updated_at = now
                reviewer.updated_at = now

            ReviewerProfile.objects.bulk_update(batch, ["embedding_vector", "embedding_model", "embedding_updated_at", "updated_at"])
            updated_count += len(batch)
            self.stdout.write(f"embedded {updated_count}/{len(reviewers)} reviewers")

        self.stdout.write(self.style.SUCCESS(f"embedding_model={settings.OLLAMA_EMBED_MODEL} updated={updated_count}"))
