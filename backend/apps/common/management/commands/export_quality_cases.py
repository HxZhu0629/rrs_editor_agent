from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.agent.models import AgentRun
from apps.common.quality import MANUAL_CHECKS
from apps.common.quality_collection import snapshot_agent
from apps.common.quality_io import write_jsonl
from apps.extraction.models import DocumentChunk, ExtractionRun


class Command(BaseCommand):
    help = 'Export local document evidence and existing AgentRun traces for independent human annotation.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)
        parser.add_argument('--limit', type=int, default=20)

    def handle(self, *args, **options):
        if options['limit'] < 1:
            raise CommandError('limit must be positive.')
        path = Path(options['output'])
        if path.exists():
            raise CommandError('Output already exists; choose a new file.')
        cases = []
        for extraction in ExtractionRun.objects.filter(status='success').prefetch_related('document_chunks').order_by('-created_at')[:options['limit']]:
            context = [{
                'id': str(chunk.id), 'section_title': chunk.section_title, 'content': chunk.content,
                'page_start': chunk.page_start, 'page_end': chunk.page_end,
            } for chunk in extraction.document_chunks.all() if chunk.kind == DocumentChunk.Kind.CHILD]
            if context:
                cases.append({
                    'id': f'document-{extraction.id}', 'kind': 'retrieval', 'source': 'unreviewed',
                    'target': 'document', 'mode': 'hybrid', 'slice': 'default', 'k': 5,
                    'extraction_run_id': str(extraction.id), 'query': '',
                    'manuscript_id': str(extraction.manuscript_id),
                    'qrels': None, 'judgments_complete': False, 'source_context': context, 'observation': None,
                })
        for run in AgentRun.objects.select_related('conversation').order_by('-created_at')[:options['limit']]:
            cases.append({
                'id': f'agent-{run.id}', 'kind': 'agent', 'source': 'unreviewed', 'slice': 'default',
                'run_id': str(run.id), 'query': run.user_input,
                'manuscript_id': str(run.conversation.manuscript_id) if run.conversation.manuscript_id else None,
                'judgments': {key: None for key in MANUAL_CHECKS}, 'observation': snapshot_agent(run),
            })
        if not cases:
            raise CommandError('No document chunks or AgentRuns found. Select the database used by the running app.')
        path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(path, cases)
        self.stdout.write(self.style.SUCCESS(f'Exported {len(cases)} unreviewed cases to {path}; fill queries/qrels and judgments before claiming quality scores.'))
