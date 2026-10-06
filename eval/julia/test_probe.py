"""Offline gate, evidence and owned-lifecycle tests. Never starts Docker or inference."""
import contextlib
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import probe


def native(request):
    answers = {}
    for name, question in request['questions'].items():
        if question['type'] == 'choice':
            labels = list(question['criteria'])
            answers[name] = {'type': 'choice', 'choice': labels[0], 'confidence': .9,
                             'probabilities': {key: 1.0 if i == 0 else 0.0 for i, key in enumerate(labels)}}
        elif question['type'] == 'score':
            answers[name] = {'type': 'score', 'score': 0.0, 'confidence': .9, 'probabilities': {'0': 1., '1': 0., '2': 0.},
                             'legend': {str(i): label for i, label in enumerate(question['criteria'])}}
        else:
            answers[name] = {'type': 'noul', 'noul': .9}
    return {'answers': answers, 'usage': {'input_tokens': 1, 'output_tokens': 0}}


def synthetic_preflight(payload, tokenize):
    tokens = tokenize(payload['state'])
    return {'total_input_tokens': len(tokens), 'heads': {'route': {'rendered_prompt': payload['state'], 'tokens': tokens,
                               'prompt_tokens': len(tokens)}}}


class InferenceOps:
    def __init__(self, *, fail_call=None, error=None, latency=.1, token_time=0., bad_parity=False):
        self.elapsed = 0.
        self.fail_call = fail_call
        self.error = error or TimeoutError('synthetic timeout')
        self.latency = latency
        self.token_time = token_time
        self.bad_parity = bad_parity
        self.calls = []
        self.requests = []

    def clock(self):
        return self.elapsed

    def http(self, path, payload=None, timeout=10):
        if not 0 < timeout <= 10:
            raise AssertionError('unbounded HTTP call')
        self.requests.append((path, payload, timeout))
        if path == '/tokenize':
            self.elapsed += min(timeout, self.token_time)
            if self.token_time > timeout:
                raise TimeoutError('synthetic token timeout')
            return {'tokens': [2 if self.bad_parity and payload['parse_special'] else 1]}
        if path != '/v1/systemone':
            raise AssertionError(path)
        self.calls.append(copy.deepcopy(payload))
        self.elapsed += min(timeout, self.latency)
        if self.fail_call == len(self.calls):
            raise self.error
        if self.latency > timeout:
            raise TimeoutError('synthetic inference timeout')
        return native(payload)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.dataset = probe.evaluate.load_dataset(probe.DATASET)
        self.smoke = json.loads((probe.ROOT / 'examples/decision.json').read_text())

    def run_probe(self, ops, preflight=synthetic_preflight):
        with contextlib.redirect_stdout(io.StringIO()):
            return probe.experiment(self.dataset, self.smoke, 'synthetic', self.output, ops, preflight)

    def test_complete_gate_is_shape_and_latency_not_label_correctness(self):
        ops = InferenceOps()
        result = self.run_probe(ops)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(ops.calls), 53)
        self.assertTrue(result['gate']['passed'])
        self.assertEqual(result['summary']['total'], 48)
        self.assertTrue(result['summary']['complete'])
        self.assertLess(result['summary']['accuracy'], 1.)
        first_inference = next(i for i, call in enumerate(ops.requests) if call[0] == '/v1/systemone')
        self.assertTrue(all(call[0] == '/tokenize' for call in ops.requests[:first_inference]))
        self.assertTrue(all(call[0] == '/v1/systemone' for call in ops.requests[first_inference:]))
        self.assertEqual(result['rows'][0]['candidates'], list(self.dataset['categories']))
        self.assertEqual(result['rows'][1]['candidates'], list(reversed(self.dataset['categories'])))

    def test_slow_gate_leaves_all_quality_unrun_and_no_cohort_metrics(self):
        ops = InferenceOps(latency=2.001)
        result = self.run_probe(ops)
        self.assertEqual(len(ops.calls), 5)
        self.assertFalse(result['gate']['passed'])
        self.assertEqual(result['status'], 'stopped_by_latency_gate')
        self.assertTrue(all(row['status'] == 'not_run' for row in result['rows']))
        self.assertIsNone(result['summary']['accuracy'])
        self.assertEqual(result['summary']['latency_ms'], {'p50': None, 'p95': None, 'max': None})

    def test_failed_probe_stops_at_first_timeout(self):
        ops = InferenceOps(fail_call=4)
        result = self.run_probe(ops)
        self.assertEqual(len(ops.calls), 4)
        self.assertEqual(result['probes'][1]['status'], 'timeout')
        self.assertEqual(result['probes'][2]['status'], 'not_run')
        self.assertFalse(result['gate']['passed'])
        self.assertEqual(result['summary']['valid'], 0)

    def test_late_quality_error_preserves_prior_raw_results_but_no_accuracy(self):
        ops = InferenceOps(fail_call=7, error=probe.HTTPFailure(500, 'synthetic body'))
        result = self.run_probe(ops)
        self.assertEqual(len(ops.calls), 7)
        self.assertEqual(result['rows'][0]['status'], 'ok')
        self.assertIn('response', result['rows'][0])
        self.assertEqual(result['rows'][1]['status'], 'error')
        self.assertEqual(result['rows'][1]['response']['body'], 'synthetic body')
        self.assertTrue(all(row['status'] == 'not_run' for row in result['rows'][2:]))
        self.assertIsNone(result['summary']['accuracy'])
        stored = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(stored, result)

    def test_final_planned_preflight_failure_prevents_all_inference(self):
        calls = 0
        def check(payload, tokenize):
            nonlocal calls
            calls += 1
            if calls == 53:
                raise ValueError('would truncate final case')
            return synthetic_preflight(payload, tokenize)
        ops = InferenceOps()
        result = self.run_probe(ops, check)
        self.assertEqual(ops.calls, [])
        self.assertEqual(result['rows'][-1]['preflight_status'], 'failed')
        self.assertTrue(all(row['status'] == 'not_run' for _, row in probe.planned_rows(result)))

    def test_native_token_parity_failure_prevents_inference(self):
        ops = InferenceOps(bad_parity=True)
        result = self.run_probe(ops)
        self.assertEqual(ops.calls, [])
        self.assertEqual(result['smoke']['preflight_status'], 'failed')
        self.assertIn('token IDs differ', result['error'])

    def test_tokenization_counts_against_wall_budget(self):
        ops = InferenceOps(token_time=9)
        result = self.run_probe(ops)
        self.assertEqual(ops.calls, [])
        self.assertLessEqual(ops.elapsed, 180.)
        self.assertEqual(result['status'], 'stopped_on_error')

    def test_interrupt_preserves_interrupted_and_unrun(self):
        ops = InferenceOps(fail_call=7, error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_probe(ops)
        stored = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(stored['rows'][0]['status'], 'ok')
        self.assertEqual(stored['rows'][1]['status'], 'interrupted')
        self.assertEqual(stored['rows'][2]['status'], 'not_run')
        self.assertIsNone(stored['summary']['accuracy'])

    def test_malformed_score_is_stopped_before_routing(self):
        class BadSmoke(InferenceOps):
            def http(self, path, payload=None, timeout=10):
                response = super().http(path, payload, timeout)
                if path == '/v1/systemone':
                    response['answers']['urgency']['score'] = True
                return response
        ops = BadSmoke()
        result = self.run_probe(ops)
        self.assertEqual(len(ops.calls), 1)
        self.assertEqual(result['smoke']['status'], 'invalid')
        self.assertEqual(result['warmup']['status'], 'not_run')

    def test_request_manifest_preserves_order_and_all_planned_calls(self):
        report = probe.make_plan(self.dataset, self.smoke, 'synthetic')
        checksum = probe.freeze_requests(report, self.output)
        requests = json.loads((self.output / 'requests.json').read_text())
        self.assertEqual(len(requests), 53)
        self.assertEqual(checksum, probe.digest(self.output / 'requests.json'))
        self.assertEqual(list(requests[5]['request']['questions']['route']['criteria']), list(self.dataset['categories']))
        self.assertEqual(list(requests[6]['request']['questions']['route']['criteria']), list(reversed(self.dataset['categories'])))

    def test_final_response_after_budget_cannot_mark_complete(self):
        class LateFinal(InferenceOps):
            def http(self, path, payload=None, timeout=10):
                response = super().http(path, payload, timeout)
                if len(self.calls) == 53:
                    self.elapsed = 181.
                return response
        result = self.run_probe(LateFinal())
        self.assertEqual(result['rows'][-1]['status'], 'timeout')
        self.assertIsNone(result['summary']['accuracy'])

    def test_native_usage_must_match_exact_preflight(self):
        class WrongUsage(InferenceOps):
            def http(self, path, payload=None, timeout=10):
                response = super().http(path, payload, timeout)
                if path == '/v1/systemone':
                    response['usage']['input_tokens'] = 999
                return response
        result = self.run_probe(WrongUsage())
        self.assertEqual(result['smoke']['status'], 'invalid')
        self.assertEqual(result['warmup']['status'], 'not_run')

    def test_boolean_output_tokens_is_not_an_integer_zero(self):
        class BooleanUsage(InferenceOps):
            def http(self, path, payload=None, timeout=10):
                response = super().http(path, payload, timeout)
                if path == '/v1/systemone':
                    response['usage']['output_tokens'] = False
                return response
        result = self.run_probe(BooleanUsage())
        self.assertEqual(result['smoke']['status'], 'invalid')
        self.assertEqual(result['warmup']['status'], 'not_run')

    def test_malformed_semantic_bytes_and_tokenization_bytes_survive_parsing(self):
        token_body = b' { "tokens": [1] }\n'
        class RealTransport(InferenceOps):
            http = probe.Operations.http
        for bad_body in (b'\xff\xfe', b'{"answers":'):
            with self.subTest(body=bad_body):
                def open_response(request, timeout):
                    response = io.BytesIO(token_body if request.full_url.endswith('/tokenize') else bad_body)
                    response.status = 200
                    return response
                with patch.object(probe.urllib.request, 'urlopen', side_effect=open_response):
                    result = self.run_probe(RealTransport())
                receipt = result['smoke']['transport_receipt']
                self.assertEqual(base64.b64decode(receipt['body_base64']), bad_body)
                self.assertEqual(receipt['sha256'], hashlib.sha256(bad_body).hexdigest())
                self.assertEqual(receipt['observed_byte_count'], len(bad_body))
                self.assertEqual(receipt['http_status'], 200)
                self.assertFalse(receipt['truncated'])
                token_receipt = result['smoke']['tokenizations'][0]['transport_receipt']
                self.assertEqual(base64.b64decode(token_receipt['body_base64']), token_body)
                self.assertEqual(result['smoke']['status'], 'invalid')

    def test_primitive_extra_answer_or_bad_score_confidence_rejected(self):
        request = dict(self.smoke, model='synthetic')
        for mutation in ('extra', 'confidence'):
            with self.subTest(mutation=mutation):
                response = native(request)
                if mutation == 'extra':
                    response['answers']['extra'] = {}
                else:
                    response['answers']['urgency']['confidence'] = float('nan')
                with self.assertRaises(ValueError):
                    probe.validate_smoke(response, request)

    def test_protocol_profile_drift_rejected(self):
        protocol = json.loads((probe.ROOT / 'eval/julia/protocol.json').read_text())
        for field, value in [('context_tokens', 512), ('cpu_limit', 8), ('inference_and_tokenization_budget_seconds', 360)]:
            with self.subTest(field=field):
                changed = dict(protocol, **{field: value})
                with self.assertRaises(ValueError):
                    probe.validate_protocol(changed, self.dataset)


class LifecycleOps:
    def __init__(self, lock):
        self.lock = lock
        self.commands = []
        self.container = None
        self.elapsed = 0.
        self.stop_stays_running = False
        self.fail_stop = False
        self.ready = True
        self.image_id = probe.IMAGE_ID
        self.port_checks = 0

    def clock(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds

    def closed(self):
        self.port_checks += 1

    def http(self, path, payload=None, timeout=10):
        if path == '/health':
            if not self.ready:
                raise OSError('synthetic not ready')
            return {'status': 'ok'}
        if path == '/props':
            return {'default_generation_settings': {'n_ctx': 1024}, 'total_slots': 1}
        raise AssertionError(path)

    def run(self, command, timeout=30):
        if not 0 < timeout <= 30:
            raise AssertionError('unbounded command')
        self.commands.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': self.image_id, 'Os': 'linux', 'Architecture': 'arm64',
                'Config': {'Labels': {'io.semselect.llama-revision': probe.REVISION}}}])
        if command[:2] == ['docker', 'ps']:
            return '{"Names":"unrelated"}\n'
        if command[:2] == ['docker', 'create']:
            owner = next(arg.split('=', 1)[1] for arg in command if arg.startswith('io.semselect.owner='))
            self.container = {'Id': 'b' * 64, 'Image': probe.IMAGE_ID,
                'Config': {'Labels': {'io.semselect.owner': owner, 'io.semselect.experiment': 'julia-cpu'}},
                'HostConfig': {'NanoCpus': 4_000_000_000, 'Memory': probe.MEMORY, 'MemorySwap': probe.MEMORY,
                    'ReadonlyRootfs': True, 'DeviceRequests': [], 'NetworkMode': 'bridge', 'CapDrop': ['ALL'],
                    'PidsLimit': 128, 'SecurityOpt': ['no-new-privileges:true'],
                    'PortBindings': {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(probe.PORT)}]}},
                'Mounts': [{'Destination': '/models/model.gguf', 'Source': str(probe.ROOT / 'models' / self.lock['filename']),
                            'Type': 'bind', 'RW': False}],
                'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}
            return self.container['Id']
        if command[:2] == ['docker', 'inspect']:
            return json.dumps([self.container])
        if command[:2] == ['docker', 'start']:
            self.container['State']['Running'] = True
            return self.container['Id']
        if command[:2] == ['docker', 'exec']:
            return '123\n'
        if command[:2] == ['docker', 'stop']:
            if self.fail_stop:
                raise subprocess.TimeoutExpired(command, timeout)
            if not self.stop_stays_running:
                self.container['State']['Running'] = False
            return self.container['Id']
        if command[:2] == ['docker', 'kill']:
            self.container['State']['Running'] = False
            return self.container['Id']
        if command[:2] == ['docker', 'logs']:
            return 'synthetic complete runtime logs\n'
        raise AssertionError(command)


class TransportTests(unittest.TestCase):
    def test_http_error_keeps_lossless_bounded_prefix(self):
        body = b'\xff' + b'z' * (probe.evaluate.MAX_RESPONSE_BYTES + 50)
        error = urllib.error.HTTPError('http://127.0.0.1/', 500, 'synthetic', {}, io.BytesIO(body))
        ops = probe.Operations()
        with patch.object(probe.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(probe.HTTPFailure):
                ops.http('/v1/systemone', {})
        receipt = ops.last_http_receipt
        prefix = body[:probe.evaluate.MAX_RESPONSE_BYTES]
        self.assertEqual(base64.b64decode(receipt['body_base64']), prefix)
        self.assertEqual(receipt['sha256'], hashlib.sha256(prefix).hexdigest())
        self.assertEqual(receipt['http_status'], 500)
        self.assertEqual(receipt['observed_byte_count'], probe.evaluate.MAX_RESPONSE_BYTES + 1)
        self.assertEqual(receipt['retained_byte_count'], len(prefix))
        self.assertTrue(receipt['truncated'])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.lock = {'filename': 'Julia.gguf', 'alias': 'synthetic'}
        self.ops = LifecycleOps(self.lock)
        self.runtime = probe.Runtime(self.lock, self.output, self.ops)

    def test_owned_container_is_immutable_read_only_and_verified_stopped(self):
        self.runtime.start()
        self.runtime.stop()
        create = next(command for command in self.ops.commands if command[:2] == ['docker', 'create'])
        self.assertIn('--pull=never', create)
        self.assertIn(probe.IMAGE_ID, create)
        self.assertNotIn(probe.IMAGE, create)
        self.assertTrue(self.runtime.metadata['stopped'])
        self.assertEqual(self.runtime.metadata['cgroup_before_stop']['memory.peak'], '123')
        self.assertIn('unrelated', self.runtime.metadata['other_running_containers'][0])
        self.assertTrue((self.output / 'runtime.log').is_file())
        self.assertEqual(self.ops.port_checks, 2)
        stops = [c for c in self.ops.commands if c[:2] in (['docker', 'stop'], ['docker', 'kill'])]
        self.assertEqual([c[-1] for c in stops], ['b' * 64])

    def test_ownership_mismatch_never_stops_container(self):
        self.runtime.start()
        self.ops.container['Config']['Labels']['io.semselect.owner'] = 'another-owner'
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            self.runtime.stop()
        self.assertFalse(self.runtime.metadata['stopped'])
        self.assertFalse(any(c[:2] in (['docker', 'stop'], ['docker', 'kill']) for c in self.ops.commands))

    def test_stop_command_success_does_not_substitute_for_verified_exit(self):
        self.runtime.start()
        self.ops.stop_stays_running = True
        with self.assertRaisesRegex(RuntimeError, 'remains running'):
            self.runtime.stop()
        self.assertFalse(self.runtime.metadata['stopped'])

    def test_stop_timeout_uses_owned_id_kill_and_verifies_exit(self):
        self.runtime.start()
        self.ops.fail_stop = True
        self.runtime.stop()
        self.assertIn(['docker', 'kill', 'b' * 64], self.ops.commands)
        self.assertTrue(self.runtime.metadata['stopped'])

    def test_readiness_timeout_can_still_cleanup_owned_container(self):
        self.ops.ready = False
        with self.assertRaises(TimeoutError):
            self.runtime.start()
        self.assertLessEqual(self.ops.elapsed, 90.)
        self.runtime.stop()
        self.assertTrue(self.runtime.metadata['stopped'])

    def test_changed_image_blocks_creation(self):
        self.ops.image_id = 'sha256:' + 'd' * 64
        with self.assertRaisesRegex(ValueError, 'image'):
            self.runtime.start()
        self.assertFalse(self.runtime.create_attempted)
        self.assertFalse(any(c[:2] == ['docker', 'create'] for c in self.ops.commands))

    def test_readiness_props_cannot_extend_deadline(self):
        prior_http = self.ops.http
        timeouts = []
        def delayed_props(path, payload=None, timeout=10):
            timeouts.append((path, timeout))
            if path == '/health':
                self.ops.elapsed = 89.5
            elif path == '/props':
                self.ops.elapsed += 1.
            return prior_http(path, payload, timeout)
        self.ops.http = delayed_props
        with self.assertRaises(TimeoutError):
            self.runtime.start()
        self.assertEqual(next(timeout for path, timeout in timeouts if path == '/props'), .5)
        self.runtime.stop()
        self.assertTrue(self.runtime.metadata['stopped'])

    def test_main_masks_second_signals_during_cleanup_and_restores_handlers(self):
        handlers_before = [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
        cleanup_handlers = []
        class InterruptedRuntime:
            def __init__(self, *args):
                pass
            def start(self):
                raise KeyboardInterrupt('first interruption')
            def stop(self):
                cleanup_handlers.extend(signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM))
        target = self.output / 'interrupted'
        with patch.object(probe, 'Runtime', InterruptedRuntime), patch.object(probe.model, 'verify', return_value=True), \
                patch.object(sys, 'argv', ['probe.py', '--output', str(target)]), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(probe.main(), 1)
        self.assertEqual(cleanup_handlers, [signal.SIG_IGN, signal.SIG_IGN])
        self.assertEqual([signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)], handlers_before)
        failure = json.loads((target / 'failure.json').read_text())
        self.assertIn('KeyboardInterrupt: first interruption', failure['error'])
        self.assertEqual(len(json.loads((target / 'requests.json').read_text())), 53)
        self.assertTrue((target / 'source/eval/julia/probe.py').is_file())


if __name__ == '__main__':
    unittest.main()
