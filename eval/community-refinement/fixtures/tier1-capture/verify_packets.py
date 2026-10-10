#!/usr/bin/env python3
"""Tokenize every frozen reviewer packet on both pinned runtimes, no inference.

Policy exact-runtime-tokenization-v1 (eval/synthesis/context_preflight.py): each
packet is rendered the way the arm would send it, then counted by the runtime's
own /tokenize over a 4,096-token slot. Qwen JSON: /apply-template over the chat
messages, thinking disabled, reserve 128 output tokens. Kev native: the GGUF's
SystemOne choice template (verified against the pinned renderer), reserve 1 for
the decision head; a bundle renders one prompt per question over the shared
state. A packet whose prompt plus reserve exceeds the slot is recorded as
out-of-profile, never truncated.

Launches one llama-server per model on Metal, tokenizes, and stops them.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'eval' / 'synthesis'))
import metal  # noqa: E402
from model import load_lock, verify  # noqa: E402
from compare_scoring import port_is_listening  # noqa: E402
import context_preflight as preflight  # noqa: E402

POLICY = preflight.POLICY
CONTEXT = 4096
RESERVE = {'qwen_json': 128, 'kev': 1}
PORTS = {'qwen_json': 18389, 'kev': 18387}
LOCKS = {'qwen_json': 'models.baseline.lock.json', 'kev': 'models.lock.json'}


def qwen_request(alias, request):
    """The JSON reviewer's chat request for one single-question packet, in the
    shape scripts/evaluate.py build_request uses for the JSON baseline."""
    (_, q), = request['questions'].items()
    labels = list(q['criteria'])
    schema = {'type': 'object', 'properties': {'choice': {'type': 'string', 'enum': labels}},
              'required': ['choice'], 'additionalProperties': False}
    return {
        'model': alias,
        'messages': [
            {'role': 'system', 'content': q['instructions'] + '\nCategories: ' + json.dumps(q['criteria'])
             + '\nReturn only JSON with one "choice" field.'},
            {'role': 'user', 'content': request['state']},
        ],
        'temperature': 0, 'max_tokens': RESERVE['qwen_json'], 'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {'type': 'json_schema', 'json_schema': {'name': 'choice', 'strict': True, 'schema': schema}},
    }


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def launch(arm, lock, log_path):
    command = [str(metal.SERVER), '-m', str(ROOT / 'models' / lock['filename']), '--alias', lock['alias'],
               '--host', '127.0.0.1', '--port', str(PORTS[arm]), '-ngl', '99', '-t', '4', '-tb', '4',
               '-c', str(CONTEXT), '-b', '512', '-ub', '512', '-np', '1', '--no-context-shift', '--metrics', '-lv', '4']
    if arm == 'qwen_json':
        command += ['--chat-template-kwargs', '{"enable_thinking":false}']
    env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
    with open(log_path, 'w') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    metal.ready(f'http://127.0.0.1:{PORTS[arm]}/health', [child], timeout=180)
    return child, command


def read_props(arm):
    import urllib.request
    with urllib.request.urlopen(f'http://127.0.0.1:{PORTS[arm]}/props', timeout=5) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('runtime props exceeded one MiB')
    return json.loads(raw)


def count_pair(packet, aliases):
    request = packet['request']
    qwen = preflight.tokenize('qwen_gate', f"http://127.0.0.1:{PORTS['qwen_json']}",
                              request=qwen_request(aliases['qwen_json'], request), reserve=RESERVE['qwen_json'])
    kev = preflight.tokenize('kev_gate', f"http://127.0.0.1:{PORTS['kev']}",
                             rendered=preflight.kev_choice_prompt(request), reserve=RESERVE['kev'])
    return {'pair_hash': packet['pair_hash'], 'rank': packet['rank'], 'state_bytes': packet['state_bytes'],
            'qwen_json': {'prompt_tokens': qwen['prompt_tokens'], 'fits': qwen['fits']},
            'kev': {'prompt_tokens': kev['prompt_tokens'], 'fits': kev['fits']}}


def count_bundle(packet):
    per_question = []
    for key, q in packet['request']['questions'].items():
        single = {'state': packet['request']['state'], 'questions': {key: q}}
        kev = preflight.tokenize('kev_gate', f"http://127.0.0.1:{PORTS['kev']}",
                                 rendered=preflight.kev_choice_prompt(single), reserve=RESERVE['kev'])
        per_question.append({'question': key, 'prompt_tokens': kev['prompt_tokens'], 'fits': kev['fits']})
    return {'entity_id': packet['entity_id'], 'part': packet['part'], 'parts': packet['parts'],
            'state_bytes': packet['state_bytes'], 'kev': {
                'questions': per_question, 'prompt_tokens_max': max(r['prompt_tokens'] for r in per_question),
                'fits': all(r['fits'] for r in per_question)}}


def summarize(values):
    return {'min': min(values), 'median': int(statistics.median(values)), 'max': max(values)} if values else None


def verify_family(packets_dir, aliases):
    pairs = [count_pair(p, aliases) for p in read_jsonl(packets_dir / 'pairs.jsonl')]
    bundles = [count_bundle(b) for b in read_jsonl(packets_dir / 'bundles.jsonl')]
    index = json.load(open(packets_dir / 'index.json', encoding='utf-8'))
    record = {
        'policy': POLICY, 'family': index['family'], 'split': index['split'], 'context': CONTEXT, 'reserve': RESERVE,
        'packets': {'pairs_sha256': index['pairs']['sha256'], 'bundles_sha256': index['bundles']['sha256']},
        'pairs': {'count': len(pairs),
                  'qwen_json': {**(summarize([p['qwen_json']['prompt_tokens'] for p in pairs]) or {}),
                                'out_of_profile': [p['pair_hash'] for p in pairs if not p['qwen_json']['fits']]},
                  'kev': {**(summarize([p['kev']['prompt_tokens'] for p in pairs]) or {}),
                          'out_of_profile': [p['pair_hash'] for p in pairs if not p['kev']['fits']]},
                  'bytes_per_qwen_token': round(sum(p['state_bytes'] for p in pairs)
                                                / max(1, sum(p['qwen_json']['prompt_tokens'] for p in pairs)), 3)},
        'bundles': {'requests': len(bundles), 'questions': sum(len(b['kev']['questions']) for b in bundles),
                    'kev': {**(summarize([b['kev']['prompt_tokens_max'] for b in bundles]) or {}),
                            'out_of_profile': [f"{b['entity_id']}#{b['part']}" for b in bundles if not b['kev']['fits']]}},
        'per_pair': pairs, 'per_bundle': bundles,
    }
    record['all_fit'] = not (record['pairs']['qwen_json']['out_of_profile'] or record['pairs']['kev']['out_of_profile']
                             or record['bundles']['kev']['out_of_profile'])
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--packets', nargs='+', required=True, help='packet directories (make_packets.py --out)')
    ap.add_argument('--out', required=True, help='roll-up JSON (must not exist); per-family tokens.json goes beside the packets')
    ap.add_argument('--logs', required=True, help='directory for runtime logs (created)')
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        ap.error(f'{out} exists')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        ap.error('the pinned runtimes are native macOS arm64 builds')
    dirs = [Path(p) for p in args.packets]
    for d in dirs:
        if (d / 'tokens.json').exists():
            ap.error(f'{d}/tokens.json exists; packets are verified once')
    build = json.loads((metal.CACHE / 'build.json').read_text())
    if build['runtime_revision'] != metal.REVISION or metal.runtime_hashes() != build['runtime_files_sha256']:
        ap.error('native runtime build provenance mismatch; run task metal:build')
    locks = {arm: load_lock(ROOT / name) for arm, name in LOCKS.items()}
    for arm, lock in locks.items():
        if lock['runtime_revision'] != metal.REVISION or not verify(ROOT / 'models' / lock['filename'], lock):
            ap.error(f'{arm}: pinned model missing, mismatched or corrupt; no download attempted')
    for arm, port in PORTS.items():
        if port_is_listening(port):
            ap.error(f'port {port} for {arm} already has a listener')
    actual_template = preflight.gguf_template(ROOT / 'models' / locks['kev']['filename'])
    if actual_template != preflight.KEV_TEMPLATE:
        ap.error('pinned native Kev choice template changed; review the renderer before trusting counts')
    logs = Path(args.logs)
    logs.mkdir(parents=True, exist_ok=True)
    aliases = {arm: lock['alias'] for arm, lock in locks.items()}
    children, info = [], {'runtime_revision': metal.REVISION, 'locks': locks, 'kev_template': actual_template}
    started = time.monotonic()
    try:
        for arm in ('qwen_json', 'kev'):
            child, command = launch(arm, locks[arm], logs / f'{arm}.runtime.log')
            children.append(child)
            info[arm] = {'command': command, 'offload': metal.metal_offload((logs / f'{arm}.runtime.log').read_text())}
            p = read_props(arm)
            if p.get('default_generation_settings', {}).get('n_ctx') != CONTEXT:
                raise ValueError(f'{arm}: effective slot context differs from the frozen {CONTEXT} profile')
            info[arm]['n_ctx'] = CONTEXT
        families = []
        for d in dirs:
            record = verify_family(d, aliases)
            with open(d / 'tokens.json', 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2)
                f.write('\n')
            families.append({k: record[k] for k in ('family', 'split', 'all_fit')} | {
                'pairs': {arm: {k: record['pairs'][arm][k] for k in ('min', 'median', 'max')} | {
                    'out_of_profile': len(record['pairs'][arm]['out_of_profile'])} for arm in ('qwen_json', 'kev')},
                'bundles_kev': {k: record['bundles']['kev'].get(k) for k in ('min', 'median', 'max')} | {
                    'requests': record['bundles']['requests'], 'out_of_profile': len(record['bundles']['kev']['out_of_profile'])},
                'bytes_per_qwen_token': record['pairs']['bytes_per_qwen_token']})
            print(f"{record['family']}: pairs qwen {record['pairs']['qwen_json']['max']} max, kev {record['pairs']['kev']['max']} max; "
                  f"bundles kev {record['bundles']['kev'].get('max')} max; all fit: {record['all_fit']}", flush=True)
    finally:
        metal.stop(children)
    rollup = {'policy': POLICY, 'context': CONTEXT, 'reserve': RESERVE, 'inference_calls': 0,
              'runtime': info, 'families': families, 'all_fit': all(f['all_fit'] for f in families),
              'wall_seconds': round(time.monotonic() - started, 1)}
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rollup, f, indent=2)
        f.write('\n')
    print(f"all packets fit on both runtimes: {rollup['all_fit']}")
    return 0 if rollup['all_fit'] else 1


if __name__ == '__main__':
    sys.exit(main())
