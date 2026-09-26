from datetime import datetime
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.manuscripts.models import Affiliation, Author, Manuscript, ManuscriptAuthor
from apps.recommendations.services import METHOD_LEXICAL, METHOD_VECTOR, build_recommendations
from apps.reviewers.models import ReviewerCoauthorEdge, ReviewerPaper, ReviewerProfile


def normalize_name(value: str) -> str:
    return ' '.join((value or '').strip().lower().split())


class RecommendationApiTests(APITestCase):
    def setUp(self):
        self.aff_a = Affiliation.objects.create(name='Org A', normalized_name='org a')
        self.aff_b = Affiliation.objects.create(name='Org B', normalized_name='org b')
        self.aff_c = Affiliation.objects.create(name='Org C', normalized_name='org c')
        self.aff_d = Affiliation.objects.create(name='Org D', normalized_name='org d')

        self.author_alice = Author.objects.create(
            name='Alice Smith',
            normalized_name=normalize_name('Alice Smith'),
            email='alice@example.com',
        )
        self.author_bob = Author.objects.create(
            name='Bob Jones',
            normalized_name=normalize_name('Bob Jones'),
            email='bob@example.com',
        )

        self.manuscript = Manuscript.objects.create(
            title='LLM Security Review',
            abstract='Large language models for software security and test automation.',
            keywords=['Software Security', 'LLM', 'Testing'],
            status=Manuscript.Status.CONFIRMED,
            domain='software_engineering_llm',
        )
        ManuscriptAuthor.objects.create(
            manuscript=self.manuscript,
            author=self.author_alice,
            affiliation=self.aff_a,
            author_order=1,
            is_corresponding=True,
            contact_email='alice@example.com',
        )
        ManuscriptAuthor.objects.create(
            manuscript=self.manuscript,
            author=self.author_bob,
            affiliation=self.aff_b,
            author_order=2,
            contact_email='bob@example.com',
        )

        self.same_affiliation = ReviewerProfile.objects.create(
            name='Same Affiliation Reviewer',
            email='same-aff@example.com',
            affiliation=self.aff_a,
            expertise_keywords=['Software Security', 'LLM'],
            expertise_text='software security llm testing',
            recent_publication_count=8,
            is_active=True,
        )
        self.recent_coauthor = ReviewerProfile.objects.create(
            name='Recent Coauthor Reviewer',
            email='coauthor@example.com',
            affiliation=self.aff_c,
            expertise_keywords=['Software Security', 'Testing'],
            expertise_text='software security testing',
            recent_publication_count=7,
            is_active=True,
        )
        ReviewerCoauthorEdge.objects.create(
            reviewer=self.recent_coauthor,
            author_name='Alice Smith',
            normalized_author_name=normalize_name('Alice Smith'),
            last_coauthor_year=datetime.now().year,
        )
        self.eligible_best = ReviewerProfile.objects.create(
            name='Eligible Best Reviewer',
            email='best@example.com',
            affiliation=self.aff_c,
            expertise_keywords=['Software Security', 'LLM', 'Testing'],
            expertise_text='software security llm testing automation',
            recent_publication_count=10,
            is_active=True,
        )
        self.eligible_other = ReviewerProfile.objects.create(
            name='Eligible Other Reviewer',
            email='other@example.com',
            affiliation=self.aff_d,
            expertise_keywords=['LLM'],
            expertise_text='llm benchmark',
            recent_publication_count=2,
            is_active=True,
        )

    @patch("apps.recommendations.services.embed_text", return_value=[])
    def test_recommendation_run_filters_conflicts_and_reports_candidate_count(self, _mock_embed_text):
        response = self.client.post(reverse('recommendation-run'), {'manuscript_id': str(self.manuscript.id), 'top_k': 20}, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'success')
        self.assertEqual(response.data['summary']['candidate_count'], 2)
        self.assertEqual(response.data['summary']['returned_count'], 2)

        names = [item['reviewer_name'] for item in response.data['items']]
        self.assertEqual(names, ['Eligible Best Reviewer', 'Eligible Other Reviewer'])
        self.assertNotIn('Same Affiliation Reviewer', names)
        self.assertNotIn('Recent Coauthor Reviewer', names)

    @patch("apps.recommendations.services.embed_text", return_value=[1.0, 0.0])
    def test_build_recommendations_supports_lexical_and_vector_methods(self, _mock_embed_text):
        self.eligible_best.embedding_vector = [0.1, 1.0]
        self.eligible_best.embedding_model = 'test-embed'
        self.eligible_best.save(update_fields=['embedding_vector', 'embedding_model'])

        self.eligible_other.embedding_vector = [1.0, 0.0]
        self.eligible_other.embedding_model = 'test-embed'
        self.eligible_other.save(update_fields=['embedding_vector', 'embedding_model'])

        lexical_rows, lexical_candidate_count = build_recommendations(self.manuscript, 2, method=METHOD_LEXICAL)
        vector_rows, vector_candidate_count = build_recommendations(self.manuscript, 2, method=METHOD_VECTOR)

        self.assertEqual(lexical_candidate_count, 2)
        self.assertEqual(vector_candidate_count, 2)
        self.assertEqual(lexical_rows[0]['reviewer'].name, 'Eligible Best Reviewer')
        self.assertEqual(vector_rows[0]['reviewer'].name, 'Eligible Other Reviewer')

    @patch("apps.recommendations.retrieval.embed_text", return_value=[])
    def test_hybrid_recommendation_returns_routes_rerank_and_evidence(self, _mock_embed_text):
        ReviewerPaper.objects.create(
            reviewer=self.eligible_best,
            title='Graph Retrieval for Secure LLM Systems',
            abstract='Graph retrieval and software security for large language models.',
            keywords=['Software Security', 'LLM', 'Testing'],
            publication_year=datetime.now().year,
            doi='https://doi.org/10.1000/test',
        )

        response = self.client.post(
            reverse('recommendation-run'),
            {'manuscript_id': str(self.manuscript.id), 'top_k': 1},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['top_k'], 1)
        self.assertEqual(len(response.data['items']), 1)
        item = response.data['items'][0]
        self.assertGreaterEqual(item['rerank_score'], 0)
        self.assertIn('sparse', item['rationale']['retrieval_routes'])
        self.assertEqual(item['evidence'][0]['paper_title'], 'Graph Retrieval for Secure LLM Systems')
        self.assertTrue(item['evidence'][0]['paper_id'])

    @patch('apps.recommendations.views.AcademicMCPClient.resolve_access')
    def test_paper_access_resolves_open_access_pdf_through_mcp(self, mock_resolve):
        paper = ReviewerPaper.objects.create(
            reviewer=self.eligible_best,
            title='Open Reviewer Evidence',
            publication_year=2026,
            doi='https://doi.org/10.1000/open',
            source_id='W123',
        )
        mock_resolve.return_value = {
            'status': 'resolved',
            'provider': 'OpenAlex',
            'paper': {
                'openalex_id': 'W123',
                'landing_page_url': 'https://example.org/article',
                'open_access_url': 'https://example.org/article',
                'pdf_url': 'https://example.org/article.pdf',
                'is_open_access': True,
                'open_access_status': 'gold',
                'open_access_license': 'cc-by',
            },
        }

        response = self.client.get(reverse('reviewer-paper-access', kwargs={'paper_id': paper.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['doi_url'], 'https://doi.org/10.1000/open')
        self.assertEqual(response.data['pdf_url'], 'https://example.org/article.pdf')
        self.assertTrue(response.data['is_open_access'])
        mock_resolve.assert_called_once_with(
            doi='10.1000/open',
            openalex_id='W123',
            title='Open Reviewer Evidence',
        )

    @patch('apps.recommendations.views.AcademicMCPClient.resolve_access')
    def test_paper_access_keeps_doi_link_when_open_pdf_is_unavailable(self, mock_resolve):
        paper = ReviewerPaper.objects.create(
            reviewer=self.eligible_best,
            title='Publisher Landing Page Only',
            doi='10.1000/closed',
        )
        mock_resolve.return_value = {
            'status': 'not_found',
            'provider': 'OpenAlex',
            'paper': {},
        }

        response = self.client.get(reverse('reviewer-paper-access', kwargs={'paper_id': paper.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'not_found')
        self.assertEqual(response.data['doi_url'], 'https://doi.org/10.1000/closed')
        self.assertEqual(response.data['pdf_url'], '')
