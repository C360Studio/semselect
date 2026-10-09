#!/usr/bin/env python3
"""Compare Qwen JSON labels and raw one-token option scores on CPU/Docker or Metal.

Evaluation-only code: this does not extend semselect's production API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time

import evaluate
import metal
from model import load_lock, verify

ROOT = Path(__file__).resolve().parents[1]
PORT = 18086
BASE = f'http://127.0.0.1:{PORT}'
TOP_LOGPROBS = 256
PROMPT_VERSION = 1


def post(path, body):
    return evaluate.post_json(BASE + path, body, 120)


def score_messages(dataset, case, labels):
    options = '\n'.join(f'{chr(65 + i)}: {label}: {dataset["categories"][label]}'
                        for i, label in enumerate(labels))
    return [
        {'role': 'system', 'content': dataset['instructions'] + '\nOptions:\n' + options
         + '\nReturn only the letter of the selected option.'},
        {'role': 'user', 'content': case['text']},
    ]


def check_label_tokens(prompt_ids, appended_ids):
    """Require one distinct token at the actual answer position, not in isolation."""
    ids = []
    for tokens in appended_ids:
        if len(tokens) != len(prompt_ids) + 1 or tokens[:-1] != prompt_ids:
            raise ValueError('Option label is not one token at the answer position')
        ids.append(tokens[-1])
    if len(set(ids)) != len(ids):
        raise ValueError('Option labels do not have distinct token IDs')
    return ids


def prepare_score(dataset, case, labels):
    if not 2 <= len(labels) <= 26:
        raise ValueError('This experiment supports 2..26 single-letter options')
    messages = score_messages(dataset, case, labels)
    prompt = post('/apply-template', {
        'messages': messages, 'chat_template_kwargs': {'enable_thinking': False},
    })['prompt']
    if '<think>' in prompt.rsplit('<|im_start|>assistant', 1)[-1] and not prompt.endswith('</think>\n\n'):
        raise ValueError('Expected a closed Qwen thinking block at the answer position')
    def tokenize(text):
        return post('/tokenize', {'content': text, 'add_special': False, 'parse_special': True})['tokens']
    prompt_ids = tokenize(prompt)
    letters = [chr(65 + i) for i in range(len(labels))]
    label_ids = check_label_tokens(prompt_ids, [tokenize(prompt + letter) for letter in letters])
    request = {
        'prompt': prompt_ids, 'n_predict': 1, 'temperature': 0,
        'samplers': ['temperature'], 'seed': 0,
        'grammar': 'root ::= ' + ' | '.join(json.dumps(letter) for letter in letters),
        'n_probs': TOP_LOGPROBS, 'post_sampling_probs': False,
        'cache_prompt': False, 'return_tokens': True,
    }
    return {'request': request, 'prompt': prompt, 'messages': messages,
            'label_token_ids': dict(zip(labels, label_ids)), 'letters': dict(zip(labels, letters))}


def validate_score(response, label_ids):
    """Normalize complete raw vocabulary scores; reject missing or altered evidence."""
    try:
        outputs = response['completion_probabilities']
        settings = response['generation_settings']
        if (len(outputs) != 1 or response.get('truncated') is not False
                or response.get('stop_type') != 'limit'
                or settings.get('post_sampling_probs') is not False):
            raise ValueError('Expected one complete token with pre-sampling logprobs')
        entries = outputs[0]['top_logprobs']
        if len({entry['id'] for entry in entries}) != len(entries):
            raise ValueError('Duplicate token scores')
        by_id = {entry['id']: entry['logprob'] for entry in entries}
        scores = {label: by_id[token] for label, token in label_ids.items()}
        if any(not evaluate.finite_number(v) or v > 1e-6 for v in scores.values()):
            raise ValueError('Invalid raw token log-probability')
        peak = max(scores.values())
        weights = {label: math.exp(score - peak) for label, score in scores.items()}
        total = sum(weights.values())
        probabilities = {label: weight / total for label, weight in weights.items()}
        label_mass = math.exp(peak) * total
        if not 0 < label_mass <= 1 + 1e-5:
            raise ValueError('Invalid full-vocabulary candidate mass')
        selected_id = response['tokens'][0]
        if response['tokens'] != [selected_id] or selected_id not in label_ids.values():
            raise ValueError('Generated token is not exactly one supplied label')
        choice = next(label for label, token in label_ids.items() if token == selected_id)
        if not math.isclose(probabilities[choice], max(probabilities.values()), abs_tol=1e-7):
            raise ValueError('Greedy generated label disagrees with raw-score argmax')
        return {'choice': choice, 'probabilities': probabilities,
                'raw_label_logprobs': scores, 'label_mass': label_mass,
                'pmax': probabilities[choice], 'confidence': None}
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('Missing complete raw scores for every candidate') from exc


def summaries(rows, unknown):
    result = {}
    for mode in ('json', 'token_scores'):
        selected = [row for row in rows if row['mode'] == mode]
        # Pair IDs include trial, keeping repeats separate from order reversals.
        summary = evaluate.summarize(selected, unknown, 'semselect' if mode == 'token_scores' else 'seminstruct')
        summary.pop('native_confidence_available')
        summary['candidate_probabilities_available'] = mode == 'token_scores'
        valid = [row for row in selected if row['status'] == 'ok']
        summary['brier_multiclass_sum_valid_only'] = (
            sum(sum((prob - (label == row['expected'])) ** 2
                    for label, prob in row['probabilities'].items()) for row in valid) / len(valid)
            if mode == 'token_scores' and valid else None)
        summary['per_trial'] = {}
        for trial in sorted({row['trial'] for row in selected}):
            trial_rows = [row for row in selected if row['trial'] == trial]
            summary['per_trial'][str(trial)] = {
                'correct': sum(row['status'] == 'ok' and row['choice'] == row['expected'] for row in trial_rows),
                'total': len(trial_rows),
                'p50_ms': statistics.median(row['latency_ms'] for row in trial_rows),
                'p95_ms': evaluate.percentile([row['latency_ms'] for row in trial_rows], .95),
            }
        result[mode] = summary
    return result


def compare(run_dir, dataset, model, repeats):
    result = {'kind': 'matched-format-routing-smoke-not-benchmark', 'prompt_version': PROMPT_VERSION,
              'started_at': datetime.now(timezone.utc).isoformat(), 'dataset': dataset,
              'model': model, 'repeats': repeats, 'cache_prompt': False,
              'notes': 'Same model/runtime/hardware. Different output-format prompts remain a confounder. '
              'Template/token validation is recorded but excluded from inference HTTP latency. '
              'One slot; alternating format order; warmup excluded; no retries. '
              'Scores are raw vocabulary logprobs normalized only over complete supplied candidates. '
              'No calibrated correctness claim; top-256 truncation is rejected if any candidate is missing. '
              'Repeats and candidate reversals are correlated, not independent examples.',
              'prepared': [], 'warmup': [], 'rows': []}
    output = run_dir / 'comparison.json'
    def save():
        result['summary'] = summaries(result['rows'], dataset['unknown_label'])
        output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    try:
        labels = list(dataset['categories'])
        for case in dataset['cases']:
            for order, candidates in (('normal', labels), ('reverse', labels[::-1])):
                started = time.monotonic()
                prepared = prepare_score(dataset, case, candidates)
                baseline = evaluate.build_request('seminstruct', model, dataset, case, candidates)
                baseline['cache_prompt'] = False
                baseline['seed'] = 0
                prepared.update(case=case, order=order, candidates=candidates, json_request=baseline,
                                preparation_ms=(time.monotonic() - started) * 1000)
                result['prepared'].append(prepared)
        first = result['prepared'][0]
        for mode in ('json', 'token_scores'):
            started = time.monotonic()
            warmup = {'mode': mode}
            result['warmup'].append(warmup)
            try:
                response = post('/v1/chat/completions' if mode == 'json' else '/completion',
                                first['json_request'] if mode == 'json' else first['request'])
                warmup['response'] = response
                warmup['validation'] = (evaluate.validate_baseline(response, labels) if mode == 'json'
                                        else validate_score(response, first['label_token_ids']))
                warmup['status'] = 'ok'
            except ValueError as exc:
                warmup.update(status='invalid', error=str(exc))
                raise
            except OSError as exc:
                warmup.update(status='error', error=str(exc))
                raise
            finally:
                warmup['latency_ms'] = (time.monotonic() - started) * 1000
                save()
        save()
        for trial in range(1, repeats + 1):
            for index, prepared in enumerate(result['prepared']):
                modes = ('json', 'token_scores') if (index + trial) % 2 else ('token_scores', 'json')
                for mode in modes:
                    case = prepared['case']
                    row = {'id': f'{trial}/{case["id"]}', 'case_id': case['id'], 'trial': trial,
                           'mode': mode, 'order': prepared['order'], 'expected': case['expected'],
                           'candidates': prepared['candidates'], 'prepared_index': index}
                    started = time.monotonic()
                    try:
                        response = post('/v1/chat/completions' if mode == 'json' else '/completion',
                                        prepared['json_request'] if mode == 'json' else prepared['request'])
                        row['response'] = response
                        row.update(evaluate.validate_baseline(response, prepared['candidates']) if mode == 'json'
                                   else validate_score(response, prepared['label_token_ids']))
                        row['status'] = 'ok'
                    except ValueError as exc:
                        row.update(status='invalid', error=str(exc))
                    except OSError as exc:
                        row.update(status='error', error=str(exc))
                    row['latency_ms'] = (time.monotonic() - started) * 1000
                    result['rows'].append(row)
                    save()
                    print(f'{row["id"]}/{row["order"]}/{mode}: {row.get("choice", row["status"])} '
                          f'{row["latency_ms"]:.0f}ms', flush=True)
        result['status'] = 'passed' if all(row['status'] == 'ok' for row in result['rows']) else 'failed'
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    if result['status'] != 'passed':
        raise RuntimeError('Comparison contains failed or invalid responses; evidence preserved')


def command(args):
    return subprocess.check_output(args, cwd=ROOT, text=True, timeout=30).strip()


def port_is_listening(port):
    # bind() also rejects TIME_WAIT sockets after a clean server exit. Connect
    # checks the listener; errors other than refusal are not proof of closure.
    with socket.socket() as sock:
        sock.settimeout(1)
        code = sock.connect_ex(('127.0.0.1', port))
    if code == 0:
        return True
    if code == errno.ECONNREFUSED:
        return False
    raise OSError(code, 'Could not establish experiment listener state')


def cleanup_runtime(info, child, container, run_dir):
    """Save each cleanup outcome; a successful comparison cannot hide a live runtime."""
    errors = []
    if child is not None:
        try:
            metal.stop([child])
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f'native stop: {exc}')
        info['runtime_exit_code'] = child.poll()
        info['runtime_stopped'] = child.poll() is not None
        if not info['runtime_stopped']:
            errors.append('owned native runtime is still running')
    if container is not None:
        try:
            info['cgroup_memory_peak_bytes'] = int(command(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.peak']))
            info['cgroup_cpu_stat'] = command(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/cpu.stat'])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            info['resource_error'] = str(exc)
        try:
            command(['docker', 'stop', '--time=10', container])
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f'container stop: {exc}')
        try:
            state = json.loads(command(['docker', 'inspect', container]))[0]['State']
            if state['Running']:
                command(['docker', 'kill', container])
                state = json.loads(command(['docker', 'inspect', container]))[0]['State']
            info['stopped_container'] = state
            info['runtime_stopped'] = not state['Running']
        except (OSError, KeyError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            errors.append(f'container stop verification: {exc}')
            info['runtime_stopped'] = False
        try:
            log = subprocess.run(['docker', 'logs', container], capture_output=True, text=True, timeout=15, check=True)
            (run_dir / 'runtime.log').write_text(log.stdout + log.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f'container logs: {exc}')
        # Do not force-remove a container whose stopped state could not be checked.
        if info['runtime_stopped']:
            try:
                command(['docker', 'rm', container])
                info['container_removed'] = True
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f'container removal: {exc}')
        else:
            errors.append('owned container shutdown is unproved')
    try:
        info['port_closed'] = not port_is_listening(PORT)
        if not info['port_closed']:
            errors.append('experiment port still accepts connections')
    except OSError as exc:
        info['port_closed'] = False
        errors.append(f'port closure: {exc}')
    info['cleanup_errors'] = errors
    if errors:
        info['status'] = 'failed'


def run(args):
    lock = load_lock(ROOT / 'models.baseline.lock.json')
    model = ROOT / 'models' / lock['filename']
    if not verify(model, lock):
        raise RuntimeError('Pinned Qwen model missing or corrupt; run task metal:baseline:fetch')
    if port_is_listening(PORT):
        raise RuntimeError(f'Experiment port {PORT} already has a listener')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    run_dir = ROOT / 'results' / 'scoring-comparison' / f'{run_id}-{args.hardware}'
    run_dir.mkdir(parents=True)
    dataset = evaluate.load_dataset(ROOT / 'eval/routing-smoke.json')
    info = {'run_id': run_id, 'hardware': args.hardware, 'model': lock,
            'runtime_revision': metal.REVISION, 'client_platform': platform.platform(),
            'code_commit': command(['git', 'rev-parse', 'HEAD']), 'repeats': args.repeats,
            'dataset_sha256': metal.sha256(ROOT / 'eval/routing-smoke.json'),
            'notes': 'Shared laptop; no controlled thermal state. No CUDA. See explicit runtime_stopped and port_closed checks.'}
    source_dir = run_dir / 'source'
    source_dir.mkdir()
    for name in ('scripts/compare_scoring.py', 'scripts/evaluate.py', 'scripts/metal.py', 'scripts/model.py',
                 'models.baseline.lock.json', 'Dockerfile', 'eval/routing-smoke.json'):
        shutil.copy2(ROOT / name, source_dir / Path(name).name)
    info['source_sha256'] = {path.name: metal.sha256(path) for path in source_dir.iterdir()}
    (run_dir / 'working-tree.diff').write_text(command(['git', 'diff', 'HEAD']))
    child = None
    container = None
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    common = ['--alias', lock['alias'], '-t', '4', '-tb', '4', '-c', '4096', '-b', '512',
              '-ub', '512', '-np', '1', '--no-context-shift', '--metrics', '-lv', '4']
    try:
        if args.hardware == 'metal':
            if platform.system() != 'Darwin' or platform.machine() != 'arm64':
                raise RuntimeError('Metal requires macOS arm64')
            build = json.loads((metal.CACHE / 'build.json').read_text())
            if build['runtime_revision'] != lock['runtime_revision'] or metal.runtime_hashes() != build['runtime_files_sha256']:
                raise RuntimeError('Native runtime provenance mismatch')
            info['build'] = build
            info['host_cpu'] = command(['sysctl', '-n', 'machdep.cpu.brand_string'])
            info['host_memory_bytes'] = int(command(['sysctl', '-n', 'hw.memsize']))
            launch = [str(metal.SERVER), '-m', str(model), '--host', '127.0.0.1', '--port', str(PORT), '-ngl', '99'] + common
            info['launch'] = launch
            with (run_dir / 'runtime.log').open('w') as log:
                env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
                child = subprocess.Popen(launch, stdout=log, stderr=subprocess.STDOUT, env=env)
            metal.ready(BASE + '/health', [child])
            info['gpu_offload'] = metal.metal_offload((run_dir / 'runtime.log').read_text())
        else:
            image = json.loads(command(['docker', 'image', 'inspect', 'semselect-runtime:dev']))[0]
            if image['Config']['Labels'].get('io.semselect.llama-revision') != lock['runtime_revision']:
                raise RuntimeError('Docker image runtime revision does not match model lock')
            info['image'] = {key: image.get(key) for key in ('Id', 'RepoDigests', 'Architecture', 'Os', 'Config')}
            container = 'semselect-scoring-' + run_id.lower().replace('.', '-')
            launch = ['docker', 'run', '-d', '--pull=never', '--name', container,
                      '--cpus=4', '--memory=8g', '--read-only', '--tmpfs=/tmp',
                      '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                      '-p', f'127.0.0.1:{PORT}:8080', '-v', f'{ROOT / "models"}:/models:ro',
                      image['Id'], '-m', '/models/' + lock['filename'], '--host', '0.0.0.0',
                      '--port', '8080', '-ngl', '0'] + common
            info['launch'] = launch
            info['container_id'] = command(launch)
            metal.ready(BASE + '/health', [], timeout=180)
            info['container'] = json.loads(command(['docker', 'inspect', container]))[0]
            info['runtime_files_sha256'] = command(['docker', 'exec', container, 'sh', '-c', 'sha256sum /opt/llama/*'])
        print(f'Ready {args.hardware}; evidence: {run_dir}', flush=True)
        compare(run_dir, dataset, lock['alias'], args.repeats)
        info['status'] = 'passed'
    except BaseException as exc:
        info.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        cleanup_runtime(info, child, container, run_dir)
        if child is not None:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            info['largest_child_peak_rss_bytes'] = after.ru_maxrss
            info['child_cpu_seconds'] = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
        info['wall_seconds'] = time.monotonic() - started
        (run_dir / 'provenance.json').write_text(json.dumps(info, indent=2) + '\n')
        print(f'Evidence saved: {run_dir}', flush=True)
    if info['cleanup_errors']:
        raise RuntimeError('Runtime cleanup failed; see preserved provenance')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', choices=['metal', 'cpu-docker'], required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error('--repeats must be 1..3')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    metal.CACHE.mkdir(exist_ok=True)
    with (metal.CACHE / 'operation.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args)


if __name__ == '__main__':
    main()
