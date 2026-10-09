import hashlib
import json
import unittest

import fixtures
import evaluate
import kevmlx_requests
import runner
from test_runner import Stub

INPUTS = fixtures.load_inputs(['w1', 'w2'])


def to_answers(raw, criteria):
    """kev/api.py to_answers (api.py:149-160) for Choice questions: argmax of the unrounded distribution,
    probabilities and confidence rounded to 4 decimals (round_prob, api.py:143-146; choice_confidence, api.py:127-130)."""
    answers = {}
    for qid, p in raw.items():
        keys, total = list(criteria[qid]), sum(p)
        k = len(p)
        confidence = 1.0 if k == 1 else (max(x / total for x in p) - 1 / k) / (1 - 1 / k)
        answers[qid] = {'type': 'choice', 'choice': keys[max(range(k), key=lambda i: p[i])],
                        'confidence': round(float(confidence), 4),
                        'probabilities': {key: round(float(v), 4) for key, v in zip(keys, p)}}
    return answers


def kev_response(answers, model='semselect-kev-4b'):
    """Server._body (serve.py:211-220) with truncate_states off."""
    return {'model': model, 'answers': answers, 'usage': {'input_tokens': 1234, 'output_tokens': 56}, 'latency_ms': 812.4}


def first_choice(body):
    """Kev-shaped answer favouring each question's first candidate."""
    request = json.loads(body)
    criteria = kevmlx_requests.criteria_of(request)
    raw = {qid: [0.5] + [0.5 / (len(keys) - 1)] * (len(keys) - 1) for qid, keys in criteria.items()}
    return kev_response(to_answers(raw, criteria), request['model'])


class RequestMappingTests(unittest.TestCase):
    def test_w1_body_is_the_serial_request_byte_for_byte(self):
        job = kevmlx_requests.w1_jobs(INPUTS)[0]
        serial = INPUTS['w1_reference']['requests'][('kev', 'H03', 'normal')]
        self.assertEqual((job.case_id, job.order, job.path), ('H03', 'normal', '/v1/systemone'))
        self.assertEqual(job.body, json.dumps(serial).encode())
        self.assertEqual(len(job.body), 1901)
        self.assertEqual(hashlib.sha256(job.body).hexdigest(), '734d6a60b608696b2db2dd4e769a9b3af6f5d59cfc03ef70775fabf8538c4747')
        self.assertTrue(job.body.startswith(b'{"model": "semselect-kev-4b", "state": "{\\"question\\": \\"Which retry preset'))
        self.assertEqual(list(json.loads(job.body)['questions']['route']['criteria']), ['allow', 'defer'])

    def test_every_frozen_body_is_sent_unchanged(self):
        w1 = fixtures.w1_jobs('kev', INPUTS['w1'], INPUTS['w1_reference'], INPUTS['locks']['kev']['alias'])
        w2 = fixtures.w2_jobs('kev', INPUTS['w2'], INPUTS['w2_reference'])
        self.assertEqual([j.body for j in kevmlx_requests.w1_jobs(INPUTS)], [j.body for j in w1])
        self.assertEqual([j.body for j in kevmlx_requests.w2_jobs(INPUTS)], [j.body for j in w2])
        self.assertEqual((len(w1), len(w2)), (44, 32))

    def test_w2_split_splices_state_and_question_bytes(self):
        frozen = fixtures.w2_jobs('kev', INPUTS['w2'], INPUTS['w2_reference'])[0].body
        a, b = kevmlx_requests.w2_jobs(INPUTS, split=True)[:2]
        head_end = frozen.index(b'"questions":{') + len(b'"questions":{')
        node_at = frozen.index(b',"node":{"type":"choice"')
        self.assertEqual(a.body, frozen[:head_end] + frozen[head_end:node_at] + b'}}')
        self.assertEqual(b.body, frozen[:head_end] + frozen[node_at + 1:])
        self.assertEqual(hashlib.sha256(frozen).hexdigest(), 'f75bc042b8c5370bc813e19d3c4b735e7e98bf26aed988b84ed8cf612f71aa4c')
        self.assertEqual(hashlib.sha256(a.body).hexdigest(), 'aacaad16f3a673086da08859fd94dd05e0c0e51365751b65667c7aee4882f560')
        self.assertEqual(hashlib.sha256(b.body).hexdigest(), 'd82a91ead5ce535dfb2ee778816c6dbb75dc89f9a20deb5973eb5925b5633624')
        self.assertEqual((len(frozen), len(a.body), len(b.body)), (8999, 6933, 6860))
        self.assertEqual((a.case_id, a.questions, a.reference), ('R01', ('operation',), {'operation': 'path'}))
        self.assertEqual((b.case_id, b.questions, b.reference), ('R01', ('node', 'field'), {'node': 'gateway-main', 'field': 'none'}))

    def test_cached_cell_interleaves_a_then_b_for_every_case(self):
        split = kevmlx_requests.w2_jobs(INPUTS, split=True)
        frozen = fixtures.w2_jobs('kev', INPUTS['w2'], INPUTS['w2_reference'])
        self.assertEqual(len(split), 64)
        self.assertEqual([j.case_id for j in split], [j.case_id for j in frozen for _ in range(2)])
        self.assertEqual({j.questions for j in split[0::2]} | {j.questions for j in split[1::2]}, {('operation',), ('node', 'field')})
        self.assertTrue(all(j.questions == ('operation',) for j in split[0::2]))
        for a, b, whole in zip(split[0::2], split[1::2], frozen):
            whole_request, parts = json.loads(whole.body), [json.loads(a.body), json.loads(b.body)]
            self.assertTrue(all(p['state'] == whole_request['state'] and p['model'] == whole_request['model'] for p in parts))
            self.assertEqual({**parts[0]['questions'], **parts[1]['questions']}, whole_request['questions'])
            self.assertEqual({**a.reference, **b.reference}, whole.reference)

    def test_schema_refuses_what_kev_would_read_differently(self):
        body = json.loads(kevmlx_requests.w1_jobs(INPUTS)[0].body)
        with self.assertRaisesRegex(ValueError, 'SystemOneRequest'):
            kevmlx_requests.kev_body(json.dumps(dict(body, seed=0)).encode())
        body['questions']['route']['type'] = 'noul'
        with self.assertRaisesRegex(ValueError, 'Choice'):
            kevmlx_requests.kev_body(json.dumps(body).encode())


class ResponseParsingTests(unittest.TestCase):
    def test_four_decimal_rounding_fails_the_shared_validator_but_not_the_kev_parser(self):
        job = kevmlx_requests.w2_jobs(INPUTS)[0]
        criteria = kevmlx_requests.criteria_of(json.loads(job.body))
        operation = [1 / 9 - 0.00003 / 8] * 9
        operation[criteria['operation'].index('path')] = 1 / 9 + 0.00003
        raw = {'operation': operation,
               'node': [0.0623, 0.0043, 0.0076, 0.9055, 0.0077, 0.0126],
               'field': [0.7517, 0.0392, 0.0413, 0.0844, 0.0834]}
        response = kev_response(to_answers(raw, criteria))
        self.assertAlmostEqual(sum(response['answers']['operation']['probabilities'].values()), 0.9999, places=9)
        with self.assertRaisesRegex(ValueError, 'sum to one'):
            evaluate.validate_native({'answers': {'route': response['answers']['operation']}}, criteria['operation'])
        self.assertEqual(job.parse(response), {'operation': 'path', 'node': 'gateway-main', 'field': 'none'})
        self.assertEqual(job.parse(response), job.reference)

    def test_w1_response_and_row_shape(self):
        job = kevmlx_requests.w1_jobs(INPUTS)[0]
        criteria = kevmlx_requests.criteria_of(json.loads(job.body))
        payload = json.dumps(kev_response(to_answers({'route': [0.23456, 0.76544]}, criteria))).encode()
        with Stub(lambda path, body: (200, payload)) as stub:
            row = runner.call(stub.url, job, 5)
        self.assertEqual((row['status'], row['labels']), ('ok', {'action': 'defer'}))
        self.assertEqual(row['tokens'], {'input_tokens': 1234})
        self.assertEqual(row['matched'], int(job.reference['action'] == 'defer'))

    def test_invalid_heads_are_none_and_other_heads_survive(self):
        job = kevmlx_requests.w2_jobs(INPUTS)[0]
        criteria = kevmlx_requests.criteria_of(json.loads(job.body))
        answers = to_answers({key: [1 / len(v)] * len(v) for key, v in criteria.items()}, criteria)
        answers['node']['probabilities'] = {k: 0.1 for k in criteria['node']}  # sums to 0.6: beyond rounding
        answers['field'].update(choice='pressure', probabilities={'none': 0.6, 'temperature': 0.1, 'pressure': 0.1,
                                                                  'power': 0.1, 'humidity': 0.1})  # choice not maximal
        labels = job.parse(kev_response(answers))
        self.assertEqual(labels, {'operation': 'no_override', 'node': None, 'field': None})
        with self.assertRaisesRegex(ValueError, 'missing native answers'):
            job.parse({'model': 'semselect-kev-4b'})


if __name__ == '__main__':
    unittest.main()
