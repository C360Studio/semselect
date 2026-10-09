#!/usr/bin/env python3
"""Throughput screen, Kev MLX arm: Kev's own server (kev.serve, MLX bf16) under the frozen workloads.

--validate verifies the frozen inputs, rebuilds every Kev request offline and prints the
planned cells; no model runs. A run re-checks .kev/ offline, starts one owned kev.serve per
cell, sends one warmup pass and the measured passes directly to it, and stops on the
45-minute budget or three consecutive runtime errors in the measured passes. Cells, cache
reset and readings: eval/throughput/kevmlx.md. Small pilot; not a benchmark.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import time

import fixtures
import kevmlx_requests
import kevmlx_runtime
import kevmlx_setup
import report
import run as shared
import runner
import evaluate
import metal

BUDGET_SECONDS = shared.BUDGET_SECONDS
CACHED_STATE_RATIO_AT_MOST = 0.5
KEV_SOURCES = ('eval/throughput/kevmlx_setup.py', 'eval/throughput/kevmlx_runtime.py',
               'eval/throughput/kevmlx_requests.py', 'eval/throughput/run_kevmlx.py', 'eval/throughput/kevmlx.md')
SOURCES = tuple(dict.fromkeys(shared.SOURCES + KEV_SOURCES))
SERVER = ('kev.serve: one model thread drains up to 64 queued requests into a batch (serve.py:34,150-170) and answers '
          'them all when the batch ends; on MLX a batch runs one request at a time (mlx_model.py:226-229); a request\'s '
          'questions share one state pass (mlx_model.py:176-205)')
PROTOCOL = {
    # The shared harness version: run_cell, its stop rules and amendments apply to this arm unchanged.
    'version': shared.PROTOCOL['version'],
    'amendments': shared.PROTOCOL.get('amendments', []),
    'arm_design': 'eval/throughput/kevmlx.md, 2026-10-09, fixed before inference',
    'question': 'Does Kev\'s own MLX server deliver materially more decisions per second with concurrent clients, '
                'and does its state cache make a repeated state materially cheaper, at unchanged labels?',
    'yardstick': shared.PROTOCOL['yardstick'],
    'guard': 'None. Clients call kev.serve directly; the frozen Kev bodies are sent byte for byte.',
    'runtime': f'kev.serve from .kev/venv at Kev {kevmlx_setup.KEV_COMMIT}, adapter {kevmlx_setup.ADAPTER.repo}@'
               f'{kevmlx_setup.ADAPTER.revision}, base {kevmlx_setup.BASE.repo}@{kevmlx_setup.BASE.revision}; MLX bf16 '
               f'backbone, Kev\'s fp32 pointer head; HF offline; fresh server per cell on port {kevmlx_runtime.PORT}. '
               + SERVER,
    'cache': 'kev.serve has no cache-clearing route. New-state cells run with KEV_PREFIX_CACHE=0, so no state is cached '
             '(serve.py:27,55) and warmup cannot serve a measured request. The cached-state cell runs with '
             'KEV_PREFIX_CACHE=4 (the default): per case, request A (operation) on a new state, then request B '
             '(node, field) on the same state; /v1/models hit and miss deltas must equal B and A counts.',
    'failures': shared.PROTOCOL['failures'],
    'budget': shared.PROTOCOL['budget'],
    'agreement': 'Primary: within-runtime against this run\'s 1x1 new-state cell of the same workload, measured pass 1. '
                 'Diagnostic only: the serial llama.cpp Kev labels (GGUF Q4_K_M, a different precision and implementation).',
    'readings': 'eval/throughput/kevmlx.md, fixed before inference; screening readings, not adoption gates.',
}


@dataclass(frozen=True)
class KevCell:
    """One Kev MLX cell. kev.serve has one model thread, so slots is 1; clients vary."""
    workload: str
    concurrency: int
    split: bool = False
    model = 'kev'
    slots = 1
    kv_unified = False
    n_ctx_total = None     # llama.cpp context and batch settings do not apply to kev.serve
    n_ctx_per_slot = None
    n_batch = None
    n_ubatch = None

    @property
    def id(self):
        return f'{self.workload}-kevmlx' + ('-cached' if self.split else '') + f'-1x{self.concurrency}'

    @property
    def arm(self):
        # Distinct from the llama.cpp arm names so report.py never pairs these cells with llama.cpp cells.
        return 'kevmlx_cached' if self.split else 'kevmlx'

    @property
    def measured_passes(self):
        return fixtures.MEASURED_PASSES[self.workload]

    @property
    def prefix_cache(self):
        return 4 if self.split else 0


CELLS = (KevCell('w1', 1), KevCell('w1', 4), KevCell('w1', 8),
         KevCell('w2', 1), KevCell('w2', 4), KevCell('w2', 8), KevCell('w2', 1, split=True))


def select_cells(workloads, ids):
    planned = [c for c in CELLS if c.workload in workloads]
    if ids:
        unknown = sorted(set(ids) - {c.id for c in planned})
        if unknown:
            raise ValueError(f'unknown cells for this workload: {unknown}; choose from {[c.id for c in planned]}')
        planned = [c for c in planned if c.id in ids]
    return planned


def reference_for(cell, inputs):
    """The cross-runtime diagnostic reference the shared runner scores against."""
    reference = fixtures.reference_for(fixtures.Cell(cell.workload, 'kev', 1, 1), inputs)
    return dict(reference, runtime='llama.cpp serial, GGUF Q4_K_M',
                caveat='different precision and implementation; diagnostic only')


# --- After a cell: kev.serve counters, within-runtime agreement, the cached-state split --------------

def journal_rows(cell_dir, phase='measured'):
    rows = [json.loads(line) for line in (Path(cell_dir) / 'journal.jsonl').read_text().splitlines()]
    return sorted((r for r in rows if r['phase'] == phase), key=lambda r: r['sequence'])


def response_of(row):
    if row.get('http_status') != 200 or not row.get('response_bytes_base64') or row.get('response_truncated'):
        return None
    try:
        value = json.loads(base64.b64decode(row['response_bytes_base64']))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def server_latency(rows):
    values = [r['latency_ms'] for r in map(response_of, rows) if r and evaluate.finite_number(r.get('latency_ms'))]
    return {'p50': statistics.median(values) if values else None, 'p95': evaluate.percentile(values, .95),
            'samples': len(values), 'meaning': 'model time of the server batch the request ran in (serve.py:186-190)'}


def label_map(rows, passes=(1,)):
    return {(r['case_id'], r['order'], q): label for r in rows if r['pass_number'] in passes
            for q, label in r['labels'].items()}


def agreement(rows, reference):
    """Questions with a reference label; errors, invalid answers and not_run count as non-matches."""
    total = matched = 0
    for row in rows:
        for question, label in row['labels'].items():
            expected = reference.get((row['case_id'], row['order'], question))
            if expected is not None:
                total += 1
                matched += label == expected
    return {'matched': matched, 'total': total}


def split_summary(rows):
    parts = {}
    for name, questions, state in (('A', kevmlx_requests.SPLIT[0], 'new'), ('B', kevmlx_requests.SPLIT[1], 'cached')):
        part = runner.summarize([r for r in rows if tuple(r['labels']) == questions], 0.0, len(questions))
        parts[name] = {'questions': list(questions), 'state': state,
                       **{k: part[k] for k in ('planned', 'valid', 'valid_questions', 'invalid', 'not_run', 'errors', 'request_ms')}}
    a, b = parts['A']['request_ms']['p50'], parts['B']['request_ms']['p50']
    parts.update(new_state_one_question_ms_p50=a, cached_cost_per_question_ms_p50=None if b is None else b / 2,
                 b_over_a_p50=b / a if a and b is not None else None)
    return parts


def cache_check(cell, delta, rows):
    attempted = [r for r in rows if r['status'] != 'not_run']
    if cell.split:
        expected = {'prefix_cache_hits': sum(tuple(r['labels']) == kevmlx_requests.SPLIT[1] for r in attempted),
                    'prefix_cache_misses': sum(tuple(r['labels']) == kevmlx_requests.SPLIT[0] for r in attempted)}
    else:
        expected = {'prefix_cache_hits': 0, 'prefix_cache_misses': 0}  # size 0: no key, nothing counted (serve.py:55,83)
    if delta is None:
        return {'ok': None, 'expected': expected, 'observed': None, 'reason': 'no measured /v1/models window'}
    observed = {key: delta.get(key) for key in expected}
    return {'ok': observed == expected, 'expected': expected, 'observed': observed}


def finish_cell(cell, cell_dir, result, references, provenance=None):
    """Replace llama.cpp-only fields with kev.serve's, add the agreement views, and re-save the cell summary."""
    rows = journal_rows(cell_dir)
    snapshots = result['metrics']['snapshots']
    delta = kevmlx_runtime.counters_delta(snapshots.get('after_warmup'), snapshots.get('end'))
    result['metrics'].update(measured_delta=delta,
                             warmup_delta=kevmlx_runtime.counters_delta(snapshots.get('start'), snapshots.get('after_warmup')),
                             source='kev.serve /v1/models counters (serve.py:309-311); it has no llama.cpp /metrics')
    result['profile'].update(prefix_cache_states=cell.prefix_cache, server=SERVER,
                             request_shape='A: operation on a new state, then B: node and field on the cached state'
                             if cell.split else 'one request per case with all of its questions')
    result['kev_server'] = {'latency_ms': server_latency(rows),
                            'measured_requests_attempted': sum(r['status'] != 'not_run' for r in rows),
                            'measured_batched_requests': (delta or {}).get('batched_requests'),
                            'requests_per_batch': (delta or {}).get('requests_per_batch')}
    if cell.split:
        result['planned_questions'] = sum(len(r['labels']) for r in rows)
        result['split'] = split_summary(rows)
    result['cache_check'] = cache_check(cell, delta, rows)
    cross = dict(result['label_agreement'], kind='cross-runtime diagnostic',
                 caveat='serial llama.cpp labels from GGUF Q4_K_M; MLX runs bf16 through Kev\'s own encoder and head')
    if not cell.split and cell.concurrency == 1:
        references[cell.workload] = (cell.id, label_map(rows))
    reference = references.get(cell.workload)
    if reference:
        within = dict(agreement(rows, reference[1]), kind='within-runtime', reference_cell=reference[0],
                      reference_view='measured pass 1', reference_path=f'{reference[0]}/journal.jsonl')
    else:
        within = {'matched': None, 'total': None, 'kind': 'within-runtime', 'reference_cell': None,
                  'reason': 'not evaluable: the 1x1 new-state cell of this workload did not run in this invocation'}
    result['label_agreement'], result['label_agreement_cross_runtime'] = within, cross
    if result['cache_check']['ok'] is False and result['status'] == 'complete':
        result.update(status='stopped', stop_reason='prefix-cache accounting differs from the plan')
    shared.save(Path(cell_dir) / 'summary.json', dict(result, provenance=provenance) if provenance else result)
    return result


# --- Readings (kevmlx.md) ----------------------------------------------------------------------------

ACCOUNTING = {True: 'as planned', False: 'differs', None: 'not measured'}


def find(cells, cell_id):
    return next((cell for cell in cells if cell['cell'] == cell_id), None)


def evaluable(*cells):
    return all(report.complete(c) and c['label_agreement'].get('matched') is not None for c in cells)


def batching(cells, workload):
    """1x8 vs 1x1: >=2x questions/s, p95 <=3x, within-runtime agreement no more than 2 below (report.py thresholds)."""
    one, top = find(cells, f'{workload}-kevmlx-1x1'), find(cells, f'{workload}-kevmlx-1x8')
    reading = {'workload': workload, 'arm': 'kevmlx', 'compared': '1x8 / 1x1', 'result': None}
    if not evaluable(one, top) or not one['decisions_per_s'] or not one['request_ms']['p95']:
        reading['reason'] = 'not evaluable: the 1x1 and 1x8 cells must both complete'
        return reading
    speedup = (top['decisions_per_s'] or 0) / one['decisions_per_s']
    p95_ratio = top['request_ms']['p95'] / one['request_ms']['p95']
    drop = one['label_agreement']['matched'] - top['label_agreement']['matched']
    reading.update(speedup=speedup, p95_ratio=p95_ratio, agreement_drop=drop,
                   result=speedup >= report.SPEEDUP_AT_LEAST and p95_ratio <= report.P95_RATIO_AT_MOST
                   and drop <= report.AGREEMENT_DROP_AT_MOST)
    return reading


def cached_state(cells):
    """B (two questions, cached state) p50 at most half of A (one question, new state) p50, labels unchanged."""
    cached, one = find(cells, 'w2-kevmlx-cached-1x1'), find(cells, 'w2-kevmlx-1x1')
    reading = {'result': None}
    if not evaluable(cached, one) or not cached['split']['A']['request_ms']['p50'] \
            or cached['split']['B']['request_ms']['p50'] is None:
        reading['reason'] = ('not evaluable: w2-kevmlx-1x1 and w2-kevmlx-cached-1x1 must both complete, '
                             'the latter with the planned cache accounting')
        return reading
    a, b = cached['split']['A']['request_ms']['p50'], cached['split']['B']['request_ms']['p50']
    drop = one['label_agreement']['matched'] - cached['label_agreement']['matched']
    reading.update(a_p50_ms=a, b_p50_ms=b, b_over_a=b / a, cached_cost_per_question_ms=b / 2, agreement_drop=drop,
                   result=b <= CACHED_STATE_RATIO_AT_MOST * a and drop <= report.AGREEMENT_DROP_AT_MOST)
    return reading


def readings(cells):
    workloads = sorted({c['workload'] for c in cells if c['arm'] == 'kevmlx'})
    return {'batches_usefully': [batching(cells, w) for w in workloads], 'cached_state_cheaper': cached_state(cells)}


def render(summary):
    cells = summary.get('cells', [])
    result = readings(cells)
    lines = ['# Throughput screen: Kev MLX (kev.serve, bf16)', '',
             'Small pilot on one laptop; screening readings, not adoption gates or a benchmark. Questions/s counts valid '
             'answers over measured passes only; latency includes queueing at the server. Label agreement is '
             'within-runtime, against this run\'s 1x1 new-state cell (measured pass 1). The prompt-token and busy-slot '
             'columns are llama.cpp `/metrics` fields that kev.serve does not report; its own counters follow. '
             'Protocol: `eval/throughput/kevmlx.md`.', '',
             f'- `{summary.get("runtime")}` / `{summary.get("model")}`: status {summary.get("status")}, stop reason '
             f'{summary.get("stop_reason")}, run {summary.get("run_id")}, protocol version '
             f'{report.number(report.protocol_version(summary))}', '', report.table(cells), '',
             '## kev.serve counters over the measured passes', '',
             '| Cell | KEV_PREFIX_CACHE | Requests attempted / batched | Requests per server batch | Prefix hits / misses '
             '| Cache accounting | Server batch latency_ms p50 |', '| --- | ---: | ---: | ---: | ---: | --- | ---: |']
    for cell in cells:
        delta, server, check = cell['metrics'].get('measured_delta') or {}, cell.get('kev_server') or {}, cell['cache_check']
        lines.append(f'| `{cell["cell"]}` | {cell["profile"]["prefix_cache_states"]} '
                     f'| {report.number(server.get("measured_requests_attempted"))} / {report.number(server.get("measured_batched_requests"))} '
                     f'| {report.number(server.get("requests_per_batch"), 2)} '
                     f'| {report.number(delta.get("prefix_cache_hits"))} / {report.number(delta.get("prefix_cache_misses"))} '
                     f'| {ACCOUNTING[check["ok"]]} '
                     f'| {report.number((server.get("latency_ms") or {}).get("p50"))} |')
    lines += ['', '## Pre-declared readings', '', '| Reading | Speedup (1×8 / 1×1) | p95 ratio | Agreement drop | Result |',
              '| --- | ---: | ---: | ---: | --- |']
    for reading in result['batches_usefully']:
        lines.append(f'| {reading["workload"].upper()} Kev MLX batches usefully | {report.number(reading.get("speedup"), 2)} '
                     f'| {report.number(reading.get("p95_ratio"), 2)} | {report.number(reading.get("agreement_drop"))} '
                     f'| {report.verdict(reading["result"])} |')
    cached = result['cached_state_cheaper']
    lines += ['', '| Reading | A p50 ms (operation, new state) | B p50 ms (node + field, cached) | B / A '
              '| Cached ms per question | Agreement drop | Result |', '| --- | ---: | ---: | ---: | ---: | ---: | --- |',
              f'| Cached state is materially cheaper | {report.number(cached.get("a_p50_ms"))} '
              f'| {report.number(cached.get("b_p50_ms"))} | {report.number(cached.get("b_over_a"), 2)} '
              f'| {report.number(cached.get("cached_cost_per_question_ms"))} | {report.number(cached.get("agreement_drop"))} '
              f'| {report.verdict(cached["result"])} |']
    lines += ['', '## Cross-runtime diagnostic: serial llama.cpp labels (GGUF Q4_K_M; different precision)', '',
              '| Cell | Matched / total |', '| --- | ---: |']
    for cell in cells:
        cross = cell.get('label_agreement_cross_runtime') or {}
        lines.append(f'| `{cell["cell"]}` | {cross.get("matched")} / {cross.get("total")} |')
    return '\n'.join(lines + [''])


# --- Invocation ----------------------------------------------------------------------------------------

def provenance(output, inputs, setup):
    def git(*args):
        return subprocess.run(args, cwd=fixtures.ROOT, capture_output=True, check=True, timeout=30).stdout
    lock = inputs['locks']['kev']
    fixtures.require(lock['conversion_source_revision'] == kevmlx_setup.ADAPTER.revision,
                     'the llama.cpp reference GGUF was converted from another adapter revision')
    diff = git('git', 'diff', 'HEAD')
    (output / 'working-tree.diff').write_bytes(diff)
    for name in SOURCES:
        (output / 'source' / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixtures.ROOT / name, output / 'source' / name)
    layout = kevmlx_setup.Layout()
    shutil.copy2(layout.provenance, output / 'kev-provenance.json')
    shutil.copy2(layout.freeze, output / 'requirements.freeze.txt')
    return {
        'code_commit': git('git', 'rev-parse', 'HEAD').decode().strip(),
        'working_tree_diff_sha256': fixtures.digest(diff),
        'git_status_porcelain': git('git', 'status', '--porcelain').decode(),
        'source_sha256': {name: metal.sha256(fixtures.ROOT / name) for name in SOURCES},
        'datasets': {'w1': {'path': fixtures.relative(fixtures.W1_DATASET), 'sha256': fixtures.W1_DATASET_SHA256},
                     'w2': {'path': 'eval/query-routing/heldout.json', 'sha256': inputs['w2']['heldout_sha256'],
                            'freeze_sha256': inputs['w2']['freeze_sha256']} if 'w2' in inputs else None},
        'kev': {'repository': kevmlx_setup.KEV_REPOSITORY, 'commit': kevmlx_setup.KEV_COMMIT,
                'commit_date': kevmlx_setup.KEV_COMMIT_DATE, 'archive_sha256': setup['kev']['archive']['sha256'],
                'license': kevmlx_setup.KEV_LICENSE},
        'adapter': {'repository': kevmlx_setup.ADAPTER.repo, 'revision': kevmlx_setup.ADAPTER.revision,
                    'license': kevmlx_setup.ADAPTER.license, 'bytes': setup['adapter']['bytes']},
        'base': {'repository': kevmlx_setup.BASE.repo, 'revision': kevmlx_setup.BASE.revision,
                 'license': kevmlx_setup.BASE.license, 'bytes': setup['base']['bytes']},
        'quantization': 'bf16',
        'dtype': {'KEV_DTYPE': kevmlx_runtime.KEV_ENV['KEV_DTYPE'], 'backend': kevmlx_runtime.KEV_ENV['KEV_BACKEND'],
                  'loaded': 'per cell: runtime.dtype_loaded from /v1/models',
                  'note': 'MLX runs the backbone as stored (bf16) and Kev\'s pointer head in fp32 '
                          '(checkpoint.py:110-112, mlx_model.py:158-163)'},
        'head_meta': setup['head_meta'],
        'dependency_freeze': {'path': '.kev/requirements.freeze.txt', 'copy': 'requirements.freeze.txt',
                              'sha256': setup['install']['freeze']['sha256']},
        'packages': setup['install']['packages'],
        'setup_provenance': {'path': '.kev/provenance.json', 'copy': 'kev-provenance.json',
                             'sha256': setup['provenance_sha256'], 'verified_at': setup['verified_at']},
        'cross_runtime_reference_model': {'lock': fixtures.LOCKS['kev'].name, 'lock_sha256': inputs['lock_sha256']['kev'],
                                          'filename': lock['filename'], 'quantization': 'Q4_K_M',
                                          'conversion_source_revision': lock['conversion_source_revision']},
        'hardware': {'cpu': git('sysctl', '-n', 'machdep.cpu.brand_string').decode().strip(),
                     'memory_bytes': int(git('sysctl', '-n', 'hw.memsize')), 'platform': platform.platform(),
                     'machine': platform.machine(), 'tested_path': 'native macOS MLX on Metal; not CPU Docker, AMD64 or CUDA'},
    }


def run(args, cells, workloads):
    inputs = fixtures.load_inputs(workloads)
    setup = kevmlx_runtime.verify_runtime()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = args.output or fixtures.ROOT / 'results/throughput' / f'{run_id}-kevmlx-kev'
    output.mkdir(parents=True, exist_ok=False)
    summary = {'kind': 'throughput-screen-not-benchmark', 'run_id': run_id, 'runtime': 'kevmlx', 'model': 'kev',
               'workloads': workloads, 'planned_cells': [c.id for c in cells], 'budget_seconds': BUDGET_SECONDS,
               'protocol': PROTOCOL, 'started_at': shared.now(), 'status': 'running', 'stop_reason': None, 'cells': []}
    summary['provenance'] = provenance(output, inputs, setup)
    shared.save(output / 'summary.json', summary)
    print(f'Evidence: {output}', flush=True)
    deadline = time.monotonic() + BUDGET_SECONDS
    references = {}
    try:
        for cell in cells:
            cell_dir = output / cell.id
            result = shared.run_cell(cell, cell_dir, kevmlx_requests.jobs_for(cell, inputs), reference_for(cell, inputs),
                                     lambda c, d: kevmlx_runtime.Runtime(c, d, setup), deadline,
                                     provenance=summary['provenance'])
            result = finish_cell(cell, cell_dir, result, references, summary['provenance'])
            summary['cells'].append(result)
            if result['stop_reason'] == 'budget':
                summary['stop_reason'] = 'budget'
            shared.save(output / 'summary.json', summary)
            print(f'{cell.id}: {result["status"]}; {result["valid_questions"]}/{result["planned_questions"]} questions; '
                  f'{report.number(result["decisions_per_s"], 2)} questions/s; stop: {result["stop_reason"]}; '
                  f'warmup {report.warmup(result)} ok/errors/attempted/planned; '
                  f'cache accounting {ACCOUNTING[result["cache_check"]["ok"]]}', flush=True)
            if (result.get('runtime') or {}).get('cleanup_errors'):
                raise RuntimeError('owned runtime cleanup failed; no further cell was launched')
        summary['status'] = 'complete' if all(c['status'] == 'complete' for c in summary['cells']) else 'stopped'
    except BaseException as exc:
        summary.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                       error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        summary['readings'] = readings(summary['cells'])
        summary['finished_at'] = shared.now()
        shared.save(output / 'summary.json', summary)
        (output / 'report.md').write_text(render(summary))
        print(f'Evidence saved: {output}', flush=True)
    return 0 if summary['status'] == 'complete' else 1


def setup_status(layout):
    record = kevmlx_setup.load_provenance(layout)
    if record is None:
        return f'Setup: {fixtures.relative(layout.provenance)} not found; run python3 eval/throughput/kevmlx_setup.py (network) first'
    if record.get('pins') != kevmlx_setup.pins():
        return 'Setup: .kev/provenance.json records other pins; move .kev aside and set up again'
    return (f'Setup: .kev/provenance.json pins match (updated {record.get("updated_at")}); a run re-hashes every '
            'recorded byte first (kevmlx_setup.py --verify)')


def validate(workloads, ids):
    """Offline: verify every frozen input, rebuild every Kev request, print the plan."""
    inputs = fixtures.load_inputs(workloads)
    cells = select_cells(workloads, ids)
    lock = inputs['locks']['kev']
    fixtures.require(lock['conversion_source_revision'] == kevmlx_setup.ADAPTER.revision,
                     'the llama.cpp reference GGUF was converted from another adapter revision')
    fixtures.require(kevmlx_runtime.PORT not in kevmlx_runtime.RESERVED_PORTS, 'Kev MLX port collides')
    adapter, base = kevmlx_setup.ADAPTER, kevmlx_setup.BASE
    lines = ['kevmlx --validate: frozen inputs verified and Kev requests rebuilt offline; no model launched',
             f'Kev {kevmlx_setup.KEV_COMMIT} ({kevmlx_setup.KEV_COMMIT_DATE}); adapter {adapter.repo}@{adapter.revision} '
             f'(= {fixtures.LOCKS["kev"].name} conversion_source_revision); base {base.repo}@{base.revision}; '
             f'MLX bf16; loopback port {kevmlx_runtime.PORT}',
             f'Downloads: adapter {sum(f[0] for f in adapter.files.values()):,} bytes ({len(adapter.files)} files), base '
             f'{sum(f[0] for f in base.files.values()):,} bytes ({len(base.files)} files); setup refuses below '
             f'{kevmlx_setup.MIN_FREE_BEFORE_BASE >> 30} GiB free before the base']
    if 'w1' in workloads:
        reference = inputs['w1_reference']
        jobs = kevmlx_requests.w1_jobs(inputs)
        same = sum(job.body == json.dumps(reference['requests'][('kev', job.case_id, job.order)]).encode() for job in jobs)
        fixtures.require(same == len(jobs) == 2 * fixtures.W1_MODEL_ROUTED_CASES, 'W1 Kev requests differ from the serial run')
        lines.append(f'W1 {fixtures.relative(fixtures.W1_DATASET)} sha256 {fixtures.W1_DATASET_SHA256}: {len(jobs)} Kev '
                     f'requests per pass, byte-identical to the serial run {same}/{len(jobs)}, all within Kev\'s schema; '
                     f'largest body {max(len(j.body) for j in jobs):,} bytes')
    if 'w2' in workloads:
        frozen = fixtures.w2_jobs('kev', inputs['w2'], inputs['w2_reference'])
        new = kevmlx_requests.w2_jobs(inputs)
        split = kevmlx_requests.w2_jobs(inputs, split=True)
        same = sum(a.body == b.body for a, b in zip(new, frozen))
        fixtures.require(same == len(frozen) == fixtures.W2_CASES and len(split) == 2 * fixtures.W2_CASES,
                         'W2 Kev requests differ from the executed Metal run')
        lines.append(f'W2 eval/query-routing freeze sha256 {inputs["w2"]["freeze_sha256"]}: new-state bodies byte-identical '
                     f'to request-manifest.json and the executed Metal run {same}/{fixtures.W2_CASES}; cached-state split '
                     f'{len(split) // 2} A (operation) + {len(split) // 2} B (node, field), model, state and question bytes '
                     f'spliced verbatim')
    lines += ['Responses: Kev rounds probabilities to 4 decimals; the sum check allows K x 5e-5 (W1 K=2; W2 operation 9, '
              'node 6, field 5), then the shared validator applies unchanged',
              'Agreement: within-runtime vs the 1x1 new-state cell (primary); serial llama.cpp Q4_K_M labels (diagnostic)',
              setup_status(kevmlx_setup.Layout()), '',
              f'{"cell":<24}{"clients":>8}{"cache":>7}{"warmup":>8}{"measured":>9}{"questions":>10}']
    totals = {'warmup': 0, 'measured': 0, 'questions': 0}
    for cell in cells:
        jobs = kevmlx_requests.jobs_for(cell, inputs)
        measured = len(jobs) * cell.measured_passes
        questions = sum(len(job.questions) for job in jobs) * cell.measured_passes
        for key, value in (('warmup', len(jobs)), ('measured', measured), ('questions', questions)):
            totals[key] += value
        lines.append(f'{cell.id:<24}{cell.concurrency:>8}{cell.prefix_cache:>7}{len(jobs):>8}{measured:>9}{questions:>10}')
    lines += ['', f'kevmlx: {len(cells)} cells, {totals["warmup"] + totals["measured"]} requests ({totals["warmup"]} warmup, '
                  f'{totals["measured"]} measured), {totals["questions"]} measured questions; budget {BUDGET_SECONDS // 60} min']
    print('\n'.join(lines))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true', help='Verify inputs and print the plan offline; no inference')
    parser.add_argument('--workload', nargs='+', choices=['w1', 'w2'], default=['w1', 'w2'],
                        help='Run both in one invocation so the budget covers them')
    parser.add_argument('--cells', nargs='+', help='Cell IDs (comma or space separated); default every planned cell')
    parser.add_argument('--output', type=Path, help='New directory; default results/throughput/<ts>-kevmlx-kev')
    args = parser.parse_args(argv)
    workloads = sorted(set(args.workload))
    ids = [part for value in (args.cells or []) for part in value.split(',') if part]
    if args.validate:
        try:
            return validate(workloads, ids)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'kevmlx --validate FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1
    try:
        cells = select_cells(workloads, ids)
    except ValueError as exc:
        parser.error(str(exc))
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('the Kev MLX runner targets native Apple Silicon')

    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    metal.CACHE.mkdir(exist_ok=True)
    with (metal.CACHE / 'operation.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another Metal build/run owns this checkout; stop it first', file=sys.stderr)
            return 1
        try:
            return run(args, cells, workloads)
        except KeyboardInterrupt:
            print('Stopped the owned runtime; partial evidence saved.', file=sys.stderr)
            return 130
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'Kev MLX throughput run failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1


if __name__ == '__main__':
    sys.exit(main())
