"""Frozen throughput requests mapped onto SGLang's endpoints without touching prompt text.

Every SGLang body starts from a body fixtures.py rebuilt and byte-verified against the
serial llama.cpp runs. Only field names, the model name and llama.cpp-only fields
change; prompt text, labels and candidate order do not. MAPPING records every
difference, with pinned-source references, and run_sglang.py saves it in each summary.
"""
from __future__ import annotations

import dataclasses
import json
import math
import time

import fixtures
import compare_scoring
import evaluate
import runner
import sglang_runtime

require = fixtures.require
SERVED_MODEL = sglang_runtime.SERVED_MODEL
PATHS = {'qwen_json': '/v1/chat/completions', 'qwen_decisions': '/v1/decisions', 'qwen_score': '/v1/score'}
ARMS = {'w1': ('qwen_json', 'qwen_decisions', 'qwen_score'), 'w2': ('qwen_json', 'qwen_decisions')}
# Pins the server-owned decision wording; the probe's responses report version 1.
PROMPT_FORMAT_VERSION = 1
# Cross-runtime diagnostic reference for every SGLang arm: serial llama.cpp Qwen JSON labels.
CROSS_RUNTIME_ARM = 'qwen_json'
SRC = 'https://github.com/sgl-project/sglang/blob/' + sglang_runtime.SOURCE_REVISION + '/python/sglang/srt/'

MAPPING = {
    'qwen_json': {
        'source': 'fixtures.w1_jobs / w2_jobs("qwen_json"): the llama.cpp Qwen JSON bodies, byte-verified',
        'endpoint': 'llama.cpp /v1/chat/completions -> SGLang /v1/chat/completions',
        'changed': [
            'model: llama.cpp alias "qwen3.5-4b" -> SGLang served name "qwen35-4b-mlx". SGLang echoes it and parses '
            'it only for a "base:adapter" LoRA suffix (entrypoints/openai/serving_base.py:39-52).',
            'cache_prompt removed (W1 true, W2 false): a llama.cpp field; SGLang ChatCompletionRequest has no such '
            'field and ignores unknown ones (entrypoints/openai/protocol.py:858). Prefix reuse is a server setting '
            'here: --disable-radix-cache in every cell except 4x4-radix.'],
        'unchanged': ['messages (system and user text)', 'temperature 0', 'max_tokens 128', 'seed 0',
                      'chat_template_kwargs {"enable_thinking": false}', 'response_format json_schema (strict)',
                      'key order and JSON encoding of the source body'],
        'semantics': [
            'seed is accepted but ignored on MLX unless --enable-deterministic-inference '
            '(hardware_backend/mlx/sampling.py:76-86); temperature 0 is greedy on both runtimes.',
            'max_tokens is deprecated in SGLang in favour of max_completion_tokens but still honoured '
            '(entrypoints/openai/protocol.py:870-874); the probe used it.',
            'The JSON schema is enforced by --grammar-backend llguidance, not llama.cpp grammar sampling.'],
        'parser': 'shared: evaluate.validate_baseline (W1) and fixtures W2 qwen_json parser; same OpenAI chat shape',
    },
    'qwen_decisions': {
        'source': 'fixtures.w1_jobs / w2_jobs("kev"): the Kev /v1/systemone bodies, byte-verified; Qwen is served',
        'endpoint': 'llama.cpp Kev /v1/systemone -> SGLang /v1/decisions on Qwen (SGLang does not serve Kev)',
        'changed': [
            'state -> input: the same string; render_text returns strings verbatim '
            '(entrypoints/openai/serving_decisions.py:449-454).',
            'questions {id: {type: choice, instructions, criteria}} -> questions [{id, type: choice, question: '
            'instructions, options: [{name, description}] in criteria order}], in head order. This is the mapping '
            'SGLang\'s own /v1/systemone applies for a chat model (entrypoints/systemone/serving.py:80-110, 214-221).',
            'model: Kev alias -> SGLang served name (echoed only).',
            'added prompt_format_version 1: pins the server-owned wording; another served version returns 400.',
            'added return_prompt_token_ids true, as in the probe: each answer carries its rendered prompt and label '
            'token ids, so a run can be replayed through /v1/score.',
            'temperature omitted (default 1.0): it scales probabilities only, never the argmax choice.'],
        'unchanged': ['state text', 'instructions text', 'criteria names, descriptions and candidate order',
                      'W1 normal and reverse orders', 'W2: one request carrying all three heads (SGLang\'s bundled path)'],
        'semantics': [
            'Prompt wrapper differs from Kev\'s runtime: SGLang renders each question as one user message with '
            'thinking off: input, blank line, "Question: <instructions>", "A: <name> - <description>" per option, '
            '"Answer with the letter of one option only." (serving_decisions.py:514-547). Labels are letters A.. in '
            'option order; each head is scored by one prefill.',
            'usage.prompt_tokens counts every question\'s prompt, so a W2 request counts the state three times '
            '(serving_decisions.py:441-444).'],
        'parser': 'sglang_requests.validate_decision: shape differs from evaluate.validate_native (no confidence; '
                  'label_mass and token ids instead); per-head validity as in the Kev W2 parser',
    },
    'qwen_score': {
        'source': 'fixtures.w1_jobs("qwen_score"): the evidence text and labels the llama.cpp one-token arm scores',
        'endpoint': 'llama.cpp /completion (n_predict 1, letter grammar, n_probs 256) -> SGLang /v1/score',
        'changed': [
            'Template and tokens: llama.cpp /apply-template + /tokenize -> SGLang /v1/tokenize (messages, '
            'enable_thinking false), /v1/detokenize, /v1/tokenize (prompt + each letter, add_special_tokens false), '
            'exactly the probe\'s preparation; built after startup, before warmup, outside the timed window.',
            'Body: {model, query: [], items: [prompt_ids], label_token_ids, apply_softmax: true, '
            'return_token_logprobs: true}, the probe\'s independent /v1/score call. No token is generated.',
            'cache_prompt false and seed 0 have no counterpart: nothing is sampled and the radix cache is off.'],
        'unchanged': ['messages from compare_scoring.score_messages (same system and user text)',
                      'letters A.. in candidate order', 'one distinct token per label at the answer position'],
        'semantics': ['Choice is the argmax of the label softmax (first on ties); llama.cpp\'s is the greedy '
                      'grammar-constrained token. Same label set, different readout.'],
        'parser': 'sglang_requests.validate_score: the /v1/score shape differs from llama.cpp /completion',
    },
}


def encoder(workload):
    """W1 bodies were sent as json.dumps(request); W2 bodies with the query-routing encoder."""
    return (lambda value: json.dumps(value).encode()) if workload == 'w1' else fixtures.routing.encoded


def chat_body(source):
    body = dict(source)
    body['model'] = SERVED_MODEL
    body.pop('cache_prompt')
    return body


def decisions_body(kev):
    questions = []
    for key, head in kev['questions'].items():
        require(head['type'] == 'choice' and 2 <= len(head['criteria']) <= 26,
                '/v1/decisions choice questions take 2 to 26 options')
        questions.append({'id': key, 'type': 'choice', 'question': head['instructions'],
                          'options': [{'name': name, 'description': text} for name, text in head['criteria'].items()]})
    return {'model': SERVED_MODEL, 'input': kev['state'], 'questions': questions,
            'prompt_format_version': PROMPT_FORMAT_VERSION, 'return_prompt_token_ids': True}


# --- Response validation ------------------------------------------------------------------

def probabilities_over(values, labels):
    require(isinstance(values, dict) and set(values) == set(labels), 'probabilities must cover exactly the candidates')
    require(all(evaluate.finite_number(v) and 0 <= v <= 1 for v in values.values()),
            'probabilities must be finite numbers in [0, 1]')
    require(math.isclose(math.fsum(values.values()), 1.0, abs_tol=1e-5), 'candidate probabilities do not sum to one')
    return values


def validate_decision(answer, labels):
    """One /v1/decisions choice answer (protocol.py DecisionAnswer); not a calibrated confidence."""
    require(isinstance(answer, dict) and answer.get('type') == 'choice', 'missing choice answer')
    probabilities = probabilities_over(answer.get('probabilities'), labels)
    choice = answer.get('choice')
    require(isinstance(choice, str) and choice in labels, 'answer is not an allowed choice')
    pmax = max(probabilities.values())
    require(math.isclose(probabilities[choice], pmax, abs_tol=1e-7), 'selected candidate is not maximal')
    mass = answer.get('label_mass')
    require(evaluate.finite_number(mass) and 0 < mass <= 1 + 1e-5, 'invalid full-vocabulary label mass')
    if 'label_token_ids' in answer:
        ids = answer['label_token_ids']
        require(isinstance(ids, list) and len(ids) == len(labels) and len(set(ids)) == len(ids)
                and all(type(i) is int for i in ids), 'label token ids must be one distinct id per candidate')
    if 'prompt_token_ids' in answer:
        ids = answer['prompt_token_ids']
        require(isinstance(ids, list) and ids and all(type(i) is int for i in ids), 'invalid prompt token ids')
    return {'choice': choice, 'probabilities': probabilities, 'label_mass': mass, 'pmax': pmax}


def validate_score(response, labels):
    """One /v1/score item over the supplied labels (apply_softmax, return_token_logprobs)."""
    require(isinstance(response, dict), 'response must be an object')
    scores, logprobs = response.get('scores'), response.get('token_logprobs')
    require(isinstance(scores, list) and len(scores) == 1 and isinstance(scores[0], list)
            and len(scores[0]) == len(labels), 'expected one score per candidate for one item')
    require(isinstance(logprobs, list) and len(logprobs) == 1 and isinstance(logprobs[0], list)
            and len(logprobs[0]) == len(labels), 'expected one raw log-probability per candidate')
    probabilities = probabilities_over(dict(zip(labels, scores[0])), labels)
    raw = logprobs[0]
    require(all(evaluate.finite_number(v) and v <= 1e-6 for v in raw), 'invalid raw label log-probability')
    peak = max(raw)
    weights = [math.exp(v - peak) for v in raw]
    # The scores must be the softmax of these label log-probabilities, in candidate order.
    require(all(math.isclose(w / math.fsum(weights), s, abs_tol=1e-5) for w, s in zip(weights, scores[0])),
            'scores are not the softmax of the returned label log-probabilities')
    mass = math.fsum(math.exp(v) for v in raw)
    require(0 < mass <= 1 + 1e-5, 'invalid full-vocabulary label mass')
    choice = labels[scores[0].index(max(scores[0]))]
    return {'choice': choice, 'probabilities': probabilities, 'raw_label_logprobs': dict(zip(labels, raw)),
            'label_mass': mass, 'pmax': probabilities[choice]}


def _w1_decisions_parser(candidates):
    def parse(response):
        require(isinstance(response, dict) and isinstance(response.get('answers'), dict)
                and set(response['answers']) == {'route'}, 'expected exactly the route answer')
        return {'action': validate_decision(response['answers']['route'], candidates)['choice']}
    return parse


def _w2_decisions_parser(candidates):
    def parse(response):
        require(isinstance(response, dict) and isinstance(response.get('answers'), dict), 'missing decision answers')
        labels = {}
        for key, allowed in candidates.items():
            # Heads are scored independently, so each is valid or invalid on its own (as for Kev).
            try:
                labels[key] = validate_decision(response['answers'].get(key), allowed)['choice']
            except ValueError:
                labels[key] = None
        return labels
    return parse


def _score_parser(candidates):
    return lambda response: {'action': validate_score(response, candidates)['choice']}


def usage(response):
    """Server-reported token usage; cached_tokens appears only with --enable-cache-report (not set)."""
    found = response.get('usage') if isinstance(response, dict) else None
    if not isinstance(found, dict):
        return {}
    counts = {key: found[key] for key in ('prompt_tokens', 'completion_tokens') if type(found.get(key)) is int}
    details = found.get('prompt_tokens_details')
    if isinstance(details, dict) and type(details.get('cached_tokens')) is int:
        counts['cached_tokens'] = details['cached_tokens']
    return counts


# --- Jobs -----------------------------------------------------------------------------------

def _aligned(jobs, references):
    require([(j.case_id, j.order) for j in jobs] == [(r.case_id, r.order) for r in references],
            'source and reference jobs are not in the same order')
    return zip(jobs, references)


def w1_jobs(arm, inputs):
    """One W1 pass in fixtures order; reference labels are the serial llama.cpp Qwen JSON labels."""
    require(arm in ARMS['w1'], 'unknown SGLang W1 arm')
    dataset, reference, locks = inputs['w1'], inputs['w1_reference'], inputs['locks']
    json_jobs = fixtures.w1_jobs('qwen_json', dataset, reference, locks['qwen']['alias'])
    encode = encoder('w1')
    if arm == 'qwen_json':
        return [dataclasses.replace(job, path=PATHS[arm], body=encode(chat_body(json.loads(job.body)))) for job in json_jobs]
    if arm == 'qwen_score':
        # fixtures already references qwen_score against the Qwen JSON labels.
        return [dataclasses.replace(job, path=PATHS[arm]) for job in fixtures.w1_jobs('qwen_score', dataset, reference, locks['qwen']['alias'])]
    jobs = []
    for kev, ref in _aligned(fixtures.w1_jobs('kev', dataset, reference, locks['kev']['alias']), json_jobs):
        source = json.loads(kev.body)
        jobs.append(fixtures.Job(kev.case_id, kev.order, PATHS[arm], encode(decisions_body(source)), kev.questions,
                                 ref.reference, _w1_decisions_parser(list(source['questions']['route']['criteria']))))
    return jobs


def w2_jobs(arm, inputs):
    require(arm in ARMS['w2'], 'unknown SGLang W2 arm')
    w2, reference = inputs['w2'], inputs['w2_reference']
    json_jobs = fixtures.w2_jobs('qwen_json', w2, reference)
    encode = encoder('w2')
    if arm == 'qwen_json':
        return [dataclasses.replace(job, path=PATHS[arm], body=encode(chat_body(json.loads(job.body)))) for job in json_jobs]
    jobs = []
    for kev, ref in _aligned(fixtures.w2_jobs('kev', w2, reference), json_jobs):
        source = json.loads(kev.body)
        candidates = {key: list(head['criteria']) for key, head in source['questions'].items()}
        require(tuple(candidates) == fixtures.FIELDS, 'unexpected W2 heads')
        jobs.append(fixtures.Job(kev.case_id, kev.order, PATHS[arm], encode(decisions_body(source)), kev.questions,
                                 ref.reference, _w2_decisions_parser(candidates)))
    return jobs


def jobs_for(cell, inputs):
    """Offline jobs for one pass; qwen_score still needs finalize_score_jobs at run time."""
    return (w1_jobs if cell.workload == 'w1' else w2_jobs)(cell.arm, inputs)


def cross_runtime_reference(cell, inputs):
    reference = inputs['w1_reference' if cell.workload == 'w1' else 'w2_reference']
    return {'path': reference['path'], 'sha256': reference['sha256'], 'arm': CROSS_RUNTIME_ARM,
            'view': ('llama.cpp serial Qwen JSON, trial 1, both candidate orders' if cell.workload == 'w1'
                     else 'llama.cpp serial Qwen JSON, normal order')}


# --- One-token scoring, prepared against the live runtime ---------------------------------------

def prepare_score(dataset, text, candidates, base=sglang_runtime.BASE, timeout=runner.TIMEOUT_SECONDS):
    """The probe's independent-score preparation applied to compare_scoring.score_messages."""
    require(2 <= len(candidates) <= 26, 'this experiment supports 2..26 single-letter options')

    def post(path, body):
        return evaluate.post_json(base + path, body, timeout)
    messages = compare_scoring.score_messages(dataset, {'text': text}, list(candidates))
    prompt_ids = post('/v1/tokenize', {'model': SERVED_MODEL, 'messages': messages,
                                       'chat_template_kwargs': {'enable_thinking': False}})['tokens']
    prompt = post('/v1/detokenize', {'model': SERVED_MODEL, 'tokens': prompt_ids, 'skip_special_tokens': False})['text']
    if '<think>' in prompt.rsplit('<|im_start|>assistant', 1)[-1] and not prompt.endswith('</think>\n\n'):
        raise ValueError('Expected a closed Qwen thinking block at the answer position')
    letters = [chr(65 + i) for i in range(len(candidates))]
    tokens = post('/v1/tokenize', {'model': SERVED_MODEL, 'prompt': [prompt] + [prompt + letter for letter in letters],
                                   'add_special_tokens': False})['tokens']
    require(isinstance(tokens, list) and len(tokens) == len(letters) + 1 and tokens[0] == prompt_ids,
            'rendered score prompt does not round-trip through the tokenizer')
    label_ids = compare_scoring.check_label_tokens(prompt_ids, tokens[1:])
    request = {'model': SERVED_MODEL, 'query': [], 'items': [prompt_ids], 'label_token_ids': label_ids,
               'apply_softmax': True, 'return_token_logprobs': True}
    return {'request': request, 'prompt': prompt, 'messages': messages,
            'label_token_ids': dict(zip(candidates, label_ids)), 'letters': dict(zip(candidates, letters))}


def finalize_score_jobs(jobs, dataset, deadline, base=sglang_runtime.BASE, record=None, clock=time.monotonic):
    """Tokenize score requests through the cell's runtime, before warmup and outside the timed window."""
    finished, prepared_rows = [], []
    for job in jobs:
        if job.score_input is None:
            finished.append(job)
            continue
        remaining = deadline - clock()
        require(remaining > 0, 'budget exhausted while preparing score requests')
        text, candidates = job.score_input
        prepared = prepare_score(dataset, text, candidates, base, min(runner.TIMEOUT_SECONDS, remaining))
        finished.append(dataclasses.replace(job, body=json.dumps(prepared['request']).encode(),
                                            parse=_score_parser(list(candidates))))
        prepared_rows.append({'case_id': job.case_id, 'order': job.order, 'prompt': prepared['prompt'],
                              'label_token_ids': prepared['label_token_ids'], 'letters': prepared['letters']})
    if record is not None:
        record.write_text(json.dumps(prepared_rows, indent=2, ensure_ascii=False) + '\n')
    return finished


# --- Offline mapping checks (--validate and tests) --------------------------------------------

def chat_changes(source, mapped):
    """Top-level differences between a llama.cpp chat body and its SGLang body, and whether key order held."""
    before, after = json.loads(source), json.loads(mapped)
    keys = list(before) + [key for key in after if key not in before]
    changes = [(key, before.get(key, '<absent>'), after.get(key, '<removed>')) for key in keys
               if key not in before or key not in after or before[key] != after[key]]
    return changes, [key for key in before if key in after] == list(after)


def decisions_matches(kev_body, mapped):
    """True when input, question text and options are the Kev body's state, instructions and criteria, in order."""
    kev, body = json.loads(kev_body), json.loads(mapped)
    expected = [(key, head['instructions'], list(head['criteria'].items())) for key, head in kev['questions'].items()]
    found = [(q['id'], q['question'], [(o['name'], o['description']) for o in q['options']]) for q in body['questions']]
    return body['input'] == kev['state'] and found == expected
