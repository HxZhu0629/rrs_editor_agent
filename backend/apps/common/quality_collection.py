from time import perf_counter
import hashlib
import json

from django.conf import settings
from django.test.utils import override_settings


def snapshot_agent(run):
    return {
        'status': run.status, 'stop_reason': run.stop_reason, 'answer': run.answer,
        'tool_trace': run.tool_trace, 'turn_count': run.turn_count,
        'pending_approval': run.pending_approval, 'error_message': run.error_message,
        'latency_ms': ((run.execution_seconds + run.queue_wait_seconds) * 1000
                       if run.execution_seconds is not None and run.queue_wait_seconds is not None else None),
        'timing_scope': 'latest_execution_segment_including_queue',
        'queue_wait_seconds': run.queue_wait_seconds,
        'execution_seconds': run.execution_seconds,
        'request_id': run.request_id,
        'created_at': run.created_at.isoformat(), 'updated_at': run.updated_at.isoformat(),
    }


def collect_case(case):
    if case['kind'] == 'agent':
        from apps.agent.models import AgentRun

        run = AgentRun.objects.get(id=case['run_id'])
        if case.get('query') and case['query'] != run.user_input:
            raise ValueError('Case query differs from AgentRun.user_input; labels may target a different run.')
        return {**case, 'query': run.user_input, 'observation': snapshot_agent(run)}
    started = perf_counter()
    mode = case.get('mode', 'hybrid')
    target = case.get('target', 'document')
    cutoff = case.get('k', 5)
    if target == 'reviewer':
        from apps.manuscripts.models import Manuscript
        from apps.recommendations.services import build_recommendations

        if mode not in ('lexical', 'vector', 'hybrid'):
            raise ValueError('Reviewer mode must be lexical, vector, or hybrid.')
        manuscript = Manuscript.objects.get(id=case['manuscript_id'])
        rows, count = build_recommendations(manuscript, cutoff, method=mode)
        observation = {
            'ranked_ids': [str(row['reviewer'].id) for row in rows], 'candidate_count': count,
            'results': [{'reviewer_id': str(row['reviewer'].id), 'score': row['score'], 'rationale': row['rationale']} for row in rows],
            'input_snapshot': {'title': manuscript.title, 'abstract': manuscript.abstract, 'keywords': manuscript.keywords},
        }
    elif target == 'document':
        from apps.extraction.models import DocumentChunk, ExtractionRun
        from apps.extraction.vector_search import _bm25_scores, _row, _vector_scores, advanced_search_document_chunks

        if mode not in ('bm25', 'vector', 'hybrid'):
            raise ValueError('Document mode must be bm25, vector, or hybrid.')
        if not str(case.get('query', '')).strip():
            raise ValueError('A retrieval query must be filled in before collection.')
        extraction = ExtractionRun.objects.get(id=case['extraction_run_id'])
        chunks = list(extraction.document_chunks.select_related('parent').filter(kind=DocumentChunk.Kind.CHILD))
        chunk_ids = {str(chunk.id) for chunk in chunks}
        if any(item not in chunk_ids for item in (case.get('qrels') or {})):
            raise ValueError('qrels contain IDs outside this extraction; check the index snapshot.')
        options = case.get('options', {})
        if any(key not in ('query_rewrite', 'hyde', 'rerank', 'reflect') or type(value) is not bool for key, value in options.items()):
            raise ValueError('options only accepts boolean query_rewrite, hyde, rerank, reflect.')
        if mode == 'hybrid':
            with override_settings(RAG_CRAG_ENABLED=False):
                observation = advanced_search_document_chunks(
                    case['query'], extraction, top_k=cutoff,
                    **{key: options.get(key, False) for key in ('query_rewrite', 'hyde', 'rerank', 'reflect')},
                )
        else:
            scoring = _bm25_scores if mode == 'bm25' else _vector_scores
            rows = scoring(case['query'], chunks)[:cutoff]
            observation = {'results': [_row(chunk, score, {mode: score}) for chunk, score in rows]}
        observation['ranked_ids'] = [row['chunk_id'] for row in observation['results']]
        observation['index'] = {
            'corpus_sha256': hashlib.sha256(json.dumps([
                {'id': str(chunk.id), 'content': chunk.content, 'section_title': chunk.section_title,
                 'page_start': chunk.page_start, 'page_end': chunk.page_end,
                 'parent_content': chunk.parent.content if chunk.parent else ''}
                for chunk in sorted(chunks, key=lambda chunk: str(chunk.id))
            ], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
            'child_count': len(chunks),
            'embedded_child_count': sum(bool(chunk.embedding_vector) or chunk.embedding is not None for chunk in chunks),
            'embedding_models': sorted({chunk.embedding_model for chunk in chunks if chunk.embedding_model}),
            'configured_embedding_model': settings.OLLAMA_EMBED_MODEL,
            'rerank_applied': any('rerank_score' in row.get('route_scores', {}) for row in observation['results']),
        }
    else:
        raise ValueError('target must be document or reviewer.')
    observation['latency_ms'] = (perf_counter() - started) * 1000
    return {**case, 'observation': observation}
