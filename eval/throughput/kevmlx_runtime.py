"""Owned kev.serve (MLX, bf16) lifecycle for one Kev MLX throughput cell.

Launch: `.kev/venv/bin/python -I -m kev.serve --run <adapter snapshot> --host 127.0.0.1
--port 18096`, from .kev/. `--run` takes a local directory holding head.pt (serve.py:322;
checkpoint.py:38-42), so no Hub id is resolved and the model card's release date comes
from file times rather than the Hub (checkpoint.py:202-217). The base is resolved through
the Hugging Face cache in .kev/hf at the revision head.pt names (checkpoint.py:238,258),
with HF_HUB_OFFLINE=1, so nothing is fetched. Every KEV_* variable is set explicitly and
inherited ones are dropped. kev.serve has no /health route; readiness is GET /v1/models,
which answers only after the model is loaded (uvicorn binds after loading, serve.py:332-339).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import time
import urllib.request

import fixtures
import compare_scoring
import kevmlx_setup
import metal

PORT = 18096
RESERVED_PORTS = frozenset({18086, 18087, 18088, 8084, 8085, 30101})
READY_SECONDS = 600  # merging the adapter on the CPU stream (mlx_model.py:97-125) plus 9.3 GB of weights
MODEL_NAMES = ['kev-latest', 'jev-latest']  # serve.py:35
MAX_STATE_TOKENS = 65536  # kev.model.SERVE_MAX_STATE (model.py:18)
# Fixed for every launch (serve.py:27-33, checkpoint.py:133-146). KEV_BACKEND=mlx refuses the torch
# fallback that "auto" would take silently (checkpoint.py:226-231). KEV_DTYPE=bf16 equals kev.serve's own
# default off CPU (serve.py:327); the MLX path runs the backbone as stored, bf16 (checkpoint.py:110-112).
KEV_ENV = {'KEV_BACKEND': 'mlx', 'KEV_DTYPE': 'bf16', 'KEV_DATE_FACTS': '0', 'KEV_TRUNCATE_STATES': '0'}
COUNTERS = ('batches', 'batched_requests', 'prefix_cache_hits', 'prefix_cache_misses', 'prefix_cache_oom_retries')
SERVING = re.compile(r'serving (?P<run>\S+) \((?P<path>[^)]*)\) on (?P<device>\S+) via (?P<backend>\S+) '
                     r'\((?P<dtype>[^)]+)\) (?P<host>[^\s:]+):(?P<port>\d+); states over (?P<max_state>[\d,]+) tokens '
                     r'(?P<policy>.+)')  # serve.py:336-337
UVICORN = re.compile(r'Uvicorn running on (\S+)')


def base_url(port=PORT):
    return f'http://127.0.0.1:{port}'


def command(layout, port=PORT):
    fixtures.require(port not in RESERVED_PORTS, f'port {port} belongs to another runtime')
    adapter = str(layout.snapshot(kevmlx_setup.ADAPTER))
    return [str(layout.python), '-I', '-m', 'kev.serve', '--run', adapter, '--fallback', adapter,
            '--host', '127.0.0.1', '--port', str(port)]


def environment(layout, prefix_cache, inherited=None):
    """Inherited environment minus every Kev, Hub, MLX and Python-path setting, plus the pinned ones."""
    inherited = os.environ if inherited is None else inherited
    drop = ('KEV_', 'HF_', 'HUGGING', 'TRANSFORMERS_', 'MLX_', 'PYTHON', 'VIRTUAL_ENV', 'UV_', 'CONDA_')
    env = {k: v for k, v in inherited.items() if not k.startswith(drop)}
    env.update(KEV_ENV, KEV_PREFIX_CACHE=str(prefix_cache), HF_HOME=str(layout.hf_home), HF_HUB_CACHE=str(layout.hub),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTHONUNBUFFERED='1')
    return env


def parse_startup(log):
    found = {}
    serving = SERVING.search(log)
    if serving:
        found.update(serving.groupdict())
        found['port'] = int(found['port'])
        found['max_state'] = int(found['max_state'].replace(',', ''))
    uvicorn = UVICORN.search(log)
    if uvicorn:
        found['uvicorn'] = uvicorn[1]
    return found


def card_of(body):
    models = body.get('models') if isinstance(body, dict) else None
    fixtures.require(isinstance(models, list) and models and all(isinstance(m, dict) for m in models),
                     '/v1/models returned no model cards')
    return models[0]


def expected_card(layout, prefix_cache, head):
    """What /v1/models must report (serve.py:298-312) for the cell to be measured."""
    return {'names': MODEL_NAMES, 'backend': 'mlx', 'dtype': 'bfloat16', 'device': 'mps',
            'run': str(layout.snapshot(kevmlx_setup.ADAPTER)), 'base': kevmlx_setup.BASE.repo, 'lora': head['lora'],
            'temperature': head['temperature'], 'max_state_tokens': MAX_STATE_TOKENS, 'truncate_states': False,
            'prefix_cache_size': prefix_cache, 'prefix_cache_min_state_tokens': 0}  # MLX prefix_min_tokens, mlx_model.py:131


def check_card(body, expected):
    card = card_of(body)
    cache = card.get('prefix_cache') or {}
    observed = {'names': [m.get('name') for m in body['models']], 'prefix_cache_size': cache.get('size'),
                'prefix_cache_min_state_tokens': cache.get('min_state_tokens'),
                **{key: card.get(key) for key in expected if key in card}}
    wrong = {}
    for key, value in expected.items():
        seen = observed.get(key)
        same = (isinstance(value, float) and isinstance(seen, (int, float)) and math.isclose(seen, value, rel_tol=1e-9)) \
            or seen == value
        if not same:
            wrong[key] = (seen, value)
    if wrong:
        raise RuntimeError(f'kev.serve reports a different model or profile than the cell (observed, expected): {wrong}')
    return card


def check_startup(startup, card, port):
    fixtures.require(startup.get('backend') == card['backend'] and startup.get('dtype') == card['dtype']
                     and startup.get('port') == port, f'startup line disagrees with /v1/models: {startup}')


def counters(body):
    """Server counters kev.serve reports on /v1/models (serve.py:309-311)."""
    card = card_of(body)
    batches, cache = card.get('batches') or {}, card.get('prefix_cache') or {}
    return {'batches': batches.get('count'), 'batched_requests': batches.get('requests'), 'queued': batches.get('queued'),
            'prefix_cache_hits': cache.get('hits'), 'prefix_cache_misses': cache.get('misses'),
            'prefix_cache_states': cache.get('cached_states'), 'prefix_cache_oom_retries': cache.get('oom_retries')}


def counters_delta(before, after):
    """Counter differences over a window, and requests per server batch (the model thread drains up to 64 queued
    requests per batch, serve.py:34,150-160)."""
    if not before or not after or 'error' in before or 'error' in after:
        return None
    delta = {key: after[key] - before[key] for key in COUNTERS
             if isinstance(before.get(key), int) and isinstance(after.get(key), int)}
    if delta.get('batches'):
        delta['requests_per_batch'] = delta.get('batched_requests', 0) / delta['batches']
    return delta


def listener_pids(port):
    result = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'], capture_output=True, text=True, timeout=10)
    return sorted({int(pid) for pid in result.stdout.split()})


def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read(1024 * 1024))


def verify_runtime(layout=None):
    """Every recorded .kev byte, offline (kevmlx_setup.verify); once per invocation, before any launch."""
    return kevmlx_setup.verify(layout or kevmlx_setup.Layout())


class Runtime:
    """One owned kev.serve. The caller must call stop(), which never raises."""

    def __init__(self, cell, cell_dir, setup, layout=None, port=PORT):
        self.cell, self.cell_dir, self.setup = cell, Path(cell_dir), setup
        self.layout = layout or kevmlx_setup.Layout()
        self.port, self.base = port, base_url(port)
        self.child = None
        self.command = command(self.layout, port)
        self.env = environment(self.layout, cell.prefix_cache)
        self.info = {'command': self.command, 'cwd': str(self.layout.root), 'endpoint_base': self.base,
                     'env': {k: self.env[k] for k in sorted(self.env) if k.startswith(('KEV_', 'HF_', 'TRANSFORMERS_'))},
                     'kev_dtype_env': self.env['KEV_DTYPE'], 'prefix_cache_states': cell.prefix_cache,
                     'guard': 'none: clients call kev.serve directly; semselect has no Kev MLX service path'}
        self.started = time.monotonic()
        self.usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)

    def start(self, deadline):
        fixtures.require(not compare_scoring.port_is_listening(self.port), f'port {self.port} already has a listener')
        log_path = self.cell_dir / 'runtime.log'
        with log_path.open('x') as log:
            self.child = subprocess.Popen(self.command, cwd=self.layout.root, env=self.env, stdout=log,
                                          stderr=subprocess.STDOUT)
        metal.ready(self.base + '/v1/models', [self.child],
                    timeout=max(1, min(READY_SECONDS, deadline - time.monotonic())))
        self.info['startup_seconds'] = time.monotonic() - self.started
        self.info['listener_pids'] = listener_pids(self.port)
        fixtures.require(self.info['listener_pids'] == [self.child.pid],
                         f'port {self.port} is not served by the owned kev.serve (pid {self.child.pid}): '
                         f'{self.info["listener_pids"]}')
        body = get_json(self.base + '/v1/models')
        (self.cell_dir / 'models.json').write_text(json.dumps(body, indent=2, ensure_ascii=False) + '\n')
        self.info['models'] = body
        card = check_card(body, expected_card(self.layout, self.cell.prefix_cache, self.setup['head_meta']))
        self.info.update(backend=card['backend'], dtype_loaded=card['dtype'], device=card['device'])
        self.info['startup'] = parse_startup(log_path.read_text(errors='replace'))
        check_startup(self.info['startup'], card, self.port)
        return self

    def metrics(self):
        try:
            return counters(get_json(self.base + '/v1/models'))
        except (OSError, ValueError) as exc:
            return {'error': f'{type(exc).__name__}: {exc}'}

    def stop(self):
        errors = []
        if self.child is not None:
            try:
                metal.stop([self.child])  # SIGTERM, 10 s, then SIGKILL
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f'stop: {exc}')
            self.info['exit_code'] = self.child.poll()
            if self.child.poll() is None:
                errors.append('owned runtime is still running')
        try:
            if compare_scoring.port_is_listening(self.port):
                errors.append(f'port {self.port} still accepts connections')
        except OSError as exc:
            errors.append(f'port closure unproved: {exc}')
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.info.update(
            cleanup_errors=errors, wall_seconds=time.monotonic() - self.started,
            child_cpu_seconds=(after.ru_utime + after.ru_stime) - (self.usage_before.ru_utime + self.usage_before.ru_stime),
            # RUSAGE_CHILDREN keeps the largest peak of any reaped child in this process, so this is
            # cumulative across earlier cells, as in scripts/metal.py and llamacpp.py. Metal buffers in
            # unified memory are not all counted in RSS.
            largest_child_peak_rss_bytes_so_far=after.ru_maxrss)
        return self.info
