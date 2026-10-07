#!/usr/bin/env python3
"""Frozen staged evaluation. Endpoints are explicitly provisioned outside this runner.

No command downloads weights, starts services, reads a held-out result for tuning,
or changes production configuration. Every learned call uses a separate bounded
HTTP process. A run directory is an append-only evidence bundle, never reused.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import shutil
import sys
import time

import adapters
import baselines
import evidence as e
import scoring as s
import resources as telemetry
from transport import encoded, strict_json, loopback

ARMS = ('code', 'embeddings', 'gliclass', 'deberta', 'qwen')
MODELS = ('gliclass', 'deberta', 'qwen')


def now():
    return datetime.now(timezone.utc).isoformat()


def protocol():
    return e.read(e.ROOT/'protocol.json')


def dataset(split):
    return e.read(e.ROOT/'fixtures'/f'{split}.json')['cases']


def fixture_manifest():
    return e.read(e.ROOT/'fixtures'/'manifest.json')


def selected_ids(kind):
    manifest = fixture_manifest()
    return manifest[kind]['case_ids']


def request(case, arm, variant=0, view='primary'):
    envelope = {'arm': arm, 'payload': adapters.embedding_request(case['input']['query'])} if arm == 'embeddings' else adapters.build_request(case['input']['query'], arm, variant=variant, view=view, protocol=protocol())
    return {'id': case['id'], 'arm': arm, 'variant': variant, 'view': view, 'request': envelope,
            'payload_sha256': hashlib.sha256(encoded(envelope['payload'])).hexdigest()}


def prepare(output, driver=None):
    """Mechanically freeze held-out inputs without displaying their text or labels."""
    if output.resolve().is_relative_to(e.ROOT):
        raise ValueError('results must live outside the frozen evaluation source directory')
    output.mkdir(parents=True, exist_ok=False)
    # The fixtures validator owns family separation and label/readiness consistency.
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(e.ROOT), '-p', 'test_fixtures.py'], check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    inputs = []
    sensitivity_ids = set(selected_ids('sensitivity'))
    for split in ('development', 'heldout'):
        for case in dataset(split):
            for arm in (*MODELS, 'embeddings'):
                for variant in range(1 if arm == 'embeddings' else 2):
                    views = ('primary', 'reverse', 'remap') if arm != 'embeddings' and split == 'heldout' and case['id'] in sensitivity_ids else ('primary',)
                    for view in views:
                        inputs.append({'split': split, **request(case, arm, variant, view)})
    examples = e.read(e.REPO/'eval/query-routing/training.json')['examples']
    inputs.extend({'split': 'examples', **request({'id': f'example-{i}', 'input': {'query': ex['query']}}, 'embeddings')} for i, ex in enumerate(examples))
    e.save_new(output/'requests.json', inputs)
    for arm in (*MODELS, 'embeddings'):
        e.save_new(output/f'requests-{arm}.json', [row['request'] for row in inputs if row['arm'] == arm])
    driver_hash = e.digest(driver) if driver else None
    target = code_target()
    sources = e.source_hashes()
    for relative in sources:
        target_path = output/'source'/relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(e.REPO/relative, target_path)
    e.save_new(output/'freeze.json', {'version': 1, 'created_at': now(), 'sources': sources,
                                    'driver_sha256': driver_hash, 'code_target': target,
                                    'requests_sha256': e.digest(output/'requests.json'),
                                    'heldout_access': 'Mechanical payload generation only. Tuners must not inspect heldout text, gold or outputs before selection.',
                                    'review_status': 'author-checked; independent execution review must be recorded before primary'})
    return {'prepared': str(output), 'request_count': len(inputs), 'inference_run': False}


def code_target():
    pins = e.read(e.REPO/'eval/query-routing/driver/source-pins.json')
    sibling = e.REPO.parent/'semstreams'
    compared = []
    for file in pins['files']:
        path = sibling/file['path']
        actual = e.digest(path) if path.exists() else None
        compared.append({'path': file['path'], 'pinned_sha256': file['sha256'], 'checkout_sha256': actual, 'matches': actual == file['sha256']})
    return {'version': pins['version'], 'files': compared, 'matches_inspected_checkout': all(r['matches'] for r in compared), 'claim': 'historical pinned algorithm; not a current production claim'}


def verify_run(run):
    frozen = e.read(run/'freeze.json')
    e.verify_sources(frozen['sources'])
    if e.digest(run/'requests.json') != frozen['requests_sha256']:
        raise ValueError('frozen payload manifest changed')
    return frozen


def stage_rows(run, stage, arm):
    path = run/stage/arm/'rows.json'
    completion = path.parent/'completion.json'
    if path.exists() and completion.exists():
        record = e.read(completion)
        if 'rows_sha256' in record and record['rows_sha256'] != e.digest(path):
            raise ValueError('stage rows differ from completion record')
    return e.read(path) if path.exists() else []


def stage_complete(run, stage, arm):
    return (run/stage/arm/'completion.json').exists()


def barrier(run, stage, arm):
    verify_run(run)
    if arm not in ARMS:
        raise ValueError('unknown arm')
    order = ARMS if stage != 'sensitivity' else MODELS
    if arm not in order:
        raise ValueError('this stage does not apply to arm')
    for earlier in order[:order.index(arm)]:
        if not stage_complete(run, stage, earlier):
            raise ValueError('stage order requires completion or explicit unavailability: '+earlier)
    if stage != 'development':
        verify_selection(run)
    if stage == 'sensitivity' and not all(stage_complete(run, 'primary', a) for a in ARMS):
        raise ValueError('all primary arms must close before sensitivities')


def verify_selection(run):
    freeze = e.read(run/'selection-freeze.json')
    if e.digest(run/'selection.json') != freeze['selection_sha256']:
        raise ValueError('development selection changed')
    for arm in ARMS:
        if not stage_complete(run, 'development', arm):
            raise ValueError('all development stages must close before heldout')
        if e.digest(run/'development'/arm/'rows.json') != freeze['development_rows'][arm]:
            raise ValueError('development evidence changed')
        environment = run/'development'/arm/'environment.json'
        actual = e.digest(environment) if environment.exists() else None
        if actual != freeze['development_environments'][arm]:
            raise ValueError('development environment identity changed')
    review = e.read(run/'review.json')
    if review.get('payloads_verified') is not True or review.get('scoring_verified') is not True or review.get('fixture_labels_verified') is not True:
        raise ValueError('pre-execution payload/scoring/label review is incomplete')
    if review.get('freeze_sha256') != e.digest(run/'freeze.json') or not review.get('reviewer') or review.get('independent') is not True:
        raise ValueError('review must bind preparation freeze and disclose independence')
    return e.read(run/'selection.json')


def previous_stop(run, arm):
    for stage in ('development', 'primary', 'sensitivity', 'load'):
        path = run/stage/arm/'completion.json'
        if path.exists() and e.read(path).get('stop_reason'):
            return e.read(path)['stop_reason']
    return None


def frozen_environment(run, arm, environment):
    path = run/'development'/arm/'environment.json'
    if path.exists():
        previous = e.read(path)
        keys = ('model_revision', 'artifacts', 'dependencies', 'runtime_sha256', 'architecture', 'precision', 'container_digest', 'runtime_revision', 'threads', 'query_batch_size', 'pair_batch_size')
        if any(environment.get(key) != previous.get(key) for key in keys):
            raise ValueError('runtime identity changed after development freeze')


def configurations(arm):
    p = protocol()
    if arm == 'code':
        return [{'arm': 'keyword', 'threshold': .7}, {'arm': 'keyword_bm25_default', 'threshold': .7}] + [{'arm': 'keyword_bm25_tuned', 'threshold': t} for t in p['grids']['bm25']] + [{'arm': 'improved_rules', 'threshold': .7}]
    if arm == 'qwen':
        return [{'arm': arm, 'variant': v} for v in range(2)]
    grid = p['grids']['cosine'] if arm == 'embeddings' else p['grids']['score']
    variants = range(1) if arm == 'embeddings' else range(2)
    return [{'arm': arm, 'variant': v, 'unfiltered': True} for v in variants] + [{'arm': arm, 'variant': v, 'threshold': t, 'margin': m} for v in variants for t in grid for m in p['grids']['margin']]


def select(run):
    verify_run(run)
    if not all(stage_complete(run, 'development', arm) for arm in ARMS):
        raise ValueError('development must finish for every arm before selection')
    cases = dataset('development')
    choices = {}
    code_rows = stage_rows(run, 'development', 'code')
    code_configs = []
    for config in configurations('code'):
        rows = [r for r in code_rows if r.get('configuration') == config and r.get('phase') == 'measured']
        code_configs.append({**config, 'rows': rows})
    # Tune BM25 on development complete output then operation; threshold order is frozen ascending.
    tuned = s.select_code(cases, [c for c in code_configs if c['arm'] == 'keyword_bm25_tuned'])
    finalists = [c for c in code_configs if c['arm'] != 'keyword_bm25_tuned' or c['threshold'] == tuned['threshold']]
    choices['code'] = s.select_code(cases, finalists)
    choices['bm25'] = tuned['threshold']
    for arm in ARMS[1:]:
        rows = stage_rows(run, 'development', arm)
        configs = [{**c, 'rows': [r for r in rows if r.get('phase') == 'measured' and r.get('variant', 0) == c.get('variant', 0)]} for c in configurations(arm)]
        choices[arm] = s.select_model(cases, configs)
    choices['nominee'] = s.nominate(choices)
    choices['reuse_comparator'] = s.select_reuse(choices['code'], choices['embeddings'])
    choices['selected_at'] = now()
    choices['scope'] = 'development-only'
    e.save_new(run/'selection.json', choices)
    e.save_new(run/'selection-freeze.json', {'selection_sha256': e.digest(run/'selection.json'), 'development_rows': {a: e.digest(run/'development'/a/'rows.json') for a in ARMS}, 'development_environments': {a: e.digest(run/'development'/a/'environment.json') if (run/'development'/a/'environment.json').exists() else None for a in ARMS}})
    return {k: v for k, v in choices.items() if k in ('nominee', 'reuse_comparator', 'selected_at')}


def http_call(url, req, arm, timeout):
    loopback(url)
    started = time.monotonic()
    result = {'status': 'error'}
    try:
        process = subprocess.run([sys.executable, str(e.ROOT/'transport.py')], input=encoded({'url': url, 'payload': req['request']['payload'], 'timeout': timeout}), capture_output=True, timeout=timeout+1)
        if process.returncode:
            raise ValueError('transport exited '+str(process.returncode)+': '+process.stderr.decode(errors='replace')[:1024])
        result = strict_json(process.stdout)
        if arm in ('gliclass', 'deberta') and result.get('http_status') == 400:
            result['fatal'] = True
        if result['status'] == 'received':
            validation_started = time.monotonic()
            try:
                result.update(adapters.decode_response(result['response'], arm, req['request']))
            except (ValueError, KeyError, TypeError) as error:
                result.update(status='error', error=f'{type(error).__name__}: {error}', fatal=arm in ('gliclass', 'deberta'))
            finally:
                result['http_ms'] += (time.monotonic()-validation_started)*1000
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError) as exc:
        result.update(status='error', error=f'{type(exc).__name__}: {exc}')
    result['elapsed_ms'] = (time.monotonic()-started)*1000
    return result


def budget_remaining(run, arm):
    cap = 300 if arm == 'code' else 600 if arm == 'embeddings' else 1800
    spent = 0
    for stage in ('cpu-probe', 'development', 'primary', 'sensitivity', 'load'):
        rows = stage_rows(run, stage, arm)
        spent += sum(r.get('elapsed_ms', 0) for r in rows if s.valid_time(r.get('elapsed_ms')))/1000
        index_path = run/stage/arm/'example-index.json'
        if index_path.exists():
            spent += e.read(index_path).get('setup_ms', 0)/1000
    return max(0, cap-spent)


def cpu_probe(run, url, environment, tokens):
    barrier(run, 'development', 'qwen')
    if stage_complete(run, 'development', 'qwen'):
        raise ValueError('CPU feasibility must precede Qwen development selection')
    env = e.verify_environment(e.read(environment), 'qwen', cpu_probe=True)
    if env.get('endpoint') != url or observe(env).get('verified') is not True:
        raise ValueError('CPU probe endpoint/container failed live audit')
    cases = [c for c in dataset('development') if c['id'] in selected_ids('feasibility')]
    jobs = [('warmup', c) for c in cases[:3]] + [('feasibility', c) for c in cases]
    requests = [request(c, 'qwen') for _, c in jobs]
    e.verify_tokens(e.read(tokens), requests, 'qwen', env)
    output = run/'cpu-probe'/'qwen'
    output.mkdir(parents=True, exist_ok=False)
    e.save_new(output/'environment.json', env)
    rows = [{'id': c['id'], 'phase': phase, 'status': 'unattempted'} for phase, c in jobs]
    e.save_new(output/'planned.json', rows)
    deadline = time.monotonic()+min(600, budget_remaining(run, 'qwen'))
    stop = None
    failures = 0
    try:
        with (output/'journal.jsonl').open('x') as journal:
            for row, req in zip(rows, requests):
                remaining = deadline-time.monotonic()
                if stop or remaining <= 0:
                    stop = stop or 'CPU probe ten-minute budget exhausted'
                    row['reason'] = stop
                    continue
                row.update(http_call(url, req, 'qwen', min(60, remaining)))
                failures = failures+1 if row['status'] == 'error' else 0
                if failures >= 3:
                    stop = 'three consecutive CPU probe failures'
                journal.write(json.dumps(row, allow_nan=False)+'\n')
                journal.flush()
    finally:
        e.save_new(output/'rows.json', rows)
        e.save_new(output/'resources-final.json', {**env, **observe(env), 'rows_sha256': e.digest(output/'rows.json')})
        e.save_new(output/'completion.json', {'planned_cases': 12, 'warmups': 3, 'stop_reason': stop, 'rows_sha256': e.digest(output/'rows.json'), 'scope': 'development CPU feasibility only; never full-cohort CPU quality or speed ratio'})
    return e.read(output/'completion.json')


def make_jobs(run, stage, arm):
    split = 'development' if stage == 'development' else 'heldout'
    cases = dataset(split)
    if stage == 'sensitivity':
        cases = [c for c in cases if c['id'] in selected_ids('sensitivity')]
    random.Random(protocol()['seed']).shuffle(cases)
    selected = e.read(run/'selection.json') if stage != 'development' else None
    if arm == 'code':
        configs = configurations('code') if not selected else [c for c in configurations('code') if c['arm'] != 'keyword_bm25_tuned' or c['threshold'] == selected['bm25']]
        return [{'id': c['id'], 'case': c, 'configuration': config, 'view': 'primary', 'phase': 'measured'} for config in configs for c in cases]
    variants = range(2) if stage == 'development' and arm != 'embeddings' else [selected[arm]['variant'] if selected else 0]
    views = ('reverse', 'remap') if stage == 'sensitivity' else ('primary',)
    jobs = []
    # Warmups never enter grading, but their time and failures stay in the ledger.
    warm = dataset('development')[0]
    for i in range(3):
        jobs.append({'id': f'warmup-{i}', 'case': warm, 'variant': variants[0], 'view': 'primary', 'phase': 'warmup'})
    if stage == 'development':
        for case in dataset('development'):
            if case['id'] in selected_ids('feasibility'):
                jobs.append({'id': case['id'], 'case': case, 'variant': 0, 'view': 'primary', 'phase': 'feasibility'})
    jobs.extend({'id': c['id'], 'case': c, 'variant': v, 'view': view, 'phase': 'measured'} for v in variants for view in views for c in cases)
    return jobs


def run_stage(run, stage, arm, url=None, environment=None, tokens=None, driver=None, unavailable=None):
    barrier(run, stage, arm)
    jobs = make_jobs(run, stage, arm)
    remaining = budget_remaining(run, arm)
    destination = run/stage/arm
    rows = [{k: v for k, v in job.items() if k != 'case'} | {'status': 'unattempted'} for job in jobs]
    stop = previous_stop(run, arm) or unavailable
    resources = None
    if arm == 'code' and driver and not stop:
        frozen = e.read(run/'freeze.json')
        if frozen.get('driver_sha256') != e.digest(driver):
            raise ValueError('code driver differs from frozen binary; prepare with --driver')
        if not frozen['code_target']['matches_inspected_checkout']:
            raise ValueError('current caller code differs or is unavailable; resolve comparison target before execution')
    if not stop and arm != 'code':
        if not url or not environment:
            raise ValueError('learned arm requires loopback URL and verified environment record')
        loopback(url)
        resources = e.verify_environment(e.read(environment), arm)
        frozen_environment(run, arm, resources)
        if resources.get('endpoint') != url:
            raise ValueError('request URL differs from recorded runtime endpoint')
        if arm in ('gliclass', 'deberta', 'embeddings'):
            observation = observe(resources)
            if observation.get('verified') is not True:
                raise ValueError('live CPU container failed pre-execution audit: '+str(observation))
        if arm != 'code':
            inputs = [request(j['case'], arm, j['variant'], j['view']) for j in jobs]
            if arm == 'embeddings':
                examples = e.read(e.REPO/'eval/query-routing/training.json')['examples']
                inputs.extend(request({'id': f'example-{i}', 'input': {'query': ex['query']}}, arm) for i, ex in enumerate(examples))
            if not tokens:
                raise ValueError('full-input token manifest required')
            token_records = e.read(tokens)
            e.verify_tokens(token_records, inputs, arm, resources)
            frozen_inputs = e.read(run/'requests.json')
            frozen = {(r['id'], r['arm'], r['variant'], r['view']): r for r in frozen_inputs}
            for req in inputs:
                original = frozen.get((req['id'], arm, req['variant'], req['view']))
                if not original or original['request'] != req['request'] or original['payload_sha256'] != req['payload_sha256']:
                    raise ValueError('request differs from frozen payload')
    destination.mkdir(parents=True, exist_ok=False)
    e.save_new(destination/'planned.json', rows)
    if resources:
        e.save_new(destination/'environment.json', resources)
        e.save_new(destination/'tokens.json', token_records)
    deadline = time.monotonic()+remaining
    failures = 0
    started = now()
    index = None
    active_row = None
    call_started = None
    try:
        if arm == 'embeddings' and not stop:
            index = embedding_index(url, destination, max(0, deadline-time.monotonic()), run)
        with (destination/'journal.jsonl').open('x') as journal:
            for job, row in zip(jobs, rows):
                if stop:
                    row['reason'] = stop
                elif time.monotonic() >= deadline:
                    stop = row['reason'] = 'inference budget exhausted'
                else:
                    timeout = min(30 if arm == 'qwen' else 10, deadline-time.monotonic())
                    active_row = row
                    call_started = time.monotonic()
                    row.update(status='error', attempted=True, started_at=now())
                    if arm == 'code':
                        config = job['configuration']
                        row.update(baselines.classify_code(job['case']['input']['query'], config['arm'], threshold=config['threshold'], driver=driver, timeout=timeout))
                    elif arm == 'embeddings':
                        row.update(embedding_call(url, job['case']['input']['query'], index, timeout))
                    else:
                        req = request(job['case'], arm, job['variant'], job['view'])
                        row.update(http_call(url, req, arm, timeout))
                    active_row = None
                    failures = failures+1 if row['status'] == 'error' else 0
                    if row['status'] == 'error' and resources and arm in ('gliclass', 'deberta', 'embeddings'):
                        observation = observe(resources)
                        if observation.get('oom'):
                            row['fatal'] = True
                            row['error'] = str(row.get('error', ''))+'; container OOM'
                    if row.get('fatal') or failures >= 3:
                        stop = 'fatal input/mapping failure' if row.get('fatal') else 'three consecutive runtime failures'
                journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
                journal.flush()
    except BaseException as error:
        stop = f'{type(error).__name__}: {error}'
        if active_row is not None:
            active_row.update(status='error', error='interrupted in flight: '+stop, elapsed_ms=(time.monotonic()-call_started)*1000)
        raise
    finally:
        e.save_new(destination/'rows.json', rows)
        if resources and arm in ('gliclass', 'deberta', 'embeddings'):
            final_resources = {**resources, **observe(resources), 'rows_sha256': e.digest(destination/'rows.json')}
            e.save_new(destination/'resources-final.json', final_resources)
            if final_resources.get('oom'):
                stop = 'container OOM'
        e.save_new(destination/'completion.json', {'started_at': started, 'finished_at': now(), 'arm': arm, 'stage': stage, 'stop_reason': stop, 'planned': len(rows), 'valid': sum(r['status'] in ('selected', 'deferred') for r in rows), 'inference_seconds': sum(r.get('elapsed_ms', 0) for r in rows)/1000, 'rows_sha256': e.digest(destination/'rows.json'), 'environment_verified': resources is not None, 'cleanup': 'externally managed; attach verified shutdown record after stopping owned endpoint'})
    return e.read(destination/'completion.json')


def observe(environment):
    try:
        return telemetry.capture(environment)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        return {'verified': False, 'resource_error': f'{type(error).__name__}: {error}'}


def embedding_index(url, destination, timeout, run=None):
    if timeout <= 0:
        raise TimeoutError('embedding budget exhausted before index setup')
    examples = e.read(e.REPO/'eval/query-routing/training.json')['examples']
    if run is not None:
        cached_path = run/'development/embeddings/example-index.json'
        if cached_path.exists() and cached_path.parent != destination:
            cached = e.read(cached_path)
            if cached.get('complete') is not True or cached.get('examples_sha256') != e.digest(e.REPO/'eval/query-routing/training.json') or len(cached['records']) != len(examples):
                raise ValueError('cached example index does not match frozen examples')
            vectors = [r['vector'] for r in cached['records']]
            for vector in vectors:
                adapters.cosine(vector, vector)
            e.save_new(destination/'example-index.json', {'complete': True, 'setup_ms': 0, 'reused_from': str(cached_path), 'source_sha256': e.digest(cached_path), 'example_count': len(examples)})
            return {'examples': examples, 'vectors': vectors}
    records, vectors = [], []
    started = time.monotonic()
    for example in examples:
        remaining = timeout-(time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('embedding index setup exceeded budget')
        result = embedding_http(url, example['query'], min(10, remaining))
        records.append(result)
        if result['status'] != 'received':
            e.save_new(destination/'example-index.json', {'records': records, 'complete': False, 'setup_ms': (time.monotonic()-started)*1000})
            raise ValueError('embedding index failed')
        vectors.append(result['vector'])
    e.save_new(destination/'example-index.json', {'records': records, 'complete': True, 'setup_ms': (time.monotonic()-started)*1000, 'examples_sha256': e.digest(e.REPO/'eval/query-routing/training.json')})
    return {'examples': examples, 'vectors': vectors}


def embedding_http(url, query, timeout):
    started = time.monotonic()
    result = {'status': 'error'}
    try:
        payload = adapters.embedding_request(query)
        process = subprocess.run([sys.executable, str(e.ROOT/'transport.py')], input=encoded({'url': url, 'payload': payload, 'timeout': timeout}), capture_output=True, timeout=timeout+1)
        if process.returncode:
            raise ValueError('embedding transport failed')
        result = strict_json(process.stdout)
        if result['status'] == 'received':
            result['vector'] = adapters.embed([query], url, 'Snowflake/snowflake-arctic-embed-s',
                                               transport=lambda *args: result['response'])[0]
    except (subprocess.TimeoutExpired, ValueError, OSError, KeyError, TypeError) as error:
        result.update(status='error', error=str(error))
    result['elapsed_ms'] = (time.monotonic()-started)*1000
    return result


def embedding_call(url, query, index, timeout):
    result = embedding_http(url, query, timeout)
    if result['status'] == 'received':
        start = time.monotonic()
        result.update(adapters.nearest_example(result['vector'], index['examples'], index['vectors']))
        comparison = (time.monotonic()-start)*1000
        result['http_ms'] += comparison
        result['elapsed_ms'] += comparison
    return result


def report(run):
    verify_run(run)
    selection = verify_selection(run)
    cases = dataset('heldout')
    metrics = {}
    code_rows = stage_rows(run, 'primary', 'code')
    for config in configurations('code'):
        if config['arm'] == 'keyword_bm25_tuned' and config['threshold'] != selection['bm25']:
            continue
        rows = [r for r in code_rows if r.get('configuration') == config and r.get('phase') == 'measured']
        metrics[config['arm']] = s.grade(cases, rows)
    for arm in ARMS[1:]:
        rows = [r for r in stage_rows(run, 'primary', arm) if r.get('phase') == 'measured']
        metrics[arm] = s.grade(cases, rows, selection[arm])
    name = selection['nominee']['arm']
    sensitivities = {}
    for view in ('reverse', 'remap'):
        subset = [c for c in cases if c['id'] in selected_ids('sensitivity')]
        rows = [r for r in stage_rows(run, 'sensitivity', name) if r.get('phase') == 'measured' and r.get('view') == view]
        sensitivities[view] = s.sensitivity(metrics[name], s.grade(subset, rows, selection[name]))
    env_path = run/'primary'/name/'resources-final.json'
    resources = e.read(env_path) if env_path.exists() else {}
    if resources and resources.get('rows_sha256') != e.digest(run/'primary'/name/'rows.json'):
        raise ValueError('resource finalization does not bind primary results')
    load_path = run/'load'/name/'completion.json'
    load_result = e.read(load_path) if load_path.exists() else None
    if resources:
        observations = []
        for stage in ('development', 'primary', 'sensitivity') + (('load',) if load_result else ()):
            resource_path = run/stage/name/'resources-final.json'
            observation = e.read(resource_path) if resource_path.exists() else {'verified': False}
            if observation.get('verified') and observation.get('rows_sha256') != e.digest(run/stage/name/'rows.json'):
                raise ValueError('resource finalization does not bind stage rows')
            observations.append(observation)
        resources = {**resources, 'verified': all(o.get('verified') is True for o in observations),
                     'peak_memory_bytes': max(o.get('peak_memory_bytes', 0) for o in observations),
                     'readiness_seconds': max(o.get('readiness_seconds', 0) for o in observations),
                     'oom': any(o.get('oom') for o in observations)}
    code = metrics[selection['code']['arm']]
    verdict = s.gates(metrics[name], code, metrics['embeddings'], metrics['qwen'], resources, sensitivities, load_result, protocol(), selection['nominee']['eligible'])
    tradeoffs = {}
    for arm in ARMS[1:]:
        rows = [r for r in stage_rows(run, 'primary', arm) if r.get('phase') == 'measured']
        tradeoffs[arm] = []
        for config in configurations(arm):
            if config.get('variant') != selection[arm]['variant']:
                continue
            m = s.grade(cases, rows, config, intervals=False)
            tradeoffs[arm].append({'configuration': config, 'accepted': m['accepted'], 'correct_accepted': m['accepted_correct'], 'wrong_accepted': m['wrong_accepted'], 'wrong_specialized': m['wrong_specialized'], 'diagnostic_only': True})
    return {'created_at': now(), 'nominee': name, 'selection': selection, 'metrics': metrics, 'sensitivities': sensitivities,
            'coverage_error_tradeoffs': tradeoffs,
            'qwen_cpu_probe': stage_rows(run, 'cpu-probe', 'qwen'),
            'paired': {arm: s.paired(metrics[name], m) for arm, m in metrics.items() if arm != name},
            'resources': resources, 'load': load_result, **verdict,
            'limitations': protocol()['limitations'], 'inference_claim': 'Only persisted completed requests establish measured behavior; unattempted rows stay in all-case denominators.'}


def load_check(run, url, environment, tokens):
    result = report(run)
    if any(not passed for gate, passed in result['checks'].items() if gate != 'small_load'):
        raise ValueError('load check requires every preceding advancement gate')
    arm = result['nominee']
    if previous_stop(run, arm):
        raise ValueError('nominee was stopped in an earlier stage')
    destination = run/'load'/arm
    destination.mkdir(parents=True, exist_ok=False)
    env = e.verify_environment(e.read(environment), arm)
    frozen_environment(run, arm, env)
    if env.get('endpoint') != url or observe(env).get('verified') is not True:
        raise ValueError('load endpoint/container failed pre-execution audit')
    selection = e.read(run/'selection.json')[arm]
    cases = dataset('heldout')
    rng = random.Random(protocol()['seed'])
    rng.shuffle(cases)
    jobs = [request(cases[i % len(cases)], arm, selection['variant']) for i in range(200)]
    e.verify_tokens(e.read(tokens), jobs, arm, env)
    rows = [{'id': j['id'], 'sequence': i, 'status': 'unattempted'} for i, j in enumerate(jobs)]
    e.save_new(destination/'planned.json', rows)
    started = time.monotonic()
    # Reserve two-request elapsed time against the per-model inference-work allowance.
    deadline = started + min(300, budget_remaining(run, arm)/2)
    def execute(pair):
        i, job = pair
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return rows[i] | {'reason': 'load budget exhausted'}
        return rows[i] | http_call(url, job, arm, min(10, remaining))
    stop = None
    failures = 0
    next_job = 0
    with ThreadPoolExecutor(max_workers=2) as pool, (destination/'journal.jsonl').open('x') as journal:
        pending = {}
        while pending or (next_job < len(jobs) and not stop):
            while len(pending) < 2 and next_job < len(jobs) and not stop:
                future = pool.submit(execute, (next_job, jobs[next_job]))
                pending[future] = next_job
                next_job += 1
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: pending[f]):
                i = pending.pop(future)
                rows[i] = future.result()
                failures = failures+1 if rows[i]['status'] == 'error' else 0
                if rows[i].get('fatal') or failures >= 3 or rows[i]['status'] == 'unattempted':
                    stop = 'fatal/runtime failure or load budget exhausted'
                journal.write(json.dumps(rows[i], allow_nan=False)+'\n')
                journal.flush()
    e.save_new(destination/'rows.json', rows)
    final_resources = {**env, **observe(env), 'rows_sha256': e.digest(destination/'rows.json')}
    e.save_new(destination/'resources-final.json', final_resources)
    if final_resources.get('oom'):
        stop = 'container OOM'
    times = [r['http_ms'] for r in rows if r['status'] in ('selected', 'deferred') and s.valid_time(r.get('http_ms'))]
    completion = {'planned': 200, 'valid': sum(r['status'] in ('selected', 'deferred') for r in rows), 'errors': sum(r['status'] == 'error' for r in rows), 'unattempted': sum(r['status'] == 'unattempted' for r in rows), 'concurrency': 2, 'elapsed_seconds': time.monotonic()-started, 'latency_samples': len(times), 'p95_ms': s.percentile(times, .95), 'stop_reason': stop, 'rows_sha256': e.digest(destination/'rows.json')}
    e.save_new(destination/'completion.json', completion)
    return completion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'select', 'report', 'load', 'probe-cpu'))
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--stage', choices=('development', 'primary', 'sensitivity'))
    parser.add_argument('--arm', choices=ARMS)
    parser.add_argument('--url')
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--tokens', type=Path)
    parser.add_argument('--driver', type=Path)
    parser.add_argument('--unavailable', help='Record a failed compatibility/setup attempt; retain all planned rows')
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.run, args.driver)
    elif args.command == 'select':
        result = select(args.run)
    elif args.command == 'report':
        result = report(args.run)
        output = args.run/'reports'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        output.mkdir(parents=True, exist_ok=False)
        e.save_new(output/'summary.json', result)
        (output/'verdict.md').write_text(render_report(result))
        print(json.dumps({k: result[k] for k in ('verdict', 'nominee', 'checks', 'failed')}, indent=2))
        return
    elif args.command == 'load':
        result = load_check(args.run, args.url, args.environment, args.tokens)
    elif args.command == 'probe-cpu':
        result = cpu_probe(args.run, args.url, args.environment, args.tokens)
    else:
        if not args.stage or not args.arm:
            parser.error('run requires --stage and --arm')
        result = run_stage(args.run, args.stage, args.arm, args.url, args.environment, args.tokens, args.driver, args.unavailable)
    print(json.dumps(result, indent=2, allow_nan=False))


def render_report(result):
    lines = ['# Specialist intent pilot', '', '**Verdict: '+result['verdict']+'**', '',
             'Development nominee: '+result['nominee']+'. Stratified authored pilot; aggregate accuracy is not production traffic accuracy.', '',
             '| Arm | Raw correct /120 | Correct accepted | Wrong accepted | Deferred | Errors | Unattempted | Complete correct | Executable correct | Native complete correct |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm, m in result['metrics'].items():
        lines.append('| '+arm+' | '+' | '.join(str(m[k]) for k in ('raw_correct', 'accepted_correct', 'wrong_accepted', 'deferred', 'errors', 'unattempted', 'complete_correct', 'executable_correct', 'native_complete_correct'))+' |')
    lines += ['', 'Native full-output counts apply only where native SearchOptions were available; missing native output is not a measured zero-quality model result.', '', '## Screening gates', '']
    lines += ['- '+name+': '+('pass' if passed else 'not met') for name, passed in result['checks'].items()]
    lines += ['', 'Per-intent recall, no_override strata, coverage, confusion matrices, native scores, paired corrections/regressions and clustered uncertainty are preserved in summary.json and the immutable raw stage journals.', '', '## Limits', '']
    lines += ['- '+text for text in result['limitations']]
    lines += ['', 'No classification authorizes execution. A prototype recommendation requires every gate, including the nominee-only load check. Incomplete runtime, missing telemetry or unresolved fixture defects cannot establish a win.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    main()
