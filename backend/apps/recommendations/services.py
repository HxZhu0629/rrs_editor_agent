import re
from datetime import datetime
from typing import Dict, List, Tuple

from apps.manuscripts.models import Manuscript
from .embeddings import compose_manuscript_embedding_text, cosine_similarity, embed_text
from apps.reviewers.models import ReviewerProfile

GENERIC_KEYWORDS = {
    'artificial intelligence',
    'business',
    'computer science',
    'data science',
    'engineering',
    'implementation',
    'language model',
    'machine learning',
    'political science',
    'risk analysis (engineering)',
    'software engineering',
    'systems engineering',
}

METHOD_LEXICAL = 'lexical'
METHOD_VECTOR = 'vector'
METHOD_HYBRID = 'hybrid'
DEFAULT_METHOD = METHOD_HYBRID
RECOMMENDATION_METHODS = {METHOD_LEXICAL, METHOD_VECTOR, METHOD_HYBRID}


def normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', value.strip().lower())


def tokenize(value: str) -> set:
    text = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]+', ' ', value.lower())
    return {part for part in text.split() if part}


def _build_conflict_context(manuscript: Manuscript) -> Tuple[set, set, int]:
    links = list(manuscript.author_links.select_related('author', 'affiliation').all())
    manuscript_affiliations = {
        normalize(link.affiliation.name)
        for link in links
        if link.affiliation
    }
    manuscript_author_names = {
        normalize(link.author.name)
        for link in links
    }
    year_threshold = datetime.now().year - 5
    return manuscript_affiliations, manuscript_author_names, year_threshold


def _check_conflict(reviewer: ReviewerProfile, manuscript_affiliations: set, manuscript_author_names: set, year_threshold: int) -> str:
    reviewer_aff = normalize(reviewer.affiliation.name) if reviewer.affiliation else ''
    if reviewer_aff and reviewer_aff in manuscript_affiliations:
        return 'same_affiliation'

    for edge in reviewer.coauthor_edges.all():
        if edge.last_coauthor_year >= year_threshold and edge.normalized_author_name in manuscript_author_names:
            return 'recent_coauthor'
    return ''


def _top_overlap_terms(values: set, limit: int = 8) -> List[str]:
    return sorted(values, key=lambda item: (-len(item), item))[:limit]


def _select_representative_papers(
    reviewer: ReviewerProfile,
    manuscript_keywords: set,
    manuscript_tokens: set,
) -> List[Dict]:
    papers = []
    for paper in reviewer.papers.all():
        paper_keywords = {normalize(item) for item in (paper.keywords or []) if str(item).strip()}
        keyword_overlap = manuscript_keywords & paper_keywords
        specific_keyword_overlap = {item for item in keyword_overlap if item not in GENERIC_KEYWORDS}
        paper_tokens = tokenize(f'{paper.title} {paper.abstract}')
        token_overlap = manuscript_tokens & paper_tokens
        recency_bonus = 0.0
        if paper.publication_year:
            recency_bonus = max(0, paper.publication_year - 2019) * 0.03
        evidence_score = (
            2.5 * len(specific_keyword_overlap)
            + 1.2 * len(keyword_overlap)
            + min(len(token_overlap) / 12.0, 1.0)
            + recency_bonus
        )
        papers.append(
            {
                'id': str(paper.id),
                'title': paper.title,
                'publication_year': paper.publication_year,
                'doi': paper.doi,
                'matched_keywords': sorted(keyword_overlap),
                'token_overlap_count': len(token_overlap),
                'evidence_score': round(evidence_score, 4),
            }
        )

    papers.sort(
        key=lambda item: (
            item['evidence_score'],
            item['publication_year'] or 0,
            item['token_overlap_count'],
        ),
        reverse=True,
    )
    return papers[:3]


def _build_risk_flags(
    matched_keywords: List[str],
    specific_matched_keywords: List[str],
    text_overlap_terms: List[str],
    activity_score: float,
    workload_penalty: float,
    vector_score: float,
    has_vector: bool,
) -> List[str]:
    flags: List[str] = []
    if has_vector and vector_score >= 0.8 and not specific_matched_keywords:
        flags.append('语义相似度较高，但强关键词支撑偏弱')
    if len(matched_keywords) < 2 and len(text_overlap_terms) < 4:
        flags.append('词面证据较少，推荐主要依赖泛化信号')
    if activity_score < 0.2:
        flags.append('近年发文数量较少，学术活跃度一般')
    if workload_penalty >= 0.6:
        flags.append('当前工作量偏高，实际邀请时建议人工复核')
    return flags


def _build_score_breakdown(
    method: str,
    lexical_match: float,
    vector_score: float,
    activity_score: float,
    specific_keyword_score: float,
    keyword_boost: float,
    workload_penalty: float,
    final_score: float,
    has_vector: bool,
) -> Dict:
    components: List[Dict] = []

    if method == METHOD_LEXICAL or not has_vector:
        components.extend(
            [
                {'key': 'lexical_match', 'label': '词面匹配', 'value': round(0.8 * lexical_match, 4), 'raw': round(lexical_match, 4)},
                {'key': 'activity', 'label': '活跃度加分', 'value': round(0.2 * activity_score, 4), 'raw': round(activity_score, 4)},
                {'key': 'workload', 'label': '工作量扣分', 'value': round(-0.2 * workload_penalty, 4), 'raw': round(workload_penalty, 4)},
            ]
        )
    elif method == METHOD_VECTOR:
        components.extend(
            [
                {'key': 'vector', 'label': '语义相似', 'value': round(0.85 * vector_score, 4), 'raw': round(vector_score, 4)},
                {'key': 'activity', 'label': '活跃度加分', 'value': round(0.10 * activity_score, 4), 'raw': round(activity_score, 4)},
                {'key': 'specific_keyword', 'label': '强关键词加分', 'value': round(0.05 * specific_keyword_score, 4), 'raw': round(specific_keyword_score, 4)},
                {'key': 'workload', 'label': '工作量扣分', 'value': round(-0.12 * workload_penalty, 4), 'raw': round(workload_penalty, 4)},
            ]
        )
    else:
        components.extend(
            [
                {'key': 'vector', 'label': '语义相似', 'value': round(0.5 * vector_score, 4), 'raw': round(vector_score, 4)},
                {'key': 'lexical_match', 'label': '词面匹配', 'value': round(0.4 * lexical_match, 4), 'raw': round(lexical_match, 4)},
                {'key': 'activity', 'label': '活跃度加分', 'value': round(0.05 * activity_score, 4), 'raw': round(activity_score, 4)},
                {'key': 'specific_keyword', 'label': '强关键词加分', 'value': round(0.05 * specific_keyword_score, 4), 'raw': round(specific_keyword_score, 4)},
                {'key': 'keyword_boost', 'label': '关键词增强', 'value': round(keyword_boost, 4), 'raw': round(keyword_boost, 4)},
                {'key': 'workload', 'label': '工作量扣分', 'value': round(-0.12 * workload_penalty, 4), 'raw': round(workload_penalty, 4)},
            ]
        )

    return {
        'method': method,
        'final_score': round(final_score, 4),
        'components': components,
    }


def _build_explanation(
    reviewer: ReviewerProfile,
    matched_keywords: List[str],
    text_overlap_terms: List[str],
    vector_score: float,
    has_vector: bool,
    representative_papers: List[Dict],
    risk_flags: List[str],
) -> str:
    keyword_part = '、'.join(matched_keywords[:3]) if matched_keywords else ''
    overlap_part = '、'.join(text_overlap_terms[:3]) if text_overlap_terms else ''
    paper_part = representative_papers[0]['title'] if representative_papers else ''

    reasons: List[str] = []
    if keyword_part:
        reasons.append(f'与稿件在 {keyword_part} 等关键词上直接匹配')
    if overlap_part:
        reasons.append(f'画像文本与稿件在 {overlap_part} 等术语上重合明显')
    if has_vector:
        reasons.append(f'语义相似度为 {vector_score:.3f}')
    if reviewer.recent_publication_count:
        reasons.append(f'近年发文 {reviewer.recent_publication_count} 篇')
    if paper_part:
        reasons.append(f'代表论文证据包括《{paper_part}》')

    sentence = '；'.join(reasons[:4]) if reasons else '该审稿人与稿件主题存在一定匹配。'
    if risk_flags:
        sentence += f'。需注意：{risk_flags[0]}。'
    else:
        sentence += '。未发现明显冲突信号。'
    return sentence


def _score_candidate(manuscript: Manuscript, reviewer: ReviewerProfile) -> Dict:
    manuscript_keywords = {normalize(x) for x in manuscript.keywords if str(x).strip()}
    reviewer_keywords = {normalize(x) for x in reviewer.expertise_keywords if str(x).strip()}

    overlap = manuscript_keywords & reviewer_keywords
    specific_overlap = {item for item in overlap if item not in GENERIC_KEYWORDS}
    specific_manuscript_keywords = {item for item in manuscript_keywords if item not in GENERIC_KEYWORDS}
    keyword_score = len(overlap) / max(1, len(manuscript_keywords))
    specific_keyword_score = len(specific_overlap) / max(1, len(specific_manuscript_keywords))

    manuscript_tokens = tokenize(f'{manuscript.title} {manuscript.abstract}')
    reviewer_tokens = tokenize(reviewer.expertise_text)
    text_overlap = manuscript_tokens & reviewer_tokens
    text_score = len(text_overlap) / max(1, min(len(manuscript_tokens), 150))

    lexical_match_score = (
        0.5 * specific_keyword_score
        + 0.25 * keyword_score
        + 0.25 * min(text_score * 10, 1.0)
    )
    activity_score = min(reviewer.recent_publication_count / 20.0, 1.0)
    workload_penalty = min(reviewer.workload_score / 10.0, 1.0)

    final_score = 0.8 * lexical_match_score + 0.2 * activity_score - 0.2 * workload_penalty
    return {
        'score': final_score,
        'match_score': lexical_match_score,
        'activity_score': activity_score,
        'workload_penalty': workload_penalty,
        'rationale': {
            'matched_keywords': sorted(overlap),
            'specific_matched_keywords': sorted(specific_overlap),
            'keyword_score': keyword_score,
            'specific_keyword_score': specific_keyword_score,
            'token_overlap_count': len(text_overlap),
            'text_overlap_terms': _top_overlap_terms(text_overlap),
            'recent_publication_count': reviewer.recent_publication_count,
        },
    }


def _apply_method_scores(method: str, scores: Dict, vector_score: float, has_vector: bool) -> Dict:
    lexical_match = scores['match_score']
    specific_keyword_score = scores['rationale'].get('specific_keyword_score', 0.0)
    keyword_boost = min(0.08, 0.04 * len(scores['rationale'].get('specific_matched_keywords', [])))

    scores['rationale']['vector_score'] = vector_score if has_vector else None
    scores['rationale']['lexical_match_score'] = lexical_match
    scores['rationale']['keyword_boost'] = 0.0

    if method == METHOD_LEXICAL or not has_vector:
        scores['rationale']['score_breakdown'] = _build_score_breakdown(
            METHOD_LEXICAL if not has_vector else method,
            lexical_match,
            vector_score,
            scores['activity_score'],
            specific_keyword_score,
            0.0,
            scores['workload_penalty'],
            scores['score'],
            has_vector=False,
        )
        return scores

    if method == METHOD_VECTOR:
        scores['match_score'] = (
            0.85 * vector_score
            + 0.10 * scores['activity_score']
            + 0.05 * specific_keyword_score
        )
        scores['score'] = scores['match_score'] - 0.12 * scores['workload_penalty']
        scores['rationale']['score_breakdown'] = _build_score_breakdown(
            method,
            lexical_match,
            vector_score,
            scores['activity_score'],
            specific_keyword_score,
            0.0,
            scores['workload_penalty'],
            scores['score'],
            has_vector=has_vector,
        )
        return scores

    scores['match_score'] = (
        0.5 * vector_score
        + 0.4 * lexical_match
        + 0.05 * scores['activity_score']
        + 0.05 * specific_keyword_score
        + keyword_boost
    )
    scores['score'] = scores['match_score'] - 0.12 * scores['workload_penalty']
    scores['rationale']['keyword_boost'] = keyword_boost
    scores['rationale']['score_breakdown'] = _build_score_breakdown(
        method,
        lexical_match,
        vector_score,
        scores['activity_score'],
        specific_keyword_score,
        keyword_boost,
        scores['workload_penalty'],
        scores['score'],
        has_vector=has_vector,
    )
    return scores


def build_recommendations(manuscript: Manuscript, top_k: int, method: str = DEFAULT_METHOD) -> Tuple[List[Dict], int]:
    method = (method or DEFAULT_METHOD).strip().lower()
    if method not in RECOMMENDATION_METHODS:
        raise ValueError(f"Unsupported recommendation method: {method}")

    if method == METHOD_HYBRID:
        from .retrieval import build_multiroute_recommendations

        return build_multiroute_recommendations(manuscript, top_k)

    rows: List[Dict] = []
    manuscript_affiliations, manuscript_author_names, year_threshold = _build_conflict_context(manuscript)
    manuscript_keywords = {normalize(x) for x in manuscript.keywords if str(x).strip()}
    manuscript_tokens = tokenize(f'{manuscript.title} {manuscript.abstract}')
    candidates = ReviewerProfile.objects.select_related('affiliation').prefetch_related('coauthor_edges').filter(is_active=True)
    manuscript_embedding = embed_text(compose_manuscript_embedding_text(manuscript))

    for reviewer in candidates:
        conflict = _check_conflict(reviewer, manuscript_affiliations, manuscript_author_names, year_threshold)
        if conflict:
            continue

        scores = _score_candidate(manuscript, reviewer)
        reviewer_embedding = reviewer.embedding_vector or []
        has_vector = bool(manuscript_embedding and reviewer_embedding)
        vector_score = 0.0
        if has_vector:
            raw_similarity = cosine_similarity(manuscript_embedding, reviewer_embedding)
            vector_score = max(0.0, (raw_similarity + 1.0) / 2.0)
        scores = _apply_method_scores(method, scores, vector_score, has_vector)
        scores['rationale']['embedding_model'] = reviewer.embedding_model if has_vector else ''
        scores['rationale']['risk_flags'] = _build_risk_flags(
            scores['rationale'].get('matched_keywords', []),
            scores['rationale'].get('specific_matched_keywords', []),
            scores['rationale'].get('text_overlap_terms', []),
            scores['activity_score'],
            scores['workload_penalty'],
            vector_score,
            has_vector,
        )

        row = {
            'reviewer': reviewer,
            'has_vector_score': has_vector,
            'vector_score': vector_score,
            'method': method,
            **scores,
        }
        rows.append(row)

    rows.sort(key=lambda x: (x['has_vector_score'], x['score']), reverse=True)
    selected_rows = rows[:top_k]
    selected_ids = [row['reviewer'].id for row in selected_rows]
    selected_reviewers = {
        reviewer.id: reviewer
        for reviewer in ReviewerProfile.objects.prefetch_related('papers').filter(id__in=selected_ids)
    }

    for row in selected_rows:
        reviewer = selected_reviewers.get(row['reviewer'].id, row['reviewer'])
        row['reviewer'] = reviewer
        representative_papers = _select_representative_papers(
            reviewer,
            manuscript_keywords,
            manuscript_tokens,
        )
        row['rationale']['representative_papers'] = representative_papers
        row['rationale']['explanation'] = _build_explanation(
            reviewer,
            row['rationale'].get('matched_keywords', []),
            row['rationale'].get('text_overlap_terms', []),
            row.get('vector_score', 0.0),
            row['has_vector_score'],
            representative_papers,
            row['rationale'].get('risk_flags', []),
        )

    return selected_rows, len(rows)
