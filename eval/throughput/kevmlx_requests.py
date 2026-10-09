"""Frozen W1/W2 Kev requests on kev.serve's /v1/systemone, and parsers to the shared label rows.

Every frozen request already has Kev's schema (kev/api.py:17-46 at the pinned commit):
{"model", "state", "questions": {id: {"type": "choice", "instructions", "criteria"}}}.
So the mapping is the identity on bytes: the W1 and W2 new-state bodies sent to kev.serve
are the exact bytes of the serial runs. FIELD_DIFFERENCES records how each field is read.

The W2 cached-state cell splits each frozen body into request A (operation) and request B
(node, field). Both are spliced from the frozen bytes: the same model and state bytes and
the same question objects, in their original order, under the W2 serializer
(eval/query-routing/experiment.py `encoded`).
"""
from __future__ import annotations

import dataclasses
import json

import fixtures
import evaluate  # scripts/, put on sys.path by fixtures

require = fixtures.require
PATH = '/v1/systemone'                             # kev/serve.py:249
REQUEST_FIELDS = {'model', 'state', 'questions'}   # SystemOneRequest, kev/api.py:43-46
CHOICE_FIELDS = {'type', 'instructions', 'criteria'}  # Choice, kev/api.py:23-31
MAX_OPTIONS = 255                                  # kev/api.py:14
ROUNDING = 5e-5                                    # half the 4-decimal step of round_prob, kev/api.py:143-146
SPLIT = (('operation',), ('node', 'field'))        # cached-state cell: request A (new state), request B (cached state)

FIELD_DIFFERENCES = (
    {'field': 'model', 'frozen': '"semselect-kev-4b" (the llama.cpp alias in models.lock.json)',
     'kev': 'any string; default "kev-latest" (api.py:45); never checked, echoed in the response (serve.py:216)',
     'effect': 'none on inference; sent unchanged. Kev\'s card names are kev-latest/jev-latest (serve.py:35), '
               'reported only by /v1/models'},
    {'field': 'state', 'frozen': 'a JSON-encoded string',
     'kev': 'str | object | array (api.py:13,44); a string is rendered unchanged (api.py:53,117), then '
            '<|name|> patterns are escaped before tokenizing (model.py:75-81)',
     'effect': 'same characters reach the tokenizer; Kev\'s Python encoder and llama.cpp\'s port tokenize '
               'independently and are not compared here'},
    {'field': 'questions.<id>.type', 'frozen': '"choice"', 'kev': '"choice" (api.py:23-24)', 'effect': 'none'},
    {'field': 'questions.<id>.instructions', 'frozen': 'string', 'kev': 'rendered unchanged (api.py:116)',
     'effect': 'none'},
    {'field': 'questions.<id>.criteria', 'frozen': '{candidate: description} in candidate order',
     'kev': 'each option rendered "name: description" in object order (api.py:58-59,112); 1-255 options',
     'effect': 'same candidates, same order'},
    {'field': 'questions (W2 cached-state cell only)', 'frozen': 'operation, node, field in one request',
     'kev': 'A: {operation}; B: {node, field}; each spliced from the frozen bytes',
     'effect': 'two new byte sequences per case by design; state and question bytes verbatim'},
    {'field': 'answers.<id>.probabilities / confidence', 'frozen': 'llama.cpp returns full doubles',
     'kev': 'rounded to 4 decimals (api.py:143-146,155-156)',
     'effect': 'a K-option distribution sums to 1 within K x 5e-5, not the shared validator\'s 1e-5 '
               '(scripts/evaluate.py:49); the sum test is widened to that bound, the distribution renormalized, '
               'then the shared validator applies unchanged'},
    {'field': 'usage / latency_ms', 'frozen': 'usage.input_tokens',
     'kev': 'usage.input_tokens = every token of the packed record (serve.py:189); usage.output_tokens = tokens of '
            'the serialized answers (api.py:163-165); latency_ms = model time of the whole server batch (serve.py:186-190)',
     'effect': 'recorded; not comparable token for token with llama.cpp'},
)


def check_schema(request):
    """The frozen request as SystemOneRequest would parse it, with nothing dropped or coerced."""
    require(isinstance(request, dict) and set(request) == REQUEST_FIELDS, 'request fields differ from SystemOneRequest')
    require(isinstance(request['model'], str), 'model must be a string')
    require(isinstance(request['state'], (str, dict, list)), 'state must be a string, object or array')
    questions = request['questions']
    require(isinstance(questions, dict) and questions, 'questions must be a non-empty object')
    for question in questions.values():
        require(isinstance(question, dict) and set(question) == CHOICE_FIELDS and question['type'] == 'choice',
                'every frozen question must be a Choice')
        require(isinstance(question['instructions'], str), 'instructions must be a string')
        criteria = question['criteria']
        require(isinstance(criteria, dict) and 1 <= len(criteria) <= MAX_OPTIONS
                and all(isinstance(v, str) for v in criteria.values()), 'criteria must be 1-255 described options')
    return request


def kev_body(body):
    """Identity on bytes, after checking the body maps onto Kev's schema without change."""
    check_schema(json.loads(body))
    return body


def validate_choice(answer, labels):
    """scripts/evaluate.validate_native with the sum tolerance widened to Kev's 4-decimal serialization."""
    try:
        probabilities = answer['probabilities']
    except (KeyError, TypeError) as exc:
        raise ValueError('missing native answer fields') from exc
    require(isinstance(probabilities, dict) and probabilities
            and all(evaluate.finite_number(v) for v in probabilities.values()), 'probabilities must be finite numbers')
    total = sum(probabilities.values())
    require(abs(total - 1) <= len(probabilities) * ROUNDING + 1e-9,
            'candidate probabilities do not sum to one within 4-decimal rounding')
    scaled = dict(answer, probabilities={key: value / total for key, value in probabilities.items()})
    return evaluate.validate_native({'answers': {'route': scaled}}, labels)


def answers_of(response):
    require(isinstance(response, dict), 'response must be an object')
    answers = response.get('answers')
    require(isinstance(answers, dict), 'missing native answers')
    return answers


def w1_parser(candidates):
    """fixtures._w1_parser('kev', ...) with validate_choice."""
    return lambda response: {'action': validate_choice(answers_of(response).get('route'), candidates)['choice']}


def w2_parser(criteria):
    """fixtures._w2_parser('kev', ...) for the questions this request asks; heads are valid or invalid on their own."""
    def parse(response):
        answers = answers_of(response)
        labels = {}
        for key, candidates in criteria.items():
            try:
                labels[key] = validate_choice(answers.get(key), candidates)['choice']
            except ValueError:
                labels[key] = None
        return labels
    return parse


def criteria_of(request):
    return {key: list(question['criteria']) for key, question in request['questions'].items()}


def splice_parts(body, request, serializer):
    """-> (head, {question: member bytes}, tail) such that body == head + ','.join(members) + tail."""
    head = serializer(dict(request, questions={}))[:-2]
    members = {key: serializer({key: question})[1:-1] for key, question in request['questions'].items()}
    tail = b'}}'
    require(body == head + b','.join(members.values()) + tail, 'body is not the serializer\'s bytes; cannot splice')
    return head, members, tail


def split_job(job, serializer=None):
    """Requests A and B for one W2 case: frozen state and question bytes, reference labels split to match."""
    serializer = serializer or fixtures.routing.encoded
    request = check_schema(json.loads(job.body))
    head, members, tail = splice_parts(job.body, request, serializer)
    parts = []
    for questions in SPLIT:
        part = dict(request, questions={key: request['questions'][key] for key in questions})
        body = serializer(part)
        require(body == head + b','.join(members[key] for key in questions) + tail, 'split body is not spliced verbatim')
        parts.append(dataclasses.replace(job, body=body, questions=questions,
                                         reference={key: job.reference[key] for key in questions},
                                         parse=w2_parser(criteria_of(part))))
    return parts


def w1_jobs(inputs):
    frozen = fixtures.w1_jobs('kev', inputs['w1'], inputs['w1_reference'], inputs['locks']['kev']['alias'])
    return [dataclasses.replace(job, body=kev_body(job.body),
                                parse=w1_parser(criteria_of(json.loads(job.body))['route'])) for job in frozen]


def w2_jobs(inputs, split=False):
    frozen = fixtures.w2_jobs('kev', inputs['w2'], inputs['w2_reference'])
    if split:
        return [part for job in frozen for part in split_job(job)]
    return [dataclasses.replace(job, body=kev_body(job.body), parse=w2_parser(criteria_of(json.loads(job.body))))
            for job in frozen]


def jobs_for(cell, inputs):
    """One pass of jobs; the cached-state cell interleaves A1, B1, A2, B2, ... ."""
    if cell.workload == 'w1':
        return w1_jobs(inputs)
    return w2_jobs(inputs, split=cell.split)
