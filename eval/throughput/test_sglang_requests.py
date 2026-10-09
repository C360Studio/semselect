import copy
import json
import math
import tempfile
from pathlib import Path
import time
import unittest

import fixtures
from fixtures import compare_scoring, evaluate
import sglang_requests
import sglang_runtime
from test_runner import Stub

INPUTS = fixtures.load_inputs(['w1', 'w2'])
PROBE = json.loads(sglang_runtime.PROBE_RECORD.read_text())
# 0 server info, 1-2 chat, 3 tokenize, 4 detokenize, 5 tokenize, 6-7 score, 8 decisions, 9 replay, 10 decisions, 11 replay
ROWS = PROBE['requests']
CHAT, TOKENIZE, DETOKENIZE, LABELS, SCORE, DECISIONS = (ROWS[i] for i in (1, 3, 4, 5, 6, 8))
SERVED = sglang_runtime.SERVED_MODEL


def pick(jobs, case_id, order='normal'):
    return next(job for job in jobs if (job.case_id, job.order) == (case_id, order))


def w1_source(arm):
    model = fixtures.ARM_MODEL[arm]
    return fixtures.w1_jobs(arm, INPUTS['w1'], INPUTS['w1_reference'], INPUTS['locks'][model]['alias'])


class ProbeRecordTests(unittest.TestCase):
    def test_saved_rows_are_the_ones_these_tests_name(self):
        self.assertEqual([r['path'] for r in (CHAT, TOKENIZE, DETOKENIZE, LABELS, SCORE, DECISIONS)],
                         ['/v1/chat/completions', '/v1/tokenize', '/v1/detokenize', '/v1/tokenize', '/v1/score', '/v1/decisions'])
        self.assertTrue(all(r['status'] == 200 for r in ROWS))


class W1MappingTests(unittest.TestCase):
    def test_qwen_json_bytes_change_only_model_and_cache_prompt(self):
        source = pick(w1_source('qwen_json'), 'H03').body
        mapped = pick(sglang_requests.w1_jobs('qwen_json', INPUTS), 'H03')
        self.assertTrue(source.startswith(b'{"model": "qwen3.5-4b", "messages": [{"role": "system", "content": "Decide whether'))
        self.assertTrue(source.endswith(b', "cache_prompt": true, "seed": 0}'))
        expected = source.replace(b'{"model": "qwen3.5-4b", ', b'{"model": "qwen35-4b-mlx", ', 1) \
                         .replace(b', "cache_prompt": true, "seed": 0}', b', "seed": 0}', 1)
        self.assertEqual(mapped.body, expected)
        self.assertEqual(mapped.path, '/v1/chat/completions')
        self.assertEqual(mapped.reference, {'action': 'allow'})

    def test_qwen_decisions_bytes_carry_the_kev_state_instructions_and_criteria(self):
        kev = json.loads(pick(w1_source('kev'), 'H03').body)
        route = kev['questions']['route']
        mapped = pick(sglang_requests.w1_jobs('qwen_decisions', INPUTS), 'H03')
        enc = lambda value: json.dumps(value).encode()  # noqa: E731 - W1 bodies use json.dumps defaults
        expected = (b'{"model": "qwen35-4b-mlx", "input": ' + enc(kev['state'])
                    + b', "questions": [{"id": "route", "type": "choice", "question": ' + enc(route['instructions'])
                    + b', "options": [{"name": "allow", "description": ' + enc(route['criteria']['allow'])
                    + b'}, {"name": "defer", "description": ' + enc(route['criteria']['defer'])
                    + b'}]}], "prompt_format_version": 1, "return_prompt_token_ids": true}')
        self.assertEqual(mapped.body, expected)
        self.assertTrue(route['instructions'].startswith('Decide whether the supplied evidence is sufficient'))
        self.assertEqual(mapped.path, '/v1/decisions')
        # Cross-runtime reference is the llama.cpp Qwen JSON label, not Kev's.
        self.assertEqual(mapped.reference, pick(w1_source('qwen_json'), 'H03').reference)

    def test_reverse_order_reverses_the_options(self):
        jobs = sglang_requests.w1_jobs('qwen_decisions', INPUTS)
        names = lambda job: [o['name'] for o in json.loads(job.body)['questions'][0]['options']]  # noqa: E731
        self.assertEqual(names(pick(jobs, 'H03', 'normal')), ['allow', 'defer'])
        self.assertEqual(names(pick(jobs, 'H03', 'reverse')), ['defer', 'allow'])
        self.assertEqual(len(jobs), 2 * fixtures.W1_MODEL_ROUTED_CASES)

    def test_score_jobs_keep_the_json_arm_evidence_and_wait_for_the_runtime(self):
        jobs = sglang_requests.w1_jobs('qwen_score', INPUTS)
        chat = sglang_requests.w1_jobs('qwen_json', INPUTS)
        self.assertTrue(all(job.body is None and job.path == '/v1/score' for job in jobs))
        for score, json_job in zip(jobs, chat):
            messages = compare_scoring.score_messages(INPUTS['w1'], {'text': score.score_input[0]}, list(score.score_input[1]))
            self.assertEqual(messages[1]['content'], json.loads(json_job.body)['messages'][1]['content'])


class W2MappingTests(unittest.TestCase):
    def test_qwen_json_bytes_change_only_model_and_cache_prompt(self):
        source = pick(fixtures.w2_jobs('qwen_json', INPUTS['w2'], INPUTS['w2_reference']), 'R01').body
        mapped = pick(sglang_requests.w2_jobs('qwen_json', INPUTS), 'R01').body
        self.assertIn(b',"max_tokens":128,"cache_prompt":false,"chat_template_kwargs":', source)
        expected = source.replace(b'{"model":"qwen3.5-4b",', b'{"model":"qwen35-4b-mlx",', 1) \
                         .replace(b',"max_tokens":128,"cache_prompt":false,', b',"max_tokens":128,', 1)
        self.assertEqual(mapped, expected)

    def test_qwen_decisions_is_one_request_with_three_choice_questions(self):
        kev = json.loads(pick(fixtures.w2_jobs('kev', INPUTS['w2'], INPUTS['w2_reference']), 'R01').body)
        mapped = pick(sglang_requests.w2_jobs('qwen_decisions', INPUTS), 'R01')
        enc = lambda value: json.dumps(value, ensure_ascii=False).encode()  # noqa: E731 - query-routing encoder
        questions = b','.join(
            b'{"id":' + enc(key) + b',"type":"choice","question":' + enc(head['instructions']) + b',"options":['
            + b','.join(b'{"name":' + enc(name) + b',"description":' + enc(text) + b'}' for name, text in head['criteria'].items())
            + b']}' for key, head in kev['questions'].items())
        expected = (b'{"model":"qwen35-4b-mlx","input":' + enc(kev['state']) + b',"questions":[' + questions
                    + b'],"prompt_format_version":1,"return_prompt_token_ids":true}')
        self.assertEqual(mapped.body, expected)
        body = json.loads(mapped.body)
        self.assertEqual([q['id'] for q in body['questions']], ['operation', 'node', 'field'])
        self.assertEqual([len(q['options']) for q in body['questions']], [9, 6, 5])
        self.assertEqual(mapped.questions, fixtures.FIELDS)
        self.assertEqual(mapped.reference, {'operation': 'no_override', 'node': 'gateway-main', 'field': 'none'})


class ParserTests(unittest.TestCase):
    def test_shared_baseline_parser_accepts_sglang_chat_responses(self):
        self.assertEqual(evaluate.validate_baseline(CHAT['response'], ['billing', 'unknown'])['choice'], 'billing')
        job = pick(sglang_requests.w2_jobs('qwen_json', INPUTS), 'R01')
        response = copy.deepcopy(CHAT['response'])
        response['choices'][0]['message']['content'] = '{\n  "operation": "path",\n  "node": "gateway-main",\n  "field": "none"\n}'
        self.assertEqual(job.parse(response), {'operation': 'path', 'node': 'gateway-main', 'field': 'none'})

    def test_decision_answers_from_the_probe(self):
        answers = DECISIONS['response']['answers']
        parsed = sglang_requests.validate_decision(answers['route'], ['billing', 'unknown'])
        self.assertEqual(parsed['choice'], 'billing')
        self.assertAlmostEqual(parsed['label_mass'], 0.999800645603678)
        for other in ('refund', 'urgency'):  # yes_no and score answers are not choices
            with self.assertRaisesRegex(ValueError, 'missing choice answer'):
                sglang_requests.validate_decision(answers[other], ['yes', 'no'])
        with self.assertRaisesRegex(ValueError, 'cover exactly'):
            sglang_requests.validate_decision(answers['route'], ['billing', 'other'])
        w1 = sglang_requests._w1_decisions_parser(['billing', 'unknown'])
        self.assertEqual(w1({'answers': {'route': answers['route']}}), {'action': 'billing'})
        with self.assertRaisesRegex(ValueError, 'exactly the route answer'):
            w1(DECISIONS['response'])

    def test_w2_decision_heads_are_valid_or_invalid_on_their_own(self):
        job = pick(sglang_requests.w2_jobs('qwen_decisions', INPUTS), 'R01')
        body = json.loads(job.body)
        answers = {}
        for question in body['questions']:
            names = [o['name'] for o in question['options']]
            probabilities = {name: (0.5 if i == 1 else 0.5 / (len(names) - 1)) for i, name in enumerate(names)}
            answers[question['id']] = {'type': 'choice', 'probabilities': probabilities, 'label_mass': 0.9,
                                       'choice': names[1], 'label_token_ids': list(range(32, 32 + len(names))),
                                       'prompt_token_ids': [1, 2, 3]}
        answers['field']['choice'] = 'none'  # not the maximal candidate
        self.assertEqual(job.parse({'answers': answers}), {'operation': 'similarity', 'node': 'sensor-17', 'field': None})
        answers['node']['label_token_ids'] = [32, 32, 33, 34, 35, 36]
        self.assertIsNone(job.parse({'answers': answers})['node'])

    def test_score_response_from_the_probe(self):
        parsed = sglang_requests.validate_score(SCORE['response'], ['billing', 'unknown'])
        self.assertEqual(parsed['choice'], 'billing')
        self.assertAlmostEqual(parsed['probabilities']['billing'], 0.9968273171575148)
        self.assertAlmostEqual(parsed['label_mass'], math.exp(-0.003253936767578125) + math.exp(-5.753253936767578))
        swapped = copy.deepcopy(SCORE['response'])
        swapped['scores'][0].reverse()
        with self.assertRaisesRegex(ValueError, 'not the softmax'):
            sglang_requests.validate_score(swapped, ['billing', 'unknown'])
        missing = dict(SCORE['response'], token_logprobs=None)
        with self.assertRaisesRegex(ValueError, 'raw log-probability'):
            sglang_requests.validate_score(missing, ['billing', 'unknown'])

    def test_usage_is_the_server_reported_count(self):
        self.assertEqual(sglang_requests.usage(CHAT['response']), {'prompt_tokens': 42, 'completion_tokens': 12})
        self.assertEqual(sglang_requests.usage(SCORE['response']), {'prompt_tokens': 57, 'completion_tokens': 0})
        self.assertEqual(sglang_requests.usage(DECISIONS['response']), {'prompt_tokens': 160, 'completion_tokens': 0})
        self.assertEqual(sglang_requests.usage({'usage': {'prompt_tokens': 9, 'prompt_tokens_details': {'cached_tokens': 4}}}),
                         {'prompt_tokens': 9, 'cached_tokens': 4})


class ScorePreparationTests(unittest.TestCase):
    def tokenizer_stub(self, label_ids=(32, 33)):
        """Answers with the probe's recorded tokenization, whatever the messages."""
        seen = []
        prompt_ids, prompt = TOKENIZE['response']['tokens'], DETOKENIZE['response']['text']

        def respond(path, body):
            request = json.loads(body)
            seen.append((path, request))
            if path == '/v1/tokenize' and 'messages' in request:
                return 200, json.dumps({'tokens': prompt_ids, 'count': len(prompt_ids)}).encode()
            if path == '/v1/detokenize':
                return 200, json.dumps({'text': prompt}).encode()
            if path == '/v1/tokenize':
                return 200, json.dumps({'tokens': [prompt_ids] + [prompt_ids + [i] for i in label_ids]}).encode()
            return 404, b'{}'
        return respond, seen

    def test_score_request_mirrors_the_probe_call(self):
        respond, seen = self.tokenizer_stub()
        job = pick(sglang_requests.w1_jobs('qwen_score', INPUTS), 'H03')
        with Stub(respond) as stub, tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'score-preparation.json'
            [finished] = sglang_requests.finalize_score_jobs([job], INPUTS['w1'], time.monotonic() + 30, stub.url, record)
            saved = json.loads(record.read_text())
        body = json.loads(finished.body)
        self.assertEqual(list(body), list(SCORE['request']))
        self.assertEqual(body, dict(SCORE['request'], model=SERVED))
        self.assertEqual(seen[0], ('/v1/tokenize', {'model': SERVED, 'messages': compare_scoring.score_messages(
            INPUTS['w1'], {'text': job.score_input[0]}, ['allow', 'defer']), 'chat_template_kwargs': {'enable_thinking': False}}))
        self.assertEqual(seen[2][1]['add_special_tokens'], False)
        self.assertEqual(saved[0]['label_token_ids'], {'allow': 32, 'defer': 33})
        # The probe's recorded /v1/score response, read with this job's labels.
        self.assertEqual(finished.parse(SCORE['response']), {'action': 'allow'})

    def test_labels_that_are_not_distinct_single_tokens_are_refused(self):
        respond, _ = self.tokenizer_stub(label_ids=(32, 32))
        job = pick(sglang_requests.w1_jobs('qwen_score', INPUTS), 'H03')
        with Stub(respond) as stub, self.assertRaisesRegex(ValueError, 'distinct'):
            sglang_requests.finalize_score_jobs([job], INPUTS['w1'], time.monotonic() + 30, stub.url)

    def test_exhausted_budget_prepares_nothing(self):
        job = pick(sglang_requests.w1_jobs('qwen_score', INPUTS), 'H03')
        with self.assertRaisesRegex(ValueError, 'budget'):
            sglang_requests.finalize_score_jobs([job], INPUTS['w1'], time.monotonic() - 1, 'http://127.0.0.1:9')


if __name__ == '__main__':
    unittest.main()
