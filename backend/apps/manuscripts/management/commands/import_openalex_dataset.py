import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.manuscripts.models import Affiliation, Author, Manuscript, ManuscriptAuthor
from apps.reviewers.models import ReviewerCoauthorEdge, ReviewerPaper, ReviewerProfile


def normalize_name(value: str) -> str:
    value = (value or "").strip().lower()
    return re.sub(r"\s+", " ", value)


def cut(value: str, max_len: int) -> str:
    return (value or "").strip()[:max_len]


def normalize_orcid(value: str):
    raw = (value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^https?://(www\.)?orcid\.org/", "", raw, flags=re.I)
    raw = raw.strip("/")
    raw = cut(raw, 32)
    return raw or None


def ensure_email(email: str, name: str) -> str:
    email = (email or "").strip().lower()
    if email and "@" in email:
        return cut(email, 254)
    base = re.sub(r"[^a-z0-9]+", "", name.lower())[:20] or "author"
    return f"{base}@noemail.local"


def parse_publication_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Import OpenAlex SE+LLM dataset json into manuscripts and reviewer profiles"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True, help="Path to dataset JSON file")
        parser.add_argument("--domain", type=str, default="software_engineering_llm")
        parser.add_argument("--limit", type=int, default=0, help="Limit papers to import, 0 means all")
        parser.add_argument(
            "--manuscripts-only",
            action="store_true",
            help="Import manuscripts and authors only, without creating or updating reviewer profiles",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["file"]).resolve()
        if not path.exists():
            raise CommandError(f"dataset file not found: {path}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            raise CommandError("dataset has no items")

        limit = int(options.get("limit") or 0)
        if limit > 0:
            items = items[:limit]

        domain = options["domain"]
        manuscripts_only = bool(options.get("manuscripts_only"))
        imported_papers = 0

        reviewer_acc = defaultdict(
            lambda: {
                "keywords": set(),
                "titles": [],
                "count": 0,
                "affiliation": None,
                "coauthors": defaultdict(int),
                "papers": [],
            }
        )

        for item in items:
            title = cut(item.get("title") or "", 512)
            abstract = (item.get("abstract") or "").strip()
            keywords = [k.strip() for k in (item.get("keywords") or []) if str(k).strip()]
            authors = item.get("authors") or []
            if not title or not authors:
                continue

            manuscript = Manuscript.objects.create(
                domain=domain,
                title=title,
                abstract=abstract,
                keywords=keywords,
                status=Manuscript.Status.CONFIRMED,
                confirmed_at=timezone.now(),
            )

            link_rows = []
            used_order = set()
            seen_author_ids = set()
            for idx, a in enumerate(authors, start=1):
                name = cut(a.get("name") or "", 150)
                if not name:
                    continue

                email = ensure_email(a.get("email") or "", name)
                author_order = int(a.get("author_order") or idx)
                while author_order in used_order:
                    author_order += 1
                used_order.add(author_order)

                normalized_author_name = cut(normalize_name(name), 150)
                orcid = normalize_orcid(a.get("orcid") or "")
                existing_author = Author.objects.filter(normalized_name=normalized_author_name, email=email).first()
                if existing_author:
                    author = existing_author
                    dirty = False
                    if author.name != name:
                        author.name = name
                        dirty = True
                    if orcid and not author.orcid:
                        author.orcid = orcid
                        dirty = True
                    if dirty:
                        author.save(update_fields=["name", "orcid", "updated_at"])
                elif orcid:
                    author, created = Author.objects.get_or_create(
                        orcid=orcid,
                        defaults={
                            "name": name,
                            "normalized_name": normalized_author_name,
                            "email": email,
                        },
                    )
                    if not created:
                        dirty = False
                        if author.name != name:
                            author.name = name
                            dirty = True
                        if author.normalized_name != normalized_author_name:
                            author.normalized_name = normalized_author_name
                            dirty = True
                        if not author.email and email:
                            author.email = email
                            dirty = True
                        if dirty:
                            author.save(update_fields=["name", "normalized_name", "email", "updated_at"])
                else:
                    author, _ = Author.objects.get_or_create(
                        normalized_name=normalized_author_name,
                        email=email,
                        defaults={"name": name, "orcid": None},
                    )
                if author.id in seen_author_ids:
                    continue
                seen_author_ids.add(author.id)

                affiliation = None
                aff_name = cut(a.get("affiliation") or "", 255)
                if aff_name:
                    affiliation, _ = Affiliation.objects.get_or_create(
                        normalized_name=normalize_name(aff_name),
                        defaults={"name": aff_name},
                    )

                link_rows.append(
                    ManuscriptAuthor(
                        manuscript=manuscript,
                        author=author,
                        affiliation=affiliation,
                        author_order=author_order,
                        is_corresponding=bool(a.get("is_corresponding", False)),
                        contact_email=email,
                    )
                )

            if not link_rows:
                manuscript.delete()
                continue

            ManuscriptAuthor.objects.bulk_create(link_rows)
            imported_papers += 1

            if manuscripts_only:
                continue

            # Accumulate reviewer profile stats and coauthor years
            pub_year = int(item.get("publication_year") or timezone.now().year)
            author_names = [(l.author.name, l.contact_email, l.affiliation.name if l.affiliation else "") for l in link_rows]

            for i, (name, email, aff_name) in enumerate(author_names):
                key = (name, ensure_email(email, name))
                rec = reviewer_acc[key]
                rec["count"] += 1
                rec["keywords"].update(keywords)
                if len(rec["titles"]) < 12:
                    rec["titles"].append(title)
                if aff_name and not rec["affiliation"]:
                    rec["affiliation"] = aff_name
                rec["papers"].append(
                    {
                        "title": title,
                        "abstract": abstract,
                        "keywords": keywords,
                        "publication_year": pub_year,
                        "doi": cut(item.get("doi") or "", 255),
                        "source_id": cut(item.get("source_id") or "", 255),
                        "venue": cut(item.get("venue") or item.get("source") or "", 255),
                        "publication_date": parse_publication_date(item.get("publication_date")),
                    }
                )

                for j, (co_name, _, _) in enumerate(author_names):
                    if i == j:
                        continue
                    co_key = normalize_name(co_name)
                    rec["coauthors"][co_key] = max(rec["coauthors"][co_key], pub_year)

        if manuscripts_only:
            self.stdout.write(self.style.SUCCESS(
                f"imported_papers={imported_papers} manuscripts_only=1 reviewer_profiles_created=0 coauthor_edges=0"
            ))
            return

        # Upsert reviewer profiles and coauthor edges
        reviewer_created = 0
        edge_created = 0
        for (name, email), data in reviewer_acc.items():
            affiliation = None
            if data["affiliation"]:
                affiliation, _ = Affiliation.objects.get_or_create(
                    normalized_name=normalize_name(data["affiliation"]),
                    defaults={"name": data["affiliation"]},
                )

            profile, created = ReviewerProfile.objects.get_or_create(
                name=name,
                email=email,
                defaults={
                    "affiliation": affiliation,
                    "expertise_keywords": sorted(list(data["keywords"]))[:30],
                    "expertise_text": " ".join(data["titles"]),
                    "recent_publication_count": data["count"],
                    "workload_score": 0.0,
                    "is_active": True,
                },
            )
            if not created:
                profile.affiliation = profile.affiliation or affiliation
                profile.expertise_keywords = sorted(list(set(profile.expertise_keywords or []) | data["keywords"]))[:30]
                text = (profile.expertise_text or "").strip()
                add_text = " ".join(data["titles"])
                profile.expertise_text = f"{text} {add_text}".strip()[:4000]
                profile.recent_publication_count = max(profile.recent_publication_count, data["count"])
                profile.save(update_fields=["affiliation", "expertise_keywords", "expertise_text", "recent_publication_count", "updated_at"])
            else:
                reviewer_created += 1

            profile.coauthor_edges.all().delete()
            edges = []
            for co_name_norm, year in data["coauthors"].items():
                display = co_name_norm.title()
                edges.append(
                    ReviewerCoauthorEdge(
                        reviewer=profile,
                        author_name=display,
                        normalized_author_name=co_name_norm,
                        last_coauthor_year=int(year),
                    )
                )
            if edges:
                ReviewerCoauthorEdge.objects.bulk_create(edges)
                edge_created += len(edges)

            existing_paper_keys = {
                ((paper.title or "").strip(), int(paper.publication_year or 0))
                for paper in profile.papers.all()
            }
            paper_rows = []
            for paper in data["papers"]:
                paper_key = ((paper["title"] or "").strip(), int(paper.get("publication_year") or 0))
                if not paper_key[0] or paper_key in existing_paper_keys:
                    continue
                existing_paper_keys.add(paper_key)
                paper_rows.append(
                    ReviewerPaper(
                        reviewer=profile,
                        title=cut(paper["title"], 512),
                        abstract=(paper.get("abstract") or "").strip(),
                        keywords=[k.strip() for k in (paper.get("keywords") or []) if str(k).strip()],
                        publication_year=paper.get("publication_year"),
                        doi=paper.get("doi") or "",
                        source_id=paper.get("source_id") or "",
                        venue=paper.get("venue") or "",
                        publication_date=paper.get("publication_date"),
                    )
                )
            if paper_rows:
                ReviewerPaper.objects.bulk_create(paper_rows)

        self.stdout.write(self.style.SUCCESS(
            f"imported_papers={imported_papers} reviewer_profiles_created={reviewer_created} coauthor_edges={edge_created}"
        ))
