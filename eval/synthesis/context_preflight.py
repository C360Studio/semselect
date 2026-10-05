"""Tokenize exact visible requests without inference; pinned experiment profiles only."""
import json
from pathlib import Path
import re
import struct
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import answerability
import metal

POLICY = 'exact-runtime-tokenization-v1'
# This template is read back from the verified GGUF before using its choice branch.
KEV_TEMPLATE = "<|fim_prefix|>{{ state }}<|fim_middle|>{{ instructions }}{% for o in options %}<|box_start|>{% if type == 'score' %}{% if o.description %}{{ o.description }}{% endif %}{% else %}{% if type != 'noul' %}{{ o.key }}{% elif o.key == 'true' %}yes{% else %}no{% endif %}{% if o.description %}: {{ o.description }}{% endif %}{% endif %}<|box_end|>{% endfor %}<|fim_suffix|>"


def gguf_template(path):
    """Read the fixed metadata key; skip tensors and numeric metadata arrays."""
    with path.open('rb') as f:
        def unpack(fmt):
            return struct.unpack(fmt, f.read(struct.calcsize(fmt)))
        def string():
            size, = unpack('<Q')
            if size > 16 * 1024 * 1024:
                raise ValueError('unexpected GGUF metadata string length')
            return f.read(size).decode('utf-8')
        sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
        def value(typ):
            if typ == 8:
                return string()
            if typ == 9:
                subtype, size = unpack('<IQ')
                if size > 1_000_000:
                    raise ValueError('unexpected GGUF metadata array length')
                if subtype in sizes:
                    f.seek(size * sizes[subtype], 1)
                else:
                    for _ in range(size):
                        value(subtype)
            elif typ in sizes:
                f.seek(sizes[typ], 1)
            else:
                raise ValueError('unknown GGUF metadata type')
        if f.read(4) != b'GGUF' or unpack('<I')[0] != 3:
            raise ValueError('expected pinned GGUF v3')
        _, count = unpack('<QQ')
        if count > 10000:
            raise ValueError('unexpected GGUF metadata count')
        for _ in range(count):
            key = string()
            item = value(unpack('<I')[0])
            if key == 'tokenizer.chat_template.systemone':
                return item
    raise ValueError('missing SystemOne template')


def kev_choice_prompt(request):
    # server-decision.cpp decision_kev_text: strings pass through except this exact
    # special-token escape. The state is already a string, not a parsed JSON object.
    def escape(text):
        if not isinstance(text, str):
            raise ValueError('this preflight supports only the frozen text choice task')
        return re.sub(r'<\|([A-Za-z0-9_]+)\|>', lambda m: '<¦' + m[1] + '¦>', text)
    question, = request['questions'].values()
    if question['type'] != 'choice':
        raise ValueError('only choice is in scope')
    options = ''.join('<|box_start|>' + escape(key) + (': ' + escape(description) if description else '')
                      + '<|box_end|>' for key, description in question['criteria'].items())
    return '<|fim_prefix|>' + escape(request['state']) + '<|fim_middle|>' + escape(question['instructions']) + options + '<|fim_suffix|>'


def post(url, body):
    request = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('tokenization response exceeds one MiB')
    return json.loads(raw)


def tokenize(name, base, request=None, rendered=None, reserve=500):
    record = {'request': request, 'context': 4096, 'reserved_output_tokens': reserve}
    if rendered is None:
        record['apply_template'] = post(base + '/apply-template', request)
        rendered = record['apply_template']['prompt']
    record['rendered_prompt'] = rendered
    record['tokenization'] = post(base + '/tokenize', {'content': rendered, 'add_special': name != 'kev_gate', 'parse_special': True})
    tokens = record['tokenization']['tokens']
    if not isinstance(tokens, list) or not tokens or not all(isinstance(t, int) and not isinstance(t, bool) for t in tokens):
        raise ValueError('tokenizer did not return a nonempty integer token array')
    record['prompt_tokens'] = len(tokens)
    record['fits'] = len(tokens) + reserve <= 4096
    return record

def check(cases, dataset, aliases, output):
    """No completion endpoint is called. Shared errors exclude a case in all arms."""
    output.mkdir(parents=True, exist_ok=False)
    kev_lock = json.loads((ROOT / 'models.lock.json').read_text())
    actual_template = gguf_template(ROOT / 'models' / kev_lock['filename'])
    if actual_template != KEV_TEMPLATE:
        raise ValueError('pinned native Kev choice template changed; review preflight renderer')
    result = {'policy': POLICY, 'case_errors': {}, 'cases': {}, 'inference_calls': 0,
              'kev_template': actual_template,
              'limits': '4096 tokens per slot, reserve500 for synthesis,128 for JSON gate,1 for Kev head. '
                        'Reserve is a conservative full-output allowance; no context shifting or input truncation. '
                        'CPU availability failure does not prevent the separate Metal stratum.'}
    try:
        with urllib.request.urlopen('http://127.0.0.1:48083/health', timeout=3) as response:
            cpu_ready = response.status == 200
    except OSError as exc:
        cpu_ready = False
        result['cpu_unavailable'] = str(exc)
    result['cpu_available'] = cpu_ready
    result['runtime_props'] = {}
    for name, base in [('qwen_json', 'http://127.0.0.1:18089'), ('kev', 'http://127.0.0.1:18087')] + ([('cpu_06b', 'http://127.0.0.1:48083')] if cpu_ready else []):
        with urllib.request.urlopen(base + '/props', timeout=5) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('runtime props exceeded one MiB')
        props = json.loads(raw)
        result['runtime_props'][name] = props
        if props.get('default_generation_settings', {}).get('n_ctx') != 4096:
            raise ValueError(f'{name}: effective slot context differs from frozen4096 profile')
    for ident, case in cases.items():
        if case['acquisition']['status'] != 'ok' or case['profile_errors'] or not case['input']['passages']:
            result['cases'][ident] = {'skipped': 'acquisition/profile failure or empty evidence'}
            continue
        messages = case['capture']['calls'][0]['messages']
        generator_request = {'model': aliases['qwen_json'], 'messages': messages, 'temperature': 0.3, 'max_tokens': 500}
        qwen = answerability.prepare(dataset, case, 'qwen_json', aliases['qwen_json'], 'normal')['request']
        kev = answerability.prepare(dataset, case, 'kev', aliases['kev'], 'normal')['request']
        records = {'metal_generator': tokenize('metal_generator', 'http://127.0.0.1:18089', request=generator_request),
                   'qwen_gate': tokenize('qwen_gate', 'http://127.0.0.1:18089', request=qwen, reserve=128),
                   'kev_gate': tokenize('kev_gate', 'http://127.0.0.1:18087', rendered=kev_choice_prompt(kev), reserve=1)}
        if cpu_ready:
            records['cpu_generator'] = tokenize('cpu_generator', 'http://127.0.0.1:48083',
                                               request={**generator_request, 'model': 'seminstruct'})
        errors = [f'{name}: {r["prompt_tokens"]} prompt + {r["reserved_output_tokens"]} reserve >4096 context'
                  for name, r in records.items() if not r['fits']]
        if errors:
            result['case_errors'][ident] = errors
        result['cases'][ident] = records
        metal_path = output / (ident + '.json')
        metal_path.write_text(json.dumps(records, indent=2) + '\n')
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
