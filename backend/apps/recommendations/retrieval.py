import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Iterable, List, Set, Tuple

from django.conf import settings

from apps.manuscripts.models import Manuscript
from apps.reviewers.models import ReviewerPaper, ReviewerProfile

from .embeddings import compose_manuscript_embedding_text, cosine_similarity, embed_text


GENERIC_TERMS = {
    'artificial intelligence',
    'computer science',
    'data science',
    'engineering',
    'implementation',
    'language model',
    'machine learning',
    'software engineering',
}


@dataclass
class Candidate:
    reviewer: ReviewerProfile
    route_scores: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, Dict] = field(default_factory=dict)
    rrf_score: float = 0.0
    rerank_score: float = 0.0


def _normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().lower())


def _tokens(value: str) -> Set[str]:
    text = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]+', ' ', (value or '').lower())
    return {part for part in text.split() if part}


def _keyword_set(values: Iterable) -> Set[str]:
    return {_normalize(str(value)) for value in (values or []) if str(value).strip()}


def _manuscript_terms(manuscript: Manuscript, query_keywords: Iterable[str] = ()) -> Tuple[Set[str], Set[str]]:
    keywords = _keyword_set(list(manuscript.keywords or []) + list(query_keywords or []))
    tokens = _tokens(f'{manuscript.title} {manuscript.abstract}')
    return keywords, tokens


def _paper_score(paper: ReviewerPaper, manuscript_keywords: Set[str], manuscript_tokens: Set[str]) -> Tuple[float, Set[str], Set[str]]:
    paper_keywords = _keyword_set(paper.keywords)
    paper_tokens = _tokens(f'{paper.title} {paper.abstract}')
    keyword_overlap = manuscript_keywords & paper_keywords
    token_overlap = manuscript_tokens & paper_tokens
    keyword_score = len(keyword_overlap) / max(1, len(manuscript_keywords))
    token_score = min(len(token_overlap) / 12.0, 1.0)
    return 0.65 * keyword_score + 0.35 * token_score, keyword_overlap, token_overlap


def _paper_evidence(paper: ReviewerPaper, score: float, route: str, keyword_overlap: Set[str], token_overlap: Set[str]) -> Dict:
    return {
        'paper': paper,
        'recall_route': route,
        'retrieval_score': round(score, 6),
        'evidence_text': f'{paper.title}. {paper.abstract[:1600]}'.strip(),
        'matched_keywords': sorted(keyword_overlap),
        'token_overlap_terms': sorted(token_overlap)[:8],
    }


def _profile_sparse_score(reviewer: ReviewerProfile, manuscript_keywords: Set[str], manuscript_tokens: Set[str]) -> float:
    reviewer_keywords = _keyword_set(reviewer.expertise_keywords)
    profile_tokens = _tokens(reviewer.expertise_text)
    keyword_score = len(manuscript_keywords & reviewer_keywords) / max(1, len(manuscript_keywords))
    token_score = min(len(manuscript_tokens & profile_tokens) / 12.0, 1.0)
    return 0.65 * keyword_score + 0.35 * token_score


def _route_sparse(reviewers: List[ReviewerProfile], manuscript_keywords: Set[str], manuscript_tokens: Set[str], limit: int) -> List[Tuple[ReviewerProfile, float, List[Dict]]]:
    try:
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
        from django.db import connection

        if connection.vendor == 'postgresql':
            reviewer_ids = [reviewer.id for reviewer in reviewers]
            reviewer_map = {reviewer.id: reviewer for reviewer in reviewers}
            query_terms = sorted(manuscript_keywords) or sorted(manuscript_tokens)[:12]
            query = SearchQuery(' '.join(query_terms), search_type='websearch', config='english')
            vector = SearchVector('title', weight='A', config='english') + SearchVector('abstract', weight='B', config='english')
            paper_rows = list(
                ReviewerPaper.objects.filter(reviewer_id__in=reviewer_ids)
                .annotate(rank=SearchRank(vector, query))
                .filter(rank__gt=0)
                .order_by('-rank')[: max(limit * 5, limit)]
            )
            grouped: Dict = {}
            for paper in paper_rows:
                reviewer = reviewer_map.get(paper.reviewer_id)
                if not reviewer:
                    continue
                raw_score = float(paper.rank)
                score = raw_score / (1.0 + raw_score)
                keyword_overlap = manuscript_keywords & _keyword_set(paper.keywords)
                token_overlap = manuscript_tokens & _tokens(f'{paper.title} {paper.abstract}')
                row = grouped.setdefault(paper.reviewer_id, {'reviewer': reviewer, 'score': 0.0, 'evidence': []})
                row['score'] = max(row['score'], score)
                row['evidence'].append(_paper_evidence(paper, score, 'sparse', keyword_overlap, token_overlap))
            rows = [
                (
                    row['reviewer'],
                    row['score'],
                    sorted(row['evidence'], key=lambda item: item['retrieval_score'], reverse=True)[:3],
                )
                for row in grouped.values()
            ]
            rows.sort(key=lambda item: item[1], reverse=True)
            if rows:
                return rows[:limit]
    except Exception:
        pass

    rows = []
    for reviewer in reviewers:
        paper_rows = []
        for paper in reviewer.papers.all():
            score, keyword_overlap, token_overlap = _paper_score(paper, manuscript_keywords, manuscript_tokens)
            if score > 0:
                paper_rows.append((_paper_evidence(paper, score, 'sparse', keyword_overlap, token_overlap), score))
        paper_rows.sort(key=lambda item: item[1], reverse=True)
        score = paper_rows[0][1] if paper_rows else _profile_sparse_score(reviewer, manuscript_keywords, manuscript_tokens)
        if score > 0:
            rows.append((reviewer, score, [item[0] for item in paper_rows[:3]]))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _route_dense(reviewers: List[ReviewerProfile], manuscript: Manuscript, limit: int) -> List[Tuple[ReviewerProfile, float, List[Dict]]]:
    if not any(
        paper.embedding is not None or paper.embedding_vector
        for reviewer in reviewers
        for paper in reviewer.papers.all()
    ):
        return []
    try:
        manuscript_vector = embed_text(compose_manuscript_embedding_text(manuscript))
    except Exception:
        return []
    if not manuscript_vector:
        return []

    reviewer_ids = [reviewer.id for reviewer in reviewers]
    paper_rows = []
    if manuscript_vector and len(manuscript_vector) == getattr(settings, 'OLLAMA_EMBED_DIMENSION', 768):
        try:
            from pgvector.django import CosineDistance

            vector_rows = list(
                ReviewerPaper.objects.select_related('reviewer')
                .filter(reviewer_id__in=reviewer_ids, embedding__isnull=False)
                .annotate(distance=CosineDistance('embedding', manuscript_vector))
                .order_by('distance')[: max(limit * 5, limit)]
            )
            if vector_rows:
                for paper in vector_rows:
                    score = max(0.0, min(1.0, 1.0 - float(paper.distance)))
                    paper_rows.append((paper, score))
        except Exception:
            paper_rows = []

    if not paper_rows:
        for reviewer in reviewers:
            for paper in reviewer.papers.all():
                if not paper.embedding_vector or len(paper.embedding_vector) != len(manuscript_vector):
                    continue
                raw_score = cosine_similarity(manuscript_vector, paper.embedding_vector)
                paper_rows.append((paper, max(0.0, min(1.0, (raw_score + 1.0) / 2.0))))
    if not paper_rows:
        return []

    reviewer_rows: Dict = {}
    for paper, score in paper_rows:
        current = reviewer_rows.setdefault(paper.reviewer_id, {'reviewer': paper.reviewer, 'score': 0.0, 'evidence': []})
        current['score'] = max(current['score'], score)
        current['evidence'].append(
            _paper_evidence(paper, score, 'dense', set(), set())
        )

    rows = []
    for row in reviewer_rows.values():
        evidence = sorted(row['evidence'], key=lambda item: item['retrieval_score'], reverse=True)[:3]
        rows.append((row['reviewer'], row['score'], evidence))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _route_metadata(reviewers: List[ReviewerProfile], manuscript_keywords: Set[str], limit: int) -> List[Tuple[ReviewerProfile, float, List[Dict]]]:
    rows = []
    for reviewer in reviewers:
        reviewer_keywords = _keyword_set(reviewer.expertise_keywords)
        keyword_score = len(manuscript_keywords & reviewer_keywords) / max(1, len(manuscript_keywords))
        activity_score = min(reviewer.recent_publication_count / 20.0, 1.0)
        score = 0.7 * keyword_score + 0.3 * activity_score
        if score > 0:
            rows.append((reviewer, score, []))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _build_conflict_sets(manuscript: Manuscript) -> Tuple[Set[str], Set[str], int]:
    links = list(manuscript.author_links.select_related('author', 'affiliation').all())
    affiliations = {_normalize(link.affiliation.name) for link in links if link.affiliation}
    authors = {_normalize(link.author.name) for link in links}
    from datetime import datetime

    return affiliations, authors, datetime.now().year - 5


def _conflict_reason(reviewer: ReviewerProfile, affiliations: Set[str], authors: Set[str], year_threshold: int) -> str:
    reviewer_affiliation = _normalize(reviewer.affiliation.name) if reviewer.affiliation else ''
    if reviewer_affiliation and reviewer_affiliation in affiliations:
        return 'same_affiliation'
    for edge in reviewer.coauthor_edges.all():
        if edge.last_coauthor_year >= year_threshold and edge.normalized_author_name in authors:
            return 'recent_coauthor'
    return ''


def _merge_routes(route_rows: Dict[str, List[Tuple[ReviewerProfile, float, List[Dict]]]], k: int = 60) -> Dict:
    candidates: Dict = {}
    for route, rows in route_rows.items():
        for rank, (reviewer, score, evidence) in enumerate(rows, start=1):
            candidate = candidates.setdefault(reviewer.id, Candidate(reviewer=reviewer))
            candidate.route_scores[route] = score
            candidate.rrf_score += 1.0 / (k + rank)
            for item in evidence:
                paper_id = str(item['paper'].id)
                current = candidate.evidence.get(paper_id)
                if current is None or item['retrieval_score'] > current['retrieval_score']:
                    candidate.evidence[paper_id] = item
                elif current['recall_route'] != item['recall_route']:
                    current['recall_route'] = f"{current['recall_route']}+{item['recall_route']}"
    return candidates


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str, max_length: int):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, max_length=max_length)


def _cross_encoder_scores(query: str, candidates: List[Candidate]) -> Dict:
    model_name = getattr(settings, 'RERANKER_MODEL', '')
    if not model_name:
        return {}
    try:
        model = _load_cross_encoder(model_name, getattr(settings, 'RERANKER_MAX_LENGTH', 512))
        pairs = []
        for candidate in candidates:
            evidence = sorted(candidate.evidence.values(), key=lambda item: item['retrieval_score'], reverse=True)
            evidence_text = ' '.join(item['evidence_text'] for item in evidence[:3])
            profile_text = candidate.reviewer.expertise_text or ', '.join(candidate.reviewer.expertise_keywords or [])
            pairs.append((query, f'{profile_text}\n{evidence_text}'.strip()))
        scores = model.predict(pairs)
        return {candidate.reviewer.id: _sigmoid(float(score)) for candidate, score in zip(candidates, scores)}
    except Exception:
        return {}


def _explanation(candidate: Candidate, manuscript_keywords: Set[str], rerank_score: float) -> str:
    reviewer_keywords = _keyword_set(candidate.reviewer.expertise_keywords)
    overlap = sorted(manuscript_keywords & reviewer_keywords)[:4]
    evidence = sorted(candidate.evidence.values(), key=lambda item: item['retrieval_score'], reverse=True)
    reasons = []
    if overlap:
        reasons.append(f"关键词匹配：{'、'.join(overlap)}")
    if evidence:
        reasons.append(f"代表论文包括《{evidence[0]['paper'].title}》")
    reasons.append(f'综合重排分数为 {rerank_score:.3f}')
    reasons.append(f'召回来源：{"、".join(sorted(candidate.route_scores))}')
    return '；'.join(reasons) + '。'


def build_multiroute_recommendations(
    manuscript: Manuscript,
    top_k: int,
    route_limit: int = 100,
    query_keywords: Iterable[str] = (),
    min_publication_year: int = 0,
    exclude_affiliations: Iterable[str] = (),
    diversify_affiliations: bool = False,
) -> Tuple[List[Dict], int]:
    manuscript_keywords, manuscript_tokens = _manuscript_terms(manuscript, query_keywords)
    affiliations, authors, year_threshold = _build_conflict_sets(manuscript)
    reviewers = list(
        ReviewerProfile.objects.select_related('affiliation')
        .prefetch_related('coauthor_edges', 'papers')
        .filter(is_active=True)
    )
    excluded_affiliations = {_normalize(value) for value in (exclude_affiliations or []) if str(value).strip()}
    eligible_reviewers = [
        reviewer
        for reviewer in reviewers
        if not _conflict_reason(reviewer, affiliations, authors, year_threshold)
        and _normalize(reviewer.affiliation.name if reviewer.affiliation else '') not in excluded_affiliations
        and (
            not min_publication_year
            or any((paper.publication_year or 0) >= min_publication_year for paper in reviewer.papers.all())
        )
    ]
    route_rows = {
        'sparse': _route_sparse(eligible_reviewers, manuscript_keywords, manuscript_tokens, route_limit),
        'metadata': _route_metadata(eligible_reviewers, manuscript_keywords, route_limit),
    }
    route_rows['dense'] = _route_dense(eligible_reviewers, manuscript, route_limit)
    candidates = list(_merge_routes(route_rows).values())
    candidates.sort(key=lambda candidate: candidate.rrf_score, reverse=True)

    query = compose_manuscript_embedding_text(manuscript)
    cross_scores = _cross_encoder_scores(query, candidates)
    for candidate in candidates:
        dense_score = candidate.route_scores.get('dense', 0.0)
        sparse_score = candidate.route_scores.get('sparse', 0.0)
        metadata_score = candidate.route_scores.get('metadata', 0.0)
        candidate.rerank_score = cross_scores.get(
            candidate.reviewer.id,
            0.55 * dense_score + 0.30 * sparse_score + 0.15 * metadata_score,
        )
        activity_score = min(candidate.reviewer.recent_publication_count / 20.0, 1.0)
        workload_penalty = min(candidate.reviewer.workload_score / 10.0, 1.0)

    candidates.sort(
        key=lambda candidate: (
            candidate.rerank_score + 0.08 * min(candidate.rrf_score * 60.0, 1.0) + 0.05 * min(candidate.reviewer.recent_publication_count / 20.0, 1.0) - 0.12 * min(candidate.reviewer.workload_score / 10.0, 1.0),
            candidate.rrf_score,
        ),
        reverse=True,
    )
    if diversify_affiliations:
        selected = []
        seen_affiliations = set()
        for candidate in candidates:
            affiliation = _normalize(candidate.reviewer.affiliation.name if candidate.reviewer.affiliation else '')
            if affiliation and affiliation in seen_affiliations:
                continue
            selected.append(candidate)
            if affiliation:
                seen_affiliations.add(affiliation)
            if len(selected) >= top_k:
                break
        if len(selected) < top_k:
            selected_ids = {candidate.reviewer.id for candidate in selected}
            selected.extend(candidate for candidate in candidates if candidate.reviewer.id not in selected_ids)
            selected = selected[:top_k]
    else:
        selected = candidates[:top_k]
    rows = []
    for candidate in selected:
        evidence = sorted(candidate.evidence.values(), key=lambda item: item['retrieval_score'], reverse=True)[:3]
        activity_score = min(candidate.reviewer.recent_publication_count / 20.0, 1.0)
        workload_penalty = min(candidate.reviewer.workload_score / 10.0, 1.0)
        score = candidate.rerank_score + 0.08 * min(candidate.rrf_score * 60.0, 1.0) + 0.05 * activity_score - 0.12 * workload_penalty
        rows.append(
            {
                'reviewer': candidate.reviewer,
                'score': score,
                'match_score': candidate.rerank_score,
                'activity_score': activity_score,
                'workload_penalty': workload_penalty,
                'rerank_score': candidate.rerank_score,
                'rationale': {
                    'method': 'hybrid_rerank',
                    'retrieval_routes': sorted(candidate.route_scores),
                    'route_scores': {key: round(value, 6) for key, value in candidate.route_scores.items()},
                    'rrf_score': round(candidate.rrf_score, 6),
                    'rerank_score': round(candidate.rerank_score, 6),
                    'matched_keywords': sorted(manuscript_keywords & _keyword_set(candidate.reviewer.expertise_keywords)),
                    'token_overlap_count': len(manuscript_tokens & _tokens(candidate.reviewer.expertise_text)),
                    'text_overlap_terms': sorted(manuscript_tokens & _tokens(candidate.reviewer.expertise_text))[:8],
                    'vector_score': candidate.route_scores.get('dense'),
                    'recent_publication_count': candidate.reviewer.recent_publication_count,
                    'risk_flags': [],
                    'score_breakdown': {
                        'method': 'hybrid_rerank',
                        'final_score': round(score, 6),
                        'components': [
                            {'key': 'rerank', 'label': '重排得分', 'value': round(candidate.rerank_score, 6), 'raw': round(candidate.rerank_score, 6)},
                            {'key': 'rrf', 'label': '多路融合', 'value': round(0.08 * min(candidate.rrf_score * 60.0, 1.0), 6), 'raw': round(candidate.rrf_score, 6)},
                            {'key': 'activity', 'label': '活跃度加分', 'value': round(0.05 * activity_score, 6), 'raw': round(activity_score, 6)},
                            {'key': 'workload', 'label': '工作量扣分', 'value': round(-0.12 * workload_penalty, 6), 'raw': round(workload_penalty, 6)},
                        ],
                    },
                    'representative_papers': [
                        {
                            'id': str(item['paper'].id),
                            'title': item['paper'].title,
                            'publication_year': item['paper'].publication_year,
                            'doi': item['paper'].doi,
                            'matched_keywords': item['matched_keywords'],
                            'token_overlap_count': len(item['token_overlap_terms']),
                        }
                        for item in evidence
                    ],
                    'evidence': [
                        {
                            'paper_id': str(item['paper'].id),
                            'recall_route': item['recall_route'],
                            'retrieval_score': item['retrieval_score'],
                            'evidence_text': item['evidence_text'],
                        }
                        for item in evidence
                    ],
                    'explanation': _explanation(candidate, manuscript_keywords, candidate.rerank_score),
                },
            }
        )
    return rows, len(eligible_reviewers)
