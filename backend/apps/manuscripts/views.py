import re
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Affiliation, Author, Manuscript, ManuscriptAuthor
from .serializers import (
    ManuscriptConfirmSerializer,
    ManuscriptDetailSerializer,
    ManuscriptListSerializer,
    ManuscriptUploadSerializer,
)


def normalize_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'\s+', ' ', value)
    return value


class ManuscriptListView(APIView):
    def get(self, request):
        qs = Manuscript.objects.all().order_by('-created_at')
        return Response(ManuscriptListSerializer(qs, many=True).data)


class ManuscriptUploadView(APIView):
    def post(self, request):
        serializer = ManuscriptUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_obj = serializer.validated_data['file']
        if not file_obj.name.lower().endswith(('.pdf', '.docx')):
            return Response({'detail': 'Only PDF and DOCX files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

        manuscript = Manuscript.objects.create(
            title=file_obj.name,
            source_pdf=file_obj,
            status=Manuscript.Status.DRAFT,
        )
        return Response(ManuscriptDetailSerializer(manuscript).data, status=status.HTTP_201_CREATED)


class ManuscriptDetailView(APIView):
    def get(self, request, manuscript_id):
        manuscript = get_object_or_404(Manuscript, id=manuscript_id)
        return Response(ManuscriptDetailSerializer(manuscript).data)


class ManuscriptConfirmView(APIView):
    @transaction.atomic
    def patch(self, request, manuscript_id):
        manuscript = get_object_or_404(Manuscript, id=manuscript_id)
        serializer = ManuscriptConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        manuscript.title = data['title']
        manuscript.abstract = data.get('abstract', '')
        manuscript.keywords = data.get('keywords', [])
        manuscript.domain = data.get('domain', '')
        manuscript.status = Manuscript.Status.CONFIRMED
        manuscript.confirmed_at = timezone.now()
        manuscript.save(update_fields=['title', 'abstract', 'keywords', 'domain', 'status', 'confirmed_at', 'updated_at'])

        manuscript.author_links.all().delete()
        for item in sorted(data['authors'], key=lambda x: x['author_order']):
            author_name = item['name'].strip()
            author_email = (item.get('email') or '').strip() or None
            normalized = normalize_name(author_name)
            author, _ = Author.objects.get_or_create(
                normalized_name=normalized,
                email=author_email,
                defaults={'name': author_name},
            )
            if author.name != author_name:
                author.name = author_name
                author.save(update_fields=['name', 'updated_at'])

            affiliation = None
            affiliation_name = (item.get('affiliation') or '').strip()
            if affiliation_name:
                affiliation, _ = Affiliation.objects.get_or_create(
                    normalized_name=normalize_name(affiliation_name),
                    defaults={'name': affiliation_name},
                )

            ManuscriptAuthor.objects.create(
                manuscript=manuscript,
                author=author,
                affiliation=affiliation,
                author_order=item['author_order'],
                is_corresponding=item.get('is_corresponding', False),
                is_co_first=item.get('is_co_first', False),
                contact_email=author_email,
            )

        manuscript.refresh_from_db()
        return Response(ManuscriptDetailSerializer(manuscript).data)
