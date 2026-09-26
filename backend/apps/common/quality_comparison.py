import json
from collections import Counter

from .quality import evaluate_case, summarize


CONTRACT_FIELDS = (
    'kind', 'source', 'query', 'target', 'slice', 'split', 'k', 'qrels',
    'judgments_complete', 'forbidden_ids', 'required_calls', 'allowed_tools',
    'forbidden_tools', 'expected_order', 'expected_status', 'expected_stop_reason',
    'thresholds', 'manuscript_id', 'extraction_run_id', 'source_context',
)


def compare_cases(baseline, candidate):
    before = {case['id']: evaluate_case(case) for case in baseline}
    after = {case['id']: evaluate_case(case) for case in candidate}
    if len(before) != len(baseline) or len(after) != len(candidate):
        raise ValueError('Comparison requires unique case IDs.')
    if not before or set(before) != set(after):
        raise ValueError('Comparison requires exactly the same non-empty case ID set; do not drop failed cases.')
    pairs = []
    for case_id, current in after.items():
        previous = before[case_id]
        changed = [key for key in CONTRACT_FIELDS if previous.get(key) != current.get(key)]
        if changed:
            raise ValueError(f'{case_id}: input or acceptance contract changed: {", ".join(changed)}')
        old_observation, new_observation = previous.get('observation') or {}, current.get('observation') or {}
        for old_snapshot, new_snapshot in (
            (old_observation.get('input_snapshot'), new_observation.get('input_snapshot')),
            (old_observation.get('index', {}).get('corpus_sha256'), new_observation.get('index', {}).get('corpus_sha256')),
        ):
            if old_snapshot is not None and new_snapshot is not None and old_snapshot != new_snapshot:
                raise ValueError(f'{case_id}: collected input or document corpus changed; freeze data before comparing versions.')
        old_metrics, new_metrics = previous['metrics'], current['metrics']
        lost = sorted(name for name, value in old_metrics.items() if value is not None and new_metrics.get(name) is None)
        introduced = sorted(set(current['flags']) - set(previous['flags']))
        resolved = sorted(set(previous['flags']) - set(current['flags']))
        complete = previous['evaluated'] and current['evaluated'] and not lost
        if not complete:
            status = 'inconclusive'
        elif introduced:
            status = 'regressed'
        elif resolved:
            status = 'improved'
        elif current['flags']:
            status = 'persistent'
        else:
            status = 'unchanged'
        pairs.append({
            'id': case_id, 'status': status, 'introduced_flags': introduced,
            'resolved_flags': resolved if complete else [],
            'persistent_flags': sorted(set(previous['flags']) & set(current['flags'])),
            'lost_metrics': lost,
            'metric_deltas': {name: new_metrics[name] - value for name, value in old_metrics.items()
                              if value is not None and new_metrics.get(name) is not None},
            'same_agent_run': current['kind'] == 'agent' and bool(current.get('run_id'))
                              and current.get('run_id') == previous.get('run_id'),
        })
    groups = []
    group_fields = ('kind', 'target', 'mode', 'slice', 'source', 'k', 'options', 'split')
    identities = {row['id']: summarize([row])[0] for row in after.values()}
    for group in summarize(list(after.values())):
        rows = [row for row in after.values()
                if all(identities[row['id']][key] == group[key] for key in group_fields)]
        old_group = summarize([before[row['id']] for row in rows])
        metrics = {}
        metric_names = set(group['metrics'])
        for row in rows:
            metric_names.update(before[row['id']]['metrics'])
        for name in sorted(metric_names):
            paired = [(before[row['id']]['metrics'].get(name), row['metrics'].get(name)) for row in rows]
            valid = [(old, new) for old, new in paired if old is not None and new is not None]
            metrics[name] = {
                'paired_samples': len(valid), 'total_samples': len(rows),
                'coverage': len(valid) / len(rows),
                'baseline_mean': sum(old for old, new in valid) / len(valid) if valid else None,
                'candidate_mean': sum(new for old, new in valid) / len(valid) if valid else None,
                'mean_delta': sum(new - old for old, new in valid) / len(valid) if valid else None,
            }
        groups.append({
            'candidate': {key: value for key, value in group.items() if key not in ('metrics', 'flags')},
            'baseline_variants': [{'mode': row['mode'], 'options': row['options']} for row in old_group],
            'metrics': metrics,
        })
    return {
        'cases': len(pairs), 'outcomes': dict(Counter(pair['status'] for pair in pairs)),
        'groups': groups, 'pairs': pairs,
        'scope': 'Paired observations, not a significance test. Flags are symptoms, not confirmed root causes. '
                 'Agent manual judgments must be reviewed for each new run; same run cannot prove a runtime improvement.',
    }


def write_comparison(result, output_dir):
    (output_dir / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    lines = ['# 同样本版本对比', '', result['scope'], '',
             f'样本数：{result["cases"]}；状态：{json.dumps(result["outcomes"], ensure_ascii=False)}', '',
             'delta = 新版本 − 基线；耗时/错误率通常越低越好，质量指标通常越高越好。',
             '只对双方都有值的样本计算差值，覆盖不足不能宣称全量改善。', '']
    for group in result['groups']:
        lines += [f'## {json.dumps(group["candidate"], ensure_ascii=False)}', '',
                  '| 指标 | 基线 | 新版本 | 差值 | 配对覆盖 |', '| --- | ---: | ---: | ---: | ---: |']
        for name, values in group['metrics'].items():
            formatted = ['未评' if values[key] is None else f'{values[key]:.4f}'
                         for key in ('baseline_mean', 'candidate_mean', 'mean_delta')]
            lines.append(f'| {name} | {" | ".join(formatted)} | {values["paired_samples"]}/{values["total_samples"]} |')
        lines.append('')
    lines += ['## 逐条回归', '']
    for pair in result['pairs']:
        lines.append(f'- `{pair["id"]}`：{pair["status"]}；新增 {pair["introduced_flags"]}；'
                     f'消除 {pair["resolved_flags"]}；缺失指标 {pair["lost_metrics"]}')
    (output_dir / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
