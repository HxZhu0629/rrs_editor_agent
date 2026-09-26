from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.extraction.chunking import build_document_chunks, estimate_tokens, persist_document_chunks, _token_tail
from apps.extraction.models import DocumentChunk, ExtractionRun
from apps.extraction.parser import ParsedDocument, _quality, _table_to_markdown
from apps.extraction.vector_search import advanced_search_document_chunks, embed_document_chunks, search_document_chunks
from apps.manuscripts.models import Manuscript


class DocumentParserTests(APITestCase):
    def test_table_to_markdown_preserves_header_and_rows(self):
        markdown = _table_to_markdown(
            '<table><tr><th>Method</th><th>Score</th></tr>'
            '<tr><td>Hybrid</td><td>0.91</td></tr></table>',
            'Method Score',
        )

        self.assertIn('| Method | Score |', markdown)
        self.assertIn('| Hybrid | 0.91 |', markdown)

    def test_quality_marks_short_documents_for_review(self):
        quality = _quality(
            [{'type': 'narrativetext', 'text': 'short', 'markdown': 'short', 'page': 1}],
            'short',
        )

        self.assertTrue(quality['needs_review'])
        self.assertTrue(quality['scan_suspected'])
        self.assertIn('low_text_content', quality['warnings'])


class DocumentChunkingTests(APITestCase):
    def setUp(self):
        self.blocks = [
            {'index': 0, 'type': 'title', 'text': 'Introduction', 'markdown': '# Introduction', 'page': 1},
            {'index': 1, 'type': 'narrativetext', 'text': 'Background', 'markdown': 'Background', 'page': 1},
            {'index': 2, 'type': 'title', 'text': 'Methods', 'markdown': '# Methods', 'page': 2},
            {'index': 3, 'type': 'narrativetext', 'text': 'Method details', 'markdown': 'Method details', 'page': 2},
        ]

    def test_headings_start_new_parent_chunks(self):
        drafts = build_document_chunks(self.blocks)
        parents = [draft for draft in drafts if draft.kind == DocumentChunk.Kind.PARENT]

        self.assertEqual([parent.section_title for parent in parents], ['Introduction', 'Methods'])
        self.assertEqual(parents[0].source_block_indexes, [0, 1])
        self.assertEqual((parents[1].page_start, parents[1].page_end), (2, 2))

    def test_long_section_creates_overlapping_bounded_children(self):
        sentences = [f'Sentence {index} describes retrieval evidence and reviewer expertise.' for index in range(220)]
        blocks = [
            {'index': 0, 'type': 'title', 'text': 'Results', 'markdown': '# Results', 'page': 1},
            {'index': 1, 'type': 'narrativetext', 'text': ' '.join(sentences), 'markdown': ' '.join(sentences), 'page': 1},
        ]

        drafts = build_document_chunks(blocks)
        children = [draft for draft in drafts if draft.kind == DocumentChunk.Kind.CHILD]

        self.assertGreater(len(children), 1)
        self.assertTrue(all(child.token_count <= 480 for child in children))
        expected_overlap = _token_tail(children[0].content, 64)
        self.assertTrue(children[1].content.startswith(expected_overlap))

    def test_table_is_an_atomic_child_even_when_oversized(self):
        table = '| Column | Value |\n| --- | --- |\n' + '\n'.join(
            f'| field {index} | value {index} |' for index in range(300)
        )
        blocks = [
            {'index': 0, 'type': 'title', 'text': 'Data', 'markdown': '# Data', 'page': 3},
            {'index': 1, 'type': 'table', 'text': 'table', 'markdown': table, 'page': 3},
        ]

        drafts = build_document_chunks(blocks)
        table_children = [draft for draft in drafts if draft.metadata.get('element_type') == 'table']

        self.assertEqual(len(table_children), 1)
        self.assertEqual(table_children[0].content, table)
        self.assertTrue(table_children[0].metadata['oversized_atomic'])
        self.assertGreater(table_children[0].token_count, 480)

    def test_persistence_links_children_to_parent(self):
        manuscript = Manuscript.objects.create(title='chunk-test.pdf')
        run = ExtractionRun.objects.create(manuscript=manuscript, model_name='test')

        persist_document_chunks(run, self.blocks)

        parents = run.document_chunks.filter(kind=DocumentChunk.Kind.PARENT)
        children = run.document_chunks.filter(kind=DocumentChunk.Kind.CHILD)
        self.assertEqual(parents.count(), 2)
        self.assertGreater(children.count(), 0)
        self.assertTrue(all(child.parent_id in set(parents.values_list('id', flat=True)) for child in children))
        self.assertTrue(all(estimate_tokens(child.content) == child.token_count for child in children))

    @patch('apps.extraction.vector_search.embed_texts')
    def test_embedding_backfill_stores_vectors_on_child_chunks(self, mock_embed):
        manuscript = Manuscript.objects.create(title='embedding-test.pdf')
        run = ExtractionRun.objects.create(manuscript=manuscript, model_name='test', status=ExtractionRun.Status.SUCCESS)
        persist_document_chunks(run, self.blocks)
        mock_embed.return_value = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]

        count = embed_document_chunks(run)

        self.assertEqual(count, 2)
        self.assertEqual(run.document_chunks.filter(embedding_vector=[1.0, 0.0, 0.0]).count(), 2)
        self.assertEqual(mock_embed.call_count, 1)

    @patch('apps.extraction.vector_search.embed_texts')
    def test_vector_search_returns_child_and_parent_context(self, mock_embed):
        manuscript = Manuscript.objects.create(title='search-test.pdf')
        run = ExtractionRun.objects.create(manuscript=manuscript, model_name='test', status=ExtractionRun.Status.SUCCESS)
        persist_document_chunks(run, self.blocks)
        run.document_chunks.filter(kind=DocumentChunk.Kind.CHILD).update(embedding_vector=[1.0, 0.0])
        mock_embed.return_value = [[1.0, 0.0]]

        results = search_document_chunks('methods', extraction_run=run, top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['section_title'], 'Methods')
        self.assertIsNotNone(results[0]['parent_id'])
        self.assertIn('parent_content', results[0])

    @patch('apps.extraction.vector_search.embed_texts')
    def test_advanced_search_fuses_bm25_and_vector_routes(self, mock_embed):
        manuscript = Manuscript.objects.create(title='advanced-search.pdf')
        run = ExtractionRun.objects.create(manuscript=manuscript, model_name='test', status=ExtractionRun.Status.SUCCESS)
        persist_document_chunks(run, self.blocks)
        children = list(run.document_chunks.filter(kind=DocumentChunk.Kind.CHILD).order_by('chunk_index'))
        children[0].embedding_vector = [1.0, 0.0]
        children[1].embedding_vector = [0.0, 1.0]
        DocumentChunk.objects.bulk_update(children, ['embedding_vector'])
        mock_embed.return_value = [[0.0, 1.0]]

        payload = advanced_search_document_chunks('methods', extraction_run=run, top_k=2, rerank=False)

        self.assertEqual(payload['retrieval_routes'], ['bm25', 'vector'])
        self.assertEqual(payload['results'][0]['section_title'], 'Methods')
        self.assertIn('bm25', payload['results'][0]['retrieval_routes'])
        self.assertIn('vector', payload['results'][0]['retrieval_routes'])

    @patch('apps.extraction.vector_search.corrective_web_search')
    @patch('apps.extraction.vector_search.embed_texts', return_value=[[]])
    def test_crag_quality_gate_can_trigger_web_fallback(self, _mock_embed, mock_web):
        manuscript = Manuscript.objects.create(title='crag-test.pdf')
        run = ExtractionRun.objects.create(manuscript=manuscript, model_name='test', status=ExtractionRun.Status.SUCCESS)
        persist_document_chunks(run, self.blocks)
        mock_web.return_value = [{'title': 'External evidence', 'url': 'https://example.test'}]

        with self.settings(RAG_CRAG_ENABLED=True, RAG_WEB_SEARCH_URL='https://search-adapter.test'):
            payload = advanced_search_document_chunks('unknown query', extraction_run=run, top_k=2, rerank=False)

        self.assertTrue(payload['crag_triggered'])
        self.assertEqual(payload['web_results'][0]['title'], 'External evidence')

    @patch('apps.extraction.views.advanced_search_document_chunks')
    @patch('apps.extraction.views.generate_text')
    @patch('apps.extraction.views.parse_document')
    def test_vector_search_api_validates_query_and_returns_results(self, mock_parse, mock_generate, mock_search):
        manuscript = Manuscript.objects.create(
            title='api-search.pdf',
            source_pdf=SimpleUploadedFile('api-search.pdf', b'%PDF-1.4 test'),
            status=Manuscript.Status.DRAFT,
        )
        mock_parse.return_value = ParsedDocument(
            markdown='# Paper\n\nEvidence',
            blocks=[
                {'index': 0, 'type': 'title', 'text': 'Paper', 'markdown': '# Paper', 'page': 1},
                {'index': 1, 'type': 'narrativetext', 'text': 'Evidence', 'markdown': 'Evidence', 'page': 1},
            ],
            quality={'parser': 'unstructured', 'block_count': 2, 'needs_review': False},
        )
        mock_generate.return_value = '{"title":"Paper","abstract":"Evidence","keywords":[],"authors":[]}'
        run_response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': manuscript.id}))
        mock_search.return_value = {'query': 'evidence', 'results': [{'chunk_id': 'chunk', 'parent_id': 'parent', 'score': 0.9}]}

        response = self.client.get(
            reverse('extraction-run-vector-search', kwargs={'run_id': run_response.data['id']}),
            {'q': 'evidence', 'top_k': 3},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['score'], 0.9)
        mock_search.assert_called_once()

        missing_query = self.client.get(
            reverse('extraction-run-vector-search', kwargs={'run_id': run_response.data['id']}),
        )
        self.assertEqual(missing_query.status_code, status.HTTP_400_BAD_REQUEST)


class ExtractionApiTests(APITestCase):
    def setUp(self):
        self.manuscript = Manuscript.objects.create(
            title='submission.pdf',
            source_pdf=SimpleUploadedFile('submission.pdf', b'%PDF-1.4 test'),
            status=Manuscript.Status.DRAFT,
        )

    @patch('apps.extraction.views.generate_text')
    @patch('apps.extraction.views.parse_document')
    def test_extraction_persists_structured_document(self, mock_parse, mock_generate):
        mock_parse.return_value = ParsedDocument(
            markdown='# A Structured Paper\n\nAbstract text',
            blocks=[{'index': 0, 'type': 'title', 'text': 'A Structured Paper', 'markdown': '# A Structured Paper', 'page': 1, 'metadata': {}}],
            quality={'parser': 'unstructured', 'block_count': 1, 'needs_review': False},
        )
        mock_generate.return_value = '{"title":"A Structured Paper","abstract":"Abstract text","keywords":[],"authors":[]}'

        response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': self.manuscript.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['parser_name'], 'unstructured')
        self.assertEqual(response.data['document_markdown'], '# A Structured Paper\n\nAbstract text')
        self.assertEqual(response.data['document_blocks'][0]['page'], 1)
        self.assertFalse(response.data['document_quality']['needs_review'])
        self.assertEqual(response.data['parent_chunk_count'], 1)
        self.assertEqual(response.data['child_chunk_count'], 1)
        run = ExtractionRun.objects.get(id=response.data['id'])
        child = run.document_chunks.get(kind=DocumentChunk.Kind.CHILD)
        self.assertIsNotNone(child.parent_id)
        prompt = mock_generate.call_args.args[0]
        self.assertIn('Structured Markdown:', prompt)
        self.assertTrue(mock_generate.call_args.kwargs['json_mode'])

    @patch('apps.extraction.views.generate_text')
    @patch('apps.extraction.views.parse_document')
    def test_each_extraction_run_keeps_its_own_chunks(self, mock_parse, mock_generate):
        mock_parse.return_value = ParsedDocument(
            markdown='# Paper\n\nEvidence',
            blocks=[
                {'index': 0, 'type': 'title', 'text': 'Paper', 'markdown': '# Paper', 'page': 1},
                {'index': 1, 'type': 'narrativetext', 'text': 'Evidence', 'markdown': 'Evidence', 'page': 1},
            ],
            quality={'parser': 'unstructured', 'block_count': 2, 'needs_review': False},
        )
        mock_generate.return_value = '{"title":"Paper","abstract":"Evidence","keywords":[],"authors":[]}'

        first_response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': self.manuscript.id}))
        second_response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': self.manuscript.id}))

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        first_run = ExtractionRun.objects.get(id=first_response.data['id'])
        second_run = ExtractionRun.objects.get(id=second_response.data['id'])
        self.assertEqual(first_run.document_chunks.count(), 2)
        self.assertEqual(second_run.document_chunks.count(), 2)

    @patch('apps.extraction.views.generate_text')
    @patch('apps.extraction.views.parse_document')
    def test_chunks_can_be_listed_by_kind(self, mock_parse, mock_generate):
        mock_parse.return_value = ParsedDocument(
            markdown='# Paper\n\nEvidence',
            blocks=[
                {'index': 0, 'type': 'title', 'text': 'Paper', 'markdown': '# Paper', 'page': 1},
                {'index': 1, 'type': 'narrativetext', 'text': 'Evidence', 'markdown': 'Evidence', 'page': 1},
            ],
            quality={'parser': 'unstructured', 'block_count': 2, 'needs_review': False},
        )
        mock_generate.return_value = '{"title":"Paper","abstract":"Evidence","keywords":[],"authors":[]}'
        run_response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': self.manuscript.id}))

        response = self.client.get(
            reverse('extraction-run-chunks', kwargs={'run_id': run_response.data['id']}),
            {'kind': DocumentChunk.Kind.CHILD},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['kind'], DocumentChunk.Kind.CHILD)

    def test_extraction_accepts_docx_source(self):
        self.manuscript.source_pdf.delete(save=False)
        self.manuscript.source_pdf = SimpleUploadedFile('submission.docx', b'PK fake docx')
        self.manuscript.save(update_fields=['source_pdf', 'updated_at'])

        with patch('apps.extraction.views.parse_document', side_effect=RuntimeError('parser unavailable')):
            response = self.client.post(reverse('manuscript-extraction-run', kwargs={'manuscript_id': self.manuscript.id}))

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn('Failed to parse source document', response.data['error_message'])


class ManuscriptUploadTests(APITestCase):
    def test_upload_accepts_docx(self):
        response = self.client.post(
            reverse('manuscript-upload'),
            {'file': SimpleUploadedFile('paper.docx', b'PK fake docx')},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_upload_rejects_unsupported_extension(self):
        response = self.client.post(
            reverse('manuscript-upload'),
            {'file': SimpleUploadedFile('paper.txt', b'plain text')},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
