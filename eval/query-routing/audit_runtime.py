#!/usr/bin/env python3
"""Read-only runtime evidence audit. Does not launch, query, or stop any service.

Usage: python3 audit_runtime.py RESULTS_DIRECTORY
Prints derived JSON to stdout; the caller decides where to preserve that output.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def load(path):
    return json.loads(path.read_text()) if path.exists() else None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def unique_numbers(pattern, log):
    return sorted(set(int(value) for value in re.findall(pattern, log)))


def log_observations(log):
    """First cache value per task is prefix reuse; later values are progress."""
    tasks = {}
    for line in log.splitlines():
        ident = re.search(r'id\s+(\d+)\s*\|\s*task\s+(-?\d+)\s*\|', line)
        if not ident or int(ident[2]) < 0:
            continue
        key = ident[1] + ':' + ident[2]
        prompt = re.search(r'new prompt, n_ctx_slot = (\d+), n_keep = (-?\d+), task.n_tokens = (\d+)', line)
        if prompt:
            tasks[key] = {'slot': int(ident[1]), 'task': int(ident[2]), 'context': int(prompt[1]),
                          'prompt_tokens': int(prompt[3]), 'first_cached_tokens': None,
                          'prompt_eval_tokens': None, 'truncated': None}
        row = tasks.get(key)
        if row is None:
            continue
        cached = re.search(r'cached n_tokens = (\d+)', line)
        if cached and row['first_cached_tokens'] is None:
            row['first_cached_tokens'] = int(cached[1])
        timing = re.search(r'prompt eval time =\s*[\d.]+ ms /\s*(\d+) tokens', line)
        if timing:
            row['prompt_eval_tokens'] = int(timing[1])
        release = re.search(r'stop processing: n_tokens = \d+, truncated = (\d+)', line)
        if release:
            row['truncated'] = int(release[1])
    values = list(tasks.values())
    known_cache = [row['first_cached_tokens'] for row in values if row['first_cached_tokens'] is not None]
    known_eval = [row for row in values if row['prompt_eval_tokens'] is not None]
    return {
        'effective_context': unique_numbers(r'llama_context:\s+n_ctx\s*=\s*(\d+)', log),
        'effective_context_per_sequence': unique_numbers(r'llama_context:\s+n_ctx_seq\s*=\s*(\d+)', log),
        'effective_batch': unique_numbers(r'llama_context:\s+n_batch\s*=\s*(\d+)', log),
        'effective_ubatch': unique_numbers(r'llama_context:\s+n_ubatch\s*=\s*(\d+)', log),
        'effective_slots': unique_numbers(r'initializing, n_slots = (\d+)', log),
        'offload': [{'offloaded': int(a), 'total': int(b)} for a, b in re.findall(r'offloaded (\d+)/(\d+) layers to GPU', log)],
        'metal_devices': re.findall(r'using device MTL\d+ \((Apple [^)]+)\)', log),
        'embedding_batch_clamp_logged': 'embeddings enabled: setting n_batch = n_ubatch' in log,
        'ram_prompt_cache_disabled_logged': 'prompt cache is disabled' in log,
        'tasks': values,
        'cache': {'observed_tasks': len(values), 'first_cache_reported_tasks': len(known_cache),
                  'first_cache_positive_tasks': sum(n > 0 for n in known_cache),
                  'first_cache_zero_tasks': known_cache.count(0),
                  'prompt_eval_reported_tasks': len(known_eval),
                  'full_prompt_eval_tasks': sum(row['prompt_eval_tokens'] == row['prompt_tokens'] for row in known_eval),
                  'truncated_tasks': sum(row['truncated'] not in (None, 0) for row in values),
                  'release_unobserved_tasks': sum(row['truncated'] is None for row in values),
                  'interpretation': 'First cached token count is prefix reuse. Later positive counts within a task are processed-prompt progress, not evidence of cross-request reuse. Missing observations remain unknown.'},
    }


def command_value(command, flag):
    try:
        return command[command.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def token_budgets(preflights, observations):
    if preflights is None:
        return None
    heads = [(row, name, head) for row in preflights for name, head in row['check']['heads'].items()]
    context = observations['effective_context_per_sequence']
    batch = observations['effective_batch']
    # Refuse to collapse unknown/conflicting observations into a convenient limit.
    context = context[0] if len(context) == 1 else None
    batch = batch[0] if len(batch) == 1 else None
    violations = []
    for row, name, head in heads:
        reasons = []
        tokens = head['tokenization']['tokens']
        if head['prompt_tokens'] != len(tokens) or any(type(token) is not int for token in tokens):
            reasons.append('saved token count/type does not match token array')
        if context is not None and len(tokens) + head['reserved_output_tokens'] > context:
            reasons.append('prompt plus reserve exceeds actual context')
        if 'decision_tail_tokens' in head:
            if batch is not None and head['decision_tail_tokens'] > batch:
                reasons.append('decision tail exceeds actual batch')
            if len(head['marker_positions']) != head['expected_markers']:
                reasons.append('marker layout mismatch')
            marker = row['check']['marker_tokenization']['tokens'][0]
            positions = [i for i, token in enumerate(tokens) if token == marker]
            if positions != head['marker_positions'] or not positions or len(tokens) - positions[0] != head['decision_tail_tokens']:
                reasons.append('saved decision tail/positions do not match token array')
        if reasons:
            violations.append({'id': row['id'], 'view': row['view'], 'head': name, 'reasons': reasons})
    return {'requests': len(preflights), 'heads': len(heads),
            'heads_by_question': dict(Counter(name for _, name, _ in heads)),
            'max_prompt_tokens': max((len(h['tokenization']['tokens']) for _, _, h in heads), default=None),
            'max_prompt_plus_reserve': max((len(h['tokenization']['tokens']) + h['reserved_output_tokens'] for _, _, h in heads), default=None),
            'max_decision_tail_tokens': max((h['decision_tail_tokens'] for _, _, h in heads if 'decision_tail_tokens' in h), default=None),
            'observed_context': context, 'observed_batch': batch,
            'actual_limit_violations': violations,
            'actual_limits_verified': bool(heads) and context is not None and batch is not None and not violations,
            'all_saved_preflights_claim_fit': all(row['check']['fits'] for row in preflights),
            'note': 'Rechecks saved token arrays/counts against logged effective limits, including embedding-mode batch clamping. Does not infer full-task correctness.'}


def runtime_audit(directory, preflights=None):
    completed = load(directory / 'runtime.json')
    started = load(directory / 'runtime.start.json')
    metadata = completed or started
    if metadata is None:
        return {'directory': directory.name, 'status': 'no runtime metadata'}
    log_path = directory / 'runtime.log'
    log_bytes = log_path.read_bytes() if log_path.exists() else None
    observations = log_observations(log_bytes.decode('utf-8', errors='replace') if log_bytes is not None else '')
    command = metadata.get('runtime_command', [])
    configured = {name: command_value(command, flag) for name, flag in
                  [('context', '-c'), ('batch', '-b'), ('ubatch', '-ub'), ('threads', '-t'),
                   ('batch_threads', '-tb'), ('slots', '-np'), ('gpu_layers', '-ngl'),
                   ('cache_ram', '--cache-ram'), ('cache_reuse', '--cache-reuse')]}
    configured['no_cache_prompt'] = '--no-cache-prompt' in command
    container = metadata.get('container_stopped') or metadata.get('container_created')
    host = (container or {}).get('HostConfig', {})
    resources = {'observed_peak_memory_bytes': None,
                 'native_quota': 'not enforced for native runtime/guard',
                 'container_cpu_limit': host['NanoCpus'] / 1e9 if 'NanoCpus' in host else None,
                 'container_memory_limit_bytes': host.get('Memory'),
                 'container_memory_swap_limit_bytes': host.get('MemorySwap'),
                 'container_state': (container or {}).get('State'),
                 'note': 'Docker inspect records configured/enforced quota and exit/OOM state; it is not peak RSS or peak GPU memory. A missing OOM record is unknown, not false.'}
    cleanup = {'final_metadata_present': completed is not None,
               'stopped': metadata.get('stopped'), 'errors': metadata.get('cleanup_errors'),
               'child_exit_codes': metadata.get('child_exit_codes'),
               'confirmed': completed is not None and metadata.get('stopped') is True and metadata.get('cleanup_errors') == []}
    discrepancies = []
    for name in ('context', 'batch', 'ubatch', 'slots'):
        observed = observations['effective_' + name]
        if configured[name] is not None and observed and observed != [int(configured[name])]:
            discrepancies.append({'setting': name, 'requested': int(configured[name]), 'logged_effective': observed})
    budget = token_budgets(preflights, observations)
    return {'directory': directory.name, 'hardware': metadata['hardware'], 'arm': metadata['arm'],
            'evidence_sha256': {**{name: digest(directory / name) for name in ('runtime.start.json', 'runtime.json')},
                                'runtime.log': hashlib.sha256(log_bytes).hexdigest() if log_bytes is not None else None},
            'configured': configured, 'observed': observations, 'token_budgets': budget,
            'requested_effective_discrepancies': discrepancies, 'resources': resources, 'cleanup': cleanup}


def response_audit(result, arm, preflights=None):
    records = [row for row in result.get('rows', []) if row['arm'] == arm]
    warmups = [row for row in result.get('warmups', []) if row['arm'] == arm]
    times = [row.get('response', {}).get('timings') for row in records + warmups]
    times = [value for value in times if isinstance(value, dict)]
    expected = {row['request_sha256']: sum(len(head['tokenization']['tokens']) for head in row['check']['heads'].values())
                for row in preflights or []}
    usage_comparisons = []
    usage_key = 'input_tokens' if arm == 'kev' else 'prompt_tokens'
    for row in records + warmups:
        if row.get('http_status') != 200:
            continue
        reported = row.get('response', {}).get('usage', {}).get(usage_key)
        frozen = expected.get(row.get('request_sha256'))
        usage_comparisons.append({'id': row['id'], 'view': row.get('view', 'warmup'),
                                  'reported_input_tokens': reported, 'preflight_total_input_tokens': frozen,
                                  'verified': type(reported) is int and type(frozen) is int and reported == frozen})
    return {'planned_rows': len(records), 'status_counts': dict(Counter(row['status'] for row in records)),
            'attempted_model_requests': sum(row.get('model_requests', 0) for row in records),
            'warmup_model_requests_separate': sum(row.get('model_requests', 0) for row in warmups),
            'http_status_counts': dict(Counter(str(row.get('http_status', 'absent')) for row in records)),
            'response_timings_including_warmup': len(times),
            'response_cache_positive': sum(value.get('cache_n', 0) > 0 for value in times),
            'response_cache_zero': sum(value.get('cache_n') == 0 for value in times),
            'response_cache_unreported': sum('cache_n' not in value for value in times),
            'max_response_prompt_tokens': max((value['prompt_n'] for value in times if 'prompt_n' in value), default=None),
            'usage_comparisons': usage_comparisons,
            'usage_matches_preflight': sum(row['verified'] for row in usage_comparisons),
            'usage_unverified_or_mismatched': [row for row in usage_comparisons if not row['verified']],
            'note': 'Request counts reflect caller records, not independently confirmed server acceptance. Native per-head tasks are observed separately in runtime logs.'}


def audit(root):
    root = Path(root)
    result_bytes = (root / 'result.json').read_bytes() if (root / 'result.json').exists() else None
    result = json.loads(result_bytes) if result_bytes is not None else {}
    preflights = {arm: load(root / ('preflight-' + arm) / 'preflight.json') for arm in ('qwen_json', 'kev')}
    runtimes = [runtime_audit(path, preflights.get(path.name.split('-', 1)[1]))
                for path in sorted(root.iterdir()) if path.is_dir() and path.name.startswith(('preflight-', 'inference-'))]
    return {'kind': 'read-only-query-routing-runtime-audit', 'result_status': result.get('status'),
            'result_sha256': hashlib.sha256(result_bytes).hexdigest() if result_bytes is not None else None, 'runtimes': runtimes,
            'preflight_sha256': {arm: digest(root / ('preflight-' + arm) / 'preflight.json') for arm in ('qwen_json', 'kev')},
            'responses': {arm: response_audit(result, arm, preflights[arm]) for arm in ('qwen_json', 'kev')},
            'limits': 'A snapshot while a run is active is provisional. Missing final metadata is pending/unproved cleanup. No service endpoint is contacted. Configured quotas are never reported as measured peak usage.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.results), indent=2))
