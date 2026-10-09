"""Owned llama-server lifecycle for one throughput cell (native Metal, pinned build).

The launch is scripts/metal.py's command with these changes: -np N, -c 4096*N so
each slot keeps 4096 tokens, -kvu only in the unified-KV cell, and -b 4096 only in
the Amendment 2 batch cells (-ub stays 512). The port is
compare_scoring's, so its prepare_score helper tokenizes against this runtime.
Clients talk to llama-server directly; the Go guard admits one inference at a
time and returns 429 otherwise, so it would hide the effect being measured.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import resource
import subprocess
import time
import urllib.request

import fixtures
import compare_scoring
import metal
from model import verify

PORT = compare_scoring.PORT
BASE = compare_scoring.BASE
READY_SECONDS = 180
COUNTERS = ('prompt_tokens_total', 'prompt_tokens_cached_total', 'prompt_seconds_total',
            'tokens_predicted_total', 'tokens_predicted_seconds_total', 'n_decode_total')
STARTUP = {
    'n_seq_max': r'llama_context: n_seq_max\s+= (\d+)',
    'n_ctx': r'llama_context: n_ctx\s+= (\d+)',
    'n_ctx_seq': r'llama_context: n_ctx_seq\s+= (\d+)',
    'n_batch': r'llama_context: n_batch\s+= (\d+)',
    'n_ubatch': r'llama_context: n_ubatch\s+= (\d+)',
    'context_kv_unified': r'llama_context: kv_unified\s+= (true|false)',
    'n_slots': r'initializing, n_slots = (\d+), n_ctx_slot = \d+',
    'n_ctx_slot': r'initializing, n_slots = \d+, n_ctx_slot = (\d+)',
    'slots_kv_unified': r"initializing, n_slots = \d+, n_ctx_slot = \d+, kv_unified = '(true|false)'",
    'kv_cache': r'llama_kv_cache: (size = .*)',
    'recurrent_state': r'llama_memory_recurrent: (size = .*)',
    'kv_buffer_mib': r'MTL0 KV buffer size =\s+([\d.]+) MiB',
    'recurrent_buffer_mib': r'MTL0 RS buffer size =\s+([\d.]+) MiB',
    'compute_buffer_mib': r'MTL0 compute buffer size =\s+([\d.]+) MiB',
}


def command(lock, slots, kv_unified, port=PORT, n_batch=fixtures.BATCH):
    launch = [str(metal.SERVER), '-m', str(fixtures.ROOT / 'models' / lock['filename']), '--alias', lock['alias'],
              '--host', '127.0.0.1', '--port', str(port), '-ngl', '99', '-t', '4', '-tb', '4',
              '-c', str(fixtures.CONTEXT_PER_SLOT * slots), '-b', str(n_batch), '-ub', str(fixtures.UBATCH),
              '-np', str(slots),
              '--no-context-shift', '--metrics', '-lv', '4']
    return launch + ['-kvu'] if kv_unified else launch


def verify_runtime(model):
    """Model bytes and native build provenance, checked before any launch."""
    lock = fixtures.load_lock(fixtures.LOCKS[model])
    fixtures.require(lock['runtime_revision'] == metal.REVISION, 'model and Metal runtime revisions disagree')
    fixtures.require(verify(fixtures.ROOT / 'models' / lock['filename'], lock),
                     f'pinned model missing or corrupt; run python3 scripts/model.py --lock {fixtures.LOCKS[model].name}')
    build = json.loads((metal.CACHE / 'build.json').read_text())
    fixtures.require(build['runtime_revision'] == metal.REVISION and metal.runtime_hashes() == build['runtime_files_sha256'],
                     'native runtime provenance mismatch; run task metal:build')
    return lock, build


def parse_startup(log):
    found = {}
    for key, pattern in STARTUP.items():
        match = re.search(pattern, log)
        if match:
            value = match[1]
            found[key] = (value == 'true' if value in ('true', 'false') else
                          float(value) if key.endswith('_mib') else int(value) if value.isdigit() else value)
    return found


def check_startup(found, cell):
    """The runtime must report the slot/context/batch layout the cell claims to measure.

    llama.cpp may lower n_batch silently (to n_ctx, or to n_ubatch for a decision model's
    embedding mode), so the logged values are checked, not the flags."""
    expected = {'n_seq_max': cell.slots, 'n_ctx': cell.n_ctx_total, 'n_slots': cell.slots,
                'n_ctx_slot': cell.n_ctx_per_slot, 'context_kv_unified': cell.kv_unified,
                'slots_kv_unified': cell.kv_unified, 'n_batch': cell.n_batch, 'n_ubatch': cell.n_ubatch}
    wrong = {key: (found.get(key), value) for key, value in expected.items() if found.get(key) != value}
    if wrong:
        raise RuntimeError(f'runtime slot/context/batch layout differs from the cell (observed, expected): {wrong}')


def parse_metrics(text):
    values = {}
    for line in text.splitlines():
        if line.startswith('llamacpp:'):
            name, _, value = line.partition(' ')
            try:
                values[name.removeprefix('llamacpp:')] = float(value)
            except ValueError:
                pass
    return values


def metrics_delta(before, after):
    """Counter differences over a window. n_busy_slots_per_decode is a lifetime
    average (server-task.cpp:1597-1599), so the window value is rebuilt from totals."""
    if not before or not after:
        return None
    delta = {key: after[key] - before[key] for key in COUNTERS if key in before and key in after}
    decodes = delta.get('n_decode_total')
    if decodes and all('n_busy_slots_per_decode' in m for m in (before, after)):
        busy = (after['n_busy_slots_per_decode'] * after['n_decode_total']
                - before['n_busy_slots_per_decode'] * before['n_decode_total'])
        delta['busy_slots_per_decode'] = busy / decodes
    return delta


class Runtime:
    """One owned llama-server. The caller must call stop(), which never raises."""

    def __init__(self, cell, lock, cell_dir):
        self.cell, self.lock, self.cell_dir = cell, lock, Path(cell_dir)
        self.base = BASE
        self.child = None
        self.command = command(lock, cell.slots, cell.kv_unified, n_batch=cell.n_batch)
        self.info = {'command': self.command, 'endpoint_base': BASE,
                     'guard': 'bypassed: clients call llama-server directly, as the Qwen baselines always did'}
        self.started = time.monotonic()
        self.usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)

    def start(self, deadline):
        fixtures.require(not compare_scoring.port_is_listening(PORT), f'port {PORT} already has a listener')
        log_path = self.cell_dir / 'runtime.log'
        env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
        with log_path.open('x') as log:
            self.child = subprocess.Popen(self.command, env=env, stdout=log, stderr=subprocess.STDOUT)
        metal.ready(BASE + '/health', [self.child], timeout=max(1, min(READY_SECONDS, deadline - time.monotonic())))
        # The log is written asynchronously; allow a bounded moment for the slot line.
        settle = time.monotonic() + 5
        while 'initializing, n_slots' not in log_path.read_text() and time.monotonic() < settle:
            time.sleep(0.1)
        log = log_path.read_text()
        self.info['gpu_offload'] = metal.metal_offload(log)
        self.info['startup'] = parse_startup(log)
        self.info['startup_seconds'] = time.monotonic() - self.started
        check_startup(self.info['startup'], self.cell)
        return self

    def metrics(self):
        try:
            with urllib.request.urlopen(BASE + '/metrics', timeout=5) as response:
                return parse_metrics(response.read(1024 * 1024).decode('utf-8', errors='replace'))
        except OSError as exc:
            return {'error': f'{type(exc).__name__}: {exc}'}

    def stop(self):
        errors = []
        if self.child is not None:
            try:
                metal.stop([self.child])
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f'stop: {exc}')
            self.info['exit_code'] = self.child.poll()
            if self.child.poll() is None:
                errors.append('owned runtime is still running')
        try:
            if compare_scoring.port_is_listening(PORT):
                errors.append(f'port {PORT} still accepts connections')
        except OSError as exc:
            errors.append(f'port closure unproved: {exc}')
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.info.update(
            cleanup_errors=errors, wall_seconds=time.monotonic() - self.started,
            child_cpu_seconds=(after.ru_utime + after.ru_stime) - (self.usage_before.ru_utime + self.usage_before.ru_stime),
            # RUSAGE_CHILDREN keeps the largest peak of any reaped child in this process,
            # so this is cumulative across earlier cells, as in scripts/metal.py.
            largest_child_peak_rss_bytes_so_far=after.ru_maxrss)
        return self.info

