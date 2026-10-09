#!/usr/bin/env python3
"""Throughput screen on SGLang MLX: decisions per second for Qwen JSON, /v1/decisions and /v1/score.

--validate verifies the frozen inputs, maps every request offline and prints the plan;
no server starts (add --checksums to verify the pinned SGLang source and model bytes).
A run first launches two one-fixture re-probe runtimes (overlap scheduling, radix cache
re-enabled), then one fresh SGLang server per cell, under run.py's budget, stop rules
and summary shape. Small pilot; not a benchmark. Protocol: eval/throughput/sglang.md.
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
import subprocess
import sys
import time
from typing import ClassVar

import fixtures
import report
import run
import runner
import sglang_requests
import sglang_runtime
import metal

require = fixtures.require
BUDGET_SECONDS = run.BUDGET_SECONDS
RUNNING = (1, 4, 8)
PROBE_REPEATS = 2
LOG_TAIL_LINES = 200
SOURCES = ('eval/throughput/sglang_runtime.py', 'eval/throughput/sglang_requests.py',
           'eval/throughput/run_sglang.py', 'eval/throughput/sglang.md')
CROSS_RUNTIME_NOTE = ('diagnostic only: serial llama.cpp Qwen JSON labels from GGUF Q4_K_M weights; this runtime '
                      'serves MLX affine 4-bit, group 64. Different quantization, kernels and (for decisions and '
                      'score) prompt wrapper; not a correctness or batching reading.')
PROTOCOL = {
    **run.PROTOCOL,
    'question': 'Does SGLang on MLX deliver materially more decisions per second than its own one-running-request '
                'profile at unchanged labels, per arm: Qwen JSON, /v1/decisions and /v1/score?',
    'guard': 'Not applicable: clients call the SGLang server directly; semselect never fronted SGLang.',
    'runtime': 'The 2026-10-05 cache-disabled probe launch (docs/evidence/20261005T154252Z-sglang-metal-cache-'
               'disabled/probe.json) with --max-running-requests N, --max-total-tokens max(8192, 4096 x N) so each '
               'running request can hold a full 4096-token context as each llama.cpp slot does, port 30111 and served '
               'name qwen35-4b-mlx; 4096-token context and default chunked prefill (4096) in every cell; fresh runtime '
               'per cell. The 4x4-overlap and 4x4-radix cells drop exactly one degraded flag and run only if their '
               'one-fixture probe passed.',
    'budget': f'{BUDGET_SECONDS // 60} minutes wall clock per invocation, including startup, warmup and shutdown. '
              'W1 and W2 run as separate invocations, each with its own budget; the re-probes run in the W1 '
              'invocation because the cells they gate are W1 cells.',
    'cache': 'Radix cache disabled at the server in every cell except 4x4-radix. cache_prompt is a llama.cpp field '
             'and is dropped. Prompt tokens computed and reused come from the scheduler "Prefill batch" log lines.',
    'agreement': 'Primary: within-runtime, every cell against measured pass 1 of the same arm\'s 1x1 cell in this '
                 'invocation. Diagnostic: cross-runtime against the serial llama.cpp Qwen JSON labels.',
    'quantization': sglang_runtime.QUANTIZATION + ' (mlx-community/Qwen3.5-4B-4bit); the llama.cpp runner serves '
                    'GGUF Q4_K_M. Cross-runtime comparisons are of throughput shape only.',
    'readings': 'The README "batches usefully" reading, applied per arm (8x8 vs 1x1). The Kev shared-state '
                'reading does not apply: SGLang does not serve Kev here.',
}


@dataclass(frozen=True)
class Cell:
    """slots is SGLang's --max-running-requests, named as in run.py so run_cell and report.py apply unchanged."""
    workload: str
    arm: str
    slots: int
    concurrency: int
    variant: str = ''
    model: ClassVar[str] = 'qwen'
    kv_unified: ClassVar[bool] = False

    @property
    def id(self):
        return f'{self.workload}-{self.arm}-{self.slots}x{self.concurrency}' + (f'-{self.variant}' if self.variant else '')

    @property
    def measured_passes(self):
        return fixtures.MEASURED_PASSES[self.workload]

    @property
    def n_ctx_total(self):
        # One token pool shared by every running request, sized at 4096 per request (min 8192).
        return sglang_runtime.pool_tokens(self.slots)

    @property
    def n_ctx_per_slot(self):
        # The per-request context limit.
        return sglang_runtime.CONTEXT_LENGTH


@dataclass(frozen=True)
class Probe:
    """One fixture on one running request: the passing probe's profile with one flag dropped."""
    variant: str
    slots: ClassVar[int] = 1
    concurrency: ClassVar[int] = 1

    @property
    def id(self):
        return 'probe-' + self.variant


CELLS = tuple(Cell(w, arm, n, n) for w in ('w1', 'w2') for arm in sglang_requests.ARMS[w] for n in RUNNING)
PROBES = (Probe('overlap'), Probe('radix'))
CONDITIONAL = {probe.variant: Cell('w1', 'qwen_decisions', 4, 4, probe.variant) for probe in PROBES}


def now():
    return datetime.now(timezone.utc).isoformat()


def select(workloads, ids):
    """Probes, base cells and probe-gated cells for these workloads and IDs."""
    cells = [c for c in CELLS if c.workload in workloads]
    probes = list(PROBES) if 'w1' in workloads else []
    conditional = dict(CONDITIONAL) if 'w1' in workloads else {}
    if ids:
        known = {c.id for c in cells} | {p.id for p in probes} | {c.id for c in conditional.values()}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise ValueError(f'unknown cells for this workload: {unknown}; choose from {sorted(known)}')
        cells = [c for c in cells if c.id in ids]
        probes = [p for p in probes if p.id in ids]
        orphans = sorted(c.id for v, c in conditional.items() if c.id in ids and 'probe-' + v not in ids)
        if orphans:
            raise ValueError(f'{orphans} run only if their probe passes in the same invocation; select the probe too')
        conditional = {v: c for v, c in conditional.items() if c.id in ids}
    return probes, cells, conditional


def with_conditional(cells, conditional, passed):
    """Add each gated cell whose probe passed, right after the W1 qwen_decisions cells."""
    added = [cell for variant, cell in conditional.items() if passed.get(variant)]
    anchor = ([i for i, c in enumerate(cells) if (c.workload, c.arm) == ('w1', 'qwen_decisions')]
              or [i for i, c in enumerate(cells) if c.workload == 'w1'])
    at = anchor[-1] + 1 if anchor else 0
    return list(cells[:at]) + added + list(cells[at:])


# --- Re-probe cells -------------------------------------------------------------------------

def decoded(row):
    try:
        return json.loads(base64.b64decode(row.get('response_bytes_base64') or ''))
    except ValueError:
        return None


def probe_passed(record):
    """Ready with the flag really dropped, every repeat valid, and a clean shutdown."""
    runtime = record.get('runtime') or {}
    rows = record.get('requests') or []
    return (record.get('error') is None and runtime.get('ready') is True and not runtime.get('cleanup_errors')
            and len(rows) == record.get('repeats') and all(row.get('status') == 'ok' for row in rows))


def repeat_diagnostics(rows):
    """Whether repeats agree; recorded, not part of the pass rule (a cached repeat may move slightly)."""
    distributions = []
    for row in rows:
        response = decoded(row) if row.get('status') == 'ok' else None
        answers = response.get('answers') if isinstance(response, dict) else None
        answer = answers.get('route') if isinstance(answers, dict) else None
        distributions.append(answer.get('probabilities') if isinstance(answer, dict) else None)
    delta = None
    if len(distributions) > 1 and all(isinstance(d, dict) for d in distributions):
        delta = max(abs(d[key] - distributions[0][key]) for d in distributions[1:] for key in distributions[0])
    labels = [row.get('labels') for row in rows]
    return {'labels_equal': len(labels) > 1 and all(label == labels[0] for label in labels),
            'max_probability_delta': delta}


def run_probe(probe, probe_dir, job, make_runtime, deadline, clock=time.monotonic):
    probe_dir.mkdir(parents=True)
    record = {'probe': probe.id, 'variant': probe.variant, 'dropped_flag': sglang_runtime.VARIANT_DROPS[probe.variant],
              'gates': CONDITIONAL[probe.variant].id, 'repeats': PROBE_REPEATS,
              'fixture': {'case_id': job.case_id, 'order': job.order, 'path': job.path,
                          'request_sha256': fixtures.digest(job.body), 'body': json.loads(job.body)},
              'started_at': now(), 'requests': [], 'error': None}
    runtime = None
    try:
        if deadline - clock() <= 0:
            record['error'] = 'budget'
        else:
            runtime = make_runtime(probe, probe_dir)
            runtime.start(deadline)
            for _ in range(PROBE_REPEATS):
                remaining = deadline - clock()
                if remaining <= 0:
                    record['error'] = 'budget'
                    break
                record['requests'].append(runner.call(runtime.base, job, min(runner.TIMEOUT_SECONDS, remaining), clock))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if runtime is not None:
            record['runtime'] = runtime.stop()
    log = probe_dir / 'runtime.log'
    record['log_tail'] = log.read_text(errors='replace').splitlines()[-LOG_TAIL_LINES:] if log.exists() else None
    record['repeat_diagnostics'] = repeat_diagnostics(record['requests'])
    record['passed'] = probe_passed(record)
    record['finished_at'] = now()
    run.save(probe_dir / 'probe.json', record)
    return record


# --- Cell post-processing: usage, log window, within-runtime agreement -------------------------

def journal_rows(cell_dir):
    path = cell_dir / 'journal.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def measured(rows):
    return sorted((r for r in rows if r['phase'] == 'measured'), key=lambda r: r['sequence'])


def usage_summary(rows):
    """Server-reported usage for every answered call, and per-request means over measured calls."""
    calls = []
    for row in rows:
        response = decoded(row) if row['status'] in ('ok', 'invalid') else None
        calls.append({key: row[key] for key in ('phase', 'pass_number', 'sequence', 'case_id', 'order', 'status')}
                     | sglang_requests.usage(response))
    tokens = {}
    for key in ('prompt_tokens', 'completion_tokens', 'cached_tokens'):
        values = [call[key] for call in calls if call['phase'] == 'measured' and key in call]
        if values:
            tokens[key] = {'requests': len(values), 'sum': sum(values), 'mean': sum(values) / len(values)}
    return calls, tokens


def reference_from(cell, cell_dir, rows):
    journal = cell_dir / 'journal.jsonl'
    return {'cell': cell.id, 'path': sglang_runtime.relative(journal), 'sha256': metal.sha256(journal),
            'labels': {(r['case_id'], r['order']): r['labels'] for r in measured(rows) if r['pass_number'] == 1}}


def within_runtime(rows, reference, questions, arm):
    """Matched questions against the 1x1 cell's measured pass 1; the denominator is every planned question."""
    rows = measured(rows)
    agreement = {'total': len(rows) * len(questions), 'reference_arm': arm, 'primary': True}
    if reference is None:
        return agreement | {'matched': None, 'reference_path': None, 'reference_sha256': None,
                            'reference_view': 'not evaluable: this arm\'s 1x1 cell did not run in this invocation'}
    labels = reference['labels']
    matched = sum(1 for r in rows for q in questions
                  if r['labels'].get(q) is not None and r['labels'].get(q) == (labels.get((r['case_id'], r['order'])) or {}).get(q))
    return agreement | {'matched': matched, 'reference_path': reference['path'], 'reference_sha256': reference['sha256'],
                        'reference_view': f'measured pass 1 of {reference["cell"]} (same runtime and arm)',
                        'reference_valid_questions': sum(v is not None for labels_ in labels.values() for v in labels_.values())}


def finish_cell(result, cell, cell_dir, jobs, references, provenance=None):
    """Add what run.run_cell cannot see for SGLang, then rewrite the cell summary."""
    rows = journal_rows(cell_dir)
    calls, tokens = usage_summary(rows)
    run.save(cell_dir / 'usage.json', calls)
    result['tokens_per_request'] = {**(result.get('tokens_per_request') or {}), **tokens}
    log = cell_dir / 'runtime.log'
    data = log.read_bytes() if log.exists() else b''
    snapshots = result['metrics']['snapshots']
    for name, (before, after) in (('measured_delta', ('after_warmup', 'end')), ('warmup_delta', ('start', 'after_warmup'))):
        counters = sglang_runtime.window(data, snapshots.get(before), snapshots.get(after))
        if counters is not None:
            result['metrics'][name] = {**(result['metrics'][name] or {}), **counters}
    result['profile'].update(max_running_requests=cell.slots, max_total_tokens=cell.n_ctx_total,
                             context_length=sglang_runtime.CONTEXT_LENGTH,
                             chunked_prefill_size=sglang_runtime.CHUNKED_PREFILL_SIZE, variant=cell.variant,
                             dropped_flag=sglang_runtime.VARIANT_DROPS[cell.variant],
                             slots_meaning='SGLang --max-running-requests; one shared pool of max(8192, 4096 x N) tokens')
    result['request_mapping'] = sglang_requests.MAPPING[cell.arm]
    result['label_agreement_cross_runtime'] = {**result['label_agreement'], 'diagnostic': CROSS_RUNTIME_NOTE}
    if (cell.slots, cell.concurrency, cell.variant) == (1, 1, '') and rows:
        references[(cell.workload, cell.arm)] = reference_from(cell, cell_dir, rows)
    result['label_agreement'] = within_runtime(rows, references.get((cell.workload, cell.arm)),
                                               run.cell_questions(jobs), cell.arm)
    run.save(cell_dir / 'summary.json', dict(result, provenance=provenance) if provenance else result)
    return result


def finalizer(cell, inputs, deadline, cell_dir, base):
    if cell.arm != 'qwen_score':
        return None
    return lambda jobs: sglang_requests.finalize_score_jobs(jobs, inputs['w1'], deadline, base,
                                                            cell_dir / 'score-preparation.json')


# --- Invocation --------------------------------------------------------------------------

def provenance(output, inputs, verified):
    record = run.provenance(output, 'qwen', inputs, None, None)
    for key in ('model_lock', 'model_lock_sha256', 'build'):
        record.pop(key)
    for name in SOURCES:
        (output / 'source' / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixtures.ROOT / name, output / 'source' / name)
        record['source_sha256'][name] = metal.sha256(fixtures.ROOT / name)
    record['runtime'] = verified
    record['quantization'] = sglang_runtime.QUANTIZATION
    record['cross_runtime_reference_lock'] = {
        'path': fixtures.relative(fixtures.LOCKS['qwen']), 'sha256': inputs['lock_sha256']['qwen'],
        'note': 'GGUF Q4_K_M pin of the llama.cpp labels used only as the cross-runtime diagnostic'}
    record['hardware']['tested_path'] = 'native macOS Metal through MLX (SGLang); not CPU Docker, AMD64 or CUDA'
    return record


def run_invocation(args, probes, cells, conditional, workloads, make_runtime=None, verify=None):
    inputs = fixtures.load_inputs(workloads)
    verified = (verify or sglang_runtime.verify_runtime)()
    make_runtime = make_runtime or (lambda cell, directory: sglang_runtime.Runtime(cell, directory))
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = args.output or fixtures.ROOT / 'results/throughput' / f'{run_id}-sglang-qwen'
    output.mkdir(parents=True, exist_ok=False)
    summary = {'kind': 'throughput-screen-not-benchmark', 'run_id': run_id, 'runtime': 'sglang-mlx', 'model': 'qwen',
               'workloads': workloads, 'planned_probes': [p.id for p in probes], 'planned_cells': [c.id for c in cells],
               'conditional_cells': {c.id: f'added only if probe-{v} passes' for v, c in conditional.items()},
               'budget_seconds': BUDGET_SECONDS, 'protocol': PROTOCOL, 'request_mapping': sglang_requests.MAPPING,
               'started_at': now(), 'status': 'running', 'stop_reason': None, 'probes': [], 'cells': []}
    summary['provenance'] = provenance(output, inputs, verified)
    run.save(output / 'summary.json', summary)
    print(f'Evidence: {output}', flush=True)
    deadline = time.monotonic() + BUDGET_SECONDS
    references = {}
    try:
        passed = {}
        if probes:
            fixture = sglang_requests.w1_jobs('qwen_decisions', inputs)[0]
            for probe in probes:
                record = run_probe(probe, output / probe.id, fixture, make_runtime, deadline)
                summary['probes'].append(record)
                passed[probe.variant] = record['passed']
                gated = CONDITIONAL[probe.variant].id
                if gated in summary['conditional_cells']:
                    summary['conditional_cells'][gated] = ('added: ' if record['passed'] else 'not added: ') + \
                        f'{probe.id} {"passed" if record["passed"] else "failed"}'
                run.save(output / 'summary.json', summary)
                print(f'{probe.id}: {"pass" if record["passed"] else "fail"}; error: {record["error"]}', flush=True)
                if (record.get('runtime') or {}).get('cleanup_errors'):
                    raise RuntimeError('owned runtime cleanup failed; no further cell was launched')
        plan = with_conditional(cells, conditional, passed)
        summary['planned_cells'] = [c.id for c in plan]
        for cell in plan:
            jobs = sglang_requests.jobs_for(cell, inputs)
            cell_dir = output / cell.id
            result = run.run_cell(cell, cell_dir, jobs, sglang_requests.cross_runtime_reference(cell, inputs),
                                  make_runtime, deadline, finalizer(cell, inputs, deadline, cell_dir, sglang_runtime.BASE),
                                  provenance=summary['provenance'])
            result = finish_cell(result, cell, cell_dir, jobs, references, summary['provenance'])
            summary['cells'].append(result)
            if result['stop_reason'] == 'budget':
                summary['stop_reason'] = 'budget'
            run.save(output / 'summary.json', summary)
            agreement = result['label_agreement']
            print(f'{cell.id}: {result["status"]}; {result["valid_questions"]}/{result["planned_questions"]} questions; '
                  f'{report.number(result["decisions_per_s"], 2)} questions/s; within-runtime agreement '
                  f'{agreement["matched"]}/{agreement["total"]}; stop: {result["stop_reason"]}; '
                  f'warmup {report.warmup(result)} ok/errors/attempted/planned', flush=True)
            if (result.get('runtime') or {}).get('cleanup_errors'):
                raise RuntimeError('owned runtime cleanup failed; no further cell was launched')
        summary['status'] = 'complete' if all(c['status'] == 'complete' for c in summary['cells']) else 'stopped'
    except BaseException as exc:
        summary.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                       error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        summary['finished_at'] = now()
        run.save(output / 'summary.json', summary)
        (output / 'report.md').write_text(report.render([summary]))
        print(f'Evidence saved: {output}', flush=True)
    return 0 if summary['status'] == 'complete' else 1


def validate(workloads, ids, checksums=False):
    """Offline: verify every frozen input, map every request, print the plan."""
    inputs = fixtures.load_inputs(workloads)
    probes, cells, conditional = select(workloads, ids)
    pins = sglang_runtime.load_provenance()
    served = sglang_requests.SERVED_MODEL
    lines = ['throughput sglang --validate: frozen inputs verified and SGLang requests mapped offline; no server launched']
    if 'w1' in workloads:
        dataset, reference, locks = inputs['w1'], inputs['w1_reference'], inputs['locks']
        source = {'qwen_json': fixtures.w1_jobs('qwen_json', dataset, reference, locks['qwen']['alias']),
                  'kev': fixtures.w1_jobs('kev', dataset, reference, locks['kev']['alias'])}
        mapped = {arm: sglang_requests.w1_jobs(arm, inputs) for arm in sglang_requests.ARMS['w1']}
        expected_changes = [('model', locks['qwen']['alias'], served), ('cache_prompt', True, '<removed>')]
        same_json = sum(sglang_requests.chat_changes(s.body, m.body) == (expected_changes, True)
                        for s, m in zip(source['qwen_json'], mapped['qwen_json']))
        same_decisions = sum(sglang_requests.decisions_matches(k.body, m.body)
                             for k, m in zip(source['kev'], mapped['qwen_decisions']))
        options = {(j.case_id, j.order): [o['name'] for o in json.loads(j.body)['questions'][0]['options']]
                   for j in mapped['qwen_decisions']}
        reversed_pairs = sum(options[(case, 'reverse')] == options[(case, 'normal')][::-1]
                             for case, order in options if order == 'normal')
        evidence = sum(fixtures.compare_scoring.score_messages(dataset, {'text': j.score_input[0]}, list(j.score_input[1]))[1]['content']
                       == json.loads(m.body)['messages'][1]['content'] for j, m in zip(mapped['qwen_score'], mapped['qwen_json']))
        labelled = sum(all(v is not None for v in j.reference.values()) for j in mapped['qwen_decisions'])
        count = 2 * fixtures.W1_MODEL_ROUTED_CASES
        for name, value in (('qwen_json', same_json), ('qwen_decisions', same_decisions), ('qwen_score', evidence),
                            ('cross-runtime labels', labelled)):
            require(value == count, f'W1 {name}: {value}/{count} requests passed the mapping check')
        require(reversed_pairs == fixtures.W1_MODEL_ROUTED_CASES, 'W1 decisions options do not follow candidate order')
        lines += [f'W1 {fixtures.relative(fixtures.W1_DATASET)} sha256 {fixtures.W1_DATASET_SHA256}: '
                  f'{fixtures.W1_MODEL_ROUTED_CASES} cases unresolved by the code precheck, {count} requests per pass',
                  f'W1 qwen_json {same_json}/{count}: llama.cpp bodies with only model -> {served} and cache_prompt removed',
                  f'W1 qwen_decisions {same_decisions}/{count}: input = Kev state, question = instructions, options = '
                  f'criteria in candidate order; reverse order reverses the options in {reversed_pairs}/{fixtures.W1_MODEL_ROUTED_CASES} cases',
                  f'W1 qwen_score {evidence}/{count}: score messages carry the JSON arm\'s evidence text (token IDs need the live runtime)',
                  f'W1 cross-runtime diagnostic: {reference["path"]} trial-1 Qwen JSON labels for {labelled}/{count}']
    if 'w2' in workloads:
        reference = inputs['w2_reference']
        source = {arm: fixtures.w2_jobs(arm, inputs['w2'], reference) for arm in ('kev', 'qwen_json')}
        mapped = {arm: sglang_requests.w2_jobs(arm, inputs) for arm in sglang_requests.ARMS['w2']}
        expected_changes = [('model', inputs['locks']['qwen']['alias'], served), ('cache_prompt', False, '<removed>')]
        same_json = sum(sglang_requests.chat_changes(s.body, m.body) == (expected_changes, True)
                        for s, m in zip(source['qwen_json'], mapped['qwen_json']))
        same_decisions = sum(sglang_requests.decisions_matches(k.body, m.body) and len(json.loads(m.body)['questions']) == 3
                             for k, m in zip(source['kev'], mapped['qwen_decisions']))
        labelled = sum(all(v is not None for v in j.reference.values()) for j in mapped['qwen_decisions'])
        for name, value in (('qwen_json', same_json), ('qwen_decisions', same_decisions)):
            require(value == fixtures.W2_CASES, f'W2 {name}: {value}/{fixtures.W2_CASES} requests passed the mapping check')
        lines += [f'W2 eval/query-routing freeze verified (freeze.json sha256 {inputs["w2"]["freeze_sha256"]}); '
                  f'heldout.json sha256 {inputs["w2"]["heldout_sha256"]}: {fixtures.W2_CASES} cases',
                  f'W2 qwen_json {same_json}/{fixtures.W2_CASES}: llama.cpp bodies with only model -> {served} and cache_prompt removed',
                  f'W2 qwen_decisions {same_decisions}/{fixtures.W2_CASES}: one request, three choice questions '
                  f'(operation, node, field) from the Kev heads, options in criteria order',
                  f'W2 cross-runtime diagnostic: {reference["path"]} normal-order Qwen JSON selections complete for '
                  f'{labelled}/{fixtures.W2_CASES}']
    lines.append(f'Runtime pins: {fixtures.relative(sglang_runtime.PROVENANCE)} sha256 {sglang_runtime.PROVENANCE_SHA256}; '
                 f'SGLang {pins["source_revision"]}; {pins["model_repository"]}@{pins["model_revision"]}, '
                 f'{sglang_runtime.QUANTIZATION}; port {sglang_runtime.PORT}')
    if checksums:
        verified = sglang_runtime.verify_runtime()
        lines.append(f'Runtime verified: {verified["source_files_verified"]} python/sglang files equal the pinned archive; '
                     f'{len(verified["model"]["files"])} model files match provenance.json')
    else:
        lines.append('Runtime source and model checksums are verified before launch (add --checksums to verify now)')
    header = f'{"cell":<34}{"running":>8}{"clients":>8}{"pool":>7}{"warmup":>8}{"measured":>9}{"questions":>10}  dropped flag'
    lines += ['', header]
    for probe in probes:
        lines.append(f'{probe.id:<34}{probe.slots:>8}{probe.concurrency:>8}{sglang_runtime.pool_tokens(probe.slots):>7}'
                     f'{0:>8}{PROBE_REPEATS:>9}{"—":>10}  {sglang_runtime.VARIANT_DROPS[probe.variant]} (one fixture, run first)')
    totals = {}
    for kind, group in (('base', cells), ('conditional', list(conditional.values()))):
        for cell in group:
            jobs = sglang_requests.jobs_for(cell, inputs)
            planned = len(jobs) * cell.measured_passes
            questions = planned * len(run.cell_questions(jobs))
            total = totals.setdefault((cell.workload, kind), [0, 0, 0, 0])
            for index, value in enumerate((1, len(jobs), planned, questions)):
                total[index] += value
            note = (f'{sglang_runtime.VARIANT_DROPS[cell.variant]} (only if probe-{cell.variant} passes)'
                    if cell.variant else '')
            lines.append(f'{cell.id:<34}{cell.slots:>8}{cell.concurrency:>8}{cell.n_ctx_total:>7}{len(jobs):>8}'
                         f'{planned:>9}{questions:>10}  {note}'.rstrip())
    lines.append('')
    # W1 and W2 run as separate invocations, each with its own budget.
    for workload in workloads:
        count, warmup, planned, questions = totals.get((workload, 'base'), [0, 0, 0, 0])
        line = (f'{workload} invocation: {count} cells, {warmup + planned} requests ({warmup} warmup, {planned} measured), '
                f'{questions} measured questions')
        if workload == 'w1' and probes:
            line += f'; {len(probes)} probes x {PROBE_REPEATS} requests'
        if (workload, 'conditional') in totals:
            count, warmup, planned, _ = totals[(workload, 'conditional')]
            line += f'; up to {count} probe-gated cells, {warmup + planned} requests ({planned} measured)'
        lines.append(line + f'; budget {BUDGET_SECONDS // 60} min')
    print('\n'.join(lines))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true', help='Verify inputs, map requests and print the plan offline')
    parser.add_argument('--checksums', action='store_true',
                        help='With --validate: also verify the pinned SGLang source tree and model bytes (reads ~3 GB)')
    parser.add_argument('--model', choices=['qwen'], help='SGLang serves Qwen direct scoring here; Kev is not attempted')
    parser.add_argument('--workload', nargs='+', choices=['w1', 'w2'], default=['w1', 'w2'],
                        help='Run both in one invocation so the budget covers them')
    parser.add_argument('--cells', nargs='+', help='Cell or probe IDs (comma or space separated); default all')
    parser.add_argument('--output', type=Path, help='New directory; default results/throughput/<ts>-sglang-qwen')
    args = parser.parse_args(argv)
    workloads = sorted(set(args.workload))
    ids = [part for value in (args.cells or []) for part in value.split(',') if part]
    if args.validate:
        try:
            return validate(workloads, ids, args.checksums)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'throughput sglang --validate FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1
    if args.model is None:
        parser.error('--model qwen is required for a run')
    try:
        probes, cells, conditional = select(workloads, ids)
    except ValueError as exc:
        parser.error(str(exc))
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('the SGLang MLX runner targets native Metal on Apple Silicon')

    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    metal.CACHE.mkdir(exist_ok=True)
    # The same lock as run.py: one Metal operation at a time on this checkout.
    with (metal.CACHE / 'operation.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another Metal build/run owns this checkout; stop it first', file=sys.stderr)
            return 1
        try:
            return run_invocation(args, probes, cells, conditional, workloads)
        except KeyboardInterrupt:
            print('Stopped the owned runtime; partial evidence saved.', file=sys.stderr)
            return 130
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'Throughput run failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1


if __name__ == '__main__':
    sys.exit(main())
