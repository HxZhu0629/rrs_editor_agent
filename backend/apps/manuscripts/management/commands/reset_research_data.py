from django.core.management.base import BaseCommand
from django.db import transaction

from apps.extraction.models import ExtractionReview, ExtractionRun
from apps.manuscripts.models import Affiliation, Author, Manuscript, ManuscriptAuthor
from apps.recommendations.models import RecommendationItem, RecommendationRun
from apps.reviewers.models import ReviewerCoauthorEdge, ReviewerPaper, ReviewerProfile


class Command(BaseCommand):
    help = "Delete imported manuscripts, reviewers, recommendations, and extraction data for a clean dataset rebuild"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-reference-data",
            action="store_true",
            help="Keep authors and affiliations after deleting experiment/manuscript/reviewer data",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        keep_reference_data = bool(options.get("keep_reference_data"))

        counts = {
            "recommendation_items": RecommendationItem.objects.count(),
            "recommendation_runs": RecommendationRun.objects.count(),
            "extraction_reviews": ExtractionReview.objects.count(),
            "extraction_runs": ExtractionRun.objects.count(),
            "reviewer_papers": ReviewerPaper.objects.count(),
            "reviewer_coauthor_edges": ReviewerCoauthorEdge.objects.count(),
            "reviewer_profiles": ReviewerProfile.objects.count(),
            "manuscript_authorships": ManuscriptAuthor.objects.count(),
            "manuscripts": Manuscript.objects.count(),
            "authors": Author.objects.count(),
            "affiliations": Affiliation.objects.count(),
        }

        RecommendationItem.objects.all().delete()
        RecommendationRun.objects.all().delete()
        ExtractionReview.objects.all().delete()
        ExtractionRun.objects.all().delete()
        ReviewerPaper.objects.all().delete()
        ReviewerCoauthorEdge.objects.all().delete()
        ReviewerProfile.objects.all().delete()
        ManuscriptAuthor.objects.all().delete()
        Manuscript.objects.all().delete()

        deleted_reference = ""
        if not keep_reference_data:
            Author.objects.all().delete()
            Affiliation.objects.all().delete()
            deleted_reference = " authors affiliations"

        self.stdout.write(
            self.style.SUCCESS(
                "reset-complete"
                f" recommendation_items={counts['recommendation_items']}"
                f" recommendation_runs={counts['recommendation_runs']}"
                f" extraction_reviews={counts['extraction_reviews']}"
                f" extraction_runs={counts['extraction_runs']}"
                f" reviewer_papers={counts['reviewer_papers']}"
                f" reviewer_coauthor_edges={counts['reviewer_coauthor_edges']}"
                f" reviewer_profiles={counts['reviewer_profiles']}"
                f" manuscript_authorships={counts['manuscript_authorships']}"
                f" manuscripts={counts['manuscripts']}"
                f"{deleted_reference}"
            )
        )
