"""Execution boundaries tested without servers or held-out classification."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import experiment as e
import runner as r


def native(choice, candidates):
    return {'type': 'choice', 'choice': choice, 'probabilities': {c: float(c == choice) for c in candidates}, 'confidence': 1.0}


class Response:
    status = 200
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, size):
        return self.body[:size]


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.protocol = e.load('protocol.json')
        self.training = e.load('training.json')
        self.case = e.load('development.json')['cases'][0]

    def test_native_requires_every_choice_and_rejects_incompatible_tuple(self):
        request = e.build_request(self.case, self.training, self.protocol, 'kev')
        answers = {k: native('no_override' if k == 'operation' else 'none', request['questions'][k]['criteria']) for k in e.FIELDS}
        self.assertEqual(r.decode_response({'answers': answers}, 'kev', request, self.protocol)[1], {})
        with self.assertRaises(ValueError):
            r.decode_response({'answers': {'operation': answers['operation']}}, 'kev', request, self.protocol)
        answers['node'] = native('pump-42', request['questions']['node']['criteria'])
        with self.assertRaises(ValueError):
            r.decode_response({'answers': answers}, 'kev', request, self.protocol)

    def test_json_rejects_truncation_duplicate_keys_and_wrong_types(self):
        request = e.build_request(self.case, self.training, self.protocol, 'qwen_json')
        valid = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"operation":"no_override","node":"none","field":"none"}'}}]}
        self.assertEqual(r.decode_response(valid, 'qwen_json', request, self.protocol)[1], {})
        for finish, content in [('length', valid['choices'][0]['message']['content']), ('stop', '{"operation":"no_override","operation":"path","node":"none","field":"none"}'), ('stop', '{"operation":[],"node":"none","field":"none"}')]:
            with self.assertRaises(ValueError):
                r.decode_response({'choices':[{'finish_reason':finish,'message':{'content':content}}]}, 'qwen_json', request, self.protocol)

    def test_exact_wire_bytes_and_invalid_response_stays_failure(self):
        request, body = r.request_for(self.case, 'qwen_json', 'normal', self.protocol, self.training)
        def respond(wire, timeout):
            self.assertEqual(wire.data, body)
            return Response(b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}')
        with patch.object(r.urllib.request, 'urlopen', side_effect=respond):
            record = r.call_model('http://127.0.0.1:18087/v1/chat/completions', request, body, 'qwen_json', self.protocol, 1)
        self.assertEqual(record['status'], 'invalid')
        self.assertIsNone(record['options'])
        self.assertIn('response_body', record)
        self.assertEqual(record['model_requests'], 1)

    def test_manifest_mismatch_stops_before_http(self):
        request_load = e.load
        def changed(name):
            result = request_load(name)
            if name == 'request-manifest.json':
                for row in result:
                    row['sha256'] = '0'*64
            return result
        with patch.object(e, 'load', side_effect=changed), patch.object(r.urllib.request, 'urlopen') as http:
            with self.assertRaises(ValueError):
                r.request_for(self.case, 'kev', 'normal', self.protocol, self.training)
            http.assert_not_called()

    def test_failure_denominators_and_conflicting_flags(self):
        cases = [self.case, {'id':'synthetic-bound','gold':{'operation':'path','node':'sensor-17','field':'none','options':{'path_intent':True,'path_start_node':'sensor-17'}}}]
        rows = r.pending(cases, ['qwen_json'], ['normal'])
        rows[0].update(status='invalid', options={})
        summary = r.grade(rows,cases)['qwen_json/normal']
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['exact'], 0)
        self.assertEqual(summary['bound_cases'], 1)
        self.assertEqual(summary['bindings_correct'], 0)
        self.assertIsNone(r.operation({'path_intent':True,'path_start_node':'sensor-17','aggregation_type':'count'}))

    def test_interrupted_call_preserves_attempt_in_pending_row(self):
        request, body = r.request_for(self.case, 'qwen_json', 'normal', self.protocol, self.training)
        record = {'id': self.case['id'], 'status': 'not_run'}
        with patch.object(r.urllib.request, 'urlopen', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                r.call_model('http://127.0.0.1:18087/v1/chat/completions', request, body, 'qwen_json', self.protocol, 1, record)
        self.assertEqual(record['status'], 'interrupted')
        self.assertEqual(record['model_requests'], 1)
        self.assertEqual(record['request_sha256'], r.hashlib.sha256(body).hexdigest())

    def test_malformed_choice_container_is_invalid_not_an_interruption(self):
        request, body = r.request_for(self.case, 'qwen_json', 'normal', self.protocol, self.training)
        with patch.object(r.urllib.request, 'urlopen', return_value=Response(b'{"choices":[null]}')):
            record = r.call_model('http://127.0.0.1:18087/v1/chat/completions', request, body, 'qwen_json', self.protocol, 1)
        self.assertEqual(record['status'], 'invalid')
        self.assertIsNone(record['options'])

    def test_invalid_utf8_is_preserved_losslessly_and_not_repaired(self):
        request, body = r.request_for(self.case, 'qwen_json', 'normal', self.protocol, self.training)
        raw=b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"operation\\":\\"no_override\\",\\"node\\":\\"none\\",\\"field\\":\\"none\\"}"}}],"extra":"\xff"}'
        with patch.object(r.urllib.request, 'urlopen', return_value=Response(raw)):
            record = r.call_model('http://127.0.0.1:18087/v1/chat/completions', request, body, 'qwen_json', self.protocol, 1)
        self.assertEqual(record['status'], 'invalid')
        self.assertEqual(__import__('base64').b64decode(record['response_bytes_base64']),raw)

    def test_second_arm_preflight_failure_prevents_all_inference_and_stops_both(self):
        original_load = e.load
        def small(name):
            if name in ('heldout.json', 'development.json'):
                return {'cases': [self.case]}
            return original_load(name)
        def start(hardware, arm, lock, directory, profile):
            directory.mkdir()
            return SimpleNamespace(base_url='http://127.0.0.1:18087', inference_url='http://127.0.0.1:18087/v1/chat/completions', metadata={'arm':arm})
        def check(request, arm, *_, **__):
            if arm == 'kev':
                raise r.runtime.PreflightError('too many tokens', {'arm':arm,'fits':False})
            return {'fits':True}
        with tempfile.TemporaryDirectory() as temp, patch.object(e, 'load', side_effect=small), patch.object(r.runtime,'start',side_effect=start), patch.object(r.runtime,'stop') as stop, patch.object(r.runtime,'preflight',side_effect=check), patch.object(r,'call_model') as infer:
            directory=Path(temp)/'run'
            with self.assertRaises(r.runtime.PreflightError):
                r.run_hardware('metal',directory,{'profile':{'context':4096,'batch':1024}})
            infer.assert_not_called()
            self.assertEqual(stop.call_count,2)
            result=json.loads((directory/'result.json').read_text())
            self.assertTrue(all(row['status']=='not_run' for row in result['rows']))
            self.assertEqual(json.loads((directory/'preflight-kev/preflight-failure.json').read_text())['record']['fits'],False)


if __name__ == '__main__':
    unittest.main()
