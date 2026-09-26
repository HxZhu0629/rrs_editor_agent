import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .quality import evaluate_case, summarize, validate_case


FAILURE_CATEGORIES = {
    'retrieval': {'empty_retrieval', 'missed_relevant', 'irrelevant_results_for_no_answer', 'duplicate_results'},
    'ranking': {'ranking_error'},
    'tool_selection': {'unexpected_tool', 'unknown_tool', 'wrong_tool_order'},
    'arguments': {'argument_mismatch', 'missing_or_wrong_required_call', 'tool_invalid_arguments'},
    'execution': {'tool_error', 'tool_timeout', 'tool_unavailable', 'tool_business_error', 'unexpected_terminal_state', 'budget_exhausted', 'collection_error'},
    'evidence': {'failed_grounded', 'empty_answer'},
    'constraints_and_permissions': {'conflict_leak', 'forbidden_tool_executed', 'failed_constraints_preserved', 'failed_approval_correct'},
}


def failure_categories(flags):
    categories = {category for category, members in FAILURE_CATEGORIES.items() if members.intersection(flags)}
    covered = set().union(*FAILURE_CATEGORIES.values())
    if set(flags) - covered:
        categories.add('needs_review')
    return sorted(categories)


def load_cases(path):
    cases = []
    seen = set()
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
            validate_case(case)
            if case['id'] in seen:
                raise ValueError(f'Duplicate id: {case["id"]}')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Line {number}: {exc}') from exc
        seen.add(case['id'])
        cases.append(case)
    if not cases:
        raise ValueError('The evaluation dataset is empty.')
    return cases


def write_jsonl(path, rows):
    with Path(path).open('x', encoding='utf-8') as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def source_fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*.py')):
        if '__pycache__' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode('utf-8'))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def write_report(cases, output_dir, manifest):
    results = [evaluate_case(case) for case in cases]
    groups = summarize(results)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(output_dir / 'cases.jsonl', cases)
    write_jsonl(output_dir / 'per_case.jsonl', results)
    bad_cases = [{**row, 'failure_categories': failure_categories(row['flags']), 'analysis': {
        'status': 'open', 'evidence': '',
        'root_cause': '待复核：flags 是观测或规则判定，不能单独证明根因。',
        'fix': '', 'regression_case_id': row['id'],
    }} for row in results if row['flags']]
    write_jsonl(output_dir / 'bad_cases.jsonl', bad_cases)
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'manifest': manifest, 'cases': len(cases),
        'evaluated': sum(row['evaluated'] for row in results),
        'flagged_cases': len(bad_cases), 'groups': groups,
        'failure_categories': dict(Counter(category for row in bad_cases for category in row['failure_categories'])),
    }
    (output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    lines = [
        '# 检索与 Agent 评测报告', '',
        f'样本数：{len(cases)}；已采集：{summary["evaluated"]}；待排查：{len(bad_cases)}。', '',
        '不同 source、任务类型、切片、方法、检索选项和 K 分别统计；synthetic 分数仅用于评测器演示。',
        '空值表示缺少证据，不能当作 0 或 100%；Agent rule_pass 不等于人工 task_success。', '',
    ]
    for group in groups:
        lines.extend([
            f'## {group["kind"]} / {group["target"]} / {group["mode"]} / {group["slice"]} / {group["source"]} / {group["split"]} / K={group["k"]}', '',
            f'检索选项：`{json.dumps(group["options"], ensure_ascii=False, sort_keys=True)}`', '',
            '| 指标 | 均值 | 已评/总数 | P50 | P95 |', '| --- | ---: | ---: | ---: | ---: |',
        ])
        for name, stats in group['metrics'].items():
            display = lambda value: '未评' if value is None else f'{value:.4f}'
            lines.append(f'| {name} | {display(stats["mean"])} | {stats["n"]}/{group["cases"]} | '
                         f'{display(stats.get("p50"))} | {display(stats.get("p95"))} |')
        lines.append('')
    lines.extend(['## 失败类别', '', '以下是症状分类，一条样本可归入多类；不自动判定根因。', ''])
    for category, count in summary['failure_categories'].items():
        lines.append(f'- {category}: {count}')
    lines.extend(['', '## 待排查样本', ''])
    for row in bad_cases:
        lines.append(f'- `{row["id"]}`：{", ".join(row["flags"])}')
    lines.extend(['', '完整输入、期望、实际结果及轨迹见 per_case.jsonl；根因、修复与回归记录见 bad_cases.jsonl。'])
    (output_dir / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary
