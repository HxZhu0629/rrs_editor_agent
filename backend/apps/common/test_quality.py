import json
import math
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from langchain_core.messages import AIMessage

from apps.agent.graph import run_editor_agent
from apps.agent.models import AgentConversation, AgentRun
from apps.common.quality import evaluate_case, summarize, validate_case
from apps.common.quality_collection import collect_case
from apps.common.quality_io import load_cases, write_jsonl, write_report
from apps.extraction.models import DocumentChunk, ExtractionRun
from apps.manuscripts.models import Manuscript


def retrieval_case():
    return {
        'id': 'retrieval-test', 'kind': 'retrieval', 'source': 'synthetic', 'k': 2,
        'qrels': {'best': 2, 'other': 1, 'irrelevant': 0}, 'judgments_complete': True,
        'observation': {'ranked_ids': ['irrelevant', 'other'], 'latency_ms': 12},
    }


def agent_case():
    return {
        'id': 'agent-test', 'kind': 'agent', 'source': 'synthetic',
        'required_calls': [{'tool': 'recommend_reviewers', 'arguments': {'min_publication_year': 2023}}],
        'allowed_tools': ['recommend_reviewers'], 'forbidden_tools': ['send_email'],
        'observation': {
            'status': 'success', 'stop_reason': 'completed', 'answer': 'Result', 'turn_count': 2,
            'tool_trace': [{'tool': 'recommend_reviewers', 'status': 'success', 'arguments': {'min_publication_year': 2023}}],
        },
    }


class QualityMetricsTests(SimpleTestCase):
    def test_recall_and_ndcg_include_relevant_items_not_returned(self):
        result = evaluate_case(retrieval_case())
        self.assertEqual(result['metrics']['recall_at_k'], 0.5)
        self.assertEqual(result['metrics']['mrr_at_k'], 0.5)
        expected = (1 / math.log2(3)) / (3 + 1 / math.log2(3))
        self.assertAlmostEqual(result['metrics']['ndcg_at_k'], expected)
        self.assertIn('missed_relevant', result['flags'])

    def test_missing_results_consume_precision_slots_and_duplicates_do_not_gain(self):
        case = retrieval_case()
        case['observation']['ranked_ids'] = ['best']
        self.assertEqual(evaluate_case(case)['metrics']['precision_at_k'], 0.5)
        case['observation']['ranked_ids'] = ['best', 'best']
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['recall_at_k'], 0.5)
        self.assertEqual(result['metrics']['precision_at_k'], 0.5)
        self.assertEqual(result['metrics']['duplicate_rate'], 0.5)

    def test_partial_judgments_cannot_produce_recall_or_ndcg(self):
        case = retrieval_case()
        case['judgments_complete'] = False
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['precision_at_k'], 0.5)
        self.assertIsNone(result['metrics']['recall_at_k'])
        self.assertIsNone(result['metrics']['ndcg_at_k'])
        case['qrels'].pop('irrelevant')
        self.assertIsNone(evaluate_case(case)['metrics']['precision_at_k'])

    def test_unlabeled_is_not_a_no_answer_case(self):
        case = retrieval_case()
        case['qrels'] = None
        result = evaluate_case(case)
        self.assertIsNone(result['metrics']['no_answer_correct'])
        self.assertIsNone(result['metrics']['precision_at_k'])

    def test_partial_all_negative_judgments_still_measure_precision(self):
        case = retrieval_case()
        case.update(qrels={'irrelevant': 0, 'other': 0}, judgments_complete=False)
        metrics = evaluate_case(case)['metrics']
        self.assertEqual(metrics['precision_at_k'], 0)
        self.assertIsNone(metrics['recall_at_k'])

    def test_no_answer_and_empty_result_cases_are_scored_separately(self):
        case = retrieval_case()
        case['qrels'] = {}
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['no_answer_correct'], 0)
        self.assertIsNone(result['metrics']['recall_at_k'])
        case['observation']['ranked_ids'] = []
        self.assertEqual(evaluate_case(case)['metrics']['no_answer_correct'], 1)
        case['qrels'] = {'best': 2}
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['recall_at_k'], 0)
        self.assertEqual(result['metrics']['ndcg_at_k'], 0)

    def test_conflict_leak_is_independent_of_relevance_labels(self):
        case = retrieval_case()
        case.update(qrels=None, forbidden_ids=['other'])
        self.assertEqual(evaluate_case(case)['metrics']['conflict_leak'], 1)

    def test_rule_success_never_implies_task_success(self):
        result = evaluate_case(agent_case())
        self.assertEqual(result['metrics']['rule_pass'], 1)
        self.assertIsNone(result['metrics']['task_success'])

    def test_budget_exit_is_not_natural_completion(self):
        case = agent_case()
        case['observation']['stop_reason'] = 'max_turns'
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['natural_completion'], 0)
        self.assertEqual(result['metrics']['rule_pass'], 0)
        self.assertIn('budget_exhausted', result['flags'])

    def test_wrong_arguments_and_repeated_requirements(self):
        case = agent_case()
        case['observation']['tool_trace'][0]['arguments']['min_publication_year'] = 2020
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['required_call_recall'], 0)
        self.assertEqual(result['metrics']['argument_accuracy'], 0)
        case = agent_case()
        case['required_calls'] *= 2
        self.assertEqual(evaluate_case(case)['metrics']['required_call_recall'], 0.5)

    def test_order_and_approval_denial_are_distinct_from_forbidden_execution(self):
        case = agent_case()
        case['observation']['tool_trace'].append({'tool': 'send_email', 'status': 'denied_by_editor'})
        case['expected_order'] = ['check_submission', 'recommend_reviewers']
        result = evaluate_case(case)
        self.assertEqual(result['metrics']['forbidden_tool_executed'], 0)
        self.assertEqual(result['metrics']['tool_order_correct'], 0)
        case['observation']['tool_trace'][-1]['status'] = 'success'
        self.assertEqual(evaluate_case(case)['metrics']['forbidden_tool_executed'], 1)

    def test_missing_usage_is_not_zero_cost(self):
        case = agent_case()
        case['observation']['tool_trace'] += [
            {'event': 'model', 'usage': {'input_tokens': 10, 'output_tokens': 2}},
            {'event': 'finalize'},
        ]
        metrics = evaluate_case(case)['metrics']
        self.assertEqual(metrics['model_usage_coverage'], 0.5)
        self.assertEqual(metrics['known_model_input_tokens'], 10)

    def test_expected_approval_is_rule_success_but_not_task_completion(self):
        case = agent_case()
        case.update(required_calls=[], expected_status='waiting_approval', expected_stop_reason='')
        case['observation'].update(status='waiting_approval', stop_reason='', answer='', tool_trace=[])
        metrics = evaluate_case(case)['metrics']
        self.assertEqual(metrics['rule_pass'], 1)
        self.assertIsNone(metrics['natural_completion'])
        self.assertIsNone(metrics['task_success'])

    def test_summary_separates_synthetic_and_human_and_counts_unscored_cases(self):
        first = retrieval_case()
        missing = {**first, 'id': 'missing', 'observation': None}
        human = {**first, 'id': 'human', 'source': 'human'}
        groups = summarize([evaluate_case(item) for item in (first, missing, human)])
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['metrics']['recall_at_k']['n'], 1)
        self.assertEqual(groups[0]['metrics']['recall_at_k']['coverage'], 0.5)
        self.assertEqual(groups[0]['metrics']['latency_ms']['p95'], 12)

    def test_invalid_labels_missing_observations_and_nan_are_rejected(self):
        for key, value in (('k', 0), ('qrels', {'best': True}), ('observation', {})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_case({**retrieval_case(), key: value})
        case = retrieval_case()
        case['observation']['latency_ms'] = float('nan')
        with self.assertRaises(ValueError):
            validate_case(case)

    def test_rerank_configurations_are_never_averaged_together(self):
        first = {**retrieval_case(), 'options': {'rerank': False}}
        second = {**retrieval_case(), 'id': 'rerank', 'options': {'rerank': True}}
        groups = summarize([evaluate_case(first), evaluate_case(second)])
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0]['options'], groups[1]['options'])
        case = agent_case()
        case['judgments'] = {'task_success': 'true'}
        with self.assertRaises(ValueError):
            validate_case(case)

    def test_running_cases_are_not_marked_failed(self):
        case = agent_case()
        case['observation']['status'] = 'processing'
        result = evaluate_case(case)
        self.assertFalse(result['evaluated'])
        self.assertEqual(result['flags'], [])

    def test_report_keeps_evidence_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / 'report'
            write_report([retrieval_case()], report, {'source_sha256': 'fixture'})
            rows = [json.loads(line) for line in (report / 'bad_cases.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows[0]['observation']['ranked_ids'], ['irrelevant', 'other'])
            with self.assertRaises(FileExistsError):
                write_report([retrieval_case()], report, {})

    def test_duplicate_ids_fail_before_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cases.jsonl'
            write_jsonl(path, [retrieval_case(), retrieval_case()])
            with self.assertRaisesRegex(ValueError, 'Duplicate id'):
                load_cases(path)

    @patch('apps.common.management.commands.evaluate_quality.collect_case', side_effect=RuntimeError('provider down'))
    def test_collection_errors_produce_report_but_fail_command(self, _collect):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'cases.jsonl'
            write_jsonl(path, [retrieval_case()])
            report = Path(temporary) / 'report'
            with self.assertRaises(CommandError):
                call_command('evaluate_quality', cases=str(path), output=str(report), collect=True)
            summary = json.loads((report / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['evaluated'], 0)
            self.assertEqual(summary['flagged_cases'], 1)


class QualityCollectionTests(TestCase):
    def setUp(self):
        self.manuscript = Manuscript.objects.create(title='A RAG manuscript', status='confirmed')
        self.extraction = ExtractionRun.objects.create(manuscript=self.manuscript, model_name='fixture', status='success')
        self.chunk = DocumentChunk.objects.create(
            extraction_run=self.extraction, kind='child', chunk_index=0, content='Hybrid retrieval with BM25',
        )

    @patch('apps.extraction.vector_search.embed_texts', side_effect=AssertionError('BM25 must not call embeddings'))
    def test_collects_actual_bm25_without_provider(self, _embed):
        case = {
            'id': 'bm25', 'kind': 'retrieval', 'source': 'synthetic', 'target': 'document',
            'mode': 'bm25', 'query': 'BM25', 'extraction_run_id': str(self.extraction.id),
            'k': 1, 'qrels': {str(self.chunk.id): 2}, 'judgments_complete': True,
        }
        collected = collect_case(case)
        self.assertEqual(evaluate_case(collected)['metrics']['recall_at_k'], 1)
        self.assertEqual(collected['observation']['index']['embedded_child_count'], 0)
        case['qrels'] = {'stale-chunk-id': 2}
        with self.assertRaisesRegex(ValueError, 'index snapshot'):
            collect_case(case)

    def test_exports_unreviewed_data_without_inventing_labels(self):
        conversation = AgentConversation.objects.create(manuscript=self.manuscript)
        AgentRun.objects.create(conversation=conversation, user_input='Recommend reviewers', status='success', stop_reason='max_turns')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'export.jsonl'
            call_command('export_quality_cases', output=str(path), limit=2)
            cases = load_cases(path)
            self.assertEqual(len(cases), 2)
            self.assertIsNone(cases[0]['qrels'])
            self.assertIsNone(cases[1]['judgments']['task_success'])
            self.assertIsNone(cases[1]['observation']['latency_ms'])

    def test_graph_and_executor_record_usage_and_latency(self):
        responses = iter([
            AIMessage(content='', tool_calls=[{'name': 'check_submission_completeness', 'args': {}, 'id': 'check'}],
                      usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}),
            AIMessage(content='The authors are missing.', usage_metadata={'input_tokens': 20, 'output_tokens': 4, 'total_tokens': 24}),
        ])

        class Model:
            def bind_tools(self, _tools):
                return self

            def invoke(self, _messages):
                return next(responses)

        state = run_editor_agent('Check submission', self.manuscript, model=Model())
        calls = [entry for entry in state['tool_trace'] if entry.get('tool')]
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0]['latency_ms'], 0)
        case = agent_case()
        case.update(required_calls=[], allowed_tools=['check_submission_completeness'])
        case['observation'] = {**state, 'status': 'success'}
        metrics = evaluate_case(case)['metrics']
        self.assertEqual(metrics['known_model_input_tokens'], 30)
        self.assertEqual(metrics['model_usage_coverage'], 1)
        self.assertGreaterEqual(metrics['known_model_latency_ms'], 0)

    def test_snapshot_checks_run_identity(self):
        conversation = AgentConversation.objects.create(manuscript=self.manuscript)
        run = AgentRun.objects.create(conversation=conversation, user_input='Original task')
        case = {**agent_case(), 'run_id': str(run.id), 'query': 'Different task'}
        with self.assertRaisesRegex(ValueError, 'differs'):
            collect_case(case)
