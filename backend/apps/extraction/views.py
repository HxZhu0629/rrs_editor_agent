import json
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.llm import generate_text
from apps.manuscripts.models import Affiliation, Author, Manuscript, ManuscriptAuthor
from .chunking import persist_document_chunks
from .models import DocumentChunk, ExtractionRun
from .parser import parse_document
from .serializers import DocumentChunkSerializer, ExtractionRunSerializer
from .vector_search import advanced_search_document_chunks


def normalize_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r'\s+', ' ', value)
    return value


def parse_json_text(text: str) -> dict:
    text = text.strip()
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.findall(r'```json\s*(.*?)\s*```', text, flags=re.S)
    if fenced:
        try:
            return json.loads(fenced[0])
        except json.JSONDecodeError:
            return {}

    first = text.find('{')
    last = text.rfind('}')
    if first >= 0 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def safe_author_order(value, fallback: int) -> int:
    try:
        num = int(value)
        return num if num > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def apply_parsed_metadata(manuscript: Manuscript, parsed: dict) -> None:
    manuscript.title = parsed.get('title') or manuscript.title
    manuscript.abstract = parsed.get('abstract') or manuscript.abstract
    keywords = parsed.get('keywords') or []
    manuscript.keywords = [str(x).strip() for x in keywords if str(x).strip()]
    manuscript.status = Manuscript.Status.REVIEW_PENDING
    manuscript.extracted_at = timezone.now()
    manuscript.save(update_fields=['title', 'abstract', 'keywords', 'status', 'extracted_at', 'updated_at'])

    authors = parsed.get('authors') or []
    if not isinstance(authors, list):
        return

    manuscript.author_links.all().delete()
    used_orders = set()

    for idx, item in enumerate(authors, start=1):
        if not isinstance(item, dict):
            continue

        name = str(item.get('name') or '').strip()
        if not name:
            continue

        email = str(item.get('email') or '').strip() or None
        affiliation_name = str(item.get('affiliation') or '').strip()
        author_order = safe_author_order(item.get('author_order'), idx)
        while author_order in used_orders:
            author_order += 1
        used_orders.add(author_order)

        is_corresponding = bool(item.get('is_corresponding', False))

        author, _ = Author.objects.get_or_create(
            normalized_name=normalize_name(name),
            email=email,
            defaults={'name': name},
        )
        if author.name != name:
            author.name = name
            author.save(update_fields=['name', 'updated_at'])

        affiliation = None
        if affiliation_name:
            affiliation, _ = Affiliation.objects.get_or_create(
                normalized_name=normalize_name(affiliation_name),
                defaults={'name': affiliation_name},
            )

        ManuscriptAuthor.objects.create(
            manuscript=manuscript,
            author=author,
            affiliation=affiliation,
            author_order=author_order,
            is_corresponding=is_corresponding,
            contact_email=email,
        )


class ManuscriptExtractionView(APIView):
    @transaction.atomic
    def post(self, request, manuscript_id):
        manuscript = get_object_or_404(Manuscript, id=manuscript_id)
        if not manuscript.source_pdf:
            return Response({'detail': 'No source document uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        manuscript.status = Manuscript.Status.EXTRACTING
        manuscript.save(update_fields=['status', 'updated_at'])

        run = ExtractionRun.objects.create(
            manuscript=manuscript,
            model_name=settings.DEEPSEEK_CHAT_MODEL,
            status=ExtractionRun.Status.PENDING,
        )

        try:
            parsed_document = parse_document(Path(manuscript.source_pdf.path))
        except Exception as exc:
            run.status = ExtractionRun.Status.FAILED
            run.parser_name = 'unstructured'
            run.error_message = f'Failed to parse source document: {exc}'
            run.save(update_fields=['status', 'parser_name', 'error_message', 'updated_at'])
            manuscript.status = Manuscript.Status.FAILED
            manuscript.save(update_fields=['status', 'updated_at'])
            return Response(ExtractionRunSerializer(run).data, status=status.HTTP_502_BAD_GATEWAY)

        document_markdown = parsed_document.markdown
        run.parser_name = 'unstructured'
        run.document_markdown = document_markdown
        run.document_blocks = parsed_document.blocks
        run.document_quality = parsed_document.quality
        run.save(update_fields=['parser_name', 'document_markdown', 'document_blocks', 'document_quality', 'updated_at'])
        if not document_markdown:
            run.status = ExtractionRun.Status.FAILED
            run.error_message = 'Document parser returned no text.'
            run.save(update_fields=['status', 'error_message', 'updated_at'])
            manuscript.status = Manuscript.Status.FAILED
            manuscript.save(update_fields=['status', 'updated_at'])
            return Response(ExtractionRunSerializer(run).data, status=status.HTTP_502_BAD_GATEWAY)

        try:
            persist_document_chunks(run, parsed_document.blocks)
        except Exception as exc:
            run.status = ExtractionRun.Status.FAILED
            run.error_message = f'Failed to chunk parsed document: {exc}'
            run.save(update_fields=['status', 'error_message', 'updated_at'])
            manuscript.status = Manuscript.Status.FAILED
            manuscript.save(update_fields=['status', 'updated_at'])
            return Response(ExtractionRunSerializer(run).data, status=status.HTTP_502_BAD_GATEWAY)

        prompt = (
            'Extract journal submission metadata from the supplied structured Markdown. '
            'The Markdown was produced by the Unstructured document parser. '
            'Extract metadata only from the supplied content and return strict JSON with keys: '
            'title (string), abstract (string), keywords (string array), authors (array). '
            'Each author item must contain: name, email, affiliation, author_order (int), is_corresponding (bool). '
            'If unknown, use empty string or false.\n\n'
            f'Structured Markdown:\n{document_markdown}'
        )

        try:
            raw_text = generate_text(prompt, json_mode=True)
            raw_response = {'model': settings.DEEPSEEK_CHAT_MODEL, 'content': raw_text}
            parsed = parse_json_text(raw_text)

            if not parsed:
                raise ValueError('Model returned empty JSON payload.')

            apply_parsed_metadata(manuscript, parsed)

            run.status = ExtractionRun.Status.SUCCESS
            run.raw_response = raw_response
            run.parsed_json = parsed
            run.confidence = 0.7
            run.save(update_fields=['status', 'raw_response', 'parsed_json', 'confidence', 'updated_at'])
            return Response(ExtractionRunSerializer(run).data)
        except Exception as exc:
            run.status = ExtractionRun.Status.FAILED
            run.error_message = str(exc)
            run.save(update_fields=['status', 'error_message', 'updated_at'])
            manuscript.status = Manuscript.Status.FAILED
            manuscript.save(update_fields=['status', 'updated_at'])
            return Response(ExtractionRunSerializer(run).data, status=status.HTTP_502_BAD_GATEWAY)


class ExtractionRunChunkListView(APIView):
    def get(self, request, run_id):
        extraction_run = get_object_or_404(ExtractionRun, id=run_id)
        chunks = extraction_run.document_chunks.select_related('parent')
        kind = request.query_params.get('kind')
        if kind:
            if kind not in DocumentChunk.Kind.values:
                return Response(
                    {'detail': 'kind must be parent or child.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            chunks = chunks.filter(kind=kind)
        return Response(DocumentChunkSerializer(chunks, many=True).data)


class ExtractionRunVectorSearchView(APIView):
    def get(self, request, run_id):
        extraction_run = get_object_or_404(ExtractionRun, id=run_id)
        query = request.query_params.get('q', '')
        try:
            top_k = int(request.query_params.get('top_k', 8))
        except (TypeError, ValueError):
            return Response({'detail': 'top_k must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        if not query.strip():
            return Response({'detail': 'q is required.'}, status=status.HTTP_400_BAD_REQUEST)
        def flag(name):
            value = request.query_params.get(name)
            return None if value is None else value.lower() in {'1', 'true', 'yes', 'on'}

        try:
            payload = advanced_search_document_chunks(
                query,
                extraction_run=extraction_run,
                top_k=top_k,
                query_rewrite=flag('rewrite'),
                hyde=flag('hyde'),
                rerank=flag('rerank'),
                reflect=flag('reflect'),
                candidate_k=request.query_params.get('candidate_k', None),
            )
        except Exception as exc:
            return Response({'detail': f'Vector search failed: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)
        payload['top_k'] = max(1, min(top_k, 100))
        return Response(payload)
