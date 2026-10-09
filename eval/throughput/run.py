#!/usr/bin/env python3
"""Throughput screen: decisions per second across llama.cpp Metal slot/concurrency cells.

--validate builds every request offline, verifies the frozen inputs and prints the
planned cells; no model runs. A run starts one owned llama-server per cell, sends one
warmup pass and the measured passes directly to it, and stops on the 45-minute
per-model budget or three consecutive runtime errors in the measured passes (warmup
errors are recorded but do not count; protocol amendment 1). Small pilot; not a benchmark.
"""
from __future__ import annotations

import argparse
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

import fixtures
import llamacpp
import report
import runner
import compare_scoring
import metal

BUDGET_SECONDS = 45 * 60
GUARD_MAX_BODY = 32 << 10  # internal/guard/guard.go MaxBody
SOURCES = ('eval/throughput/fixtures.py', 'eval/throughput/runner.py', 'eval/throughput/llamacpp.py',
           'eval/throughput/report.py', 'eval/throughput/run.py', 'scripts/answerability.py',
           'scripts/evaluate.py', 'scripts/compare_scoring.py', 'scripts/metal.py', 'scripts/model.py',
           'eval/query-routing/experiment.py', 'eval/query-routing/runner.py')
PROTOCOL = {
    # Version 1 (no 'version' key in the summary) is the protocol as frozen; bump on every amendment.
    'version': 2,
    'amendments': ['1, 2026-10-09: warmup errors are recorded but no longer count toward the consecutive-error '
                   'stop; the count starts at zero when the measured passes begin. See eval/throughput/README.md.'],
    'question': 'Does any Metal serving path deliver materially more decisions per second than the one-slot '
                'serial profile at unchanged labels, and does Kev gain more than Qwen from slots or shared state?',
    'yardstick': 'decisions_per_s = valid questions answered / measured elapsed seconds (warmup excluded); '
                 'W2 Kev counts three heads per request and W2 Qwen JSON three fields.',
    'guard': 'Bypassed. Clients call llama-server directly, as the Qwen baselines always did. The Go guard admits '
             'one inference and returns 429 otherwise; it forwards Kev bodies unchanged, so bytes are identical.',
    'runtime': 'scripts/metal.py launch flags except -np N, -c 4096*N and -kvu in the unified-KV cell; port is '
               'compare_scoring.PORT so prepare_score tokenizes against the cell runtime. Fresh runtime per cell.',
    'cache': 'Frozen requests keep their cache fields: W1 Qwen JSON cache_prompt=true, W1 one-token scoring '
             'cache_prompt=false, W2 Qwen JSON cache_prompt=false, Kev uses the server default. Measured passes '
             'repeat warmup prompts, so the RAM prompt cache can serve repeats; processed/cached counts are recorded.',
    'failures': 'No retries. 30 s per-request timeout includes queueing. Three consecutive runtime errors in the '
                'measured passes stop a cell; warmup errors are recorded but do not count. Unstarted requests '
                'are not_run and stay in denominators.',
    'budget': f'{BUDGET_SECONDS // 60} minutes wall clock per runtime x model invocation, including startup, '
              'warmup and shutdown.',
    'readings': 'Screening readings in eval/throughput/README.md, fixed before inference; not adoption gates.',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def select_cells(model, workloads, ids):
    planned = [c for c in fixtures.CELLS if (model is None or c.model == model) and c.workload in workloads]
    if ids:
        known = {c.id for c in planned}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise ValueError(f'unknown cells for this model/workload: {unknown}; choose from {sorted(known)}')
        planned = [c for c in planned if c.id in ids]
    return planned


def measured_order(jobs, cell):
    order = [(trial, job) for trial in range(1, cell.measured_passes + 1) for job in fixtures.traversal(jobs, trial)]
    return [job for _, job in order], [trial for trial, _ in order]


def run_cell(cell, cell_dir, jobs, reference, make_runtime, deadline, finalize=None, clock=time.monotonic,
             provenance=None):
    """Run one cell end to end. Every measured request is journaled exactly once."""
    cell_dir.mkdir(parents=True)
    result = {'cell': cell.id, 'workload': cell.workload, 'arm': cell.arm, 'model': cell.model,
              'profile': {'slots': cell.slots, 'concurrency': cell.concurrency, 'kv_unified': cell.kv_unified,
                          'n_ctx_total': cell.n_ctx_total, 'n_ctx_per_slot': cell.n_ctx_per_slot,
                          'warmup_passes': fixtures.WARMUP_PASSES, 'measured_passes': cell.measured_passes,
                          'timeout_s': runner.TIMEOUT_SECONDS},
              'reference': reference, 'protocol_version': PROTOCOL['version'], 'started_at': now(),
              'status': 'running'}
    state = {'consecutive_errors': 0, 'stop_reason': None}
    phases = {'warmup': {}, 'measured': {}}
    metrics = {}
    elapsed = 0.0
    runtime = None
    measured, pass_numbers = measured_order(jobs, cell)
    with (cell_dir / 'journal.jsonl').open('x') as journal:
        def writer(phase, numbers):
            def write(index, row):
                row.update(cell=cell.id, phase=phase, pass_number=numbers[index], sequence=index)
                phases[phase][index] = row
                journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                journal.flush()
            return write
        try:
            if deadline - clock() <= 0:
                state['stop_reason'] = 'budget'
            else:
                runtime = make_runtime(cell, cell_dir)
                result['runtime'] = runtime.info
                runtime.start(deadline)
                started = clock()
                jobs = finalize(jobs) if finalize else jobs
                result['preparation_s'] = clock() - started
                save(cell_dir / 'requests.json', [{'case_id': j.case_id, 'order': j.order, 'path': j.path,
                                                   'sha256': fixtures.digest(j.body), 'body': json.loads(j.body)}
                                                  for j in jobs])
                origin = clock()
                send = lambda job, timeout: runner.call(runtime.base, job, timeout, clock, origin)  # noqa: E731
                warmup = fixtures.traversal(jobs, 1)
                metrics['start'] = runtime.metrics()
                runner.run_jobs(warmup, send, cell.concurrency, deadline, clock, writer('warmup', [0] * len(warmup)), state,
                                count_errors=False)
                metrics['after_warmup'] = runtime.metrics()
                measured, pass_numbers = measured_order(jobs, cell)
                _, elapsed = runner.run_jobs(measured, send, cell.concurrency, deadline, clock,
                                             writer('measured', pass_numbers), state)
                metrics['end'] = runtime.metrics()
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            state['stop_reason'] = state['stop_reason'] or f'{type(exc).__name__}: {exc}'
            result['status'] = 'failed'
        finally:
            if runtime is not None:
                result['runtime'] = runtime.stop()
                result['profile'].update(command=result['runtime'].get('command'),
                                         n_ctx_per_slot_observed=(result['runtime'].get('startup') or {}).get('n_ctx_slot'))
                result['largest_child_peak_rss_bytes_so_far'] = result['runtime'].get('largest_child_peak_rss_bytes_so_far')
        # Startup, preparation or an early stop: keep every planned request in the denominator.
        write = writer('measured', pass_numbers)
        for index, job in enumerate(measured):
            if index not in phases['measured']:
                write(index, runner.not_run(job, state['stop_reason']))
    rows = [phases['measured'][i] for i in range(len(measured))]
    warm = list(phases['warmup'].values())
    result.update(runner.summarize(rows, elapsed, len(cell_questions(jobs))))
    result['label_agreement'].update(reference_path=reference['path'], reference_sha256=reference['sha256'],
                                     reference_arm=reference['arm'], reference_view=reference['view'])
    result.update(
        stop_reason=state['stop_reason'],
        warmup={'planned': len(fixtures.traversal(jobs, 1)), 'attempted': sum(r['status'] != 'not_run' for r in warm),
                'ok': sum(r['status'] == 'ok' for r in warm), 'errors': sum(r['status'] == 'error' for r in warm)},
        metrics={'snapshots': metrics, 'measured_delta': llamacpp.metrics_delta(metrics.get('after_warmup'), metrics.get('end')),
                 'warmup_delta': llamacpp.metrics_delta(metrics.get('start'), metrics.get('after_warmup'))},
        finished_at=now())
    if result['status'] != 'failed':
        result['status'] = 'complete' if state['stop_reason'] is None and result['not_run'] == 0 else 'stopped'
    save(cell_dir / 'summary.json', dict(result, provenance=provenance) if provenance else result)
    return result


def cell_questions(jobs):
    return jobs[0].questions if jobs else ()


def finalizer(cell, inputs):
    if cell.arm != 'qwen_score':
        return None
    return lambda jobs: fixtures.finalize_score_jobs(jobs, inputs['w1'])


def provenance(output, model, inputs, lock, build):
    def run(*args):
        return subprocess.run(args, cwd=fixtures.ROOT, capture_output=True, check=True, timeout=30).stdout
    diff = run('git', 'diff', 'HEAD')
    (output / 'working-tree.diff').write_bytes(diff)
    for name in SOURCES:
        (output / 'source' / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fixtures.ROOT / name, output / 'source' / name)
    return {
        'code_commit': run('git', 'rev-parse', 'HEAD').decode().strip(),
        'working_tree_diff_sha256': fixtures.digest(diff),
        'git_status_porcelain': run('git', 'status', '--porcelain').decode(),
        'source_sha256': {name: metal.sha256(fixtures.ROOT / name) for name in SOURCES},
        'datasets': {'w1': {'path': fixtures.relative(fixtures.W1_DATASET), 'sha256': fixtures.W1_DATASET_SHA256},
                     'w2': {'path': 'eval/query-routing/heldout.json', 'sha256': inputs['w2']['heldout_sha256'],
                            'freeze_sha256': inputs['w2']['freeze_sha256']} if 'w2' in inputs else None},
        'model_lock': lock, 'model_lock_sha256': inputs['lock_sha256'][model], 'build': build,
        'hardware': {'cpu': run('sysctl', '-n', 'machdep.cpu.brand_string').decode().strip(),
                     'memory_bytes': int(run('sysctl', '-n', 'hw.memsize')), 'platform': platform.platform(),
                     'machine': platform.machine(), 'tested_path': 'native macOS Metal; not CPU Docker, AMD64 or CUDA'},
    }


def run(args, cells, workloads):
    inputs = fixtures.load_inputs(workloads)
    lock, build = llamacpp.verify_runtime(args.model)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = args.output or fixtures.ROOT / 'results/throughput' / f'{run_id}-llamacpp-{args.model}'
    output.mkdir(parents=True, exist_ok=False)
    summary = {'kind': 'throughput-screen-not-benchmark', 'run_id': run_id, 'runtime': 'llamacpp',
               'model': args.model, 'workloads': workloads, 'planned_cells': [c.id for c in cells],
               'budget_seconds': BUDGET_SECONDS, 'protocol': PROTOCOL, 'started_at': now(), 'status': 'running',
               'stop_reason': None, 'cells': []}
    summary['provenance'] = provenance(output, args.model, inputs, lock, build)
    save(output / 'summary.json', summary)
    print(f'Evidence: {output}', flush=True)
    deadline = time.monotonic() + BUDGET_SECONDS
    try:
        for cell in cells:
            result = run_cell(cell, output / cell.id, fixtures.jobs_for(cell, inputs), fixtures.reference_for(cell, inputs),
                              lambda c, d: llamacpp.Runtime(c, lock, d), deadline, finalizer(cell, inputs),
                              provenance=summary['provenance'])
            summary['cells'].append(result)
            if result['stop_reason'] == 'budget':
                summary['stop_reason'] = 'budget'
            save(output / 'summary.json', summary)
            print(f'{cell.id}: {result["status"]}; {result["valid_questions"]}/{result["planned_questions"]} questions; '
                  f'{report.number(result["decisions_per_s"], 2)} questions/s; stop: {result["stop_reason"]}; '
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
        save(output / 'summary.json', summary)
        (output / 'report.md').write_text(report.render([summary]))
        print(f'Evidence saved: {output}', flush=True)
    return 0 if summary['status'] == 'complete' else 1


def validate(model, workloads, ids):
    """Offline: verify every frozen input, rebuild every request, print the plan."""
    inputs = fixtures.load_inputs(workloads)
    cells = select_cells(model, workloads, ids)
    lines = ['throughput --validate: frozen inputs verified and requests rebuilt offline; no model launched']
    if 'w1' in workloads:
        dataset, reference = inputs['w1'], inputs['w1_reference']
        same = {}
        for arm in ('kev', 'qwen_json'):
            jobs = fixtures.w1_jobs(arm, dataset, reference, inputs['locks'][fixtures.ARM_MODEL[arm]]['alias'])
            same[arm] = sum(job.body == json.dumps(reference['requests'][(arm, job.case_id, job.order)]).encode() for job in jobs)
            fixtures.require(same[arm] == len(jobs) == 2 * fixtures.W1_MODEL_ROUTED_CASES,
                             f'W1 {arm} requests differ from the serial run')
            fixtures.require(arm != 'kev' or all(len(job.body) <= GUARD_MAX_BODY for job in jobs), 'Kev body exceeds guard limit')
        score = fixtures.w1_jobs('qwen_score', dataset, reference, inputs['locks']['qwen']['alias'])
        evidence = sum(compare_scoring.score_messages(dataset, {'text': job.score_input[0]}, list(job.score_input[1]))[1]['content']
                       == reference['requests'][('qwen_json', job.case_id, job.order)]['messages'][1]['content'] for job in score)
        fixtures.require(evidence == len(score), 'W1 one-token scoring evidence differs from the serial JSON evidence')
        lines += [f'W1 {fixtures.relative(fixtures.W1_DATASET)} sha256 {fixtures.W1_DATASET_SHA256}: '
                  f'{len(dataset["cases"])} cases, {fixtures.W1_MODEL_ROUTED_CASES} unresolved by the code precheck, '
                  f'{len(score)} requests per pass',
                  f'W1 reference {reference["path"]} sha256 {reference["sha256"]}: trial-1 labels '
                  f'kev {sum(k[0] == "kev" for k in reference["labels"])}, qwen_json {sum(k[0] == "qwen_json" for k in reference["labels"])}; '
                  f'trial-2 disagreements {reference["later_trial_disagreements"]}',
                  f'W1 bodies byte-identical to serial run: kev {same["kev"]}/{len(score)}, qwen_json {same["qwen_json"]}/{len(score)}; '
                  f'qwen_score evidence text identical {evidence}/{len(score)} (token IDs need the live runtime)']
    if 'w2' in workloads:
        reference = inputs['w2_reference']
        built = {arm: fixtures.w2_jobs(arm, inputs['w2'], reference) for arm in ('kev', 'qwen_json')}
        fixtures.require(all(len(job.body) <= GUARD_MAX_BODY for job in built['kev']), 'Kev body exceeds guard limit')
        labelled = {arm: sum(all(v is not None for v in job.reference.values()) for job in jobs) for arm, jobs in built.items()}
        lines += [f'W2 eval/query-routing freeze verified (freeze.json sha256 {inputs["w2"]["freeze_sha256"]}, '
                  f'bound by execution.json); heldout.json sha256 {inputs["w2"]["heldout_sha256"]}: {fixtures.W2_CASES} cases',
                  f'W2 reference {reference["path"]} sha256 {reference["sha256"]}: normal-order raw selections '
                  f'kev {labelled["kev"]}/{fixtures.W2_CASES}, qwen_json {labelled["qwen_json"]}/{fixtures.W2_CASES}',
                  f'W2 bodies match request-manifest.json and the executed run: kev {len(built["kev"])}/{fixtures.W2_CASES}, '
                  f'qwen_json {len(built["qwen_json"])}/{fixtures.W2_CASES}']
    lines.append('Locks: ' + ', '.join(f'{fixtures.LOCKS[m].name} sha256 {inputs["lock_sha256"][m]}' for m in ('kev', 'qwen'))
                 + f'; runtime revision {metal.REVISION} matches scripts/metal.py')
    header = f'{"cell":<22}{"slots":>6}{"clients":>8}{"kvu":>5}{"n_ctx":>7}{"per-slot":>9}{"warmup":>8}{"measured":>9}{"questions":>10}'
    lines += ['', header]
    totals = {}
    for cell in cells:
        jobs = fixtures.jobs_for(cell, inputs)
        measured = len(jobs) * cell.measured_passes
        questions = measured * len(cell_questions(jobs))
        total = totals.setdefault(cell.model, {'cells': 0, 'warmup': 0, 'measured': 0, 'questions': 0})
        for key, value in (('cells', 1), ('warmup', len(jobs)), ('measured', measured), ('questions', questions)):
            total[key] += value
        lines.append(f'{cell.id:<22}{cell.slots:>6}{cell.concurrency:>8}{"yes" if cell.kv_unified else "no":>5}'
                     f'{cell.n_ctx_total:>7}{cell.n_ctx_per_slot:>9}{len(jobs):>8}{measured:>9}{questions:>10}')
    lines.append('')
    for model, t in totals.items():
        lines.append(f'{model}: {t["cells"]} cells, {t["warmup"] + t["measured"]} requests ({t["warmup"]} warmup, '
                     f'{t["measured"]} measured), {t["questions"]} measured questions; budget {BUDGET_SECONDS // 60} min')
    print('\n'.join(lines))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true', help='Verify inputs and print the plan offline; no inference')
    parser.add_argument('--runtime', choices=['llamacpp'], default='llamacpp',
                        help='Serving runtime; SGLang MLX and Kev MLX runners are not implemented')
    parser.add_argument('--model', choices=['kev', 'qwen'])
    parser.add_argument('--workload', nargs='+', choices=['w1', 'w2'], default=['w1', 'w2'],
                        help='Run both in one invocation so the per-model budget covers them')
    parser.add_argument('--cells', nargs='+', help='Cell IDs (comma or space separated); default every planned cell')
    parser.add_argument('--output', type=Path, help='New directory; default results/throughput/<ts>-llamacpp-<model>')
    args = parser.parse_args(argv)
    workloads = sorted(set(args.workload))
    ids = [part for value in (args.cells or []) for part in value.split(',') if part]
    if args.validate:
        try:
            return validate(args.model, workloads, ids)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'throughput --validate FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1
    if args.model is None:
        parser.error('--model is required for a run')
    try:
        cells = select_cells(args.model, workloads, ids)
    except ValueError as exc:
        parser.error(str(exc))
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('the llamacpp runner targets native Metal on Apple Silicon')

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
            print(f'Throughput run failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            return 1


if __name__ == '__main__':
    sys.exit(main())
