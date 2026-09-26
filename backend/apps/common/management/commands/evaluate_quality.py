import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.quality_collection import collect_case
from apps.common.quality_io import load_cases, source_fingerprint, write_report
from apps.common.quality_gate import evaluate_gate, validate_gate


class Command(BaseCommand):
    help = 'Evaluate saved retrieval results and AgentRun traces, with per-case bad-case reports.'

    def add_arguments(self, parser):
        parser.add_argument('--cases', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--gate', help='JSON metric thresholds, minimum samples and annotation coverage.')
        parser.add_argument('--allow-synthetic-gate', action='store_true')
        parser.add_argument('--collect', action='store_true', help='Run retrieval/read AgentRun before scoring; may call configured retrieval providers.')

    def handle(self, *args, **options):
        path = Path(options['cases'])
        output_dir = Path(options['output'])
        if output_dir.exists():
            raise CommandError('Output already exists; choose a new directory to preserve previous runs.')
        gate = None
        try:
            cases = load_cases(path)
            if options['gate']:
                gate = json.loads(Path(options['gate']).read_text(encoding='utf-8'))
                validate_gate(gate, options['allow_synthetic_gate'])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        if options['collect']:
            collected = []
            for case in cases:
                try:
                    collected.append(collect_case(case))
                except Exception as exc:
                    collected.append({**case, 'observation': {'collection_error': str(exc), 'error_type': type(exc).__name__}})
            cases = collected
        manifest = {
            'dataset_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_sha256': source_fingerprint(Path(settings.BASE_DIR) / 'apps'),
            'collection': 'live_retrieval_and_saved_agent_runs' if options['collect'] else 'saved_observations',
            'gate': gate,
            'configuration': {name: getattr(settings, name, None) for name in (
                'DEEPSEEK_CHAT_MODEL', 'OLLAMA_EMBED_MODEL', 'OLLAMA_EMBED_DIMENSION',
                'RERANKER_MODEL', 'RAG_RRF_K', 'RAG_RERANK_CANDIDATES', 'AGENT_MAX_TURNS', 'AGENT_MAX_TOOL_CALLS',
            )},
            'configuration_scope': 'Current environment; historical AgentRun model/configuration may differ.',
            'token_scope': 'Only model/finalize trace events with provider usage; excludes compaction, embeddings and tool-internal LLM calls.',
        }
        try:
            summary = write_report(cases, output_dir, manifest)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'cases={summary["cases"]} evaluated={summary["evaluated"]} flagged={summary["flagged_cases"]} report={output_dir / "report.md"}'
        ))
        if any(case.get('observation', {}).get('collection_error') for case in cases if case.get('observation')):
            raise CommandError('Some cases could not be collected; see bad_cases.jsonl. They were not scored as successes.')
        if gate:
            result = evaluate_gate(summary, gate, options['allow_synthetic_gate'])
            (output_dir / 'gate.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            if not result['passed']:
                raise CommandError('Quality gate failed; see gate.json for missing evidence or failed thresholds.')
            self.stdout.write(self.style.SUCCESS('Quality gate passed.'))
