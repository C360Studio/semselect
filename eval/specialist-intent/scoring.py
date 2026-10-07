"""All-case scoring, development-only selection, and conservative advancement gates."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import json
import random
import statistics

from binder import bind


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z*z/total
    center = (p + z*z/(2*total))/divisor
    radius = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/divisor
    return [max(0, center-radius), min(1, center+radius)]


def accept(row, config):
    """Keep the raw operation even when caller thresholds defer it."""
    result = dict(row)
    if row['status'] != 'selected' or not row.get('scores'):
        return result
    scores = list(row['scores'].values())
    if len(scores) != 9 or any(type(s) not in (int, float) or not math.isfinite(s) for s in scores):
        return {**result, 'status': 'error', 'error': 'invalid score vector'}
    ordered = sorted(scores, reverse=True)
    # Highest-example cosine may be negative. Threshold zero is not described as unfiltered.
    if config.get('unfiltered'):
        return result
    top = row.get('top_score', ordered[0])
    margin = row.get('margin', ordered[0]-ordered[1])
    if top < config.get('threshold', 0) or margin < config.get('margin', 0):
        result['status'] = 'deferred'
    return result


def grade(cases, rows, config=None, intervals=True):
    case_map = {c['id']: c for c in cases}
    if len(case_map) != len(cases):
        raise ValueError('duplicate case IDs')
    indexed = {}
    for r in rows:
        if r['id'] not in case_map or r['id'] in indexed:
            raise ValueError('unknown or duplicate result ID')
        indexed[r['id']] = r
    details = []
    for case in cases:
        row = accept(indexed.get(case['id'], {'id': case['id'], 'status': 'unattempted'}), config or {})
        if row['status'] not in ('selected', 'deferred', 'error', 'unattempted'):
            raise ValueError('unknown result status')
        operation = row.get('operation')
        if row['status'] == 'selected' and operation not in ('similarity', 'path', 'zone', 'count', 'avg', 'sum', 'min', 'max', 'no_override'):
            row = {**row, 'status': 'error', 'error': 'unknown operation'}
        valid = row['status'] in ('selected', 'deferred')
        selected = row['status'] == 'selected'
        raw_correct = valid and operation == case['gold']['operation']
        binding = bind(case['input']['query'], operation, case['input']['catalog']) if selected else None
        target = {k: case['gold'][k] for k in ('readiness', 'options', 'executable')}
        actual = {k: binding[k] for k in target} if binding else None
        complete = bool(selected and raw_correct and actual == target)
        # A wrong literal binding is any binding absent from the reviewed permissible output.
        invented = bool(binding and any(binding['options'].get(k) not in (None, case['gold']['options'].get(k))
                                       for k in ('path_start_node', 'aggregation_field')))
        details.append({'id': case['id'], 'family': case['family'], 'stratum': case['stratum'],
                        'gold_operation': case['gold']['operation'], 'gold_executable': case['gold']['executable'],
                        'status': row['status'], 'operation': operation, 'raw_correct': raw_correct,
                        'accepted_correct': selected and raw_correct, 'wrong_accepted': selected and not raw_correct,
                        'wrong_specialized': selected and operation != 'no_override' and case['gold']['operation'] == 'no_override',
                        'complete_correct': complete, 'executable_correct': complete and case['gold']['executable'],
                        'invented_binding': invented, 'binding': binding,
                        'native_complete_correct': row.get('native_options') is not None and
                            json.dumps(row['native_options'], sort_keys=True, allow_nan=False) == json.dumps(case['gold']['options'], sort_keys=True, allow_nan=False),
                        'native_available': row.get('native_options') is not None,
                        'elapsed_ms': row.get('elapsed_ms'), 'http_ms': row.get('http_ms')})
    statuses = Counter(d['status'] for d in details)
    counts = {key: sum(d[key] for d in details) for key in ('raw_correct', 'accepted_correct', 'wrong_accepted', 'wrong_specialized', 'complete_correct', 'executable_correct', 'invented_binding')}
    accepted = statuses['selected']
    successful_latency = [d['http_ms'] for d in details if d['status'] in ('selected', 'deferred') and valid_time(d['http_ms'])]
    elapsed = [d['elapsed_ms'] for d in details if valid_time(d['elapsed_ms'])]
    confusion = defaultdict(Counter)
    strata = {}
    for d in details:
        confusion[d['gold_operation']][d['operation'] if d['status'] in ('selected', 'deferred') and d['operation'] else d['status']] += 1
    for group in sorted({d['stratum'] for d in details}):
        subset = [d for d in details if d['stratum'] == group]
        strata[group] = {'total': len(subset), **{k: sum(d[k] for d in subset) for k in counts}}
    recalls = {op: {'correct': sum(d['raw_correct'] for d in details if d['gold_operation'] == op), 'total': sum(d['gold_operation'] == op for d in details)} for op in confusion}
    clustered = len({d['family'] for d in details}) < len(details)
    proportion = lambda key, subset=details: cluster_interval(subset, key) if clustered else wilson(sum(d[key] for d in subset), len(subset))
    return {'total': len(cases), **counts, 'accepted': accepted, 'deferred': statuses['deferred'],
            'native_complete_correct': sum(d['native_complete_correct'] for d in details),
            'native_available': sum(d['native_available'] for d in details),
            'errors': statuses['error'], 'unattempted': statuses['unattempted'],
            'valid': accepted+statuses['deferred'], 'complete_errors': len(cases)-counts['complete_correct'],
            'coverage': accepted/len(cases) if cases else None,
            'wrong_accepted_rate': counts['wrong_accepted']/accepted if accepted else None,
            'needs_binding': sum(d['binding'] is not None and d['binding']['readiness'] == 'needs_binding' for d in details),
            'no_override': sum(d['status'] == 'selected' and d['operation'] == 'no_override' for d in details),
            'median_ms': statistics.median(successful_latency) if successful_latency else None,
            'p95_ms': percentile(successful_latency, .95), 'latency_samples': len(successful_latency),
            'elapsed_p95_ms': percentile(elapsed, .95), 'elapsed_samples': len(elapsed),
            'intervals': {k: proportion(k) for k in ('raw_correct', 'accepted_correct', 'complete_correct')} if intervals else {},
            'interval_method': 'family-cluster percentile bootstrap' if clustered else 'Wilson 95%',
            'wrong_accepted_interval': proportion('wrong_accepted', [d for d in details if d['status'] == 'selected']) if intervals else None,
            'confusion': dict(confusion), 'strata': strata, 'recall': recalls, 'details': details}


def valid_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def cluster_interval(details, key, seed=20261007, replicates=2000):
    groups = defaultdict(list)
    for d in details:
        groups[d['family']].append(int(d[key]))
    if not groups:
        return None
    rng = random.Random(seed)
    clusters = list(groups.values())
    samples = []
    for _ in range(replicates):
        sampled = [v for group in rng.choices(clusters, k=len(clusters)) for v in group]
        samples.append(sum(sampled)/len(sampled))
    return [percentile(samples, .025), percentile(samples, .975)]


def eligible(metrics):
    return metrics['valid'] == metrics['total'] and metrics['wrong_accepted'] <= 1 and metrics['wrong_specialized'] == 0


def select_model(cases, configurations):
    """Input list order freezes configuration tie-breaking; no test data accepted."""
    if len(cases) != 60 or any(not c['id'].startswith('D') for c in cases):
        raise ValueError('selection requires the 60 development cases')
    scored = [{**c, 'metrics': grade(cases, c['rows'], c, intervals=False)} for c in configurations]
    if not scored:
        return None
    qualified = [c for c in scored if eligible(c['metrics'])]
    best = max(qualified or scored, key=lambda c: (c['metrics']['accepted_correct'], c['metrics']['raw_correct']))
    tradeoffs = [{k: v for k, v in c.items() if k not in ('rows', 'metrics')} |
                {'metrics': {k: v for k, v in c['metrics'].items() if k != 'details'}} for c in scored]
    return {k: v for k, v in best.items() if k != 'rows'} | {'eligible': bool(qualified), 'configuration_scores': tradeoffs}


def nominate(selections):
    ordered = [(name, selections[name]) for name in ('gliclass', 'deberta') if selections.get(name)]
    if not ordered:
        return None
    qualified = [p for p in ordered if p[1]['eligible']]
    name, selection = max(qualified or ordered, key=lambda p: (p[1]['metrics']['accepted_correct'], p[1]['metrics']['raw_correct'], -(p[1]['metrics']['median_ms'] if p[1]['metrics']['median_ms'] is not None else math.inf)))
    return {'arm': name, 'eligible': bool(qualified), 'configuration': {k: v for k, v in selection.items() if k not in ('metrics', 'eligible', 'configuration_scores')}}


def select_code(cases, configurations):
    if len(cases) != 60 or any(not c['id'].startswith('D') for c in cases):
        raise ValueError('selection requires development')
    scored = [{**c, 'metrics': grade(cases, c['rows'], intervals=False)} for c in configurations]
    best = max(scored, key=lambda c: (c['metrics']['complete_correct'], c['metrics']['raw_correct']))
    return {k: v for k, v in best.items() if k != 'rows'}


def select_reuse(code, embedding):
    if embedding is None:
        return 'code'
    choices = [('code', code['metrics']), ('embeddings', embedding['metrics'])]
    qualified = [(name, m) for name, m in choices if eligible(m)]
    if qualified:
        return max(qualified, key=lambda p: (p[1]['accepted_correct'], p[1]['complete_correct']))[0]
    return max(choices, key=lambda p: (-p[1]['wrong_accepted'], p[1]['accepted_correct']))[0]


def paired(a, b, key='accepted_correct', seed=20261007, replicates=2000):
    right = {d['id']: d for d in b['details']}
    if set(right) != {d['id'] for d in a['details']}:
        raise ValueError('paired cohorts differ')
    groups = defaultdict(list)
    corrected, regressed = [], []
    for d in a['details']:
        old = right[d['id']]
        delta = int(d[key])-int(old[key])
        groups[d['family']].append(delta)
        if delta > 0:
            corrected.append(d['id'])
        if delta < 0:
            regressed.append(d['id'])
    rng = random.Random(seed)
    clusters = list(groups.values())
    samples = []
    if clusters:
        for _ in range(replicates):
            sample = [v for group in rng.choices(clusters, k=len(clusters)) for v in group]
            samples.append(sum(sample)/len(sample))
    return {'corrected': corrected, 'regressed': regressed, 'net': len(corrected)-len(regressed),
            'paired_difference_interval': [percentile(samples, .025), percentile(samples, .975)],
            'method': 'paired family-cluster percentile bootstrap', 'seed': seed, 'replicates': replicates}


def sensitivity(primary, variant):
    base = {r['id']: r for r in primary['details']}
    changes = flips = new_wrong = 0
    for row in variant['details']:
        old = base[row['id']]
        outcome = lambda r: r['operation'] if r['status'] == 'selected' else r['status']
        changes += outcome(row) != outcome(old)
        flips += {row['status'], old['status']} == {'selected', 'deferred'}
        new_wrong += row['wrong_specialized'] and not old['wrong_specialized']
    return {'changes': changes, 'accepted_deferred_flips': flips, 'new_wrong_specialized': new_wrong,
            'complete': variant['total'] == 24 and variant['valid'] == 24}


def gates(nominee, code, embeddings, qwen, resources, sensitivities, load, protocol, eligible_nominee=True):
    g = protocol['gates']
    n = nominee
    complete = lambda m: bool(m and m['total'] == 120 and m['valid'] == 120)
    baselines = [code, embeddings]
    checks = {
        'nomination': eligible_nominee,
        'completeness': complete(n) and all(complete(m) for m in baselines+[qwen]),
        'intent_quality': n['raw_correct'] >= g['raw_correct'],
        'useful_acceptance': n['accepted_correct'] >= g['accepted_correct'] and n['wrong_accepted'] <= g['wrong_accepted_max'],
        'wrong_specialized_routing': n['wrong_specialized'] == 0,
        'added_value': all(m is not None and n['accepted_correct']-m['accepted_correct'] >= g['baseline_correct_gain'] and n['wrong_accepted'] <= m['wrong_accepted'] for m in baselines),
        'generalist_quality': bool(qwen and n['accepted_correct'] >= qwen['accepted_correct']-g['qwen_correct_deficit_max'] and n['wrong_accepted'] <= qwen['wrong_accepted']),
        'binding_readiness': n['invented_binding'] == 0 and all(m is not None and n['complete_errors'] <= m['complete_errors'] and n['executable_correct']-m['executable_correct'] >= g['executable_gain'] for m in baselines),
        'cpu_service_cost': bool(n['latency_samples'] == 120 and valid_time(n['median_ms']) and n['median_ms'] <= g['median_ms'] and valid_time(n['p95_ms']) and n['p95_ms'] <= g['p95_ms'] and resources.get('verified') is True and resources.get('architecture') == 'linux/arm64' and resources.get('cpu_only') is True and resources.get('peak_includes_startup') is True and resources.get('oom') is False and valid_time(resources.get('peak_memory_bytes')) and resources['peak_memory_bytes'] <= g['peak_memory_bytes'] and valid_time(resources.get('readiness_seconds')) and resources['readiness_seconds'] <= g['readiness_seconds']),
        'stability': set(sensitivities) == {'reverse', 'remap'} and all(s['complete'] and s['changes'] <= g['sensitivity_changes_max'] and s['new_wrong_specialized'] == 0 for s in sensitivities.values()),
        'small_load': bool(load and load.get('planned') == 200 and load.get('valid') == 200 and load.get('errors') == 0 and load.get('latency_samples') == 200 and load.get('concurrency') == 2 and valid_time(load.get('elapsed_seconds')) and load['elapsed_seconds'] <= 300 and valid_time(load.get('p95_ms')) and load['p95_ms'] <= g['load_p95_ms'])
    }
    incomplete = not checks['completeness'] or not resources.get('verified') or not all(s.get('complete') for s in sensitivities.values()) or set(sensitivities) != {'reverse', 'remap'} or (load is None and all(value for key, value in checks.items() if key != 'small_load'))
    verdict = 'prototype one backend' if all(checks.values()) else 'inconclusive' if incomplete else 'keep the baseline'
    return {'checks': checks, 'verdict': verdict, 'failed': [k for k, v in checks.items() if not v]}
