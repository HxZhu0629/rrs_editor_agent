from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.manuscripts.models import ManuscriptAuthor
from apps.reviewers.models import ReviewerPaper, ReviewerProfile


def _norm_email(value: str) -> str:
    return (value or "").strip().lower()


class Command(BaseCommand):
    help = "Backfill ReviewerPaper records from existing manuscript authorship data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing reviewer papers before rebuilding",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        reset = bool(options.get("reset"))
        if reset:
            deleted_count, _ = ReviewerPaper.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"deleted_existing_reviewer_papers={deleted_count}"))

        profiles = ReviewerProfile.objects.all().only("id", "name", "email")
        profile_map = {
            ((profile.name or "").strip(), _norm_email(profile.email)): profile.id
            for profile in profiles
        }

        existing_keys = set(
            ReviewerPaper.objects.values_list("reviewer_id", "title", "publication_year")
        )

        rows_by_reviewer = defaultdict(list)
        links = (
            ManuscriptAuthor.objects
            .select_related("author", "manuscript")
            .all()
        )

        for link in links:
            name = (link.author.name or "").strip()
            email = _norm_email(link.contact_email or link.author.email or "")
            reviewer_id = profile_map.get((name, email))
            if not reviewer_id:
                continue

            manuscript = link.manuscript
            year = None
            if manuscript.confirmed_at:
                year = manuscript.confirmed_at.year
            elif manuscript.created_at:
                year = manuscript.created_at.year

            key = (reviewer_id, manuscript.title, year)
            if not manuscript.title or key in existing_keys:
                continue

            existing_keys.add(key)
            rows_by_reviewer[reviewer_id].append(
                ReviewerPaper(
                    reviewer_id=reviewer_id,
                    title=(manuscript.title or "").strip()[:512],
                    abstract=(manuscript.abstract or "").strip(),
                    keywords=[k for k in (manuscript.keywords or []) if str(k).strip()],
                    publication_year=year,
                )
            )

        created = 0
        for rows in rows_by_reviewer.values():
            ReviewerPaper.objects.bulk_create(rows, batch_size=500)
            created += len(rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"reviewer_profiles={len(profile_map)} reviewer_papers_created={created} reviewer_papers_total={ReviewerPaper.objects.count()}"
            )
        )
