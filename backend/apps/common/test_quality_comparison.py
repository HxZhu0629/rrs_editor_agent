import copy
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .quality import summarize, evaluate_case
from .quality_comparison import compare_cases
from .quality_io import load_cases, write_jsonl, write_report
from .test_quality import agent_case, retrieval_case


class QualityComparisonTests(SimpleTestCase):
    def test_legacy_success_without_stop_reason_is_missing_evidence(self):
        case = agent_case()
        case['observation']['stop_reason'] = ''
        result = evaluate_case(case)
        self.assertIsNone(result['metrics']['natural_completion'])
        self.assertIsNone(result['metrics']['rule_pass'])
        self.assertIn('missing_stop_reason', result['flags'])

    def test_fix_is_paired_and_variant_change_is_visible(self):
        baseline = retrieval_case()
        candidate = copy.deepcopy(baseline)
        candidate.update(mode='hybrid', options={'rerank': True})
        candidate['observation']['ranked_ids'] = ['best', 'other']
        result = compare_cases([baseline], [candidate])
        self.assertEqual(result['outcomes'], {'improved': 1})
        self.assertEqual(result['groups'][0]['metrics']['recall_at_k']['mean_delta'], .5)
        self.assertIn('missed_relevant', result['pairs'][0]['resolved_flags'])

    def test_dropping_a_failure_or_changing_gold_is_rejected(self):
        baseline = retrieval_case()
        with self.assertRaises(ValueError):
            compare_cases([baseline], [])
        for field, value in [('query', 'different'), ('qrels', {'best': 2}), ('split', 'test')]:
            candidate = copy.deepcopy(baseline)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                compare_cases([baseline], [candidate])

    def test_missing_observation_cannot_resolve_bad_case(self):
        baseline = retrieval_case()
        result = compare_cases([baseline], [{**baseline, 'observation': None}])
        self.assertEqual(result['outcomes'], {'inconclusive': 1})
        self.assertEqual(result['pairs'][0]['resolved_flags'], [])
        self.assertEqual(result['groups'][0]['metrics']['recall_at_k']['coverage'], 0)

    def test_lost_manual_label_is_not_an_improvement(self):
        baseline = agent_case()
        baseline['judgments'] = {'task_success': False}
        candidate = copy.deepcopy(baseline)
        candidate['judgments'] = {}
        result = compare_cases([baseline], [candidate])
        self.assertEqual(result['pairs'][0]['status'], 'inconclusive')
        self.assertIn('task_success', result['pairs'][0]['lost_metrics'])

    def test_live_corpus_changes_are_not_misreported_as_model_improvement(self):
        baseline = retrieval_case()
        baseline['observation']['index'] = {'corpus_sha256': 'before'}
        candidate = copy.deepcopy(baseline)
        candidate['observation']['index']['corpus_sha256'] = 'after'
        with self.assertRaises(ValueError):
            compare_cases([baseline], [candidate])

    def test_metrics_are_paired_without_cross_slice_averaging(self):
        baseline = retrieval_case()
        other = {**copy.deepcopy(baseline), 'id': 'other', 'slice': 'no_answer', 'qrels': {}}
        result = compare_cases([baseline, other], [baseline, other])
        self.assertEqual(len(result['groups']), 2)
        self.assertEqual(result['groups'][0]['metrics']['recall_at_k']['paired_samples'], 1)

    def test_cli_preserves_output_and_exits_nonzero_on_regression(self):
        baseline = retrieval_case()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_jsonl(root / 'before.jsonl', [baseline])
            write_jsonl(root / 'after.jsonl', [{**baseline, 'observation': None}])
            with self.assertRaises(CommandError):
                call_command('compare_quality', baseline=str(root / 'before.jsonl'), candidate=str(root / 'after.jsonl'),
                             output=str(root / 'report'), fail_on_regression=True)
            self.assertTrue((root / 'report/comparison.json').exists())
            with self.assertRaises(CommandError):
                call_command('compare_quality', baseline=str(root / 'before.jsonl'), candidate=str(root / 'after.jsonl'), output=str(root / 'report'))

    def test_report_classifies_symptoms_and_separates_splits(self):
        case = retrieval_case()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            summary = write_report([case], output, {})
            self.assertEqual(summary['failure_categories']['retrieval'], 1)
            bad_case = json.loads((output / 'bad_cases.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(bad_case['analysis']['status'], 'open')
        groups = summarize([evaluate_case({**case, 'split': split}) for split in ('dev', 'test')])
        self.assertEqual(len(groups), 2)

    def test_split_groups_are_stable_and_shared_across_task_types(self):
        cases = []
        for index in range(30):
            for kind, example in [('retrieval', retrieval_case()), ('agent', agent_case())]:
                cases.append({**example, 'id': f'{kind}-{index}', 'manuscript_id': f'manuscript-{index}'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_jsonl(root / 'input.jsonl', cases)
            call_command('split_quality_cases', cases=str(root / 'input.jsonl'), output=str(root / 'split.jsonl'))
            split = load_cases(root / 'split.jsonl')
            for index in range(0, len(split), 2):
                self.assertEqual(split[index]['split'], split[index + 1]['split'])
            write_jsonl(root / 'reverse.jsonl', list(reversed(cases)))
            call_command('split_quality_cases', cases=str(root / 'reverse.jsonl'), output=str(root / 'reverse-split.jsonl'))
            self.assertEqual({row['id']: row['split'] for row in split},
                             {row['id']: row['split'] for row in load_cases(root / 'reverse-split.jsonl')})
            with self.assertRaises(CommandError):
                call_command('split_quality_cases', cases=str(root / 'split.jsonl'), output=str(root / 'again.jsonl'))
