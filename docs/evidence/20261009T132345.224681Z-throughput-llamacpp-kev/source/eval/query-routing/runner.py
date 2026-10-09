#!/usr/bin/env python3
"""Execute the frozen bounded classification pilot; no service or sibling changes."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

import experiment as e
import runtime

sys.path.insert(0, str(e.REPO/'scripts'))
import evaluate

ARMS = ('keyword', 'keyword_bm25_default', 'keyword_bm25_development')
MODEL_ARMS = ('qwen_json', 'kev')


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    temporary.replace(path)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: '+key)
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError('nonfinite JSON number: '+value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)


def verified_execution(driver):
    design = e.verify_freeze()
    manifest = e.load('execution.json')
    e.require(manifest.get('design_freeze_sha256') == e.digest(e.ROOT/'freeze.json'), 'execution does not bind the reviewed design')
    required = {'runner.py', 'test_runner.py', 'runtime.py', 'test_runtime.py', '../../scripts/evaluate.py', '../../scripts/metal.py', '../../scripts/model.py', '../../scripts/compare_scoring.py', '../synthesis/context_preflight.py'}
    e.require(required <= set(manifest.get('files', {})), 'execution manifest omits required code')
    for name, expected in manifest['files'].items():
        path = (e.ROOT/name).resolve()
        e.require(path.is_relative_to(e.REPO) and e.digest(path) == expected, 'execution source changed: '+name)
    e.require(e.digest(driver) == design['driver_sha256'], 'driver differs from frozen development binary')
    return manifest


def request_for(case, arm, view, protocol, training):
    request = e.build_request(case, training, protocol, arm, view == 'reverse')
    body = e.encoded(request)
    expected = next(r for r in e.load('request-manifest.json')
                    if r['id'] == case['id'] and r['backend'] == arm and r['order'] == view)
    e.require(hashlib.sha256(body).hexdigest() == expected['sha256'] and len(body) == expected['bytes'], 'request differs from frozen bytes')
    return request, body


def decode_response(response, arm, request, protocol):
    e.require(isinstance(response, dict), 'response must be an object')
    if arm == 'qwen_json':
        choices = response.get('choices')
        e.require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and choices[0].get('finish_reason') == 'stop', 'expected one normally finished JSON answer')
        message = choices[0].get('message')
        e.require(isinstance(message, dict) and isinstance(message.get('content'), str), 'expected a textual JSON message')
        selection = strict_json(message['content'])
    else:
        answers = response.get('answers')
        e.require(isinstance(answers, dict) and set(answers) == set(e.FIELDS), 'missing or extra native answers')
        selection = {}
        for key in e.FIELDS:
            validated = evaluate.validate_native({'answers': {'route': answers[key]}}, list(request['questions'][key]['criteria']))
            selection[key] = validated['choice']
    e.require(isinstance(selection, dict) and set(selection) == set(e.FIELDS) and all(isinstance(v, str) for v in selection.values()), 'expected exactly three string choices')
    return selection, e.decode_selection(selection, protocol)


def call_model(url, request, body, arm, protocol, timeout, record=None):
    """Send the manifest-verified byte string exactly once; retain every body."""
    if record is None:
        record = {}
    record.update(request=request, request_sha256=hashlib.sha256(body).hexdigest(), status='error', options=None, model_requests=1, decisions_requested=3)
    started = time.monotonic()
    try:
        wire = urllib.request.Request(url, body, {'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(wire, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(evaluate.MAX_RESPONSE_BYTES+1)
            record['http_status'] = response.status
        record['http_ms'] = (time.monotonic()-started)*1000
        record.update(response_bytes_base64=base64.b64encode(raw).decode(), response_bytes_read=len(raw),
                      response_sha256=hashlib.sha256(raw).hexdigest(), response_truncated=len(raw) > evaluate.MAX_RESPONSE_BYTES)
        if record['http_status'] == 200:
            record['status'] = 'invalid'
        e.require(len(raw) <= evaluate.MAX_RESPONSE_BYTES, 'response exceeded one MiB')
        record['response_body'] = raw.decode('utf-8')
        if record['http_status'] != 200:
            record['error'] = 'HTTP '+str(record['http_status'])
            return record
        record['status'] = 'invalid'
        response = strict_json(record['response_body'])
        record['response'] = response
        record['selection'], record['options'] = decode_response(response, arm, request, protocol)
        record['status'] = 'ok'
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
    except BaseException as exc:
        record.update(status='interrupted', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        record.setdefault('http_ms', (time.monotonic()-started)*1000)
        record['end_to_end_ms'] = (time.monotonic()-started)*1000
    return record


def pending(cases, arms, views):
    return [{'id': c['id'], 'arm': arm, 'view': view, 'status': 'not_run', 'options': None}
            for arm in arms for view in views for c in (cases if view != 'reverse' else list(reversed(cases)))]


def operation(options):
    """No precedence that could hide conflicting native flags."""
    opts = e.normalize(options)
    if not opts:
        return 'no_override'
    labels = []
    if opts.get('use_embeddings') is True:
        labels.append('similarity')
    if opts.get('path_intent') is True:
        predicates = opts.get('path_predicates', [])
        labels.append('zone' if predicates == ['located_in'] else 'path' if not predicates else None)
    if opts.get('aggregation_type') in ('count', 'avg', 'sum', 'min', 'max'):
        labels.append(opts['aggregation_type'])
    return labels[0] if len(labels) == 1 else None


def grade(rows, cases):
    gold = {c['id']: c['gold'] for c in cases}
    groups = {}
    for arm, view in sorted({(r['arm'], r['view']) for r in rows}):
        selected = [r for r in rows if r['arm'] == arm and r['view'] == view]
        valid = [r for r in selected if r['status'] == 'ok']
        bound = [r for r in selected if gold[r['id']]['node'] != 'none' or gold[r['id']]['field'] != 'none']
        def binding_ok(row):
            if row['status'] != 'ok':
                return False
            g, opts = gold[row['id']], e.normalize(row['options'])
            return opts.get('path_start_node', 'none') == g['node'] and opts.get('aggregation_field', 'none') == g['field']
        latency = [r['end_to_end_ms'] for r in selected if 'end_to_end_ms' in r]
        groups[arm+'/'+view] = {
            'total': len(selected), 'valid': len(valid), 'status_counts': dict(Counter(r['status'] for r in selected)),
            'exact': sum(e.exact(r['options'], gold[r['id']]['options']) for r in valid),
            'operation_correct': sum(operation(r['options']) == gold[r['id']]['operation'] for r in valid),
            'bound_cases': len(bound), 'bindings_correct': sum(binding_ok(r) for r in bound),
            'empty_options': sum(not e.normalize(r['options']) for r in valid),
            'incomplete_path_bindings': [r['id'] for r in valid if r['options'].get('path_intent') is True and not r['options'].get('path_start_node')],
            'expected_partial_path_correct': [r['id'] for r in valid if gold[r['id']]['operation'] == 'path' and gold[r['id']]['node'] == 'none' and e.exact(r['options'], gold[r['id']]['options'])],
            'unexpected_option_cases': [r['id'] for r in valid if set(e.normalize(r['options'])) - set(gold[r['id']]['options'])],
            'median_end_to_end_ms': __import__('statistics').median(latency) if latency else None,
            'p95_end_to_end_ms': evaluate.percentile(latency, .95),
            'model_requests': sum(r.get('model_requests', 0) for r in selected),
            'correct_ids': [r['id'] for r in valid if e.exact(r['options'], gold[r['id']]['options'])],
        }
    return groups


def driver_job(driver, cases, training, arm, threshold, directory):
    directory.mkdir()
    payload = {'arm': 'keyword' if arm == 'keyword' else 'keyword_bm25', 'threshold': threshold, 'examples': training,
               'queries': [{'id': c['id'], 'text': c['input']['query']} for c in cases]}
    inp, out = directory/'input.json', directory/'output.jsonl'
    inp.write_bytes(e.encoded(payload))
    started = time.monotonic()
    metadata = {}
    try:
        completed = subprocess.run([str(driver), '--input', str(inp), '--output', str(out), '--timeout', '20s'], capture_output=True, text=True, timeout=25)
        metadata.update(exit_code=completed.returncode, stderr=completed.stderr)
        e.require(completed.returncode == 0, 'code driver failed')
        records = [strict_json(line) for line in out.read_text().splitlines()]
        e.require(len(records) == len(cases)+1 and records[0]['kind'] == 'provenance', 'driver omitted records')
        e.require([r['id'] for r in records[1:]] == [c['id'] for c in cases], 'driver order/ID mismatch')
        e.require(all(r['input_sha256'] == e.digest(inp) for r in records), 'driver evidence input mismatch')
        return records
    finally:
        metadata['process_wall_ms'] = (time.monotonic()-started)*1000
        save(directory/'process.json', metadata)


def run_code(driver, output, manifest):
    protocol, training = e.load('protocol.json'), e.load('training.json')
    cases = e.load('heldout.json')['cases']
    result = {'kind': 'actual-code-heldout', 'started_at': now(), 'execution': manifest,
              'timing_scope': 'Host Darwin/ARM64 code. Primary end_to_end_ms is process wall including constructor and disk persistence; no matched Linux platform claim.',
              'rows': pending(cases, ARMS, ('primary',)) + pending(cases, ARMS[1:], ('normal', 'reverse'))}
    result['status'] = 'running'
    output.mkdir(parents=True, exist_ok=False)
    def persist():
        result['summary'] = grade(result['rows'], cases)
        save(output/'result.json', result)
    persist()
    thresholds = {'keyword': .7, 'keyword_bm25_default': .7, 'keyword_bm25_development': e.load('development-selection.json')['selected_threshold']}
    try:
        for arm in ARMS:
            for case in cases:
                directory = output/(arm+'-primary-'+case['id'])
                row = next(r for r in result['rows'] if r['arm'] == arm and r['view'] == 'primary' and r['id'] == case['id'])
                row['status'] = 'error'
                records = driver_job(driver, [case], training, arm, thresholds[arm], directory)
                native = records[1]
                row.update(status=native['status'], options=native['classification']['Options'], native=native,
                           end_to_end_ms=json.loads((directory/'process.json').read_text())['process_wall_ms'])
                persist()
        for arm in ARMS[1:]:
            for view in ('normal', 'reverse'):
                selected = cases if view == 'normal' else list(reversed(cases))
                for row in result['rows']:
                    if row['arm'] == arm and row['view'] == view:
                        row['status'] = 'error'
                records = driver_job(driver, selected, training, arm, thresholds[arm], output/(arm+'-'+view))
                for native in records[1:]:
                    row = next(r for r in result['rows'] if r['arm'] == arm and r['view'] == view and r['id'] == native['id'])
                    row.update(status=native['status'], options=native['classification']['Options'], native=native)
                persist()
        result['status'] = 'complete'
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finished_at'] = now()
        persist()


def run_hardware(hardware, output, manifest):
    protocol, training = e.load('protocol.json'), e.load('training.json')
    cases = e.load('heldout.json')['cases']
    warmup = e.load('development.json')['cases'][0]
    profile = manifest['profile']
    result = {'kind': 'bounded-classifier-model-pilot', 'hardware': hardware, 'started_at': now(),
              'execution': manifest, 'status': 'preflight', 'runtimes': [], 'warmups': [],
              'rows': pending(cases, MODEL_ARMS, ('normal', 'reverse'))}
    output.mkdir(parents=True, exist_ok=False)
    def persist():
        result['summary'] = grade(result['rows'], cases)
        save(output/'result.json', result)
    persist()
    locks = {arm: json.loads((e.REPO/('models.lock.json' if arm == 'kev' else 'models.baseline.lock.json')).read_text()) for arm in MODEL_ARMS}
    try:
        # Both complete request families must fit before either model generates.
        for arm in MODEL_ARMS:
            directory = output/('preflight-'+arm)
            descriptor = None
            checks = []
            try:
                descriptor = runtime.start(hardware, arm, locks[arm], directory, profile)
                result['runtimes'].append(descriptor.metadata)
                for view in ('normal', 'reverse'):
                    for case in cases + ([warmup] if view == 'normal' else []):
                        request, body = request_for(case, arm, view, protocol, training)
                        check = runtime.preflight(request, arm, descriptor.base_url, context=profile['context'], batch=profile['batch'])
                        checks.append({'id': case['id'], 'view': view, 'request_sha256': hashlib.sha256(body).hexdigest(), 'check': check})
                        save(directory/'preflight.json', checks)
            except BaseException as exc:
                if directory.is_dir():
                    save(directory/'preflight-failure.json', {'error': f'{type(exc).__name__}: {exc}', 'record': getattr(exc, 'record', None)})
                raise
            finally:
                if descriptor is not None:
                    runtime.stop(descriptor)
                persist()
            print(f'{hardware}/{arm}: all request contexts fit; no inference yet', flush=True)
        result['status'] = 'measuring'
        for arm in MODEL_ARMS:
            descriptor = None
            directory = output/('inference-'+arm)
            try:
                descriptor = runtime.start(hardware, arm, locks[arm], directory, profile)
                result['runtimes'].append(descriptor.metadata)
                request, body = request_for(warmup, arm, 'normal', protocol, training)
                record = {'arm': arm, 'id': warmup['id'], 'excluded_from_metrics': True}
                result['warmups'].append(record)
                call_model(descriptor.inference_url, request, body, arm, protocol, profile['request_timeout_seconds'], record)
                persist()
                e.require(record.get('http_status') == 200, 'warmup transport failed; no silent retry')
                for row in [r for r in result['rows'] if r['arm'] == arm]:
                    case = next(c for c in cases if c['id'] == row['id'])
                    request, body = request_for(case, arm, row['view'], protocol, training)
                    call_model(descriptor.inference_url, request, body, arm, protocol, profile['request_timeout_seconds'], row)
                    persist()
                    print(f'{hardware}/{arm}/{row["view"]}/{row["id"]}: {row["status"]} {row["http_ms"]:.0f}ms', flush=True)
            finally:
                if descriptor is not None:
                    runtime.stop(descriptor)
                persist()
        result['status'] = 'complete'
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        result['finished_at'] = now()
        persist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('code', 'models', 'validate'), required=True)
    parser.add_argument('--driver', type=Path, default=Path('/tmp/semselect-query-routing-driver'))
    parser.add_argument('--hardware', choices=('metal', 'cpu-docker'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    manifest = verified_execution(args.driver)
    if args.stage == 'validate':
        print(json.dumps({'execution_verified': True, 'inference_run': False}))
        return
    e.require(args.output is not None, 'execution needs a new output directory')
    if args.stage == 'models':
        e.require(args.hardware is not None, 'models need hardware')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    with (e.REPO/'.native/operation.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.stage == 'code':
            run_code(args.driver.resolve(), args.output.resolve(), manifest)
        else:
            run_hardware(args.hardware, args.output.resolve(), manifest)


if __name__ == '__main__':
    main()
