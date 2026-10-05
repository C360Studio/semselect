#!/usr/bin/env python3
"""Answerability experiments on frozen evidence; small pilots, not benchmarks."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

import evaluate
import metal
from compare_scoring import port_is_listening
from model import load_lock, verify

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PORT, GUARD_PORT = 18087, 18088
LABELS = ['allow', 'defer']
ARMS = ('no_added_gate', 'qwen_json', 'kev')
SCOPES = ('teaching/development-only', 'heldout/source-pilot')
TEACHING_FAMILIES = {'service-config', 'expense-scope', 'expense-deadline',
                     'expense-review', 'expense-destination'}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_dataset(dataset):
    """Reject ambiguous fixtures before launching any model or reading gold into input."""
    require(dataset.get('version') == 1, 'dataset version must be 1')
    require(dataset.get('scope') in SCOPES, 'dataset must declare a supported experimental scope')
    instructions = dataset.get('instructions')
    require(nonempty(instructions) and len(instructions.encode('utf-8')) <= 1024,
            'instructions must be nonempty and at most 1024 UTF-8 bytes')
    categories = dataset.get('categories')
    require(isinstance(categories, dict) and set(categories) == set(LABELS)
            and all(nonempty(v) for v in categories.values()), 'categories must describe allow and defer')
    cases = dataset.get('cases')
    require(isinstance(cases, list) and bool(cases), 'cases must be a nonempty list')
    ids = set()
    for case in cases:
        require(isinstance(case, dict), 'case must be an object')
        require(all(nonempty(case.get(k)) for k in ('id', 'family', 'title')), 'case needs id, family and title')
        require(case['id'] not in ids, 'duplicate case ID: ' + case['id'])
        ids.add(case['id'])
        inp = case.get('input')
        require(isinstance(inp, dict) and set(inp) == {'question', 'audience', 'required_fact', 'passages', 'facts'},
                'input must contain only question, audience, required_fact, passages and facts')
        require(nonempty(inp['question']) and nonempty(inp['audience']), 'question and audience must be nonempty')
        require(inp['required_fact'] is None or nonempty(inp['required_fact']), 'required_fact must be null or a key')
        require(isinstance(inp['passages'], list) and isinstance(inp['facts'], list), 'passages and facts must be lists')
        passages = {}
        for passage in inp['passages']:
            require(isinstance(passage, dict) and set(passage) == {'id', 'text', 'metadata'}, 'invalid passage fields')
            require(nonempty(passage['id']) and nonempty(passage['text']), 'passage needs ID and complete text')
            require(passage['id'] not in passages, 'duplicate passage ID')
            metadata = passage['metadata']
            require(isinstance(metadata, dict) and set(metadata) == {'audience', 'status'}
                    and nonempty(metadata['audience']) and metadata['status'] in ('current', 'superseded'),
                    'invalid passage applicability metadata')
            passages[passage['id']] = passage
        for fact in inp['facts']:
            require(isinstance(fact, dict) and set(fact) == {'key', 'value', 'passage_id'}
                    and nonempty(fact['key']) and nonempty(fact['value'])
                    and fact['passage_id'] in passages, 'invalid authoritative fact or unknown source passage')
        gold = case.get('gold')
        require(isinstance(gold, dict) and set(gold) == {'action', 'reason', 'support', 'missing'}, 'invalid gold fields')
        require(gold['action'] in LABELS and nonempty(gold['reason']) and isinstance(gold['support'], list)
                and (gold['missing'] is None or nonempty(gold['missing'])), 'invalid gold action/explanation')
        for support in gold['support']:
            require(isinstance(support, dict) and set(support) == {'passage_id', 'quote'}
                    and support['passage_id'] in passages and nonempty(support['quote'])
                    and support['quote'] in passages[support['passage_id']]['text'],
                    'gold support quote must occur verbatim in its named passage')
        require(gold['action'] != 'allow' or bool(gold['support']), 'allow needs supporting evidence')
        origin = case.get('origin')
        require(isinstance(origin, dict) and set(origin) == {'kind', 'references', 'note'}
                and origin['kind'] in ('constructed', 'source_excerpt', 'source_mutation')
                and isinstance(origin['references'], list) and nonempty(origin['note']), 'invalid origin')
        require(origin['kind'] == 'constructed' or bool(origin['references']), 'source-derived cases need pinned references')
        if dataset['scope'] == 'heldout/source-pilot':
            require(bool(origin['references']), 'source pilot cases need pinned sources')
            require(case['family'] not in TEACHING_FAMILIES, 'source pilot reuses a teaching family')
        for reference in origin['references']:
            require(isinstance(reference, dict) and set(reference) == {'repository', 'revision', 'path', 'sha256', 'note'}
                    and all(nonempty(reference[k]) for k in ('repository', 'path', 'note'))
                    and re.fullmatch(r'[0-9a-f]{40}', reference.get('revision', '')) is not None
                    and re.fullmatch(r'[0-9a-f]{64}', reference.get('sha256', '')) is not None,
                    'source reference needs full commit and SHA-256')
    return dataset


def load_dataset(path):
    return validate_dataset(json.loads(path.read_text()))


def verify_frozen_dataset(path, dataset, expected_digest):
    """Require an explicit pre-run digest for a held-out pilot, before model loading."""
    if dataset['scope'] == 'heldout/source-pilot':
        require(expected_digest is not None, 'source pilot requires --expected-dataset-sha256 from pre-run review')
    if expected_digest is not None:
        require(re.fullmatch(r'[0-9a-f]{64}', expected_digest) is not None, 'expected dataset digest must be SHA-256')
        require(metal.sha256(path) == expected_digest, 'dataset changed since freezing; review before running')


def common_gate(inp, reverse=False):
    """Caller-owned applicability/fact checks, using only supplied input fields."""
    passages = [dict(id=p['id'], text=p['text'], metadata=dict(p['metadata']))
                for p in inp['passages'] if p['metadata']['status'] == 'current'
                and p['metadata']['audience'] in ('all', inp['audience'])]
    eligible = {p['id'] for p in passages}
    facts = [dict(key=f['key'], value=f['value'], passage_id=f['passage_id'])
             for f in inp['facts'] if f['passage_id'] in eligible]
    if reverse:
        passages.reverse()
        facts.reverse()
    filtered = {k: inp[k] for k in ('question', 'audience', 'required_fact')}
    filtered.update(passages=passages, facts=facts)
    if not passages:
        return filtered, 'defer', 'no eligible current evidence'
    if inp['required_fact'] is not None:
        values = {f['value'] for f in facts if f['key'] == inp['required_fact']}
        if len(values) == 1:
            return filtered, 'allow', 'one authoritative value for the required fact'
        return filtered, 'defer', 'missing required fact' if not values else 'conflicting authoritative facts'
    return filtered, None, 'semantic sufficiency remains unresolved'


def prepare(dataset, case, arm, model, order):
    """Construct requests by an explicit input allowlist; gold/provenance never cross HTTP."""
    start = time.monotonic()
    filtered, action, reason = common_gate(case['input'], order == 'reverse')
    labels = list(reversed(LABELS)) if order == 'reverse' else list(LABELS)
    prepared = {'filtered_input': filtered, 'code_action': action, 'code_reason': reason,
                'candidates': labels, 'request': None}
    if action is None and arm != 'no_added_gate':
        prepared['request'] = evaluate.build_request(
            'semselect' if arm == 'kev' else 'seminstruct', model, dataset,
            {'text': json.dumps(filtered, ensure_ascii=False)}, labels)
        if arm == 'qwen_json':
            prepared['request'].update(cache_prompt=True, seed=0)
    prepared['preparation_ms'] = (time.monotonic() - start) * 1000
    return prepared


def http_call(url, request, timeout, evidence):
    """Keep HTTP bodies, including malformed JSON and bounded error responses."""
    wire = urllib.request.Request(url, json.dumps(request).encode(), {'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(wire, timeout=timeout)
    except urllib.error.HTTPError as exc:
        with exc:
            body = exc.read(evaluate.MAX_RESPONSE_BYTES + 1)
        evidence['http'] = {'status': exc.code, 'body_text': body[:evaluate.MAX_RESPONSE_BYTES].decode('utf-8', errors='replace'),
                            'body_truncated': len(body) > evaluate.MAX_RESPONSE_BYTES}
        raise
    with response:
        body = response.read(evaluate.MAX_RESPONSE_BYTES + 1)
        evidence['http'] = {'status': response.status,
                            'body_text': body[:evaluate.MAX_RESPONSE_BYTES].decode('utf-8', errors='replace'),
                            'body_truncated': len(body) > evaluate.MAX_RESPONSE_BYTES}
    require(len(body) <= evaluate.MAX_RESPONSE_BYTES, 'HTTP response exceeded one MiB')
    result = json.loads(body)
    json.dumps(result, allow_nan=False)
    return result


def decide(prepared, arm, url, timeout):
    row = dict(prepared)
    row.update(model_called=False, request_ms=0.0, choice=None)
    if prepared['code_action'] is not None:
        row.update(status='code', action=prepared['code_action'], choice=prepared['code_action'])
    elif arm == 'no_added_gate':
        row.update(status='ok', action='allow', choice='allow')
    else:
        row['model_called'] = True
        start = time.monotonic()
        try:
            response = http_call(url, prepared['request'], timeout, row)
            row['response'] = response
            row.update((evaluate.validate_native if arm == 'kev' else evaluate.validate_baseline)(response, prepared['candidates']))
            row.update(status='ok', action=row['choice'])
        except ValueError as exc:
            row.update(status='invalid', action='defer', error=str(exc))
        except (OSError, urllib.error.HTTPError) as exc:
            row.update(status='error', action='defer', error=str(exc))
        row['request_ms'] = (time.monotonic() - start) * 1000
    row['decision_ms'] = row['preparation_ms'] + row['request_ms']
    return row


def metrics(rows):
    valid = [r for r in rows if r['status'] in ('ok', 'code')]
    supported = [r for r in rows if r['expected'] == 'allow']
    unsupported = [r for r in rows if r['expected'] == 'defer']
    latencies = [r['decision_ms'] for r in rows if r['status'] != 'not_run']
    request_times = [r['request_ms'] for r in rows if r['model_called']]
    pairs = {}
    for row in rows:
        pairs.setdefault((row['trial'], row['case_id']), {})[row['order']] = row
    valid_pairs = [p for p in pairs.values() if set(p) == {'normal', 'reverse'}
                   and all(r['status'] in ('ok', 'code') for r in p.values())]
    return {
        'total': len(rows), 'valid': len(valid),
        'correct': sum(r['choice'] == r['expected'] for r in valid),
        'accuracy_including_failures': sum(r['choice'] == r['expected'] for r in valid) / len(rows) if rows else None,
        'supported_total': len(supported), 'unsupported_total': len(unsupported),
        'unsupported_allowed': sum(r['action'] == 'allow' for r in unsupported),
        'supported_allowed': sum(r['action'] == 'allow' for r in supported),
        'supported_deferred': sum(r['action'] == 'defer' for r in supported),
        'semantic_unnecessary_deferrals': sum(r['expected'] == 'allow' and r['choice'] == 'defer' for r in valid),
        'failure_fallback_deferrals': sum(r['status'] not in ('ok', 'code') for r in rows),
        'unattempted': sum(r['status'] == 'not_run' for r in rows),
        'status_counts': dict(Counter(r['status'] for r in rows)),
        'model_calls': len(request_times), 'calls_avoided_by_code': sum(r['status'] == 'code' for r in rows),
        'decision_ms': {'p50': statistics.median(latencies) if latencies else None, 'p95': evaluate.percentile(latencies, .95)},
        'request_ms': {'p50': statistics.median(request_times) if request_times else None, 'p95': evaluate.percentile(request_times, .95)},
        'order_comparison': {'total_pairs': len(pairs), 'valid_pairs': len(valid_pairs),
                             'flips': sum(p['normal']['choice'] != p['reverse']['choice'] for p in valid_pairs)},
    }


def summarize(rows):
    result = {}
    for arm in ARMS:
        selected = [r for r in rows if r['arm'] == arm]
        primary = [r for r in selected if r['trial'] == 1 and r['order'] == 'normal']
        result[arm] = {
            'primary': {'whole_set': metrics(primary),
                        'unresolved_only': metrics([r for r in primary if r['code_action'] is None])},
            'all_correlated_views': {'whole_set': metrics(selected),
                                     'unresolved_only': metrics([r for r in selected if r['code_action'] is None])},
            'by_view': {f'{trial}/{order}': metrics([r for r in selected if r['trial'] == trial and r['order'] == order])
                        for trial, order in sorted({(r['trial'], r['order']) for r in selected})},
        }
    return result


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def record_unattempted(result, dataset, repeats):
    """Keep planned cases in denominators if startup, warmup or cancellation stops an arm."""
    present = {(r['arm'], r['trial'], r['case_id'], r['order']) for r in result['rows']}
    for arm in ARMS:
        for trial in range(1, repeats + 1):
            for case in dataset['cases']:
                for order in ('normal', 'reverse'):
                    if (arm, trial, case['id'], order) in present:
                        continue
                    filtered, code_action, reason = common_gate(case['input'], order == 'reverse')
                    result['rows'].append({
                        'arm': arm, 'trial': trial, 'case_id': case['id'], 'order': order,
                        'expected': case['gold']['action'], 'status': 'not_run', 'action': 'defer', 'choice': None,
                        'code_action': code_action, 'code_reason': reason, 'filtered_input': filtered,
                        'candidates': list(reversed(LABELS)) if order == 'reverse' else list(LABELS),
                        'model_called': False, 'request': None, 'request_ms': 0.0, 'preparation_ms': 0.0,
                        'decision_ms': 0.0, 'error': 'Arm did not reach this planned case; see runtime/warmup/run failure.',
                    })


def measure_arm(result, dataset, arm, model, url, repeats, timeout, save):
    if arm != 'no_added_gate':
        case = next((c for c in dataset['cases'] if common_gate(c['input'])[1] is None), None)
        if case is not None:
            warmup = decide(prepare(dataset, case, arm, model, 'normal'), arm, url, timeout)
            warmup.update(arm=arm, case_id=case['id'], excluded_from_metrics=True)
            result['warmups'].append(warmup)
            save()
            if warmup['status'] != 'ok':
                raise RuntimeError(f'{arm} warmup failed; raw evidence saved without retry')
    for trial in range(1, repeats + 1):
        cases = dataset['cases'] if trial % 2 else list(reversed(dataset['cases']))
        for case in cases:
            for order in ('normal', 'reverse'):
                row = decide(prepare(dataset, case, arm, model, order), arm, url, timeout)
                row.update(arm=arm, case_id=case['id'], trial=trial, order=order, expected=case['gold']['action'])
                result['rows'].append(row)
                save()
                print(f'{arm}/{trial}/{case["id"]}/{order}: {row["action"]} '
                      f'({row["status"]}) {row["decision_ms"]:.1f}ms', flush=True)


def cleanup(children, info):
    errors = []
    info['children'] = []
    for name, child in reversed(children):
        try:
            metal.stop([child])
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f'{name} stop: {exc}')
        stopped = child.poll() is not None
        info['children'].append({'name': name, 'pid': child.pid, 'exit_code': child.poll(), 'stopped': stopped})
        if not stopped:
            errors.append(f'{name} is still running')
    info['ports_closed'] = {}
    for port in (RUNTIME_PORT, GUARD_PORT):
        try:
            closed = not port_is_listening(port)
            info['ports_closed'][str(port)] = closed
            if not closed:
                errors.append(f'port {port} still accepts connections')
        except OSError as exc:
            info['ports_closed'][str(port)] = False
            errors.append(f'port {port} closure unproved: {exc}')
    info['runtime_stopped'] = all(c['stopped'] for c in info['children'])
    info['cleanup_errors'] = errors
    if errors:
        info['status'] = 'failed'


def command(args):
    return subprocess.check_output(args, cwd=ROOT, text=True, timeout=30).strip()


def model_arm(result, dataset, arm, lock, run_dir, repeats, timeout, save):
    info = {'arm': arm, 'model': lock, 'status': 'starting'}
    result['runtimes'].append(info)
    children = []
    start = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    log_path = run_dir / (arm + '.runtime.log')
    try:
        for port in (RUNTIME_PORT, GUARD_PORT):
            if port_is_listening(port):
                raise RuntimeError(f'Experiment port {port} already has a listener')
        launch = [str(metal.SERVER), '-m', str(ROOT / 'models' / lock['filename']),
                  '--alias', lock['alias'], '--host', '127.0.0.1', '--port', str(RUNTIME_PORT),
                  '-ngl', '99', '-t', '4', '-tb', '4', '-c', '4096', '-b', '512', '-ub', '512',
                  '-np', '1', '--no-context-shift', '--metrics', '-lv', '4']
        info['launch'] = launch
        env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
        with log_path.open('w') as log:
            children.append(('runtime', subprocess.Popen(launch, env=env, stdout=log, stderr=subprocess.STDOUT)))
        metal.ready(f'http://127.0.0.1:{RUNTIME_PORT}/health', [c for _, c in children], timeout=180)
        info['gpu_offload'] = metal.metal_offload(log_path.read_text())
        url = f'http://127.0.0.1:{RUNTIME_PORT}/v1/chat/completions'
        if arm == 'kev':
            guard_config = dict(SEMSELECT_ADDR=f'127.0.0.1:{GUARD_PORT}',
                                SEMSELECT_UPSTREAM=f'http://127.0.0.1:{RUNTIME_PORT}',
                                SEMSELECT_MODEL=lock['alias'], SEMSELECT_TIMEOUT=f'{timeout}s')
            info['guard_config'] = guard_config
            env = {k: v for k, v in os.environ.items() if not k.startswith('SEMSELECT_')}
            env.update(guard_config)
            with (run_dir / (arm + '.guard.log')).open('w') as log:
                children.append(('guard', subprocess.Popen([str(metal.GUARD)], env=env, stdout=log, stderr=subprocess.STDOUT)))
            metal.ready(f'http://127.0.0.1:{GUARD_PORT}/ready', [c for _, c in children], timeout=180)
            url = f'http://127.0.0.1:{GUARD_PORT}/v1/systemone'
        info.update(startup_seconds=time.monotonic() - start, status='measuring', endpoint=url)
        save()
        measure_arm(result, dataset, arm, lock['alias'], url, repeats, timeout, save)
        info['status'] = 'passed' if all(r['status'] in ('ok', 'code') for r in result['rows'] if r['arm'] == arm) else 'failed'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        info.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    except BaseException as exc:
        info.update(status='interrupted', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        cleanup(children, info)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        info.update(wall_seconds=time.monotonic() - start,
                    child_cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                    largest_child_peak_rss_bytes=after.ru_maxrss)
        save()
    if info['cleanup_errors']:
        raise RuntimeError('Owned runtime cleanup failed; do not launch another arm')


def run(args):
    dataset = load_dataset(args.dataset)
    verify_frozen_dataset(args.dataset, dataset, args.expected_dataset_sha256)
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Metal requires macOS arm64')
    locks = {arm: load_lock(ROOT / name) for arm, name in
             [('qwen_json', 'models.baseline.lock.json'), ('kev', 'models.lock.json')]}
    for lock in locks.values():
        require(lock['runtime_revision'] == metal.REVISION, 'model/runtime revision mismatch')
        require(verify(ROOT / 'models' / lock['filename'], lock), 'pinned model missing or corrupt; fetch explicitly first')
    build = json.loads((metal.CACHE / 'build.json').read_text())
    require(build['runtime_revision'] == metal.REVISION and metal.runtime_hashes() == build['runtime_files_sha256']
            and metal.sha256(metal.GUARD) == build['guard_sha256'], 'native runtime/guard provenance mismatch')
    for port in (RUNTIME_PORT, GUARD_PORT):
        require(not port_is_listening(port), f'experiment port {port} already has a listener')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    run_dir = ROOT / 'results' / 'answerability' / (run_id + '-metal')
    run_dir.mkdir(parents=True)
    result = {
        'kind': 'answerability-' + ('source-pilot' if dataset['scope'] == 'heldout/source-pilot' else 'teaching') + '-not-benchmark',
        'run_id': run_id, 'status': 'running',
        'started_at': datetime.now(timezone.utc).isoformat(), 'repeats': args.repeats,
        'dataset': dataset, 'dataset_sha256': metal.sha256(args.dataset),
        'expected_dataset_sha256': args.expected_dataset_sha256,
        'instructions_sha256': hashlib.sha256(dataset['instructions'].encode()).hexdigest(),
        'runtimes': [], 'rows': [], 'warmups': [],
        'hardware': {'platform': platform.platform(), 'machine': platform.machine(),
                     'cpu': command(['sysctl', '-n', 'machdep.cpu.brand_string']),
                     'memory_bytes': int(command(['sysctl', '-n', 'hw.memsize']))},
        'build': build, 'code_commit': command(['git', 'rev-parse', 'HEAD']),
        'protocol': {
            'baseline': 'No added semantic gate on frozen evidence after shared coded applicability/fact checks; not a rerun of retrieval/fusion.',
            'threshold': 'Native selected argmax; no threshold tuning.',
            'perturbation': 'Reverse passage, fact and candidate order together; composite perturbation, no isolated causal claim. Second trial reverses case traversal.',
            'cache': 'Prefix caching enabled in both arms: Qwen cache_prompt=true; native SystemOne uses its pinned default true, not overrideable through the guard. Model, prompt and serving-path/cache behavior differ; these are not cold or matched-decoding timings.',
            'timing': 'Decision time is common filtering/request preparation plus HTTP, excluding evidence serialization to disk, startup and warmup. Code skips included in whole-set metrics. Shared laptop, no thermal control.',
            'failures': 'Malformed/transport responses defer for action but never count as correct semantic predictions. Unattempted cases stay in denominators, are marked not_run and have no latency sample. No retries.',
            'resources': 'Native macOS, no container limits; child CPU includes startup, warmup and shutdown. Peak RSS is cumulative largest child lifetime peak, not summed memory or Metal allocation.',
            'generalization': f'{len(dataset["cases"])} cases; scope {dataset["scope"]}. '
            'For a source pilot, cases are manually selected from new families after freezing the teaching prompt; '
            'this does not establish random live traffic or absence from pretraining. '
            'Repeats and reversed variants are correlated. No calibration or downstream generator benefit established.',
            'primary': 'Predefined first trial with normal evidence/candidate order. Other views are descriptive robustness checks, not independent examples.',
        },
    }
    source = run_dir / 'source'
    source.mkdir()
    for name in ('scripts/answerability.py', 'scripts/evaluate.py', 'scripts/metal.py', 'scripts/model.py',
                 'scripts/compare_scoring.py', 'models.baseline.lock.json', 'models.lock.json'):
        shutil.copy2(ROOT / name, source / Path(name).name)
    shutil.copy2(args.dataset, source / 'cases.json')
    result['source_sha256'] = {p.name: metal.sha256(p) for p in source.iterdir()}
    if dataset['scope'] == 'heldout/source-pilot':
        fixture = run_dir / 'fixture'
        fixture.mkdir()
        for name in ('sources.json', 'freeze.json'):
            shutil.copy2(args.dataset.parent / name, fixture / name)
        shutil.copytree(args.dataset.parent / 'source', fixture / 'source')
        result['fixture_sha256'] = {str(p.relative_to(fixture)): metal.sha256(p)
                                    for p in fixture.rglob('*') if p.is_file()}
    (run_dir / 'working-tree.diff').write_text(command(['git', 'diff', 'HEAD']))
    def save():
        result['summary'] = summarize(result['rows'])
        save_json(run_dir / 'comparison.json', result)
    save()
    print(f'Evidence: {run_dir}', flush=True)
    try:
        measure_arm(result, dataset, 'no_added_gate', '', '', args.repeats, args.timeout, save)
        for arm in ('qwen_json', 'kev'):
            model_arm(result, dataset, arm, locks[arm], run_dir, args.repeats, args.timeout, save)
        result['status'] = 'passed' if all(r['status'] == 'passed' for r in result['runtimes']) else 'failed'
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        record_unattempted(result, dataset, args.repeats)
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
        print(f'Evidence saved: {run_dir}', flush=True)
    return 0 if result['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--validate', action='store_true', help='Validate fixtures offline without any model launch')
    mode.add_argument('--hardware', choices=['metal'])
    parser.add_argument('--dataset', type=Path, default=ROOT / 'eval/answerability/cases.json')
    parser.add_argument('--expected-dataset-sha256', help='Required pre-reviewed dataset digest for a source pilot')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--timeout', type=evaluate.positive_float, default=120)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error('--repeats must be 1..3')
    if args.timeout > 120:
        parser.error('--timeout must not exceed 120 seconds')
    if args.validate:
        dataset = load_dataset(args.dataset)
        if args.expected_dataset_sha256 is not None:
            verify_frozen_dataset(args.dataset, dataset, args.expected_dataset_sha256)
        counts = Counter(common_gate(case['input'])[1] or 'unresolved' for case in dataset['cases'])
        print(json.dumps({'valid': True, 'cases': len(dataset['cases']), 'common_code': dict(counts),
                          'sha256': metal.sha256(args.dataset)}, indent=2))
        return 0
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    metal.CACHE.mkdir(exist_ok=True)
    with (metal.CACHE / 'operation.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run(args)


if __name__ == '__main__':
    sys.exit(main())
