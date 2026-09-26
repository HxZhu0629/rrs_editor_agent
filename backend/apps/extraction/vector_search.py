import math
import re
from collections import Counter
from functools import lru_cache
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from django.conf import settings
from django.utils import timezone

from apps.common.llm import generate_text
from apps.common.circuit import CircuitBreaker, CircuitOpen
from apps.common.telemetry import DEGRADATIONS, RETRIEVAL_LATENCY, event, observe
from apps.recommendations.embeddings import cosine_similarity, embed_texts

from .models import DocumentChunk, ExtractionRun


embedding_circuit = CircuitBreaker()


WORD_PATTERN = re.compile(
    r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[A-Za-z0-9]+(?:[._\'’/-][A-Za-z0-9]+)*',
    flags=re.UNICODE,
)


def compose_chunk_embedding_text(chunk: DocumentChunk) -> str:
    section = f'Section: {chunk.section_title}\n' if chunk.section_title else ''
    return f'{section}{chunk.content}'.strip()


def embed_document_chunks(extraction_run: ExtractionRun, batch_size: int = 32, only_missing: bool = True) -> int:
    queryset = extraction_run.document_chunks.filter(kind=DocumentChunk.Kind.CHILD).order_by('chunk_index')
    if only_missing:
        queryset = queryset.filter(embedding_vector=[])
    chunks = list(queryset)
    updated_count = 0
    for start in range(0, len(chunks), max(1, batch_size)):
        batch = chunks[start:start + max(1, batch_size)]
        vectors = embed_texts([compose_chunk_embedding_text(chunk) for chunk in batch])
        now = timezone.now()
        for chunk, vector in zip(batch, vectors):
            chunk.embedding_vector = vector
            chunk.embedding = vector if len(vector) == settings.OLLAMA_EMBED_DIMENSION else None
            chunk.embedding_model = settings.OLLAMA_EMBED_MODEL
            chunk.embedding_updated_at = now
            chunk.updated_at = now
        DocumentChunk.objects.bulk_update(
            batch,
            ['embedding_vector', 'embedding', 'embedding_model', 'embedding_updated_at', 'updated_at'],
        )
        updated_count += len(batch)
    return updated_count


def _tokens(text: str) -> List[str]:
    return [match.group(0).lower() for match in WORD_PATTERN.finditer(text or '')]


def _parse_text_response(raw: str) -> str:
    raw = (raw or '').strip()
    if not raw:
        return ''
    try:
        import json
        value = json.loads(raw)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ('query', 'rewritten_query', 'hypothesis', 'answer', 'text'):
                if isinstance(value.get(key), str) and value[key].strip():
                    return value[key].strip()
    except (TypeError, ValueError):
        pass
    return raw.strip('` ').strip()


def rewrite_query(query: str) -> str:
    query = (query or '').strip()
    if not query:
        return ''
    prompt = (
        'Rewrite the user query into one concise search query for an academic paper knowledge base. '
        'Preserve proper nouns, technical terms, years, and constraints. Return only the query text.\n'
        f'User query: {query}'
    )
    try:
        return _parse_text_response(generate_text(prompt)) or query
    except Exception:
        return query


def generate_hypothetical_document(query: str) -> str:
    query = (query or '').strip()
    if not query:
        return ''
    prompt = (
        'Write a short hypothetical academic passage that would answer this query. '
        'Use likely domain terminology, but do not claim specific sources. Return only the passage.\n'
        f'Query: {query}'
    )
    try:
        return _parse_text_response(generate_text(prompt))
    except Exception:
        return ''


def _bm25_scores(query: str, chunks: Sequence[DocumentChunk]) -> List[Tuple[DocumentChunk, float]]:
    query_terms = _tokens(query)
    if not query_terms or not chunks:
        return []
    documents = [_tokens(f'{chunk.section_title} {chunk.content}') for chunk in chunks]
    document_frequency = Counter(term for terms in documents for term in set(terms))
    average_length = sum(len(terms) for terms in documents) / max(1, len(documents))
    scores = []
    total_documents = len(documents)
    for chunk, terms in zip(chunks, documents):
        frequencies = Counter(terms)
        document_length = len(terms)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency.get(term, 0)
            idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.2 * (0.25 + 0.75 * document_length / max(1, average_length))
            score += idf * (frequency * 2.2) / max(0.001, denominator)
        if score > 0:
            scores.append((chunk, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    return scores


def _vector_scores(query: str, chunks: Sequence[DocumentChunk], diagnostics=None) -> List[Tuple[DocumentChunk, float]]:
    if not chunks:
        return []
    original_count = len(chunks)
    chunks = [chunk for chunk in chunks if not chunk.embedding_model or chunk.embedding_model == settings.OLLAMA_EMBED_MODEL]
    if len(chunks) != original_count and diagnostics is not None:
        diagnostics.append('embedding_model_mismatch')
    if not chunks:
        return []
    vectors = embed_texts([query])
    query_vector = vectors[0] if vectors else []
    if not query_vector:
        return []
    if not all(math.isfinite(float(value)) for value in query_vector):
        raise ValueError('Non-finite query embedding')
    incompatible = sum(bool(chunk.embedding_vector) and len(chunk.embedding_vector) != len(query_vector) for chunk in chunks)
    if incompatible:
        if diagnostics is not None:
            diagnostics.append('embedding_dimension_mismatch')
        observe(DEGRADATIONS, component='document_index', reason='dimension_mismatch')
        event('index_incompatible', skipped_chunks=incompatible)
    from django.db import connection

    if connection.vendor == 'postgresql' and len(query_vector) == getattr(settings, 'OLLAMA_EMBED_DIMENSION', 768):
        try:
            from pgvector.django import CosineDistance
            rows = DocumentChunk.objects.select_related('parent').filter(
                id__in=[chunk.id for chunk in chunks], embedding__isnull=False,
            ).annotate(distance=CosineDistance('embedding', query_vector)).order_by('distance')[:len(chunks)]
            return [(chunk, max(0.0, 1.0 - float(chunk.distance))) for chunk in rows]
        except Exception:
            pass
    scored = []
    for chunk in chunks:
        vector = chunk.embedding_vector or []
        if vector and len(vector) == len(query_vector) and all(math.isfinite(float(value)) for value in vector):
            scored.append((chunk, (cosine_similarity(query_vector, vector) + 1.0) / 2.0))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def corrective_web_search(query: str) -> List[Dict[str, Any]]:
    """Call an optional project-owned search adapter for CRAG fallback."""
    url = getattr(settings, 'RAG_WEB_SEARCH_URL', '')
    if not url:
        return []
    try:
        response = requests.post(url, json={'query': query}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get('results', payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        return [row for row in rows[:10] if isinstance(row, dict)]
    except Exception:
        return []


def reflect_on_results(query: str, results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Ask the LLM whether retrieved evidence supports the query (Self-RAG hook)."""
    if not results:
        return {'score': 0.0, 'supported': False, 'reason': 'no_local_evidence'}
    excerpts = '\n\n'.join(
        f"[{index}] {item.get('section_title', '')}\n{item.get('content', '')[:1200]}"
        for index, item in enumerate(results[:5], start=1)
    )
    prompt = (
        'Evaluate whether the retrieved passages are relevant evidence for the query. '
        'Return strict JSON with score (number 0 to 1), supported (boolean), and reason (short string).\n'
        f'Query: {query}\nPassages:\n{excerpts}'
    )
    try:
        import json
        value = json.loads(generate_text(prompt, json_mode=True))
        score = max(0.0, min(1.0, float(value.get('score', 0.0))))
        return {'score': score, 'supported': bool(value.get('supported', score >= 0.5)), 'reason': str(value.get('reason', ''))}
    except Exception:
        return {'score': 0.0, 'supported': False, 'reason': 'reflection_failed'}


def _rrf_fuse(routes: Dict[str, List[Tuple[DocumentChunk, float]]], k: int, limit: int):
    fused: Dict[str, Dict[str, Any]] = {}
    for route, rows in routes.items():
        for rank, (chunk, score) in enumerate(rows, start=1):
            entry = fused.setdefault(str(chunk.id), {'chunk': chunk, 'rrf_score': 0.0, 'route_scores': {}})
            entry['rrf_score'] += 1.0 / (k + rank)
            entry['route_scores'][route] = score
    values = list(fused.values())
    values.sort(key=lambda item: item['rrf_score'], reverse=True)
    return [(item['chunk'], {'rrf_score': item['rrf_score'], **item['route_scores']}) for item in values[:limit]]


@lru_cache(maxsize=2)
def _load_chunk_cross_encoder(model_name: str, max_length: int):
    from sentence_transformers import CrossEncoder
    return CrossEncoder(model_name, max_length=max_length)


def _rerank_chunks(query: str, candidates, enabled=None, degraded_reasons=None):
    model_name = getattr(settings, 'RERANKER_MODEL', '')
    enabled = getattr(settings, 'RAG_RERANK_ENABLED', True) if enabled is None else enabled
    if not model_name or not enabled or not candidates:
        return candidates
    try:
        model = _load_chunk_cross_encoder(model_name, getattr(settings, 'RERANKER_MAX_LENGTH', 512))
        scores = model.predict([[query, compose_chunk_embedding_text(chunk)] for chunk, _ in candidates])
        reranked = []
        for (chunk, route_scores), score in zip(candidates, scores):
            normalized = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(score)))))
            reranked.append((chunk, {**route_scores, 'rerank_score': normalized}))
        reranked.sort(key=lambda item: item[1].get('rerank_score', 0.0), reverse=True)
        return reranked
    except Exception as exc:
        if degraded_reasons is not None:
            degraded_reasons.append('reranker_unavailable')
        observe(DEGRADATIONS, component='reranker', reason='unavailable')
        event('dependency_degraded', component='reranker', error_type=type(exc).__name__)
        return candidates


def _row(chunk: DocumentChunk, score: float, route_scores: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    parent = chunk.parent
    return {
        'chunk_id': str(chunk.id),
        'parent_id': str(parent.id) if parent else None,
        'score': round(float(score), 6),
        'content': chunk.content,
        'section_title': chunk.section_title,
        'page_start': chunk.page_start,
        'page_end': chunk.page_end,
        'source_block_indexes': chunk.source_block_indexes,
        'metadata': chunk.metadata,
        'parent_content': parent.content if parent else chunk.content,
        'parent_page_start': parent.page_start if parent else chunk.page_start,
        'parent_page_end': parent.page_end if parent else chunk.page_end,
        'retrieval_routes': sorted(route_scores or {}),
        'route_scores': {key: round(float(value), 6) for key, value in (route_scores or {}).items()},
    }


def advanced_search_document_chunks(
    query: str,
    extraction_run: Optional[ExtractionRun] = None,
    top_k: int = 8,
    query_rewrite: Optional[bool] = None,
    hyde: Optional[bool] = None,
    rerank: Optional[bool] = None,
    reflect: Optional[bool] = None,
    candidate_k: Optional[int] = None,
) -> Dict[str, Any]:
    original_query = (query or '').strip()
    if not original_query:
        return {'query': '', 'rewritten_query': '', 'hyde_used': False, 'results': [], 'retrieval_quality': 'empty'}
    top_k = max(1, min(int(top_k), 100))
    candidate_k = max(top_k, min(int(candidate_k or getattr(settings, 'RAG_RERANK_CANDIDATES', 20)), 200))
    rewrite_enabled = getattr(settings, 'RAG_QUERY_REWRITE_ENABLED', False) if query_rewrite is None else bool(query_rewrite)
    hyde_enabled = getattr(settings, 'RAG_HYDE_ENABLED', False) if hyde is None else bool(hyde)
    reflect_enabled = getattr(settings, 'RAG_SELF_REFLECTION_ENABLED', False) if reflect is None else bool(reflect)
    started = perf_counter()
    degraded_reasons = []
    route_status = {}

    def vector_route(text, name):
        try:
            rows = embedding_circuit.call(
                lambda: _vector_scores(text, chunks, diagnostics=degraded_reasons),
                threshold=settings.RAG_EMBED_CIRCUIT_FAILURES,
                cooldown=settings.RAG_EMBED_CIRCUIT_COOLDOWN,
            )
            route_status[name] = 'ok' if rows else 'empty'
            return rows
        except Exception as exc:
            reason = 'circuit_open' if isinstance(exc, CircuitOpen) else 'embedding_unavailable'
            degraded_reasons.append(f'{name}_{reason}')
            route_status[name] = 'unavailable'
            observe(DEGRADATIONS, component='document_embedding', reason=reason)
            event('dependency_degraded', component='document_embedding', reason=reason, error_type=type(exc).__name__)
            return []

    try:
        rewritten_query = rewrite_query(original_query) if rewrite_enabled else original_query
        hypothetical = generate_hypothetical_document(rewritten_query) if hyde_enabled else ''
        queryset = DocumentChunk.objects.select_related('parent').filter(kind=DocumentChunk.Kind.CHILD)
        if extraction_run is not None:
            queryset = queryset.filter(extraction_run=extraction_run)
        chunks = list(queryset)
        lexical_query = f'{original_query} {rewritten_query}' if rewritten_query != original_query else original_query
        routes: Dict[str, List[Tuple[DocumentChunk, float]]] = {
            'bm25': _bm25_scores(lexical_query, chunks),
            'vector': vector_route(rewritten_query, 'vector'),
        }
        route_status['bm25'] = 'ok' if routes['bm25'] else 'empty'
        if hypothetical:
            routes['hyde'] = vector_route(hypothetical, 'hyde')
        fused = _rrf_fuse(routes, max(1, int(getattr(settings, 'RAG_RRF_K', 60))), candidate_k)
        reranked = _rerank_chunks(rewritten_query, fused, enabled=rerank, degraded_reasons=degraded_reasons)
        results = [_row(chunk, scores.get('rerank_score', scores.get('rrf_score', 0.0)), scores) for chunk, scores in reranked[:top_k]]
        reflection = reflect_on_results(rewritten_query, results) if reflect_enabled else None
        crag_triggered = False
        web_results: List[Dict[str, Any]] = []
        rerank_scores = [item['route_scores']['rerank_score'] for item in results if 'rerank_score' in item['route_scores']]
        low_quality = not results or (bool(rerank_scores) and max(rerank_scores) < 0.15)
        if reflection is not None:
            low_quality = low_quality or not reflection.get('supported') or reflection.get('score', 0.0) < 0.5
        if getattr(settings, 'RAG_CRAG_ENABLED', False) and low_quality:
            crag_triggered = True
            web_results = corrective_web_search(rewritten_query)
        return {
            'query': original_query,
            'rewritten_query': rewritten_query,
            'hyde_used': bool(hypothetical),
            'retrieval_routes': sorted(name for name, rows in routes.items() if rows),
            'route_status': route_status,
            'degraded': bool(degraded_reasons),
            'degraded_reasons': degraded_reasons,
            'latency_ms': round((perf_counter() - started) * 1000, 2),
            'candidate_count': len(fused),
            'retrieval_quality': 'insufficient' if low_quality else ('sufficient' if reflection or rerank_scores else 'unverified'),
            'self_reflection': reflection,
            'crag_triggered': crag_triggered,
            'web_results': web_results,
            'results': results,
        }
    finally:
        duration = perf_counter() - started
        observe(RETRIEVAL_LATENCY, duration)
        event('document_retrieval', duration_ms=round(duration * 1000, 2), route_status=route_status, degraded_reasons=degraded_reasons)


def search_document_chunks(query: str, extraction_run: Optional[ExtractionRun] = None, top_k: int = 8) -> List[Dict[str, Any]]:
    """Backward-compatible vector search wrapper."""
    return advanced_search_document_chunks(
        query,
        extraction_run=extraction_run,
        top_k=top_k,
        query_rewrite=False,
        hyde=False,
        rerank=False,
    )['results']
