#!/usr/bin/env python3
"""Replay frozen actual SemStreams synthesis evidence; evaluation only, no retrieval."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import answerability as gates
import metal
from compare_scoring import port_is_listening
from model import load_lock, verify

ARMS = [['no_gate', 'qwen_json', 'kev'], ['kev', 'qwen_json', 'no_gate']]
STRATA = ['cpu_06b', 'metal_4b']
QWEN_PORT, KEV_PORT, GUARD_PORT = 18089, 18087, 18088
CPU_URL, CPU_MODEL = 'http://127.0.0.1:48083/v1', 'seminstruct'
INSTRUCTIONS = ROOT / 'eval/answerability/cases.json'


def jsonl(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError(f'duplicate IDs: {path}')
    return {r['id']: r for r in rows}


def load_frozen(args):
    freeze = json.loads(args.freeze.read_text())
    paths = {'input': args.input, 'prompts': args.prompts, 'driver': args.driver,
             'acquiredstatuses': args.acquiredstatuses, 'instructions_dataset': INSTRUCTIONS}
    for key, path in paths.items():
        if freeze.get('hashes', {}).get(key) != metal.sha256(path):
            raise ValueError(f'frozen {key} hash mismatch; no runtime may launch')
    ids = freeze.get('ids', [])
    if len(ids) != 13 or len(set(ids)) != 13 or not all(isinstance(i, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', i) for i in ids):
        raise ValueError('freeze must preserve exactly 13 unique ordered IDs')
    if freeze.get('trials') != 2 or freeze.get('arm_order') != ARMS:
        raise ValueError('freeze must specify the fixed two-trial arm plan')
    if freeze.get('context_preflight_policy') != 'exact-runtime-tokenization-v1':
        raise ValueError('freeze must require exact-runtime-tokenization-v1 before any warmup or formal call')
    inputs, prompts = jsonl(args.input), jsonl(args.prompts)
    statuses = json.loads(args.acquiredstatuses.read_text())
    if isinstance(statuses, list):
        if len({r['id'] for r in statuses}) != len(statuses):
            raise ValueError('duplicate acquisition status IDs')
        statuses = {r['id']: r for r in statuses}
    if not all(set(records) == set(ids) for records in (inputs, prompts, statuses)):
        raise ValueError('input, prompt capture and acquisition status IDs must match all 13 frozen IDs')
    cases = {}
    for ident in ids:
        source, capture, status = inputs[ident], prompts[ident], statuses[ident]
        if set(source) != {'id', 'query', 'summaries', 'total_entities'}:
            raise ValueError('driver input may contain only id, query, summaries and total_entities')
        if not isinstance(source['query'], str) or not source['query'] or not isinstance(source['summaries'], list):
            raise ValueError('query must be nonempty text and summaries an array')
        if status.get('id') != ident or status.get('status') not in ('ok', 'error'):
            raise ValueError('acquisition status must be {id, status:ok|error, error:...}')
        if capture.get('mode') != 'capture_only' or capture.get('input') != source:
            raise ValueError('prompt capture is not the actual capture-only result for this exact input')
        calls = capture.get('calls')
        if not isinstance(calls, list) or len(calls) > 1:
            raise ValueError('actual synthesizer capture must contain zero or one call')
        if bool(calls) != bool(source['summaries']):
            raise ValueError('empty/nonempty summaries disagree with actual captured calls')
        text = None
        if calls:
            request = calls[0]['llm_request']
            text = request['UserPrompt']
            expected = [{'role': 'system', 'content': request['SystemPrompt']}, {'role': 'user', 'content': text}]
            if calls[0].get('messages') != expected or not isinstance(text, str) or not text:
                raise ValueError('capture message view disagrees with actual llm.ChatRequest')
        inp = {'question': source['query'], 'audience': 'developer', 'required_fact': None,
               'passages': [] if text is None else [{'id': 'actual-synthesis-prompt', 'text': text,
                                                      'metadata': {'audience': 'all', 'status': 'current'}}], 'facts': []}
        state_bytes = len(json.dumps(inp, ensure_ascii=False).encode())
        errors = freeze.get('profile_errors', {}).get(ident, [])
        if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
            raise ValueError('profile_errors must map case IDs to lists of strings')
        errors = list(errors)
        if state_bytes > 8192:
            errors.append(f'gate state is {state_bytes} UTF-8 bytes, exceeding 8192; no truncation')
        cases[ident] = {'input': inp, 'state_bytes': state_bytes, 'profile_errors': errors,
                        'acquisition': status, 'source': source, 'capture': capture}
    return freeze, cases, paths, gates.load_dataset(INSTRUCTIONS)


def pending_rows(ids):
    return [{'stratum': stratum, 'trial': trial, 'arm': arm, 'id': ident,
             'status': 'not_run', 'action': None, 'error': 'not reached', 'total_ms': None}
            for stratum in STRATA for trial in (1, 2) for arm in ARMS[trial-1] for ident in ids]


def eligibility(case):
    if case['acquisition']['status'] != 'ok':
        return 'acquisition_error', case['acquisition'].get('error') or 'capture failed'
    if case['profile_errors']:
        return 'profile_error', '; '.join(case['profile_errors'])
    return None, None


def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError('readiness response exceeds one MiB')
    return json.loads(body)


def driver_call(driver, source, base_url, model, directory):
    directory.mkdir(parents=True, exist_ok=False)
    input_path, output_path = directory / 'input.jsonl', directory / 'output.jsonl'
    input_path.write_text(json.dumps(source, ensure_ascii=False) + '\n')
    command = [str(driver), '--input', str(input_path), '--output', str(output_path),
               '--base-url', base_url, '--model', model]
    result = {'command': command, 'status': 'error', 'driver_duration_ms': None}
    started = time.monotonic()
    try:
        with (directory / 'process.log').open('w') as log:
            child = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=22, check=False)
        result['exit_code'] = child.returncode
        if output_path.exists():
            records = list(jsonl(output_path).values())
            if len(records) != 1 or records[0]['id'] != source['id']:
                raise ValueError('driver did not preserve exactly the requested case')
            record = records[0]
            result['record'] = record
            duration = record['duration_ms']
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
                raise ValueError('invalid driver duration')
            result['driver_duration_ms'] = duration
            if child.returncode != 0 or record.get('error'):
                result['error'] = record.get('error') or f'driver exited {child.returncode}'
            elif record.get('mode') != 'inference' or not isinstance(record.get('synthesis_outcome'), dict):
                result['error'] = 'driver did not return an actual synthesis outcome'
            else:
                result['status'] = 'degraded' if record['synthesis_outcome']['Degraded'] else 'ok'
        else:
            result['error'] = f'driver exited {child.returncode} without a recorded outcome'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['subprocess_wall_ms'] = (time.monotonic() - started) * 1000
        duration = result['driver_duration_ms']
        result['driver_startup_and_io_ms'] = max(0, result['subprocess_wall_ms'] - duration) if duration is not None else None
        gates.save_json(directory / 'attempt.json', result)
    return result


def gate_call(dataset, case, arm, aliases):
    profile = 'no_added_gate' if arm == 'no_gate' else arm
    port = GUARD_PORT if arm == 'kev' else QWEN_PORT
    path = '/v1/systemone' if arm == 'kev' else '/v1/chat/completions'
    prepared = gates.prepare(dataset, case, profile, aliases.get(arm, ''), 'normal')
    return gates.decide(prepared, profile, f'http://127.0.0.1:{port}{path}', 120)


def execute_row(row, case, dataset, aliases, driver, generator_url, generator_model, output):
    status, error = eligibility(case)
    if status:
        row.update(status=status, error=error, action=None)
        return
    gate = gate_call(dataset, case, row['arm'], aliases)
    row.update(gate=gate, action=gate['action'], total_ms=gate['decision_ms'])
    row.pop('error', None)
    # Empty evidence in no-gate control retains the actual driver's empty-input behavior.
    should_synthesize = row['arm'] == 'no_gate' or (gate['status'] in ('ok', 'code') and gate['action'] == 'allow')
    if not should_synthesize:
        row['status'] = 'deferred' if gate['status'] in ('ok', 'code') else 'gate_failure'
        return
    attempt = driver_call(driver, case['source'], generator_url, generator_model, output)
    row['synthesis'] = attempt
    row['status'] = {'ok': 'answered', 'degraded': 'degraded'}.get(attempt['status'], 'synthesis_failure')
    if attempt['status'] == 'ok' and not case['source']['summaries']:
        row['status'] = 'empty_no_synthesis'
    if attempt['driver_duration_ms'] is not None:
        row['total_ms'] += attempt['driver_duration_ms']
    else:
        row['total_ms'] = None


def verify_native():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('native gates/generator require macOS arm64')
    build = json.loads((metal.CACHE / 'build.json').read_text())
    if (build['runtime_revision'] != metal.REVISION or metal.runtime_hashes() != build['runtime_files_sha256']
            or metal.sha256(metal.GUARD) != build['guard_sha256']):
        raise ValueError('native runtime or guard hash mismatch')
    locks = {arm: load_lock(ROOT / name) for arm, name in
             [('qwen_json', 'models.baseline.lock.json'), ('kev', 'models.lock.json')]}
    for lock in locks.values():
        if lock['runtime_revision'] != metal.REVISION or not verify(ROOT / 'models' / lock['filename'], lock):
            raise ValueError('pinned native model missing, mismatched or corrupt; no download attempted')
    for port in (QWEN_PORT, KEV_PORT, GUARD_PORT):
        if port_is_listening(port):
            raise ValueError(f'experiment port {port} already has a listener')
    return build, locks


def launch_native(output, locks, children, info):
    for arm, port in [('qwen_json', QWEN_PORT), ('kev', KEV_PORT)]:
        lock = locks[arm]
        command = [str(metal.SERVER), '-m', str(ROOT / 'models' / lock['filename']),
                   '--alias', lock['alias'], '--host', '127.0.0.1', '--port', str(port),
                   '-ngl', '99', '-t', '4', '-tb', '4', '-c', '4096', '-b', '512', '-ub', '512',
                   '-np', '1', '--no-context-shift', '--metrics', '-lv', '4']
        if arm == 'qwen_json':
            command += ['--chat-template-kwargs', '{"enable_thinking":false}']
        info[arm] = {'command': command, 'model': lock}
        env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
        started = time.monotonic()
        with (output / (arm + '.runtime.log')).open('w') as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        children.append((arm, child))
        metal.ready(f'http://127.0.0.1:{port}/health', [child], timeout=180)
        info[arm]['startup_ms'] = (time.monotonic() - started) * 1000
        info[arm]['offload'] = metal.metal_offload((output / (arm + '.runtime.log')).read_text())
    env = {k: v for k, v in os.environ.items() if not k.startswith('SEMSELECT_')}
    env.update(SEMSELECT_ADDR=f'127.0.0.1:{GUARD_PORT}', SEMSELECT_UPSTREAM=f'http://127.0.0.1:{KEV_PORT}',
               SEMSELECT_MODEL=locks['kev']['alias'], SEMSELECT_TIMEOUT='120s')
    with (output / 'kev.guard.log').open('w') as log:
        guard = subprocess.Popen([str(metal.GUARD)], env=env, stdout=log, stderr=subprocess.STDOUT)
    children.append(('guard', guard))
    metal.ready(f'http://127.0.0.1:{GUARD_PORT}/ready', [c for _, c in children], timeout=180)


def cleanup(children):
    result = {'children': [], 'ports_closed': {}, 'errors': []}
    for name, child in reversed(children):
        try:
            metal.stop([child])
        except (OSError, subprocess.SubprocessError) as exc:
            result['errors'].append(f'{name}: {exc}')
        result['children'].append({'name': name, 'pid': child.pid, 'exit_code': child.poll()})
        if child.poll() is None:
            result['errors'].append(f'{name} still running')
    for port in (QWEN_PORT, KEV_PORT, GUARD_PORT):
        try:
            result['ports_closed'][str(port)] = not port_is_listening(port)
        except OSError as exc:
            result['ports_closed'][str(port)] = False
            result['errors'].append(f'port {port}: {exc}')
        if not result['ports_closed'][str(port)]:
            result['errors'].append(f'port {port} closure unproved')
    return result


def run(args):
    freeze, cases, paths, dataset = load_frozen(args)
    if args.validate:
        return {'validated': True, 'ids': freeze['ids'], 'max_gate_state_bytes': max(c['state_bytes'] for c in cases.values()),
                'runtime_context_not_checked': True,
                'eligibility': {i: eligibility(c)[0] or ('visible' if c['input']['passages'] else 'empty') for i, c in cases.items()}}
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'starting', 'started_at': datetime.now(timezone.utc).isoformat(), 'freeze': freeze,
              'rows': pending_rows(freeze['ids']), 'warmups': [], 'runtimes': {}, 'preflight': {},
              'limits': 'Two generator strata differ in size/hardware. Both native models remain loaded; requests serialize. '
                        'Primary=trial1; secondary=trial2. Actual SDK retries are retained; no orchestration retry. '
                        'total_ms=gate decision plus driver synthesis duration; subprocess startup/I/O and model startup separate. '
                        'No answer-quality grading is performed here. Acquisition/profile/gate/driver failures get no semantic credit.'}
    result['matched_ids'] = [i for i in freeze['ids'] if eligibility(cases[i])[0] is None]
    result['gate_state_bytes'] = {i: c['state_bytes'] for i, c in cases.items()}
    def save():
        gates.save_json(args.output / 'replay.json', result)
    for key, path in paths.items():
        if key != 'driver':
            shutil.copy2(path, args.output / (key + path.suffix))
    shutil.copy2(args.freeze, args.output / 'freeze.json')
    source = args.output / 'source'
    source.mkdir()
    for path in [Path(__file__), Path(__file__).with_name('context_preflight.py'),
                 ROOT/'scripts/answerability.py', ROOT/'scripts/evaluate.py', ROOT/'scripts/metal.py',
                 ROOT/'scripts/model.py', ROOT/'scripts/compare_scoring.py', ROOT/'models.lock.json', ROOT/'models.baseline.lock.json']:
        shutil.copy2(path, source/path.name)
    result['source_sha256'] = {p.name: metal.sha256(p) for p in source.iterdir()}
    save()
    for row in result['rows']:
        status, error = eligibility(cases[row['id']])
        if status:
            row.update(status=status, error=error)
    eligible = [i for i in freeze['ids'] if eligibility(cases[i])[0] is None and cases[i]['input']['passages']]
    if not eligible:
        result.update(status='no_usable_visible_evidence', finished_at=datetime.now(timezone.utc).isoformat())
        save()
        return result
    children = []
    try:
        build, locks = verify_native()
        result['build'] = build
        result['hardware'] = {'platform': platform.platform(), 'machine': platform.machine(),
                              'cpu': gates.command(['sysctl', '-n', 'machdep.cpu.brand_string']),
                              'memory_bytes': int(gates.command(['sysctl', '-n', 'hw.memsize']))}
        aliases = {arm: lock['alias'] for arm, lock in locks.items()}
        launch_native(args.output, locks, children, result['runtimes'])
        import context_preflight
        context = context_preflight.check(cases, dataset, aliases, args.output/'context-preflight')
        result['preflight']['context'] = context
        for ident, errors in context['case_errors'].items():
            if ident not in cases or not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
                raise ValueError('context preflight returned invalid case errors')
            cases[ident]['profile_errors'].extend(errors)
        for row in result['rows']:
            status, error = eligibility(cases[row['id']])
            if status:
                row.update(status=status, error=error)
        result['matched_ids'] = [i for i in freeze['ids'] if eligibility(cases[i])[0] is None]
        eligible = [i for i in result['matched_ids'] if cases[i]['input']['passages']]
        save()
        if not eligible:
            result['status'] = 'no_usable_visible_evidence'
            return result
        for arm in ('qwen_json', 'kev'):
            warmup = gate_call(dataset, cases[eligible[0]], arm, aliases)
            result['warmups'].append({'kind': 'gate', 'arm': arm, 'case_id': eligible[0], 'result': warmup})
            save()
            if warmup['status'] != 'ok':
                raise RuntimeError(f'{arm} gate warmup failed once; no retry')
        rows = {(r['stratum'], r['trial'], r['arm'], r['id']): r for r in result['rows']}
        for stratum in STRATA:
            url, model = (CPU_URL, CPU_MODEL) if stratum == 'cpu_06b' else (f'http://127.0.0.1:{QWEN_PORT}/v1', aliases['qwen_json'])
            try:
                if stratum == 'cpu_06b' and context.get('cpu_available') is not True:
                    raise ValueError('CPU generator context preflight unavailable; stratum is not eligible for inference')
                result['preflight'][stratum] = get_json(url + '/models')
            except (OSError, ValueError) as exc:
                result['preflight'][stratum] = {'error': str(exc)}
                for row in result['rows']:
                    if row['stratum'] == stratum and row['status'] == 'not_run':
                        row.update(status='generator_unavailable', error=str(exc))
                save()
                continue
            warmup = driver_call(args.driver, cases[eligible[0]]['source'], url, model, args.output/'warmup'/stratum)
            result['warmups'].append({'kind': 'generator', 'stratum': stratum, 'case_id': eligible[0], 'result': warmup})
            save()
            for trial in (1, 2):
                ids = freeze['ids'] if trial == 1 else list(reversed(freeze['ids']))
                for arm in ARMS[trial-1]:
                    for ident in ids:
                        row = rows[(stratum, trial, arm, ident)]
                        execute_row(row, cases[ident], dataset, aliases, args.driver, url, model,
                                    args.output/'calls'/stratum/f'trial-{trial}'/arm/ident)
                        save()
                        print(f'{stratum}/{trial}/{arm}/{ident}: {row["status"]}', flush=True)
        result['status'] = 'recorded' if all(r['status'] not in ('not_run', 'generator_unavailable', 'gate_failure', 'synthesis_failure') for r in result['rows']) else 'incomplete_or_failed'
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['cleanup'] = cleanup(children)
        if result['cleanup']['errors']:
            result['status'] = 'failed'
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'prompts', 'freeze', 'driver', 'output', 'acquiredstatuses'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--validate', action='store_true', help='Validate frozen inputs only; never launch a runtime')
    args = parser.parse_args()
    args.driver = args.driver.resolve()
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if args.validate:
        print(json.dumps(run(args), indent=2))
        return 0
    metal.CACHE.mkdir(exist_ok=True)
    with (metal.CACHE/'operation.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(args)
    return 0 if result['status'] == 'recorded' else 1


if __name__ == '__main__':
    sys.exit(main())
