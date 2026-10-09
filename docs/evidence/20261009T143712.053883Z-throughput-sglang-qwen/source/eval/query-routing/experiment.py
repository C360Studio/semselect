#!/usr/bin/env python3
"""Offline preparation and development selection; never runs held-out inference."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
FIELDS = ('operation', 'node', 'field')
REQUIRED_FROZEN = {
    'protocol.json', 'training.json', 'development.json', 'heldout.json',
    'experiment.py', 'test_experiment.py', 'request-manifest.json',
    'development-selection.json', 'development-evidence.json', 'review.md',
    'driver/main.go', 'driver/main_test.go', 'driver/go.mod', 'driver/go.sum',
    'driver/source-pins.json', '../../models.lock.json', '../../models.baseline.lock.json',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(name):
    return json.loads((ROOT / name).read_text())


def decode_selection(selection, protocol):
    require(isinstance(selection, dict) and set(selection) == set(FIELDS), 'selection needs exactly operation/node/field')
    op, node, field = (selection[k] for k in FIELDS)
    require(op in protocol['operations'] and node in protocol['nodes'] and field in protocol['fields'], 'unknown choice')
    require(op in ('path', 'zone') or node == 'none', 'node is only valid for path or zone')
    require(op in ('avg', 'sum', 'min', 'max') or field == 'none', 'field is only valid for metric aggregation')
    if op == 'no_override':
        return {}
    if op == 'similarity':
        return {'use_embeddings': True}
    if op in ('path', 'zone'):
        require(op != 'zone' or node in ('zone-A', 'area-north'), 'zone must bind a known location')
        result = {'path_intent': True}
        if node != 'none':
            result['path_start_node'] = node
        if op == 'zone':
            result['path_predicates'] = ['located_in']
        return result
    require(op == 'count' or field != 'none', 'metric operation needs its field')
    result = {'aggregation_type': op}
    if field != 'none':
        result['aggregation_field'] = field
    return result


def normalize(options):
    require(isinstance(options, dict), 'options must be an object')
    # Numeric zero is not a false boolean; preserve it and every unexpected field.
    return {k: v for k, v in options.items()
            if v is not None and v is not False and v != '' and v != []}


def exact(options, expected):
    # Typed JSON comparison avoids Python's True == 1 equality.
    return json.dumps(normalize(options), sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


def driver_input(case, training, arm='keyword_bm25', threshold=0.7):
    return {'arm': arm, 'threshold': threshold, 'examples': training,
            'queries': [{'id': case['id'], 'text': case['input']['query']}]}


def build_request(case, training, protocol, backend, reverse=False):
    """Only query + frozen training/contract cross the model boundary; no gold."""
    values = {'operation': list(protocol['operations']), 'node': protocol['nodes'], 'field': protocol['fields']}
    if reverse:
        values = {k: list(reversed(v)) for k, v in values.items()}
    criteria = {
        'operation': {v: protocol['operations'][v] for v in values['operation']},
        'node': {v: ('No literal path start node is supplied or applicable.' if v == 'none' else 'Literal caller-known node ID: ' + v) for v in values['node']},
        'field': {v: ('No numeric aggregation field is applicable.' if v == 'none' else 'Numeric property named ' + v) for v in values['field']},
    }
    state = encoded({'query': case['input']['query'], 'training_examples': training['examples'], 'choices': criteria}).decode()
    require(len(state.encode()) <= 8192, 'shared state exceeds guard limit')
    instructions = protocol['instructions']
    if backend == 'kev':
        return {'model': json.loads((REPO / 'models.lock.json').read_text())['alias'], 'state': state,
                'questions': {k: {'type': 'choice', 'instructions': instructions + '\nSelect the ' + k + '.', 'criteria': criteria[k]} for k in FIELDS}}
    require(backend == 'qwen_json', 'unknown backend')
    return {'model': json.loads((REPO / 'models.baseline.lock.json').read_text())['alias'],
            'messages': [{'role': 'system', 'content': instructions + '\nReturn only JSON with operation, node and field.'}, {'role': 'user', 'content': state}],
            'temperature': 0, 'seed': 0, 'max_tokens': 128, 'cache_prompt': False,
            'chat_template_kwargs': {'enable_thinking': False},
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'search_hints', 'strict': True, 'schema': {
                'type': 'object', 'properties': {k: {'type': 'string', 'enum': values[k]} for k in FIELDS},
                'required': list(FIELDS), 'additionalProperties': False}}}}


def validate():
    protocol, training = load('protocol.json'), load('training.json')
    locks = [json.loads((REPO / name).read_text()) for name in ('models.lock.json', 'models.baseline.lock.json')]
    require(locks[0]['runtime_revision'] == locks[1]['runtime_revision'], 'models must pin the same runtime')
    require(protocol['version'] == 1, 'unsupported protocol')
    require(len((protocol['instructions'] + '\nSelect the operation.').encode()) <= 1024, 'instruction exceeds guard limit')
    require(len(set(protocol['nodes'])) == len(protocol['nodes']) and len(set(protocol['fields'])) == len(protocol['fields']), 'duplicate choices')
    require(all(2 <= len(c) <= 16 for c in (protocol['operations'], protocol['nodes'], protocol['fields'])), 'choice count outside guard limits')
    require(all(len(v.encode()) <= 512 for v in protocol['operations'].values()), 'description exceeds guard limit')
    texts, ids = set(), set()
    for ex in training['examples']:
        require(set(ex) == {'query', 'intent', 'options'} and ex['intent'] in protocol['operations'], 'invalid training example')
        require(ex['query'] not in texts, 'duplicate training query')
        texts.add(ex['query'])
    counts = {}
    manifest = []
    for filename, scope in (('development.json', 'development-only'), ('heldout.json', 'heldout/authored-pilot')):
        dataset = load(filename)
        require(dataset['scope'] == scope, 'wrong dataset scope')
        counts[filename] = len(dataset['cases'])
        for case in dataset['cases']:
            require(case['id'] not in ids and set(case['input']) == {'query'}, 'duplicate ID or unexpected input field')
            ids.add(case['id'])
            query = case['input']['query']
            require(isinstance(query, str) and query.strip() and query not in texts, 'empty or duplicate query across splits')
            texts.add(query)
            require(case['family'] and case['gold']['reason'], 'case needs reviewable explanation')
            selection = {k: case['gold'][k] for k in FIELDS}
            require(json.dumps(decode_selection(selection, protocol), sort_keys=True) == json.dumps(case['gold']['options'], sort_keys=True), 'gold choices disagree with typed options: ' + case['id'])
            for backend in ('qwen_json', 'kev'):
                for reverse in (False, True):
                    body = encoded(build_request(case, training, protocol, backend, reverse))
                    manifest.append({'split': scope, 'id': case['id'], 'backend': backend, 'order': 'reverse' if reverse else 'normal', 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
    require(counts == {'development.json': 18, 'heldout.json': 32}, 'unexpected frozen cohort size')
    return protocol, training, counts, manifest


def verify_freeze():
    freeze = load('freeze.json')
    require(freeze.get('version') == 1 and freeze.get('kind') == 'design-and-code-baseline', 'unsupported freeze kind/version')
    require(isinstance(freeze.get('files'), dict) and REQUIRED_FROZEN <= set(freeze['files']), 'freeze omits required artifacts')
    require(re.fullmatch('[0-9a-f]{64}', freeze.get('driver_sha256', '')) is not None, 'freeze requires driver digest')
    for filename, expected in freeze['files'].items():
        path = (ROOT / filename).resolve()
        require(path.is_relative_to(REPO), 'freeze path escapes repository')
        require(digest(path) == expected, 'frozen file changed: ' + filename)
    protocol, training, _, manifest = validate()
    require(load('request-manifest.json') == manifest, 'current requests differ from frozen request manifest')
    selection = load('development-selection.json')
    require(selection.get('scope') == 'development-only' and selection.get('finished_at'), 'development selection is incomplete')
    require(selection.get('driver_sha256') == freeze['driver_sha256'], 'selection used a different driver')
    source_names = {'protocol.json', 'training.json', 'development.json', 'experiment.py'}
    require(set(selection.get('source_hashes', {})) == source_names, 'selection omits source bindings')
    for filename in source_names:
        require(selection['source_hashes'][filename] == digest(ROOT/filename), 'selection inputs changed: ' + filename)
    cases = {c['id']: c for c in load('development.json')['cases']}
    jobs = load('development-evidence.json')['jobs']
    planned = [('keyword', protocol['bm25']['default_threshold'])] + [('keyword_bm25', t) for t in protocol['bm25']['threshold_grid']]
    require(len(jobs) == len(planned)*len(cases), 'incomplete development evidence')
    counts, seen = {}, set()
    for job in jobs:
        inp, records = job['input'], job['records']
        key = (inp['arm'], inp['threshold'])
        require(key in planned and len(inp['queries']) == 1, 'unexpected development job')
        ident = inp['queries'][0]['id']
        require(ident in cases and (key, ident) not in seen, 'unknown/duplicate development job')
        seen.add((key, ident))
        require(inp == driver_input(cases[ident], training, *key), 'development driver input changed')
        require(len(records) == 2 and records[0]['kind'] == 'provenance' and records[1]['kind'] == 'classification', 'invalid driver record shape')
        input_hash = hashlib.sha256(encoded(inp)).hexdigest()
        require(all(r['input_sha256'] == input_hash for r in records), 'driver input evidence digest mismatch')
        row = records[1]
        require(job['process']['exit_code'] == 0 and row['status'] == 'ok' and row['id'] == ident, 'development execution failed')
        counts[key] = counts.get(key, 0) + exact(row['classification']['Options'], cases[ident]['gold']['options'])
    expected_scores = [{'arm': arm, 'threshold': threshold, 'exact': counts[(arm, threshold)], 'total': len(cases)} for arm, threshold in planned]
    require(selection['scores'] == expected_scores, 'development scores do not reproduce')
    best = max(expected_scores[1:], key=lambda r: (r['exact'], r['threshold']))
    default = next(r for r in expected_scores[1:] if r['threshold'] == protocol['bm25']['default_threshold'])
    strongest = max([('keyword', expected_scores[0]), ('keyword_bm25_default', default), ('keyword_bm25_development', best)], key=lambda p: p[1]['exact'])[0]
    require(selection['selected_threshold'] == best['threshold'] and selection['strongest_code_arm'] == strongest, 'development selection violates frozen rule')
    return freeze


def develop(driver, output):
    """Threshold selection on development only; no path accepts held-out execution."""
    protocol, training, _, _ = validate()
    cases = load('development.json')['cases']
    output.mkdir(parents=True, exist_ok=False)
    summary = {'scope': 'development-only', 'started_at': datetime.now(timezone.utc).isoformat(),
               'driver_sha256': digest(driver), 'source_hashes': {n: digest(ROOT/n) for n in ('protocol.json', 'training.json', 'development.json', 'experiment.py')}, 'scores': [], 'selected_threshold': None}
    (output/'selection.json').write_text(json.dumps(summary, indent=2)+'\n')
    jobs = [('keyword', protocol['bm25']['default_threshold'])] + [('keyword_bm25', t) for t in protocol['bm25']['threshold_grid']]
    for arm, threshold in jobs:
        correct = 0
        for case in cases:
            stem = f'{arm}-{threshold:.1f}-{case["id"]}'
            inp, out = output/(stem+'.input.json'), output/(stem+'.output.jsonl')
            inp.write_bytes(encoded(driver_input(case, training, arm=arm, threshold=threshold)))
            started = time.monotonic()
            completed = subprocess.run([str(driver), '--input', str(inp), '--output', str(out), '--timeout', '10s'], capture_output=True, text=True, timeout=15)
            (output/(stem+'.process.json')).write_text(json.dumps({'exit_code': completed.returncode, 'stderr': completed.stderr, 'wall_ms': (time.monotonic()-started)*1000}, indent=2)+'\n')
            require(completed.returncode == 0, 'development driver failed; evidence retained: ' + stem)
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            require(len(rows) == 2 and rows[0]['kind'] == 'provenance' and rows[1]['id'] == case['id'] and rows[1]['status'] == 'ok', 'unexpected development driver rows')
            correct += exact(rows[1]['classification']['Options'], case['gold']['options'])
        summary['scores'].append({'arm': arm, 'threshold': threshold, 'exact': correct, 'total': len(cases)})
        (output/'selection.json').write_text(json.dumps(summary, indent=2)+'\n')
    bm25 = [r for r in summary['scores'] if r['arm'] == 'keyword_bm25']
    best = max(bm25, key=lambda row: (row['exact'], row['threshold']))
    default = next(r for r in bm25 if r['threshold'] == protocol['bm25']['default_threshold'])
    candidates = [('keyword', summary['scores'][0]), ('keyword_bm25_default', default), ('keyword_bm25_development', best)]
    strongest = max(candidates, key=lambda pair: pair[1]['exact'])[0]
    summary.update(selected_threshold=best['threshold'], strongest_code_arm=strongest,
                   finished_at=datetime.now(timezone.utc).isoformat())
    (output/'selection.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--develop', action='store_true')
    parser.add_argument('--driver', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.develop:
        require(args.driver is not None and args.output is not None, '--develop requires --driver and new --output')
        print(json.dumps(develop(args.driver.resolve(), args.output.resolve()), indent=2))
    else:
        require(args.driver is None and args.output is None, 'driver/output only apply to --develop')
        _, _, counts, _ = validate()
        frozen = (ROOT/'freeze.json').exists()
        if frozen:
            verify_freeze()
        print(json.dumps({'valid': True, 'counts': counts, 'freeze_verified': frozen, 'inference_run': False}))


if __name__ == '__main__':
    main()
