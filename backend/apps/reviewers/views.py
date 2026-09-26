import re

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import ReviewerCoauthorEdge, ReviewerProfile
from .serializers import ReviewerProfileSerializer


def normalize_name(value: str) -> str:
    value = value.strip().lower()
    return re.sub(r'\s+', ' ', value)


class ReviewerProfileViewSet(viewsets.ModelViewSet):
    queryset = ReviewerProfile.objects.select_related('affiliation').all().order_by('name')
    serializer_class = ReviewerProfileSerializer

    @action(detail=False, methods=['post'], url_path='bulk-upsert')
    def bulk_upsert(self, request):
        payload = request.data if isinstance(request.data, list) else request.data.get('items', [])
        if not isinstance(payload, list):
            return Response({'detail': 'Payload must be a list.'}, status=status.HTTP_400_BAD_REQUEST)

        updated = 0
        created = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            email = (item.get('email') or '').strip()
            name = (item.get('name') or '').strip()
            if not name or not email:
                continue
            obj = ReviewerProfile.objects.filter(name=name, email=email).first()
            if obj:
                serializer = self.get_serializer(obj, data=item, partial=True)
                updated += 1
            else:
                serializer = self.get_serializer(data=item)
                created += 1
            serializer.is_valid(raise_exception=True)
            serializer.save()

        return Response({'created': created, 'updated': updated})

    @action(detail=True, methods=['post'], url_path='coauthors')
    def replace_coauthors(self, request, pk=None):
        reviewer = self.get_object()
        edges = request.data if isinstance(request.data, list) else request.data.get('items', [])
        if not isinstance(edges, list):
            return Response({'detail': 'Payload must be a list.'}, status=status.HTTP_400_BAD_REQUEST)

        reviewer.coauthor_edges.all().delete()
        created = 0
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            name = (edge.get('author_name') or '').strip()
            year = int(edge.get('last_coauthor_year') or 0)
            if not name or year <= 0:
                continue
            ReviewerCoauthorEdge.objects.create(
                reviewer=reviewer,
                author_name=name,
                normalized_author_name=normalize_name(name),
                last_coauthor_year=year,
            )
            created += 1

        return Response({'reviewer_id': str(reviewer.id), 'coauthor_edges': created})
