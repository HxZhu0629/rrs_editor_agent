#!/usr/bin/env python
import argparse
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

OPENALEX_URL = "https://api.openalex.org/works"

SEARCH_QUERIES = [
    "large language model software engineering code generation",
    "large language model program repair",
    "llm bug fixing code",
    "code review large language model",
    "code understanding llm",
    "software engineering copilots",
]

KEYWORD_GUARD = {
    "large language model",
    "llm",
    "code generation",
    "program repair",
    "bug fixing",
    "software engineering",
    "code review",
    "code summarization",
}
CS_CONCEPT_ID = "C41008148"


def build_abstract(inv_idx: Dict) -> str:
    if not inv_idx:
        return ""
    max_pos = -1
    for positions in inv_idx.values():
        if positions:
            max_pos = max(max_pos, max(positions))
    if max_pos < 0:
        return ""
    words = [""] * (max_pos + 1)
    for token, positions in inv_idx.items():
        for pos in positions:
            if 0 <= pos < len(words):
                words[pos] = token
    return " ".join(w for w in words if w).strip()


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def guard_match(title: str, abstract: str, concepts: List[str]) -> bool:
    hay = f"{title} {abstract} {' '.join(concepts)}".lower()
    return any(k in hay for k in KEYWORD_GUARD)


def stable_fake_email(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", name.lower())[:20] or "author"
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return f"{base}.{digest}@noemail.local"


class ProgressLogger:
    def __init__(self, log_file: str = "") -> None:
        self.path = Path(log_file).resolve() if log_file else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line, flush=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def transform_work(work: Dict) -> Dict:
    title = normalize_space(work.get("title") or "")
    abstract = normalize_space(build_abstract(work.get("abstract_inverted_index") or {}))
    pub_year = work.get("publication_year")
    pub_date = work.get("publication_date")

    concepts = []
    for c in work.get("concepts") or []:
        name = normalize_space(c.get("display_name") or "")
        if name:
            concepts.append(name)

    authors = []
    for idx, au in enumerate(work.get("authorships") or [], start=1):
        author_obj = au.get("author") or {}
        name = normalize_space(author_obj.get("display_name") or "")
        if not name:
            continue

        institutions = au.get("institutions") or []
        affiliation = ""
        if institutions:
            affiliation = normalize_space((institutions[0] or {}).get("display_name") or "")
        if not affiliation:
            raws = au.get("raw_affiliation_strings") or []
            if raws:
                affiliation = normalize_space(raws[0])

        email = normalize_space(au.get("author_email") or "")
        if not email:
            email = stable_fake_email(name)

        authors.append(
            {
                "name": name,
                "email": email,
                "affiliation": affiliation,
                "author_order": idx,
                "is_corresponding": bool(au.get("is_corresponding", False)),
                "orcid": normalize_space(author_obj.get("orcid") or "") or None,
            }
        )

    venue = ""
    primary_location = work.get("primary_location") or {}
    src = primary_location.get("source") or {}
    venue = normalize_space(src.get("display_name") or "")

    paper = {
        "source": "openalex",
        "source_id": work.get("id"),
        "doi": work.get("doi"),
        "title": title,
        "abstract": abstract,
        "keywords": sorted(list({x for x in concepts[:10] if x})),
        "publication_year": pub_year,
        "publication_date": pub_date,
        "venue": venue,
        "domain": "software_engineering_llm",
        "authors": authors,
    }
    return paper


def load_checkpoint(path: str, log: Callable[[str], None]) -> Dict[str, Dict]:
    if not path:
        return {}
    p = Path(path).resolve()
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        items = payload.get("items") or []
        rows = {}
        for row in items:
            sid = row.get("source_id")
            if sid:
                rows[sid] = row
        log(f"checkpoint-loaded file={p} rows={len(rows)}")
        return rows
    except Exception as exc:
        log(f"checkpoint-load-failed file={p} err={exc}")
        return {}


def save_checkpoint(path: str, rows: Dict[str, Dict], from_year: int) -> None:
    if not path:
        return
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "openalex",
            "domain": "software_engineering_llm",
            "count": len(rows),
            "from_year": from_year,
            "queries": SEARCH_QUERIES,
        },
        "items": list(rows.values()),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_dataset(
    target_count: int,
    from_year: int,
    mailto: str,
    per_page: int = 100,
    log: Callable[[str], None] = print,
    initial_rows: Optional[Dict[str, Dict]] = None,
    checkpoint_file: str = "",
    checkpoint_every: int = 1,
) -> List[Dict]:
    session = requests.Session()
    session.trust_env = False
    all_rows: Dict[str, Dict] = dict(initial_rows or {})

    common_filter = f"concepts.id:{CS_CONCEPT_ID},from_publication_date:{from_year}-01-01,language:en"
    request_count = 0

    for q_idx, query in enumerate(SEARCH_QUERIES, start=1):
        log(f"query-start {q_idx}/{len(SEARCH_QUERIES)}: {query}")
        page = 1
        while len(all_rows) < target_count and page <= 40:
            params = {
                "search": query,
                "filter": common_filter,
                "sort": "cited_by_count:desc",
                "per-page": per_page,
                "page": page,
                "mailto": mailto,
            }
            request_count += 1
            data = None
            for retry in range(3):
                try:
                    resp = session.get(OPENALEX_URL, params=params, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception as exc:
                    wait_s = 1.5 * (retry + 1)
                    log(f"request-retry query={q_idx} page={page} retry={retry + 1} err={exc}")
                    time.sleep(wait_s)
            if data is None:
                log(f"request-failed query={q_idx} page={page}, skip-to-next-page")
                page += 1
                continue

            results = data.get("results") or []
            if not results:
                log(f"query-empty query={q_idx} page={page}, stop-query")
                break

            before = len(all_rows)
            for work in results:
                sid = work.get("id")
                if not sid or sid in all_rows:
                    continue

                row = transform_work(work)
                if not row["title"] or not row["authors"]:
                    continue
                if not guard_match(row["title"], row["abstract"], row["keywords"]):
                    continue

                all_rows[sid] = row
                if len(all_rows) >= target_count:
                    break

            accepted = len(all_rows) - before
            log(
                f"query={q_idx} page={page} results={len(results)} accepted={accepted} total={len(all_rows)}/{target_count} requests={request_count}"
            )
            if checkpoint_file and (page % max(1, checkpoint_every) == 0):
                save_checkpoint(checkpoint_file, all_rows, from_year)
                log(f"checkpoint-saved file={checkpoint_file} rows={len(all_rows)}")
            page += 1
            time.sleep(0.15)

        if len(all_rows) >= target_count:
            log("target reached, stop download")
            break

    rows = list(all_rows.values())
    rows.sort(key=lambda x: ((x.get("publication_year") or 0), x.get("title") or ""), reverse=True)
    return rows[:target_count]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SE+LLM papers from OpenAlex")
    parser.add_argument("--count", type=int, default=700)
    parser.add_argument("--from-year", type=int, default=2020)
    parser.add_argument("--output", type=str, default="data/openalex_se_llm_700.json")
    parser.add_argument("--mailto", type=str, default="student@example.com")
    parser.add_argument("--log-file", type=str, default="data/download_progress.log")
    parser.add_argument("--checkpoint-file", type=str, default="data/openalex_se_llm_700.checkpoint.json")
    parser.add_argument("--checkpoint-every", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    logger = ProgressLogger(args.log_file)
    logger.log(
        f"download-start target={args.count} from_year={args.from_year} output={args.output} cs_filter={CS_CONCEPT_ID}"
    )
    start = time.time()

    initial_rows = {}
    if args.resume:
        initial_rows = load_checkpoint(args.checkpoint_file, logger.log)

    rows = fetch_dataset(
        target_count=args.count,
        from_year=args.from_year,
        mailto=args.mailto,
        log=logger.log,
        initial_rows=initial_rows,
        checkpoint_file=args.checkpoint_file,
        checkpoint_every=args.checkpoint_every,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "openalex",
            "domain": "software_engineering_llm",
            "count": len(rows),
            "from_year": args.from_year,
            "queries": SEARCH_QUERIES,
        },
        "items": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.checkpoint_file:
        save_checkpoint(args.checkpoint_file, {x["source_id"]: x for x in rows if x.get("source_id")}, args.from_year)
    elapsed = round(time.time() - start, 2)
    logger.log(f"download-finish saved={out} count={len(rows)} elapsed_s={elapsed}")


if __name__ == "__main__":
    main()
