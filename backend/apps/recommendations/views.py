from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.manuscripts.models import Manuscript
from apps.agent.mcp_client import AcademicMCPClient, MCPToolError
from apps.reviewers.models import ReviewerPaper
from .models import RecommendationEvidence, RecommendationItem, RecommendationRun
from .serializers import RecommendationRunSerializer
from .services import DEFAULT_METHOD, build_recommendations


class RecommendationRunView(APIView):
    def post(self, request):
        manuscript_id = request.data.get('manuscript_id')
        top_k = int(request.data.get('top_k', 20))
        top_k = max(1, min(50, top_k))
        method = (request.data.get('method') or DEFAULT_METHOD).strip().lower()

        manuscript = get_object_or_404(Manuscript, id=manuscript_id)

        run = RecommendationRun.objects.create(
            manuscript=manuscript,
            top_k=top_k,
            status=RecommendationRun.Status.PROCESSING,
        )

        try:
            rows, candidate_count = build_recommendations(manuscript, top_k, method=method)
            with transaction.atomic():
                items = []
                for idx, row in enumerate(rows, start=1):
                    items.append(
                        RecommendationItem(
                            run=run,
                            reviewer=row['reviewer'],
                            rank=idx,
                            score=row['score'],
                            match_score=row['match_score'],
                            activity_score=row['activity_score'],
                            workload_penalty=row['workload_penalty'],
                            rerank_score=row.get('rerank_score', row.get('match_score', 0.0)),
                            rationale=row['rationale'],
                        )
                    )
                RecommendationItem.objects.bulk_create(items)

                evidence_rows = []
                for item, row in zip(items, rows):
                    for evidence in row['rationale'].get('evidence', []):
                        evidence_rows.append(
                            RecommendationEvidence(
                                item=item,
                                paper_id=evidence['paper_id'],
                                recall_route=evidence['recall_route'],
                                retrieval_score=evidence['retrieval_score'],
                                rerank_score=row.get('rerank_score', 0.0),
                                evidence_text=evidence['evidence_text'],
                            )
                        )
                if evidence_rows:
                    RecommendationEvidence.objects.bulk_create(evidence_rows)

                manuscript.status = Manuscript.Status.RECOMMENDED
                manuscript.save(update_fields=['status', 'updated_at'])

                run.status = RecommendationRun.Status.SUCCESS
                run.summary = {
                    'candidate_count': candidate_count,
                    'returned_count': len(items),
                    'top_k': top_k,
                    'method': method,
                }
                run.save(update_fields=['status', 'summary', 'updated_at'])
            return Response(RecommendationRunSerializer(run).data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            run.status = RecommendationRun.Status.FAILED
            run.error_message = str(exc)
            run.save(update_fields=['status', 'error_message', 'updated_at'])
            return Response(RecommendationRunSerializer(run).data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RecommendationRunDetailView(APIView):
    def get(self, request, run_id):
        run = get_object_or_404(
            RecommendationRun.objects.prefetch_related(
                'items__reviewer__affiliation',
                'items__evidence__paper',
            ),
            id=run_id,
        )
        return Response(RecommendationRunSerializer(run).data)


class ReviewerPaperAccessView(APIView):
    def get(self, request, paper_id):
        paper = get_object_or_404(ReviewerPaper, id=paper_id)
        doi = str(paper.doi or '').strip()
        for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix):]
                break
        response_data = {
            'paper_id': str(paper.id),
            'title': paper.title,
            'publication_year': paper.publication_year,
            'venue': paper.venue,
            'doi': doi,
            'doi_url': f'https://doi.org/{doi}' if doi else '',
            'provider': 'OpenAlex',
            'status': 'unavailable',
            'openalex_id': '',
            'landing_page_url': '',
            'open_access_url': '',
            'pdf_url': '',
            'is_open_access': False,
            'open_access_status': '',
            'open_access_license': '',
        }
        try:
            result = AcademicMCPClient().resolve_access(
                doi=doi,
                openalex_id=paper.source_id,
                title=paper.title,
            )
        except MCPToolError as exc:
            response_data['error'] = str(exc)
            return Response(response_data, status=status.HTTP_200_OK)

        resolved = result.get('paper') or {}
        response_data.update({
            'status': result.get('status') if result.get('status') != 'success' else 'not_found',
            'provider': resolved.get('provider') or result.get('provider') or 'OpenAlex',
            'openalex_id': resolved.get('openalex_id') or '',
            'landing_page_url': resolved.get('landing_page_url') or '',
            'open_access_url': resolved.get('open_access_url') or '',
            'pdf_url': resolved.get('pdf_url') or '',
            'is_open_access': bool(resolved.get('is_open_access')),
            'open_access_status': resolved.get('open_access_status') or '',
            'open_access_license': resolved.get('open_access_license') or '',
        })
        if resolved:
            response_data['status'] = 'resolved'
        return Response(response_data)
