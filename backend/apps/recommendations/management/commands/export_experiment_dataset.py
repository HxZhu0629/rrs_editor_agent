import csv
import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.manuscripts.models import Manuscript
from apps.recommendations.services import (
    DEFAULT_METHOD,
    METHOD_HYBRID,
    METHOD_LEXICAL,
    METHOD_VECTOR,
    build_recommendations,
)


def cut_text(value: str, limit: int = 240) -> str:
    text = " ".join((value or "").split())
    return text[:limit]


def stringify_list(values) -> str:
    return " | ".join(str(item).strip() for item in (values or []) if str(item).strip())


class Command(BaseCommand):
    help = "Export a reproducible reviewer-recommendation experiment dataset for manual annotation"

    def add_arguments(self, parser):
        parser.add_argument("--sample-size", type=int, default=30, help="Number of manuscripts to sample")
        parser.add_argument("--top-k", type=int, default=10, help="Number of recommendations to export per manuscript")
        parser.add_argument("--seed", type=int, default=42, help="Random seed used for manuscript sampling")
        parser.add_argument("--output-dir", type=str, default="", help="Directory to store exported files")
        parser.add_argument(
            "--method",
            type=str,
            default=DEFAULT_METHOD,
            choices=[METHOD_LEXICAL, METHOD_VECTOR, METHOD_HYBRID],
            help="Recommendation strategy used during export",
        )
        parser.add_argument(
            "--manuscript-ids-file",
            type=str,
            default="",
            help="Optional text file listing manuscript UUIDs, one per line, used instead of random sampling",
        )
        parser.add_argument(
            "--exclude-manuscript-ids-file",
            type=str,
            default="",
            help="Optional text file listing manuscript UUIDs to exclude before random sampling",
        )

    def handle(self, *args, **options):
        sample_size = int(options["sample_size"])
        top_k = int(options["top_k"])
        seed = int(options["seed"])
        method = str(options["method"]).strip().lower()
        manuscript_ids_file = str(options["manuscript_ids_file"] or "").strip()
        exclude_ids_file = str(options["exclude_manuscript_ids_file"] or "").strip()

        if sample_size <= 0:
            raise CommandError("sample-size must be greater than 0")
        if top_k <= 0:
            raise CommandError("top-k must be greater than 0")

        if options["output_dir"]:
            output_dir = Path(options["output_dir"]).resolve()
        else:
            output_dir = (Path(settings.BASE_DIR).parent / "data" / "experiments").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        base_queryset = (
            Manuscript.objects.prefetch_related("author_links__author", "author_links__affiliation")
            .filter(status__in=[Manuscript.Status.CONFIRMED, Manuscript.Status.RECOMMENDED])
            .exclude(title="")
        )
        excluded_ids = []
        if exclude_ids_file:
            exclude_path = Path(exclude_ids_file).resolve()
            if not exclude_path.exists():
                raise CommandError(f"exclude-manuscript-ids-file not found: {exclude_path}")
            excluded_ids = [line.strip() for line in exclude_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if excluded_ids:
                base_queryset = base_queryset.exclude(id__in=excluded_ids)

        if manuscript_ids_file:
            ids_path = Path(manuscript_ids_file).resolve()
            if not ids_path.exists():
                raise CommandError(f"manuscript-ids-file not found: {ids_path}")
            manuscript_ids = [line.strip() for line in ids_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if not manuscript_ids:
                raise CommandError("manuscript-ids-file is empty")
            if sample_size != len(manuscript_ids):
                raise CommandError(
                    f"sample-size={sample_size} does not match ids count={len(manuscript_ids)} from {ids_path}"
                )
            manuscript_map = {str(item.id): item for item in base_queryset.filter(id__in=manuscript_ids)}
            missing_ids = [item for item in manuscript_ids if item not in manuscript_map]
            if missing_ids:
                raise CommandError(f"manuscripts not found for {len(missing_ids)} ids")
            sampled = [manuscript_map[item] for item in manuscript_ids]
        else:
            manuscripts = list(base_queryset)
            if len(manuscripts) < sample_size:
                raise CommandError(f"not enough manuscripts for sampling: have {len(manuscripts)}, need {sample_size}")
            manuscripts.sort(key=lambda item: str(item.id))
            sampled = random.Random(seed).sample(manuscripts, sample_size)
            sampled.sort(key=lambda item: str(item.id))

        stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        prefix = f"{method}_seed{seed}_n{sample_size}_k{top_k}_{stamp}"

        manuscripts_csv = output_dir / f"experiment_{prefix}_manuscripts.csv"
        annotations_csv = output_dir / f"experiment_{prefix}_annotations.csv"
        instructions_txt = output_dir / f"experiment_{prefix}_instructions.txt"

        manuscript_fields = [
            "sample_order",
            "manuscript_id",
            "title",
            "domain",
            "status",
            "keywords",
            "author_names",
            "author_affiliations",
            "abstract_preview",
        ]
        annotation_fields = [
            "recommendation_method",
            "sample_order",
            "manuscript_id",
            "manuscript_title",
            "manuscript_domain",
            "manuscript_keywords",
            "manuscript_author_names",
            "reviewer_rank",
            "reviewer_id",
            "reviewer_name",
            "reviewer_affiliation",
            "reviewer_email",
            "reviewer_score",
            "reviewer_match_score",
            "reviewer_activity_score",
            "reviewer_workload_penalty",
            "candidate_count",
            "matched_keywords",
            "token_overlap_count",
            "reviewer_recent_publication_count",
            "reviewer_expertise_keywords",
            "reviewer_expertise_text_preview",
            "human_label",
            "human_notes",
        ]

        total_annotation_rows = 0

        with manuscripts_csv.open("w", newline="", encoding="utf-8-sig") as manuscript_fp, annotations_csv.open(
            "w", newline="", encoding="utf-8-sig"
        ) as annotation_fp:
            manuscript_writer = csv.DictWriter(manuscript_fp, fieldnames=manuscript_fields)
            annotation_writer = csv.DictWriter(annotation_fp, fieldnames=annotation_fields)
            manuscript_writer.writeheader()
            annotation_writer.writeheader()

            for sample_order, manuscript in enumerate(sampled, start=1):
                author_links = list(manuscript.author_links.all())
                author_names = [link.author.name for link in author_links]
                author_affiliations = [link.affiliation.name for link in author_links if link.affiliation]

                manuscript_writer.writerow(
                    {
                        "sample_order": sample_order,
                        "manuscript_id": manuscript.id,
                        "title": manuscript.title,
                        "domain": manuscript.domain,
                        "status": manuscript.status,
                        "keywords": stringify_list(manuscript.keywords),
                        "author_names": stringify_list(author_names),
                        "author_affiliations": stringify_list(author_affiliations),
                        "abstract_preview": cut_text(manuscript.abstract, 400),
                    }
                )

                rows, candidate_count = build_recommendations(manuscript, top_k, method=method)
                for reviewer_rank, row in enumerate(rows, start=1):
                    reviewer = row["reviewer"]
                    annotation_writer.writerow(
                        {
                            "recommendation_method": method,
                            "sample_order": sample_order,
                            "manuscript_id": manuscript.id,
                            "manuscript_title": manuscript.title,
                            "manuscript_domain": manuscript.domain,
                            "manuscript_keywords": stringify_list(manuscript.keywords),
                            "manuscript_author_names": stringify_list(author_names),
                            "reviewer_rank": reviewer_rank,
                            "reviewer_id": reviewer.id,
                            "reviewer_name": reviewer.name,
                            "reviewer_affiliation": reviewer.affiliation.name if reviewer.affiliation else "",
                            "reviewer_email": reviewer.email,
                            "reviewer_score": f"{row['score']:.6f}",
                            "reviewer_match_score": f"{row['match_score']:.6f}",
                            "reviewer_activity_score": f"{row['activity_score']:.6f}",
                            "reviewer_workload_penalty": f"{row['workload_penalty']:.6f}",
                            "candidate_count": candidate_count,
                            "matched_keywords": stringify_list(row["rationale"].get("matched_keywords")),
                            "token_overlap_count": row["rationale"].get("token_overlap_count", 0),
                            "reviewer_recent_publication_count": row["rationale"].get("recent_publication_count", 0),
                            "reviewer_expertise_keywords": stringify_list(reviewer.expertise_keywords),
                            "reviewer_expertise_text_preview": cut_text(reviewer.expertise_text, 300),
                            "human_label": "",
                            "human_notes": "",
                        }
                    )
                    total_annotation_rows += 1

        instructions_txt.write_text(
            "\n".join(
                [
                    "Manual annotation guide",
                    "1. Open the annotations CSV file in Excel or WPS.",
                    "2. Fill the human_label column with one of: 2 (relevant), 1 (partially relevant), 0 (not relevant).",
                    "3. Use human_notes for short justification when unsure.",
                    "4. Suggested evaluation setup: sample-size manuscripts, each with Top-K recommendations.",
                    f"5. This export used method={method}, sample_size={sample_size}, top_k={top_k}, seed={seed}.",
                    (
                        f"6. Manuscripts were fixed by ids from {Path(manuscript_ids_file).resolve()}."
                        if manuscript_ids_file
                        else "6. Manuscripts were sampled randomly using the given seed."
                    ),
                    (
                        f"7. Sampling excluded {len(excluded_ids)} manuscript ids from {Path(exclude_ids_file).resolve()}."
                        if excluded_ids
                        else "7. No manuscript ids were excluded before sampling."
                    ),
                    "8. After annotation, the CSV can be used to compute Precision@K, HitRate@K, MRR, and nDCG.",
                ]
            ),
            encoding="utf-8-sig",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"method={method} sampled_manuscripts={sample_size} annotation_rows={total_annotation_rows} "
                f"manuscripts_csv={manuscripts_csv} annotations_csv={annotations_csv} instructions={instructions_txt}"
            )
        )
