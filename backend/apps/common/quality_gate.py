import math


GROUP_KEYS = {'kind', 'target', 'mode', 'slice', 'source', 'k', 'options', 'split'}


def validate_gate(gate, allow_synthetic=False):
    if not isinstance(gate, dict) or not isinstance(gate.get('checks'), list) or not gate['checks']:
        raise ValueError('Gate must contain a non-empty checks list.')
    for check in gate['checks']:
        if not isinstance(check, dict):
            raise ValueError('Each gate check must be an object.')
        match = check.get('match', {})
        if not isinstance(match, dict) or set(match) - GROUP_KEYS or match.get('kind') not in {'agent', 'retrieval'}:
            raise ValueError('Each check requires a valid kind and group selector.')
        allowed_sources = {'human', 'synthetic'} if allow_synthetic else {'human'}
        if match.get('source') not in allowed_sources:
            raise ValueError('Gate checks require source=human; synthetic requires --allow-synthetic-gate.')
        if not isinstance(check.get('metric'), str) or check.get('stat', 'mean') not in {'mean', 'p50', 'p95'}:
            raise ValueError('Gate requires a metric and mean/p50/p95 statistic.')
        if type(check.get('min_samples')) is not int or check['min_samples'] < 1:
            raise ValueError('min_samples must be a positive integer.')
        coverage = check.get('min_coverage', 1.0)
        if type(coverage) not in (int, float) or not math.isfinite(coverage) or not 0 < coverage <= 1:
            raise ValueError('min_coverage must be in (0, 1].')
        if not {'min', 'max'} & check.keys():
            raise ValueError('Each check requires min and/or max.')
        for key in ('min', 'max'):
            if key in check and (type(check[key]) not in (int, float) or not math.isfinite(check[key])):
                raise ValueError('Thresholds must be finite numbers.')
        if 'min' in check and 'max' in check and check['min'] > check['max']:
            raise ValueError('min must not exceed max.')


def evaluate_gate(summary, gate, allow_synthetic=False):
    validate_gate(gate, allow_synthetic)
    results = []
    for check in gate['checks']:
        groups = [group for group in summary['groups'] if all(group.get(key) == value for key, value in check['match'].items())]
        if not groups:
            results.append({'check': check, 'passed': False, 'reason': 'no_matching_group'})
        for group in groups:
            stats = group['metrics'].get(check['metric'], {})
            value = stats.get(check.get('stat', 'mean'))
            reasons = []
            if stats.get('n', 0) < check['min_samples']:
                reasons.append('insufficient_samples')
            if stats.get('coverage', 0) < check.get('min_coverage', 1.0):
                reasons.append('insufficient_coverage')
            if value is None:
                reasons.append('missing_metric')
            elif ('min' in check and value < check['min']) or ('max' in check and value > check['max']):
                reasons.append('threshold_failed')
            results.append({'check': check, 'group': {key: group.get(key) for key in GROUP_KEYS},
                            'value': value, 'passed': not reasons, 'reasons': reasons})
    return {'passed': all(result['passed'] for result in results), 'checks': results}
