#!/usr/bin/env python3
"""Bounded, isolated Julia CPU compatibility and routing experiment.

This CLI starts an owned local Docker container. It never builds/pulls an image,
downloads weights, changes the service, or resumes an existing results directory.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import signal
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import evaluate
import model

REVISION = '6c59c40076c00eab49754dc955d7652d93f9e125'
IMAGE = 'semselect-runtime:dev'
IMAGE_ID = 'sha256:a24e5693f8e9634ded5a8b29a438ad87035a8e23a5ac182485d5eaee13c85db1'
PORT = 18094
BASE = f'http://127.0.0.1:{PORT}'
MEMORY = 2 * 1024**3
REQUEST_TIMEOUT = 10.0
INFERENCE_BUDGET = 180.0
READINESS_TIMEOUT = 90.0
PROBE_LIMIT_MS = 2000.0
DATASET = ROOT / 'eval/routing-smoke.json'
LOCK = ROOT / 'eval/julia/models.lock.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


class HTTPFailure(OSError):
    def __init__(self, status, body):
        super().__init__(f'HTTP {status}')
        self.response = {'http_status': status, 'body': body}


class Operations:
    """Injectable OS boundary. Commands and HTTP reads are bounded."""
    clock = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def run(self, command, timeout=30):
        if not 0 < timeout <= 30:
            raise ValueError('Command timeout must be within 30 seconds')
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            combined = command[:2] == ['docker', 'logs']
            result = subprocess.run(command, stdout=out, stderr=out if combined else err,
                                    timeout=timeout, check=False)
            out.seek(0)
            err.seek(0)
            output, error = out.read(16 * 1024**2 + 1), err.read(1024**2 + 1)
        if len(output) > 16 * 1024**2 or len(error) > 1024**2:
            raise ValueError('Command output exceeded retained evidence limit')
        output, error = output.decode('utf-8'), error.decode('utf-8')
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, output, error)
        return output

    def http(self, path, payload=None, timeout=REQUEST_TIMEOUT):
        self.last_http_receipt = None
        if path not in ('/health', '/props', '/tokenize', '/v1/systemone'):
            raise ValueError('Endpoint outside the isolated experiment')
        request = urllib.request.Request(BASE + path,
            None if payload is None else json.dumps(payload, allow_nan=False).encode(),
            {'Content-Type': 'application/json'})
        def receipt(status, body):
            retained = body[:evaluate.MAX_RESPONSE_BYTES]
            self.last_http_receipt = {
                'http_status': status, 'body_base64': base64.b64encode(retained).decode('ascii'),
                'observed_byte_count': len(body), 'retained_byte_count': len(retained),
                'sha256': hashlib.sha256(retained).hexdigest(),
                'truncated': len(body) > len(retained),
                'note': 'Lossless bounded response prefix; SHA-256 covers retained bytes. When truncated, total body length is unknown.'}
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(evaluate.MAX_RESPONSE_BYTES + 1)
                receipt(response.status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read(evaluate.MAX_RESPONSE_BYTES + 1)
            receipt(exc.code, body)
            raise HTTPFailure(exc.code, body[:evaluate.MAX_RESPONSE_BYTES].decode('utf-8', errors='replace')) from exc
        if len(body) > evaluate.MAX_RESPONSE_BYTES:
            raise ValueError('HTTP response exceeded one MiB')
        result = json.loads(body)
        json.dumps(result, allow_nan=False)
        return result

    def closed(self):
        with socket.socket() as sock:
            sock.settimeout(1)
            code = sock.connect_ex(('127.0.0.1', PORT))
        if code != errno.ECONNREFUSED:
            raise RuntimeError(f'Port {PORT} absence unproved: connect_ex={code}')


class Runtime:
    def __init__(self, lock, output, ops):
        self.lock, self.output, self.ops = lock, output, ops
        self.owner = uuid.uuid4().hex
        self.name = 'semselect-julia-' + self.owner[:12]
        self.container_id = None
        self.create_attempted = False
        self.metadata = {'owner': self.owner, 'name': self.name, 'runtime_revision': REVISION,
                         'image_tag': IMAGE, 'hardware': 'cpu-docker', 'started_at': now(),
                         'resources': {'cpus': 4, 'memory_bytes': MEMORY, 'gpu_layers': 0,
                                       'threads': 4, 'context': 1024, 'batch': 1024,
                                       'ubatch': 1024, 'slots': 1}}

    def checkpoint(self):
        save(self.output / 'runtime.json', self.metadata)

    def inspect(self, timeout=30):
        value, = json.loads(self.ops.run(['docker', 'inspect', self.container_id or self.name], timeout=timeout))
        labels = value.get('Config', {}).get('Labels', {}) or {}
        if (labels.get('io.semselect.owner') != self.owner
                or labels.get('io.semselect.experiment') != 'julia-cpu'
                or value.get('Image') != self.metadata['image_id']
                or (self.container_id is not None and value.get('Id') != self.container_id)):
            raise RuntimeError('Container ownership/identity mismatch; refusing to operate')
        if not re.fullmatch(r'[0-9a-f]{64}', value.get('Id', '')):
            raise RuntimeError('Inspected container ID is invalid')
        self.container_id = value['Id']
        self.metadata['container_id'] = self.container_id
        return value

    def start(self):
        self.ops.closed()
        image, = json.loads(self.ops.run(['docker', 'image', 'inspect', IMAGE]))
        image_id = image.get('Id', '')
        if (image_id != IMAGE_ID or image.get('Os') != 'linux'
                or image.get('Architecture') != 'arm64'
                or image.get('Config', {}).get('Labels', {}).get('io.semselect.llama-revision') != REVISION):
            raise ValueError('Local image identity, architecture or pinned runtime label mismatch')
        self.metadata.update(image=image, image_id=image_id, tested_architecture=image['Architecture'])
        self.metadata['other_running_containers'] = self.ops.run(
            ['docker', 'ps', '--format', '{{json .}}']).splitlines()
        args = ['docker', 'create', '--pull=never', '--name', self.name,
                '--label', 'io.semselect.experiment=julia-cpu', '--label', 'io.semselect.owner=' + self.owner,
                '--cpus', '4', '--memory', str(MEMORY), '--memory-swap', str(MEMORY),
                '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=64m', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--pids-limit', '128', '--network', 'bridge',
                '--publish', f'127.0.0.1:{PORT}:8080', '--mount',
                f'type=bind,source={ROOT / "models" / self.lock["filename"]},target=/models/model.gguf,readonly',
                image_id, '-m', '/models/model.gguf', '--alias', self.lock['alias'],
                '--host', '0.0.0.0', '--port', '8080', '-ngl', '0', '-t', '4', '-tb', '4',
                '-c', '1024', '-b', '1024', '-ub', '1024', '-np', '1', '--no-context-shift',
                '--no-cache-prompt', '--cache-reuse', '0', '--cache-ram', '0', '-lv', '4']
        self.metadata['create_command'] = args
        self.create_attempted = True
        self.checkpoint()  # Preserve the owner before any container can exist.
        result = self.ops.run(args).strip()
        if not re.fullmatch(r'[0-9a-f]{64}', result):
            raise RuntimeError('Docker create did not return a full container ID')
        self.container_id = result
        self.metadata['container_created'] = created = self.inspect()
        host = created['HostConfig']
        bindings = [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}]
        if (host.get('NanoCpus') != 4_000_000_000 or host.get('Memory') != MEMORY
                or host.get('MemorySwap') != MEMORY or not host.get('ReadonlyRootfs')
                or host.get('DeviceRequests') or host.get('NetworkMode') != 'bridge'
                or host.get('PortBindings', {}).get('8080/tcp') != bindings
                or host.get('CapDrop') != ['ALL'] or host.get('Privileged')
                or host.get('PidsLimit') != 128
                or 'no-new-privileges:true' not in host.get('SecurityOpt', [])):
            raise RuntimeError('Inspected container resources/security/network differ from profile')
        mount = [m for m in created.get('Mounts', []) if m.get('Destination') == '/models/model.gguf']
        if (len(mount) != 1 or mount[0].get('RW') or mount[0].get('Type') != 'bind'
                or mount[0].get('Source') != str(ROOT / 'models' / self.lock['filename'])):
            raise RuntimeError('Inspected model mount differs from the read-only pinned artifact')
        self.checkpoint()
        self.ops.run(['docker', 'start', self.container_id])
        deadline = self.ops.clock() + READINESS_TIMEOUT
        def readiness_remaining(limit=10):
            remaining = deadline - self.ops.clock()
            if remaining <= 0:
                raise TimeoutError('Runtime readiness exceeded 90 seconds')
            return min(limit, remaining)
        while True:
            if not self.inspect(timeout=readiness_remaining(5))['State']['Running']:
                raise RuntimeError('Owned container exited before readiness')
            try:
                self.ops.http('/health', timeout=readiness_remaining(2))
                break
            except (OSError, ValueError):
                self.ops.sleep(min(.25, max(0, deadline - self.ops.clock())))
        props = self.ops.http('/props', timeout=readiness_remaining())
        self.metadata['runtime_props'] = props
        if props.get('default_generation_settings', {}).get('n_ctx') != 1024 or props.get('total_slots', 1) != 1:
            raise RuntimeError('Runtime context/slot count differs from the frozen profile')
        self.metadata['runtime_files_sha256'] = self.ops.run(['docker', 'exec', self.container_id,
            'sh', '-c', 'sha256sum /opt/llama/llama-server /opt/llama/*.so*'], timeout=readiness_remaining()).splitlines()
        readiness_remaining()
        self.metadata.update(status='ready', ready_at=now())
        self.checkpoint()

    def stop(self):
        errors = []
        if self.create_attempted:
            try:
                before = self.inspect()
                self.metadata['container_before_stop'] = before
                if before['State']['Running']:
                    resources = {}
                    for name in ('memory.current', 'memory.peak', 'memory.events'):
                        try:
                            resources[name] = self.ops.run(['docker', 'exec', self.container_id, 'cat',
                                '/sys/fs/cgroup/' + name], timeout=5).strip()
                        except Exception as exc:
                            resources[name] = {'unavailable': f'{type(exc).__name__}: {exc}'}
                    self.metadata['cgroup_before_stop'] = resources
                    try:
                        self.ops.run(['docker', 'stop', '--time', '5', self.container_id], timeout=10)
                    except subprocess.SubprocessError as exc:
                        self.metadata['stop_fallback_reason'] = str(exc)
                        self.inspect()  # Recheck identity before fallback.
                        self.ops.run(['docker', 'kill', self.container_id], timeout=10)
                else:
                    self.metadata['cgroup_before_stop'] = {'unavailable': 'Container had already exited; inspect preserves OOM/exit status.'}
                after = self.inspect()
                self.metadata['container_stopped'] = after
                if after['State']['Running']:
                    raise RuntimeError('Owned container remains running after stop')
                (self.output / 'runtime.log').write_text(self.ops.run(
                    ['docker', 'logs', '--timestamps', self.container_id], timeout=30))
            except subprocess.CalledProcessError as exc:
                if self.container_id is None and ('No such object' in (exc.stderr or '') or 'No such container' in (exc.stderr or '')):
                    self.metadata['container_absent_after_create_failure'] = True
                else:
                    errors.append(f'container cleanup: {exc}; stderr={exc.stderr}')
            except Exception as exc:
                errors.append(f'container cleanup: {type(exc).__name__}: {exc}')
            try:
                self.ops.closed()
            except Exception as exc:
                errors.append('port closure unproved: ' + str(exc))
        self.metadata.update(stopped_at=now(), stopped=not errors, cleanup_errors=errors)
        self.checkpoint()
        if errors:
            raise RuntimeError('; '.join(errors))


def validate_smoke(response, request):
    if not isinstance(response, dict) or not isinstance(response.get('answers'), dict) or set(response['answers']) != set(request['questions']):
        raise ValueError('Primitive smoke must return exactly the requested answer IDs')
    result = evaluate.validate_native(response, list(request['questions']['route']['criteria']))
    try:
        score = response['answers']['urgency']
        levels = request['questions']['urgency']['criteria']
        probabilities = score['probabilities']
        if score['type'] != 'score' or set(probabilities) != {str(i) for i in range(len(levels))}:
            raise ValueError('Invalid Score response type/levels')
        if any(not evaluate.finite_number(p) or not 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError('Score probabilities must be finite and in [0, 1]')
        if not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-5):
            raise ValueError('Score probabilities do not sum to one')
        if not evaluate.finite_number(score.get('confidence')) or not 0 <= score['confidence'] <= 1:
            raise ValueError('Score confidence must be finite and in [0, 1]')
        if (not evaluate.finite_number(score['score']) or not 0 <= score['score'] <= len(levels) - 1
                or not math.isclose(score['score'], sum(int(i) * p for i, p in probabilities.items()), abs_tol=1e-5)
                or score['legend'] != {str(i): label for i, label in enumerate(levels)}):
            raise ValueError('Score expectation/legend is invalid')
        noul = response['answers']['refund']
        if noul['type'] != 'noul' or not evaluate.finite_number(noul['noul']) or not 0 <= noul['noul'] <= 1:
            raise ValueError('Noul response is invalid')
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing or malformed Score/Noul response') from exc
    return result


def summary(rows, unknown):
    """Never publish cohort accuracy or latency percentiles for partial execution."""
    complete = bool(rows) and all(row['status'] == 'ok' for row in rows)
    if complete:
        value = evaluate.summarize(rows, unknown, 'semselect')
        value['complete'] = True
        return value
    counts = {state: sum(row['status'] == state for row in rows) for state in sorted({r['status'] for r in rows})}
    return {'complete': False, 'total': len(rows), 'valid': counts.get('ok', 0),
            'status_counts': counts, 'correct': None, 'accuracy': None, 'unknown_selection_rate': None,
            'unknown_policy': None, 'pmax_thresholds': None, 'order_comparison': None,
            'latency_ms': {'p50': None, 'p95': None, 'max': None},
            'note': 'Partial compatibility/latency evidence only; individual completed responses remain in rows.'}


def make_plan(dataset, smoke, alias):
    labels = list(dataset['categories'])
    def routing_row(case, order='normal'):
        candidates = labels if order == 'normal' else list(reversed(labels))
        return {'id': case['id'], 'expected': case['expected'], 'order': order,
                'tags': case.get('tags', []), 'candidates': candidates, 'status': 'not_run',
                'latency_ms': None, 'request': evaluate.build_request('semselect', alias, dataset, case, candidates)}
    smoke = dict(smoke, model=alias)
    return {'kind': 'julia-cpu-screen-not-benchmark', 'started_at': now(), 'model': alias,
              'endpoint': BASE + '/v1/systemone', 'dataset': dataset,
              'profile': {'request_timeout_seconds': REQUEST_TIMEOUT, 'inference_wall_budget_seconds': INFERENCE_BUDGET,
                          'probe_median_limit_ms': PROBE_LIMIT_MS},
              'smoke': {'status': 'not_run', 'request': smoke, 'latency_ms': None},
              'warmup': routing_row(dataset['cases'][0]),
              'probes': [routing_row(case) for case in dataset['cases'][:3]],
              'rows': [routing_row(case, order) for case in dataset['cases'] for order in ('normal', 'reverse')],
              'gate': {'passed': False, 'reason': 'not assessed', 'median_ms': None},
              'notes': 'Smoke and one routing warmup are excluded from all metrics. The three preselected first fixture cases gate the quality run; probes are not quality evidence. Quality rows repeat the existing 24-case fixture in normal/reverse order. These observations are correlated. Native distributions are not calibrated correctness confidence. The 180-second wall budget includes all token preflight and inference after readiness; HTTP latency excludes token preflight. All planned requests must pass token preflight before semantic inference begins. Cache flags explicitly disable prompt reuse; runtime logs remain authoritative for effective behavior.'}


def planned_rows(report):
    return [('smoke', report['smoke']), ('warmup', report['warmup'])] + [
        ('probe', row) for row in report['probes']] + [('quality', row) for row in report['rows']]


def freeze_requests(report, output):
    requests = [{'phase': phase, 'id': row.get('id'), 'order': row.get('order'), 'request': row['request']}
                for phase, row in planned_rows(report)]
    save(output / 'requests.json', requests)
    return digest(output / 'requests.json')


def experiment(dataset, smoke, alias, output, ops, check_request, plan=None):
    labels = list(dataset['categories'])
    report = plan if plan is not None else make_plan(dataset, smoke, alias)
    smoke = report['smoke']['request']
    deadline = ops.clock() + INFERENCE_BUDGET
    def checkpoint():
        report['summary'] = summary(report['rows'], dataset['unknown_label'])
        save(output / 'results.json', report)
    def remaining():
        value = deadline - ops.clock()
        if value <= 0:
            raise TimeoutError('180-second inference/preflight wall budget exhausted')
        return min(REQUEST_TIMEOUT, value)
    def preflight(row):
        row.update(preflight_status='running', tokenizations=[])
        def tokenize(text, parse_special=False):
            payload = {'content': text, 'add_special': False, 'parse_special': parse_special}
            entry = {'request': payload}
            row['tokenizations'].append(entry)
            checkpoint()
            ops.last_http_receipt = None
            try:
                response = ops.http('/tokenize', payload, remaining())
                entry['response'] = response
                if ops.clock() > deadline:
                    raise TimeoutError('Token response arrived after the 180-second budget')
            except BaseException as exc:
                entry['error'] = f'{type(exc).__name__}: {exc}'
                if hasattr(exc, 'response'):
                    entry['response'] = exc.response
                raise
            finally:
                if ops.last_http_receipt is not None:
                    entry['transport_receipt'] = ops.last_http_receipt
                checkpoint()
            tokens = response.get('tokens')
            if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
                raise ValueError('Tokenize must return an integer token array')
            return tokens
        try:
            row['preflight'] = check_request(row['request'], tokenize)
            for head in row['preflight']['heads'].values():
                actual = tokenize(head['rendered_prompt'], parse_special=True)
                if actual != head['tokens']:
                    raise ValueError('Native rendered token IDs differ from exact reconstructed Julia head')
                head['native_token_parity'] = True
            row['request_sha256'] = hashlib.sha256(json.dumps(row['request'], allow_nan=False).encode()).hexdigest()
            row['preflight_status'] = 'ok'
        except BaseException as exc:
            row.update(preflight_status='failed', preflight_error=f'{type(exc).__name__}: {exc}')
            if hasattr(exc, 'record'):
                row['preflight'] = exc.record
            if hasattr(exc, 'response'):
                row['response'] = exc.response
            raise
        finally:
            checkpoint()
    def execute(row, validator):
        row.update(status='running', started_at=now())
        checkpoint()
        began = None
        ops.last_http_receipt = None
        try:
            if row.get('preflight_status') != 'ok' or row['request_sha256'] != hashlib.sha256(
                    json.dumps(row['request'], allow_nan=False).encode()).hexdigest():
                raise ValueError('Request differs from its successful preflight')
            timeout = remaining()
            began = ops.clock()
            response = ops.http('/v1/systemone', row['request'], timeout)
            row['latency_ms'] = (ops.clock() - began) * 1000
            row['response'] = response
            checkpoint()  # Preserve raw response before interpretation.
            if ops.clock() > deadline:
                raise TimeoutError('Inference response arrived after the 180-second budget')
            usage = response.get('usage', {}) if isinstance(response, dict) else {}
            if (type(usage.get('input_tokens')) is not int
                    or usage['input_tokens'] != row['preflight']['total_input_tokens']
                    or type(usage.get('output_tokens')) is not int or usage['output_tokens'] != 0):
                raise ValueError('Native usage differs from exact preflight input tokens or produced output tokens')
            row.update(validator(response))
            row['status'] = 'ok'
        except BaseException as exc:
            if began is not None and row['latency_ms'] is None:
                row['latency_ms'] = (ops.clock() - began) * 1000
            row.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else
                       'timeout' if isinstance(exc, TimeoutError) else 'invalid' if isinstance(exc, ValueError) else 'error',
                       error=f'{type(exc).__name__}: {exc}')
            if hasattr(exc, 'record'):
                row['preflight'] = exc.record
            if hasattr(exc, 'response'):
                row['response'] = exc.response
            raise
        finally:
            if ops.last_http_receipt is not None:
                row['transport_receipt'] = ops.last_http_receipt
            row['finished_at'] = now()
            checkpoint()
            print(f'{row.get("id", "primitive-smoke")}/{row.get("order", "all")}: {row["status"]}'
                  f' {row["latency_ms"]}ms', flush=True)
    checkpoint()
    try:
        for _, row in planned_rows(report):
            preflight(row)
        report['all_preflights_passed'] = True
        checkpoint()
        execute(report['smoke'], lambda response: validate_smoke(response, smoke))
        execute(report['warmup'], lambda response: evaluate.validate_native(response, labels))
        for row in report['probes']:
            execute(row, lambda response: evaluate.validate_native(response, labels))
        median = statistics.median(row['latency_ms'] for row in report['probes'])
        report['gate'] = {'passed': median <= PROBE_LIMIT_MS, 'median_ms': median,
                          'reason': 'all probes valid and median within budget' if median <= PROBE_LIMIT_MS else 'probe median exceeds 2000ms'}
        checkpoint()
        if report['gate']['passed']:
            for row in report['rows']:
                execute(row, lambda response, row=row: evaluate.validate_native(response, row['candidates']))
        report['status'] = 'complete' if report['summary']['complete'] else 'stopped_by_latency_gate'
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'stopped_on_error',
                      error=f'{type(exc).__name__}: {exc}')
        if not report['gate']['passed']:
            report['gate']['reason'] = 'preflight, smoke, warmup or probe failed; quality run not started'
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        report['finished_at'] = now()
        report['budget_elapsed_seconds'] = ops.clock() - (deadline - INFERENCE_BUDGET)
        checkpoint()
    return report


def validate_protocol(protocol, dataset):
    expected = {'initial_image_id': IMAGE_ID, 'initial_image_architecture': 'linux/arm64',
                'runtime_revision': REVISION, 'cpu_limit': 4, 'threads': 4, 'memory_limit_bytes': MEMORY,
                'context_tokens': 1024, 'batch_tokens': 1024, 'physical_batch_tokens': 1024,
                'slots': 1, 'gpu_layers': 0, 'readiness_timeout_seconds': READINESS_TIMEOUT,
                'inference_and_tokenization_budget_seconds': INFERENCE_BUDGET,
                'request_timeout_seconds': REQUEST_TIMEOUT, 'routing_dataset_sha256': digest(DATASET),
                'primitive_fixture_sha256': digest(ROOT / 'examples/decision.json')}
    for key, value in expected.items():
        if protocol.get(key) != value:
            raise ValueError('Frozen protocol differs from runner: ' + key)
    gate = protocol.get('gate', {})
    if (gate.get('probe_case_ids') != [case['id'] for case in dataset['cases'][:3]]
            or gate.get('probe_order') != 'normal' or gate.get('maximum_probe_median_ms') != PROBE_LIMIT_MS
            or gate.get('primitive_shape_checks_required') != ['choice', 'score', 'noul']):
        raise ValueError('Frozen protocol gate differs from runner')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New results directory; never resumes or overwrites a run')
    args = parser.parse_args()
    from preflight import check_request
    lock = model.load_lock(LOCK)
    if lock.get('runtime_revision') != REVISION or not isinstance(lock.get('alias'), str):
        parser.error('Julia lock must provide an alias and the pinned runtime revision')
    if not model.verify(ROOT / 'models' / lock['filename'], lock):
        parser.error('Pinned Julia model is missing or failed size/SHA-256 verification')
    dataset = evaluate.load_dataset(DATASET)
    if len(dataset['cases']) != 24:
        parser.error('Expected the existing 24-case routing fixture')
    smoke = json.loads((ROOT / 'examples/decision.json').read_text())
    protocol = json.loads((ROOT / 'eval/julia/protocol.json').read_text())
    try:
        validate_protocol(protocol, dataset)
    except ValueError as exc:
        parser.error(str(exc))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = [LOCK, DATASET, ROOT / 'examples/decision.json', ROOT / 'Dockerfile',
             ROOT / 'scripts/model.py', ROOT / 'scripts/evaluate.py', Path(__file__),
             ROOT / 'eval/julia/preflight.py', ROOT / 'eval/julia/protocol.json', ROOT / 'eval/julia/gguf-metadata.json']
    provenance = {'model_lock': lock, 'model_verified': True, 'recorded_at': now(),
                  'source_sha256': {str(path.relative_to(ROOT)): digest(path) for path in paths},
                  'client_platform': platform.platform(), 'client_machine': platform.machine(),
                  'python_version': platform.python_version()}
    plan = make_plan(dataset, smoke, lock['alias'])
    plan['summary'] = summary(plan['rows'], dataset['unknown_label'])
    provenance['requests_sha256'] = freeze_requests(plan, output)
    for path in paths:
        target = output / 'source' / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    save(output / 'results.json', plan)
    save(output / 'provenance.json', provenance)
    runtime = Runtime(lock, output, Operations())
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Received signal {signum}')
    previous = signal.signal(signal.SIGTERM, interrupted)
    exit_code = 1
    try:
        runtime.start()
        result = experiment(dataset, smoke, lock['alias'], output, runtime.ops, check_request, plan)
        exit_code = 0 if result['status'] == 'complete' else 1
    except BaseException as exc:
        save(output / 'failure.json', {'error': f'{type(exc).__name__}: {exc}', 'at': now()})
        print(f'Julia experiment stopped: {type(exc).__name__}: {exc}', file=sys.stderr)
    finally:
        # A second signal must not interrupt the bounded owned-container cleanup.
        previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            runtime.stop()
        except BaseException as exc:
            exit_code = 1
            print(f'Owned runtime cleanup failed: {exc}', file=sys.stderr)
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGINT, previous_int)
    print(f'Evidence: {output}', file=sys.stderr)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
