"""Evaluation-only wire adapters. Importing this module never loads a model.

Requests are envelopes: only ``payload`` crosses the HTTP boundary. Mapping and
canonical_order are retained locally and frozen with the request manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
MAX_BODY = 1024 * 1024
ARMS = ('gliclass', 'deberta', 'qwen', 'qwen_json')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value), 'score must be finite numeric value')
    return float(value)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON object key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant: ' + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def build_request(query, arm, variant=0, view='primary', protocol=None):
    protocol = protocol or json.loads((ROOT / 'protocol.json').read_text())
    require(arm in ARMS, 'unknown model arm')
    require(type(query) is str and query.strip() and len(query.encode()) <= 16384, 'invalid query')
    require(type(variant) is int and variant in (0, 1), 'only two frozen variants allowed')
    require(view in ('primary', 'reverse', 'remap'), 'unknown sensitivity view')
    operations = protocol['operations']
    require(isinstance(operations, dict) and len(operations) == 9 and 'no_override' in operations,
            'expected nine operation definitions')
    require(all(type(v) is str and v.strip() for v in operations.values()), 'empty operation description')
    require(len(set(operations.values())) == len(operations), 'descriptions must uniquely map labels')
    variants = protocol.get('variants', [protocol['instructions'], protocol['instructions']])
    instruction = protocol['instructions'] + '\n' + variants[variant]
    require(type(instruction) is str and instruction.strip(), 'variant must be instruction text')
    mapping = [{'id': f'label_{i:02d}' if view == 'remap' else op,
                'operation': op, 'label': desc} for i, (op, desc) in enumerate(operations.items())]
    if view == 'reverse':
        mapping.reverse()
    if arm in ('qwen', 'qwen_json'):
        choices = {m['id']: m['label'] for m in mapping}
        payload = {
            'model': protocol.get('qwen_model', 'qwen3.5-4b'),
            'messages': [{'role': 'system', 'content': instruction + '\nReturn only an operation JSON object. Use defer when declining to select.'},
                         {'role': 'user', 'content': encoded({'query': query, 'operations': choices}).decode()}],
            'temperature': 0, 'seed': 0, 'max_tokens': 64, 'cache_prompt': False,
            'chat_template_kwargs': {'enable_thinking': False},
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'operation', 'strict': True, 'schema': {
                    'type': 'object', 'properties': {'operation': {'type': 'string', 'enum': list(choices) + ['defer']}},
                    'required': ['operation'], 'additionalProperties': False}}}}
    else:
        payload = {'arm': arm, 'query': query, 'instruction': instruction,
                   'candidates': [{'id': m['id'], 'label': m['label']} for m in mapping]}
    require(len(encoded(payload)) <= MAX_BODY, 'request exceeds body limit')
    return {'arm': arm, 'variant': variant, 'view': view, 'payload': payload,
            'mapping': mapping, 'canonical_order': list(operations),
            'id_remap_interpretation': 'model_output_ids' if arm in ('qwen', 'qwen_json') else 'adapter_mapping_only'}


def softmax(values):
    values = [finite(x) for x in values]
    require(bool(values), 'missing logits')
    maximum = max(values)
    exp = [math.exp(v - maximum) for v in values]
    return [v / sum(exp) for v in exp]


def entailment_index(label2id):
    require(isinstance(label2id, dict), 'missing native label2id')
    require(all(type(v) is int for v in label2id.values()) and
            set(label2id.values()) == set(range(len(label2id))), 'native label indices must be bijective')
    matches = [v for k, v in label2id.items() if k.casefold() == 'entailment']
    require(len(matches) == 1 and type(matches[0]) is int and matches[0] >= 0, 'ambiguous entailment index')
    return matches[0]


def decode_response(response, arm, request):
    require(arm == request['arm'] and arm in ARMS, 'arm mismatch')
    mapping = request['mapping']
    ids = {m['id']: m['operation'] for m in mapping}
    require(len(ids) == 9 and set(ids.values()) == set(request['canonical_order']), 'invalid ID bijection')
    require(isinstance(response, dict), 'response must be object')
    if arm in ('qwen', 'qwen_json'):
        choices = response.get('choices')
        require(isinstance(choices, list) and len(choices) == 1, 'expected one Qwen completion')
        require(choices[0].get('finish_reason') == 'stop', 'Qwen incomplete completion')
        content = choices[0].get('message', {}).get('content')
        require(type(content) is str, 'Qwen content missing')
        try:
            result = strict_json(content)
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid Qwen operation JSON') from exc
        require(type(result) is dict and set(result) == {'operation'}, 'Qwen must return only operation')
        op = result['operation']
        require(type(op) is str and (op == 'defer' or op in ids), 'unknown Qwen operation')
        return {'status': 'deferred' if op == 'defer' else 'selected',
                'operation': None if op == 'defer' else ids[op], 'scores': {}, 'raw': response}
    require(response.get('arm') == arm and response.get('truncated') is False,
            'wrong arm or unproved input truncation status')
    rows = response.get('scores')
    require(isinstance(rows, list) and len(rows) == len(ids), 'missing candidate scores')
    scores = {}
    raw_logits = []
    native_ids = []
    for row in rows:
        require(isinstance(row, dict), 'malformed score row')
        ident = row.get('id')
        require(type(ident) is str and ident in ids and ids[ident] not in scores, 'unknown or duplicate candidate ID')
        score = finite(row.get('score'))
        require(0 <= score <= 1, 'normalized score out of range')
        scores[ids[ident]] = score
        native_ids.append(ident)
        if arm == 'gliclass':
            raw_logits.append(finite(row.get('logit')))
        else:
            logits = row.get('logits')
            require(isinstance(logits, list) and len(logits) == len(response.get('label2id', {})), 'native NLI logits missing')
            logits = [finite(x) for x in logits]
            index = entailment_index(response['label2id'])
            require(index < len(logits), 'entailment index outside native logits')
            raw_logits.append(logits[index])
    expected = softmax(raw_logits)
    require(abs(sum(scores.values()) - 1) < 1e-5, 'candidate scores do not sum to one')
    require(all(abs(scores[ids[k]] - v) < 1e-5 for k, v in zip(native_ids, expected)), 'native score transformation mismatch')
    ordered = sorted(request['canonical_order'], key=lambda op: -scores[op])
    return {'status': 'selected', 'operation': ordered[0], 'scores': scores,
            'top_score': scores[ordered[0]], 'margin': scores[ordered[0]] - scores[ordered[1]], 'raw': response}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP redirects are forbidden')


def http_json(url, payload, timeout=10):
    """Bounded loopback transport; timing ownership belongs to the runner."""
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            'only explicit loopback HTTP endpoints allowed')
    require(0 < timeout <= 60, 'invalid request timeout')
    body = encoded(payload)
    require(len(body) <= MAX_BODY, 'request exceeds body limit')
    request = urllib.request.Request(url, body, {'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_BODY + 1)
    require(len(data) <= MAX_BODY, 'response exceeds body limit')
    return strict_json(data)


def cosine(a, b):
    require(isinstance(a, list) and isinstance(b, list) and 0 < len(a) == len(b) <= 8192, 'embedding dimension mismatch')
    a, b = [finite(x) for x in a], [finite(x) for x in b]
    na, nb = math.hypot(*a), math.hypot(*b)
    require(na > 0 and nb > 0 and math.isfinite(na) and math.isfinite(nb), 'invalid embedding norm')
    return max(-1.0, min(1.0, sum((x / na) * (y / nb) for x, y in zip(a, b))))


def nearest_example(vector, examples, vectors):
    require(len(examples) == len(vectors) and len(examples) > 0, 'embedding example index mismatch')
    similarities = [cosine(vector, v) for v in vectors]
    order = sorted(range(len(examples)), key=lambda i: -similarities[i])
    top = order[0]
    # Margin is against the strongest competing operation, not another example
    # of the same operation; otherwise repeated examples distort acceptance.
    scores = {}
    for example, value in zip(examples, similarities):
        op = example.get('operation', example.get('intent'))
        require(type(op) is str, 'example operation missing')
        scores[op] = max(scores.get(op, -1), value)
    operation = examples[top].get('operation', examples[top].get('intent'))
    rivals = [score for op, score in scores.items() if op != operation]
    require(bool(rivals), 'embedding index needs multiple operations')
    return {'status': 'selected', 'operation': operation, 'scores': scores,
            'top_score': similarities[top], 'margin': similarities[top] - max(rivals),
            'example_index': top, 'raw': {'cosines': similarities}}


def embed(texts, endpoint, model, *, timeout=10, transport=http_json):
    require(isinstance(texts, list) and 0 < len(texts) <= 256 and
            all(type(t) is str and t.strip() for t in texts), 'invalid embed inputs')
    response = transport(endpoint, embedding_request(texts, model), timeout)
    require(response.get('model') == model, 'semembed loaded model differs from frozen request')
    rows = response.get('data')
    require(isinstance(rows, list) and len(rows) == len(texts), 'missing embedding rows')
    vectors = [None] * len(texts)
    for row in rows:
        index = row.get('index')
        require(type(index) is int and 0 <= index < len(texts) and vectors[index] is None, 'invalid embedding index')
        vector = row.get('embedding')
        cosine(vector, vector)
        vectors[index] = vector
    require(len({len(v) for v in vectors}) == 1, 'inconsistent embedding dimensions')
    return vectors


def embedding_request(texts, model='Snowflake/snowflake-arctic-embed-s'):
    """Actual semembed OpenAI-compatible float wire shape; no task prompt."""
    if isinstance(texts, str):
        texts = [texts]
    require(isinstance(texts, list) and 0 < len(texts) <= 256 and
            all(type(t) is str and t.strip() for t in texts), 'invalid embed inputs')
    payload = {'model': model, 'input': texts, 'encoding_format': 'float'}
    require(len(encoded(payload)) <= MAX_BODY, 'embedding request exceeds body limit')
    return payload
