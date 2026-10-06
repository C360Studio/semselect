"""Owned local runtime lifecycle and exact, inference-free token preflight.

No CLI, model download, build, or automatic inference. The experiment owner calls
start/stop only after review. CPU uses an inspected immutable local image ID.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import errno
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import metal

_spec = importlib.util.spec_from_file_location('routing_gguf_reader', ROOT / 'eval/synthesis/context_preflight.py')
_gguf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gguf)
KEV_TEMPLATE = _gguf.KEV_TEMPLATE
CPU_IMAGE_ID = 'sha256:a24e5693f8e9634ded5a8b29a438ad87035a8e23a5ac182485d5eaee13c85db1'
SOURCE = metal.CACHE / 'ggml-org-llama.cpp-6c59c40'
SOURCE_PINS = {
    'tools/server/server-decision.cpp': '72243606621400bd38f7c32cfb9c393de19c91cd3bdef755ce52a5d3fa67793a',
    'tools/server/server-context.cpp': '17552eda0b9fb09006340b0833d966d4439072248e3fe551010a86078863a8e2',
    'tools/server/server-task.h': '8275cc031ef9a582eda7efb64f1619db66d2adea1dd5712934d27df862ba92df',
    'common/json.cpp': 'd8c556445cef8e7c93b0857f6ca7ae7ebd8295f169403ac66641b72f6b0598f2',
    'common/arg.cpp': '3aa4fa11aa05656f4011e59a8765f09a441aee503e9410da828515560322f439',
}


def _save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


@lru_cache(maxsize=4)
def _verified_model(filename, size, digest, stat_key):
    path = ROOT / 'models' / filename
    if not metal.verify(path, {'size_bytes': size, 'sha256': digest}):
        raise ValueError('Pinned model missing or checksum mismatch')
    return True


def _check_model(lock):
    path = ROOT / 'models' / lock['filename']
    stat = path.stat()
    _verified_model(lock['filename'], lock['size_bytes'], lock['sha256'],
                    (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return path


def _verified_template():
    lock = json.loads((ROOT / 'models.lock.json').read_text())
    template = _gguf.gguf_template(_check_model(lock))
    if template != KEV_TEMPLATE:
        raise ValueError('Pinned GGUF SystemOne template differs from audited renderer')
    for path, digest in SOURCE_PINS.items():
        if metal.sha256(SOURCE / path) != digest:
            raise ValueError('Pinned runtime source differs from preflight audit: ' + path)
    return {'template': template, 'model_sha256': lock['sha256'], 'source_sha256': SOURCE_PINS,
            'runtime_revision': metal.REVISION}


def _artifacts(hardware, arm, lock):
    expected = json.loads((ROOT / ('models.lock.json' if arm == 'kev' else 'models.baseline.lock.json')).read_text())
    if lock != expected or lock['runtime_revision'] != metal.REVISION:
        raise ValueError('Model lock does not match pinned arm/runtime')
    _check_model(lock)
    build = json.loads((metal.CACHE / 'build.json').read_text())
    if build['runtime_revision'] != metal.REVISION or build['source_sha256'] != metal.SOURCE_SHA:
        raise ValueError('Native runtime revision/source provenance mismatch')
    if metal.sha256(metal.GUARD) != build['guard_sha256']:
        raise ValueError('Native guard hash mismatch')
    if hardware == 'metal':
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise ValueError('Metal path requires native Apple Silicon')
        if metal.runtime_hashes() != build['runtime_files_sha256']:
            raise ValueError('Native runtime binary/library provenance mismatch')
    if arm == 'kev':
        _verified_template()
    return build


def _json_http(url, body=None):
    if not re.fullmatch(r'http://127\.0\.0\.1:\d+/(?:health|ready|props|apply-template|tokenize)', url):
        raise ValueError('Lifecycle/preflight permits only bounded loopback metadata endpoints')
    request = urllib.request.Request(url, None if body is None else json.dumps(body).encode(),
                                     {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Runtime metadata response exceeds 2 MiB')
    return json.loads(raw)


class Operations:
    """OS boundary, injectable for tests; all commands are local and bounded."""
    def run(self, command, timeout=30):
        combined = command[:2] == ['docker', 'logs']
        limit = 64 * 1024 * 1024
        # Bound retained memory even for verbose runtime logs. Docker logs sends
        # the container's stderr to its own stderr; combine streams before capture.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(command, stdout=stdout, stderr=stdout if combined else stderr,
                                    timeout=timeout, check=False)
            stdout.seek(0)
            stderr.seek(0)
            output, error = stdout.read(limit + 1), stderr.read(limit + 1)
        if len(output) + len(error) > limit:
            raise ValueError('Command output exceeded 64 MiB')
        output, error = output.decode('utf-8'), error.decode('utf-8')
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, output, error)
        return output

    def launch(self, command, env, log):
        return subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)

    artifacts = staticmethod(_artifacts)
    http = staticmethod(_json_http)
    template = staticmethod(_verified_template)

    def closed(self, port):
        with socket.socket() as sock:
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            if result != errno.ECONNREFUSED:
                raise RuntimeError(f'Loopback port {port} closure unproved: connect_ex={result}')

    def available(self, port):
        # A plain bind can fail during TIME_WAIT after our prior runtime exits.
        # llama-server binds with SO_REUSEADDR, so test for an actual listener.
        # Only ECONNREFUSED proves absence; live/unknown results block launch.
        self.closed(port)

    def ready(self, url, children, timeout, container_check=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('Owned child exited before readiness')
            if container_check is not None and not container_check()['State']['Running']:
                raise RuntimeError('Owned container exited before readiness')
            try:
                self.http(url)
                return
            except (OSError, ValueError):
                time.sleep(0.25)
        raise TimeoutError('Readiness timed out: ' + url)


@dataclass
class Runtime:
    base_url: str
    inference_url: str
    metadata: dict
    run_dir: Path
    ops: object = field(repr=False)
    children: list = field(default_factory=list, repr=False)
    handles: list = field(default_factory=list, repr=False)
    container_name: str | None = None
    container_id: str | None = None
    stopped: bool = False


def _inspect_owned(runtime):
    value, = json.loads(runtime.ops.run(['docker', 'inspect', runtime.container_id or runtime.container_name]))
    labels = value.get('Config', {}).get('Labels', {}) or {}
    if labels.get('io.semselect.owner') != runtime.metadata['owner'] or labels.get('io.semselect.experiment') != 'query-routing':
        raise RuntimeError('Container ownership mismatch; refusing to operate on it')
    if runtime.container_id is not None and value['Id'] != runtime.container_id:
        raise RuntimeError('Container identity changed')
    if value['Image'] != runtime.metadata['cpu_image_id']:
        raise RuntimeError('Owned container uses an unexpected image')
    runtime.container_id = value['Id']
    return value


def start(hardware, arm, lock, run_dir, profile, *, _ops=None):
    """Start one reviewed arm. hardware is metal/cpu-docker; always finally stop."""
    hardware = 'cpu-docker' if hardware == 'cpu' else hardware
    if hardware not in ('metal', 'cpu-docker') or arm not in ('qwen_json', 'kev'):
        raise ValueError('Expected hardware metal/cpu-docker and arm qwen_json/kev')
    request_timeout = profile.get('timeout_seconds', 240)
    readiness_timeout = profile.get('readiness_timeout_seconds', 240)
    if not 1 <= request_timeout <= 300 or not 1 <= readiness_timeout <= 600:
        raise ValueError('Timeout outside bounded profile')
    if profile.get('context', 4096) != 4096 or profile.get('batch', 1024) != 1024 or profile.get('ubatch', 512) != 512:
        raise ValueError('Frozen runtime requires context4096/batch1024/ubatch512')
    ops = _ops or Operations()
    ops.available(18087)
    if arm == 'kev':
        ops.available(18088)
    build = ops.artifacts(hardware, arm, lock)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    owner = uuid.uuid4().hex
    base = 'http://127.0.0.1:18087'
    metadata = {'hardware': hardware, 'arm': arm, 'owner': owner, 'model': lock,
                'build': build, 'profile': dict(profile), 'started_at': datetime.now(timezone.utc).isoformat(),
                'cache_policy': '--no-cache-prompt --cache-reuse 0 --cache-ram 0; verify observed cache in raw logs',
                'notes': 'Kev uses the same native Go guard in both strata; latency includes its passthrough. Qwen chat uses runtime directly. Metal has no OS CPU/memory quota. CPU runtime alone is limited to 4 CPUs/8 GiB; native guard is outside container quota.'}
    endpoint = 'http://127.0.0.1:18088/v1/systemone' if arm == 'kev' else base + '/v1/chat/completions'
    runtime = Runtime(base, endpoint, metadata, run_dir, ops)
    args = ['--alias', lock['alias'], '-ngl', '99' if hardware == 'metal' else '0', '-t', '4', '-tb', '4',
            '-c', '4096', '-b', '1024', '-ub', '512', '-np', '1', '--no-context-shift', '--metrics', '-lv', '4',
            '--no-cache-prompt', '--cache-reuse', '0', '--cache-ram', '0']
    try:
        if hardware == 'metal':
            command = [str(metal.SERVER), '-m', str(ROOT / 'models' / lock['filename']), '--host', '127.0.0.1', '--port', '18087'] + args
            log = (run_dir / 'runtime.log').open('x')
            runtime.handles.append(log)
            metadata['runtime_command'] = command
            env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
            runtime.children.append(ops.launch(command, env, log))
        else:
            image_id = profile.get('cpu_image_id', CPU_IMAGE_ID)
            if image_id != CPU_IMAGE_ID:
                raise ValueError('CPU image differs from inspected immutable pin')
            image, = json.loads(ops.run(['docker', 'image', 'inspect', image_id]))
            if image['Id'] != image_id or image['Os'] != 'linux' or image['Architecture'] != 'arm64' or image['Config'].get('Labels', {}).get('io.semselect.llama-revision') != metal.REVISION:
                raise ValueError('CPU image identity/architecture/runtime label mismatch')
            metadata.update(cpu_image_id=image_id, cpu_image=image)
            runtime.container_name = 'semselect-routing-' + owner[:12]
            command = ['docker', 'create', '--pull=never', '--name', runtime.container_name,
                       '--label', 'io.semselect.experiment=query-routing', '--label', 'io.semselect.owner=' + owner,
                       '--cpus', '4', '--memory', str(8 * 1024**3), '--memory-swap', str(8 * 1024**3),
                       '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=64m', '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges:true', '--pids-limit', '128', '--network', 'bridge',
                       '--publish', '127.0.0.1:18087:8080', '--mount',
                       f'type=bind,source={ROOT / "models" / lock["filename"]},target=/models/model.gguf,readonly',
                       image_id, '-m', '/models/model.gguf', '--host', '0.0.0.0', '--port', '8080'] + args
            metadata['runtime_command'] = command
            runtime.container_id = ops.run(command).strip()
            if not re.fullmatch('[0-9a-f]{64}', runtime.container_id):
                runtime.container_id = None
                raise RuntimeError('Docker create returned an invalid container ID')
            metadata['container_created'] = _inspect_owned(runtime)
            host = metadata['container_created']['HostConfig']
            expected_binding = [{'HostIp': '127.0.0.1', 'HostPort': '18087'}]
            if (host.get('NanoCpus') != 4_000_000_000 or host.get('Memory') != 8 * 1024**3
                    or host.get('MemorySwap') != 8 * 1024**3 or not host.get('ReadonlyRootfs')
                    or host.get('DeviceRequests') or host.get('NetworkMode') != 'bridge'
                    or host.get('PortBindings', {}).get('8080/tcp') != expected_binding):
                raise RuntimeError('Inspected CPU container resources/network differ from launch profile')
            ops.run(['docker', 'start', runtime.container_id])
        _save(run_dir / 'runtime.start.json', metadata)
        ops.ready(base + '/health', runtime.children, readiness_timeout,
                  (lambda: _inspect_owned(runtime)) if hardware == 'cpu-docker' else None)
        props = ops.http(base + '/props')
        metadata['runtime_props'] = props
        if props.get('default_generation_settings', {}).get('n_ctx') != 4096 or props.get('total_slots', 1) != 1:
            raise RuntimeError('Effective runtime context/slot count differs from profile')
        if hardware == 'metal':
            metadata['gpu_offload'] = metal.metal_offload((run_dir / 'runtime.log').read_text())
        if arm == 'kev':
            log = (run_dir / 'guard.log').open('x')
            runtime.handles.append(log)
            env = {k: v for k, v in os.environ.items() if not k.startswith('SEMSELECT_')}
            env.update(SEMSELECT_ADDR='127.0.0.1:18088', SEMSELECT_UPSTREAM=base,
                       SEMSELECT_MODEL=lock['alias'], SEMSELECT_TIMEOUT=f'{request_timeout}s')
            runtime.children.append(ops.launch([str(metal.GUARD)], env, log))
            metadata['guard_command'] = [str(metal.GUARD)]
            metadata['guard_timeout_seconds'] = request_timeout
            ops.ready('http://127.0.0.1:18088/ready', runtime.children, readiness_timeout)
        metadata['status'] = 'ready'
        return runtime
    except BaseException as exc:
        metadata.update(status='start_failed', error=f'{type(exc).__name__}: {exc}')
        try:
            stop(runtime)
        except BaseException as cleanup:
            exc.add_note('Owned runtime cleanup also failed: ' + str(cleanup))
        raise


def stop(runtime):
    """Stop/reap only owned processes/container, preserve logs and exit/OOM evidence."""
    if runtime.stopped:
        return runtime.metadata
    errors = []
    for child in reversed(runtime.children):
        try:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        except BaseException as exc:
            errors.append('child cleanup: ' + str(exc))
    runtime.metadata['child_exit_codes'] = [child.poll() for child in runtime.children]
    if runtime.container_name is not None:
        try:
            before = _inspect_owned(runtime)
            if before['State']['Running']:
                try:
                    runtime.ops.run(['docker', 'stop', '--time', '10', runtime.container_id], timeout=20)
                except subprocess.SubprocessError:
                    runtime.ops.run(['docker', 'kill', runtime.container_id], timeout=10)
            after = _inspect_owned(runtime)
            runtime.metadata['container_stopped'] = after
            if after['State']['Running']:
                raise RuntimeError('Owned container remains running')
            log_path = runtime.run_dir / 'runtime.log'
            if not log_path.exists():
                log_path.write_text(runtime.ops.run(['docker', 'logs', '--timestamps', runtime.container_id], timeout=30))
        except BaseException as exc:
            errors.append('container cleanup: ' + str(exc))
    for log in runtime.handles:
        log.close()
    for port in [18087] + ([18088] if runtime.metadata['arm'] == 'kev' else []):
        try:
            runtime.ops.closed(port)
        except BaseException as exc:
            errors.append(f'port {port} not confirmed closed: {exc}')
    runtime.metadata.update(stopped_at=datetime.now(timezone.utc).isoformat(), cleanup_errors=errors,
                            stopped=not errors)
    path = runtime.run_dir / 'runtime.json'
    if not path.exists():
        _save(path, runtime.metadata)
    runtime.stopped = not errors
    if errors:
        raise RuntimeError('; '.join(errors))
    return runtime.metadata


class PreflightError(ValueError):
    def __init__(self, message, record):
        super().__init__(message)
        self.record = record


def _tokens(ops, base, prompt, add_special):
    request = {'content': prompt, 'add_special': add_special, 'parse_special': True}
    response = ops.http(base + '/tokenize', request)
    tokens = response.get('tokens')
    if not isinstance(tokens, list) or not tokens or any(type(t) is not int for t in tokens):
        raise ValueError('Expected nonempty integer token array')
    return request, response


def _escape(text):
    if not isinstance(text, str):
        raise ValueError('Frozen Kev preflight supports text only')
    return re.sub(r'<\|([A-Za-z0-9_]+)\|>', lambda m: '<¦' + m[1] + '¦>', text)


def preflight(request, arm, base_url, context=4096, batch=1024, *, _ops=None):
    """Render/tokenize every head without completion; raise with record if it cannot fit."""
    if arm not in ('kev', 'qwen_json') or type(context) is not int or context <= 0 or type(batch) is not int or batch <= 0:
        raise ValueError('Invalid preflight arm/context')
    ops = _ops or Operations()
    props = ops.http(base_url + '/props')
    if props.get('default_generation_settings', {}).get('n_ctx') != context:
        raise ValueError('Effective context differs from requested preflight context')
    record = {'arm': arm, 'request': request, 'context': context, 'runtime_props': props,
              'inference_calls': 0, 'heads': {}, 'fits': True, 'batch': batch,
              'policy': 'exact-native-query-routing-multiquestion-v1'}
    if arm == 'qwen_json':
        reserve = request.get('max_tokens')
        if type(reserve) is not int or not 1 <= reserve <= context:
            raise ValueError('Missing/invalid bounded output reserve')
        applied = ops.http(base_url + '/apply-template', request)
        prompt = applied.get('prompt')
        if not isinstance(prompt, str):
            raise ValueError('Runtime apply-template did not return text')
        token_request, tokenization = _tokens(ops, base_url, prompt, True)
        n = len(tokenization['tokens'])
        record['heads']['chat'] = {'apply_template': applied, 'rendered_prompt': prompt,
                                  'tokenize_request': token_request, 'tokenization': tokenization,
                                  'prompt_tokens': n, 'reserved_output_tokens': reserve,
                                  'fits': n + reserve <= context}
    else:
        record['template_verification'] = ops.template()
        questions = request.get('questions')
        if not isinstance(questions, dict) or set(questions) != {'operation', 'node', 'field'}:
            raise ValueError('Frozen routing task requires operation, node, field questions')
        _, marker_response = _tokens(ops, base_url, '<|box_end|>', False)
        if len(marker_response['tokens']) != 1:
            raise ValueError('Kev marker must be one token')
        marker = marker_response['tokens'][0]
        record['marker_tokenization'] = marker_response
        for ident, question in questions.items():
            criteria = question.get('criteria')
            if question.get('type') != 'choice' or not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
                raise ValueError('Expected bounded native Choice criteria')
            if any(not isinstance(key, str) or not isinstance(description, str) for key, description in criteria.items()):
                raise ValueError('Frozen choice keys and descriptions must be text')
            options = ''.join('<|box_start|>' + _escape(key) + (': ' + _escape(description) if description else '') + '<|box_end|>'
                              for key, description in criteria.items())
            prompt = '<|fim_prefix|>' + _escape(request['state']) + '<|fim_middle|>' + _escape(question['instructions']) + options + '<|fim_suffix|>'
            token_request, tokenization = _tokens(ops, base_url, prompt, False)
            tokens = tokenization['tokens']
            markers = [i for i, token in enumerate(tokens) if token == marker]
            tail = len(tokens) - markers[0] if markers else len(tokens)
            # Actual causal runtime requires n_prompt < n_ctx. The head reads
            # existing hidden states, generates no completion, and reserves one
            # slot here to reflect that strict bound rather than invent output.
            record['heads'][ident] = {'rendered_prompt': prompt, 'tokenize_request': token_request,
                                     'tokenization': tokenization, 'prompt_tokens': len(tokens),
                                     'reserved_output_tokens': 1, 'reserve_reason': 'native strict prompt<context; head generates no completion tokens',
                                     'marker_positions': markers, 'decision_tail_tokens': tail,
                                     'decision_batch_limit': batch, 'expected_markers': len(criteria),
                                     'fits': len(tokens) < context and tail <= batch and len(markers) == len(criteria)}
    record['fits'] = all(head['fits'] for head in record['heads'].values())
    if not record['fits']:
        raise PreflightError('Exact prompt/context, marker layout, or decision batch limit failed; no truncation/inference', record)
    return record
