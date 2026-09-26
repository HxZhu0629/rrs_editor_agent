from types import SimpleNamespace
from unittest import expectedFailure
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.extraction.models import DocumentChunk, ExtractionRun
from apps.extraction.vector_search import _vector_scores, advanced_search_document_chunks
from apps.manuscripts.models import Manuscript
from apps.recommendations.retrieval import _conflict_reason
from apps.common.circuit import CircuitBreaker


class KnownRetrievalBadCases(TestCase):
    def setUp(self):
        circuit = patch('apps.extraction.vector_search.embedding_circuit', CircuitBreaker())
        circuit.start()
        self.addCleanup(circuit.stop)
        manuscript = Manuscript.objects.create(title='Diagnostic fixture')
        self.extraction = ExtractionRun.objects.create(manuscript=manuscript, model_name='fixture', status='success')
        self.chunk = DocumentChunk.objects.create(
            extraction_run=self.extraction, kind='child', chunk_index=0,
            content='BM25 retrieval evidence', embedding_vector=[1.0, 0.0],
        )

    @patch('apps.extraction.vector_search.embed_texts', side_effect=RuntimeError('embedding offline'))
    def test_document_hybrid_should_keep_bm25_when_embeddings_fail(self, _embed):
        result = advanced_search_document_chunks('BM25', self.extraction, rerank=False, query_rewrite=False, hyde=False, reflect=False)
        self.assertEqual(result['results'][0]['chunk_id'], str(self.chunk.id))
        self.assertTrue(result['degraded'])
        self.assertEqual(result['route_status']['vector'], 'unavailable')
        self.assertEqual(result['retrieval_routes'], ['bm25'])

    @override_settings(RAG_CRAG_ENABLED=True, RAG_RRF_K=60)
    @patch('apps.extraction.vector_search.corrective_web_search', return_value=[])
    @patch('apps.extraction.vector_search.embed_texts', return_value=[[1.0, 0.0]])
    def test_top_rank_in_both_routes_should_not_trigger_crag_by_rrf_scale(self, _embed, _web):
        result = advanced_search_document_chunks('BM25', self.extraction, rerank=False, query_rewrite=False, hyde=False, reflect=False)
        self.assertEqual(result['results'][0]['chunk_id'], str(self.chunk.id))
        self.assertFalse(result['crag_triggered'])

    @patch('apps.extraction.vector_search.embed_texts', return_value=[[1.0, 0.0, 0.0]])
    def test_mismatched_dimensions_should_not_be_ranked_as_neutral_similarity(self, _embed):
        self.assertEqual(_vector_scores('BM25', [self.chunk]), [])

    @override_settings(RAG_RERANK_ENABLED=True)
    @patch('apps.extraction.vector_search._vector_scores', return_value=[])
    @patch('apps.extraction.vector_search._rerank_chunks')
    def test_request_rerank_flag_does_not_mutate_global_settings(self, rerank, _vector):
        from django.conf import settings
        def inspect_settings(query, candidates, **kwargs):
            self.assertTrue(settings.RAG_RERANK_ENABLED)
            self.assertFalse(kwargs['enabled'])
            return candidates
        rerank.side_effect = inspect_settings
        advanced_search_document_chunks('BM25', self.extraction, rerank=False, query_rewrite=False, hyde=False, reflect=False)

    @expectedFailure
    def test_same_institution_alias_should_be_filtered(self):
        reviewer = SimpleNamespace(
            affiliation=SimpleNamespace(name='Tsinghua University'),
            coauthor_edges=SimpleNamespace(all=lambda: []),
        )
        self.assertEqual(_conflict_reason(reviewer, {'清华大学'}, set(), 2021), 'same_affiliation')
