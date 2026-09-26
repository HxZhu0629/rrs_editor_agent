import json
import math
from collections import Counter


MANUAL_CHECKS = ('task_success', 'grounded', 'constraints_preserved', 'approval_correct', 'recovery_success')


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _dcg(grades):
    return sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset(value, actual[key]) for key, value in expected.items()
        )
    return type(expected) is type(actual) and expected == actual


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_case(case):
    if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'].strip():
        raise ValueError('Each case needs a non-empty string id.')
    if case.get('kind') not in ('retrieval', 'agent'):
        raise ValueError('kind must be retrieval or agent.')
    if not isinstance(case.get('source'), str) or not case['source'].strip():
        raise ValueError('source must identify human, synthetic, or unreviewed data.')
    if case.get('split', 'unspecified') not in ('dev', 'test', 'unspecified'):
        raise ValueError('split must be dev, test, or unspecified.')
    observation = case.get('observation')
    if observation is not None and not isinstance(observation, dict):
        raise ValueError('observation must be an object or null.')
    collected = observation is not None and not observation.get('collection_error')
    if observation and observation.get('latency_ms') is not None and not _finite_number(observation['latency_ms']):
        raise ValueError('latency_ms must be finite and nonnegative.')
    if case['kind'] == 'retrieval':
        options = case.get('options', {})
        if not isinstance(options, dict) or any(
            key not in ('query_rewrite', 'hyde', 'rerank', 'reflect') or type(value) is not bool
            for key, value in options.items()
        ):
            raise ValueError('options must contain boolean query_rewrite, hyde, rerank, reflect values.')
        if type(case.get('k', 5)) is not int or not 1 <= case.get('k', 5) <= 100:
            raise ValueError('k must be an integer between 1 and 100.')
        qrels = case.get('qrels')
        if qrels is not None and (not isinstance(qrels, dict) or any(
            not isinstance(key, str) or not key or type(grade) is not int or grade not in (0, 1, 2)
            for key, grade in qrels.items()
        )):
            raise ValueError('qrels must map non-empty IDs to integer grades 0, 1, 2, or be null.')
        if type(case.get('judgments_complete', False)) is not bool:
            raise ValueError('judgments_complete must be boolean.')
        ranked = (observation or {}).get('ranked_ids', [])
        if collected and 'ranked_ids' not in observation:
            raise ValueError('A collected retrieval observation needs ranked_ids (use [] for an empty result).')
        if not isinstance(ranked, list) or any(not isinstance(item, str) or not item for item in ranked):
            raise ValueError('ranked_ids must be a list of non-empty string IDs.')
        thresholds = case.get('thresholds', {})
        if not isinstance(thresholds, dict) or any(
            key not in ('min_recall', 'min_ndcg', 'max_latency_ms') or not _finite_number(value)
            or (key != 'max_latency_ms' and value > 1) for key, value in thresholds.items()
        ):
            raise ValueError('Invalid retrieval thresholds.')
    else:
        judgments = case.get('judgments', {})
        if not isinstance(judgments, dict) or any(
            key not in MANUAL_CHECKS or (value is not None and type(value) is not bool)
            for key, value in judgments.items()
        ):
            raise ValueError('Manual judgments must be boolean or null.')
        required = case.get('required_calls', [])
        if not isinstance(required, list) or any(
            not isinstance(call, dict) or not isinstance(call.get('tool'), str)
            or not isinstance(call.get('arguments', {}), dict) for call in required
        ):
            raise ValueError('required_calls must contain tool names and argument objects.')
        trace = (observation or {}).get('tool_trace', [])
        if not isinstance(trace, list) or any(not isinstance(entry, dict) for entry in trace):
            raise ValueError('tool_trace must be a list of objects.')
        if collected and observation.get('status') not in ('processing', 'waiting_approval', 'success', 'failed'):
            raise ValueError('Agent observation must contain a valid run status.')
        for entry in trace:
            if 'arguments' in entry and not isinstance(entry['arguments'], dict):
                raise ValueError('Trace arguments must be an object.')
            if entry.get('latency_ms') is not None and not _finite_number(entry['latency_ms']):
                raise ValueError('Trace latency_ms must be finite and nonnegative.')
    for key in ('forbidden_ids', 'allowed_tools', 'forbidden_tools', 'expected_order'):
        if key in case and (not isinstance(case[key], list) or any(not isinstance(item, str) for item in case[key])):
            raise ValueError(f'{key} must be a list of strings.')


def retrieval_metrics(case):
    observation = case['observation']
    cutoff = case.get('k', 5)
    ranked = observation.get('ranked_ids', [])[:cutoff]
    qrels = case.get('qrels')
    complete = case.get('judgments_complete', False) and qrels is not None
    metrics = {
        'precision_at_k': None, 'recall_at_k': None, 'mrr_at_k': None, 'ndcg_at_k': None,
        'hit_at_k': None, 'no_answer_correct': None,
        'returned_count': len(ranked), 'empty_result': float(not ranked),
        'duplicate_rate': _ratio(len(ranked) - len(set(ranked)), len(ranked)),
        'judged_fraction': _ratio(sum(complete or item in (qrels or {}) for item in ranked), len(ranked)),
        'latency_ms': observation.get('latency_ms'),
    }
    flags = []
    if len(ranked) != len(set(ranked)):
        flags.append('duplicate_results')
    if 'forbidden_ids' in case:
        leaked = set(ranked) & set(case['forbidden_ids'])
        metrics['conflict_leak'] = float(bool(leaked))
        if leaked:
            flags.append('conflict_leak')
    if qrels is not None:
        seen = set()
        grades = []
        for item in ranked:
            grades.append(qrels.get(item, 0) if item not in seen else 0)
            seen.add(item)
        positives = sum(grade > 0 for grade in qrels.values())
        if (positives or (qrels and not complete)) and (complete or all(item in qrels for item in ranked)):
            metrics['precision_at_k'] = sum(grade > 0 for grade in grades) / cutoff
        if complete and positives:
            metrics['recall_at_k'] = sum(grade > 0 for grade in grades) / positives
            metrics['mrr_at_k'] = next((1 / rank for rank, grade in enumerate(grades, 1) if grade > 0), 0.0)
            metrics['hit_at_k'] = float(any(grade > 0 for grade in grades))
            metrics['ndcg_at_k'] = _dcg(grades) / _dcg(sorted(qrels.values(), reverse=True)[:cutoff])
            if not ranked:
                flags.append('empty_retrieval')
            if metrics['recall_at_k'] < case.get('thresholds', {}).get('min_recall', 1.0):
                flags.append('missed_relevant')
            if metrics['ndcg_at_k'] < case.get('thresholds', {}).get('min_ndcg', 0.8):
                flags.append('ranking_error')
        elif complete:
            metrics['no_answer_correct'] = float(not ranked)
            if ranked:
                flags.append('irrelevant_results_for_no_answer')
    maximum = case.get('thresholds', {}).get('max_latency_ms')
    if maximum is not None and metrics['latency_ms'] is not None and metrics['latency_ms'] > maximum:
        flags.append('slow_retrieval')
    return metrics, flags


def agent_metrics(case):
    observation = case['observation']
    trace = observation.get('tool_trace', [])
    calls = [entry for entry in trace if entry.get('tool')]
    successful = [entry for entry in calls if entry.get('status') == 'success']
    attempted = [entry for entry in calls if entry.get('status') in ('success', 'error')]
    model_events = [entry for entry in trace if entry.get('event') in ('model', 'model_error', 'finalize')]
    terminal = observation.get('status') in ('success', 'failed')
    stop = observation.get('stop_reason', '')
    metrics = {
        'natural_completion': float(observation.get('status') == 'success' and stop == 'completed') if terminal and stop else None,
        'budget_exhausted': float(stop in ('max_turns', 'tool_budget_exceeded')) if terminal and stop else None,
        'tool_execution_success_rate': _ratio(len(successful), len(attempted)),
        'tool_attempts': len(calls), 'turn_count': observation.get('turn_count'),
        'latency_ms': observation.get('latency_ms'),
        'tool_selection_precision': None, 'required_call_recall': None,
        'argument_accuracy': None, 'tool_order_correct': None,
    }
    flags = []
    rules = []
    expected_status = case.get('expected_status', 'success')
    expected_stop = case.get('expected_stop_reason', 'completed' if expected_status == 'success' else '')
    state_matches = observation.get('status') == expected_status and stop == expected_stop
    missing_stop = terminal and not stop and bool(expected_stop)
    if missing_stop:
        flags.append('missing_stop_reason')
        if observation.get('status') != expected_status:
            rules.append(False)
    else:
        rules.append(state_matches)
    if not state_matches and not missing_stop:
        flags.append('unexpected_terminal_state')
    if observation.get('status') == 'success' and not str(observation.get('answer', '')).strip():
        rules.append(False)
        flags.append('empty_answer')
    if metrics['budget_exhausted']:
        flags.append('budget_exhausted')
    if 'allowed_tools' in case:
        allowed = set(case['allowed_tools'])
        metrics['tool_selection_precision'] = _ratio(sum(entry['tool'] in allowed for entry in calls), len(calls))
        selection_ok = all(entry['tool'] in allowed for entry in calls)
        rules.append(selection_ok)
        if not selection_ok:
            flags.append('unexpected_tool')
    required = case.get('required_calls', [])
    available = list(successful)
    matched = 0
    for required_call in required:
        match = next((index for index, entry in enumerate(available) if entry['tool'] == required_call['tool']
                      and _subset(required_call.get('arguments', {}), entry.get('arguments', {}))), None)
        if match is not None:
            matched += 1
            available.pop(match)
    if required:
        metrics['required_call_recall'] = matched / len(required)
        argument_checks = []
        for entry in successful:
            contracts = [item for item in required if item['tool'] == entry['tool'] and 'arguments' in item]
            if contracts:
                argument_checks.append(any(_subset(item['arguments'], entry.get('arguments', {})) for item in contracts))
        metrics['argument_accuracy'] = _ratio(sum(argument_checks), len(argument_checks))
        rules.append(matched == len(required))
        if matched < len(required):
            flags.append('missing_or_wrong_required_call')
        if argument_checks and not all(argument_checks):
            flags.append('argument_mismatch')
    if 'expected_order' in case:
        expected = iter(case['expected_order'])
        next_tool = next(expected, None)
        for entry in successful:
            if entry['tool'] == next_tool:
                next_tool = next(expected, None)
        metrics['tool_order_correct'] = float(next_tool is None)
        rules.append(next_tool is None)
        if next_tool is not None:
            flags.append('wrong_tool_order')
    if 'forbidden_tools' in case:
        violation = any(entry['tool'] in case['forbidden_tools'] for entry in successful)
        metrics['forbidden_tool_executed'] = float(violation)
        rules.append(not violation)
        if violation:
            flags.append('forbidden_tool_executed')
    for entry in calls:
        if entry.get('status') == 'error':
            flags.append('unknown_tool' if str(entry.get('error', '')).startswith('Unknown tool:') else 'tool_error')
            if entry.get('error_type') in ('invalid_arguments', 'timeout', 'unavailable', 'business_error'):
                flags.append(f'tool_{entry["error_type"]}')
    signatures = [json.dumps([entry['tool'], entry.get('arguments', {})], sort_keys=True) for entry in successful]
    metrics['repeated_successful_call_rate'] = _ratio(len(signatures) - len(set(signatures)), len(signatures))
    if signatures and len(signatures) != len(set(signatures)):
        flags.append('repeated_call_review')
    usages = [entry['usage'] for entry in model_events if isinstance(entry.get('usage'), dict)
              and all(_finite_number(entry['usage'].get(key)) for key in ('input_tokens', 'output_tokens'))]
    metrics['model_usage_coverage'] = _ratio(len(usages), len(model_events))
    metrics['known_model_input_tokens'] = sum(item['input_tokens'] for item in usages) if usages else None
    metrics['known_model_output_tokens'] = sum(item['output_tokens'] for item in usages) if usages else None
    for label, events in (('model', model_events), ('tool', attempted)):
        measured = [entry['latency_ms'] for entry in events if entry.get('latency_ms') is not None]
        metrics[f'{label}_latency_coverage'] = _ratio(len(measured), len(events))
        metrics[f'known_{label}_latency_ms'] = sum(measured) if measured else None
    metrics['rule_pass'] = None if missing_stop and all(rules) else float(all(rules))
    for key in MANUAL_CHECKS:
        value = case.get('judgments', {}).get(key)
        metrics[key] = float(value) if value is not None else None
        if value is False:
            flags.append(f'failed_{key}')
    if case.get('judgments', {}).get('task_success') is True and not all(rules):
        flags.append('judgment_rule_disagreement')
    return metrics, sorted(set(flags))


def evaluate_case(case):
    validate_case(case)
    result = {**case, 'metrics': {}, 'flags': [], 'evaluated': False}
    if case.get('observation') is None:
        return result
    if case['observation'].get('collection_error'):
        return {**result, 'flags': ['collection_error']}
    if case['kind'] == 'agent' and case['observation'].get('status') == 'processing':
        return result
    metrics, flags = retrieval_metrics(case) if case['kind'] == 'retrieval' else agent_metrics(case)
    return {**result, 'metrics': metrics, 'flags': flags, 'evaluated': True}


def _percentile(values, quantile):
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(results):
    groups = {}
    for result in results:
        key = (result['kind'], result.get('target', 'document' if result['kind'] == 'retrieval' else ''), result.get('mode', ''),
               result.get('slice', 'default'), result['source'], result.get('k', 5) if result['kind'] == 'retrieval' else None,
               json.dumps(result.get('options', {}), sort_keys=True), result.get('split', 'unspecified'))
        groups.setdefault(key, []).append(result)
    summaries = []
    for key, rows in groups.items():
        metrics = {}
        for name in sorted({name for row in rows for name in row['metrics']}):
            values = sorted(row['metrics'][name] for row in rows if row['metrics'].get(name) is not None)
            metrics[name] = {
                'mean': sum(values) / len(values) if values else None,
                'n': len(values), 'coverage': len(values) / len(rows),
            }
            if name.endswith('latency_ms'):
                metrics[name].update({
                    'p50': _percentile(values, 0.5) if values else None,
                    'p95': _percentile(values, 0.95) if values else None,
                })
        summaries.append({
            'kind': key[0], 'target': key[1], 'mode': key[2], 'slice': key[3], 'source': key[4], 'k': key[5],
            'options': json.loads(key[6]),
            'split': key[7],
            'cases': len(rows), 'evaluated': sum(row['evaluated'] for row in rows),
            'metrics': metrics, 'flags': dict(Counter(flag for row in rows for flag in row['flags'])),
        })
    return summaries
