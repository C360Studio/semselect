"""Synthetic lifecycle and tokenizer tests; never start servers or use held-out data."""
import copy
import errno
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import runtime


class Child:
    def __init__(self, stubborn=False):
        self.code = None
        self.stubborn = stubborn
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.code = 0

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout):
        if self.code is None:
            raise subprocess.TimeoutExpired('fake-child', timeout)
        return self.code


class FakeOperations:
    def __init__(self):
        self.commands = []
        self.children = []
        self.calls = []
        self.ports = []
        self.ready_calls = 0
        self.fail_ready = None
        self.cancel_ready = None
        self.context = 4096
        self.padding = 8
        self.tail_padding = 0
        self.bad_markers = False
        self.container = None

    def artifacts(self, *args):
        return {'runtime_revision': runtime.metal.REVISION, 'guard_sha256': 'test'}

    def template(self):
        return {'template': runtime.KEV_TEMPLATE, 'model_sha256': 'synthetic-test'}

    def available(self, port):
        self.ports.append(('available', port))

    def closed(self, port):
        self.ports.append(('closed', port))

    def launch(self, command, env, log):
        self.commands.append(command)
        child = Child()
        self.children.append(child)
        if command[0] == str(runtime.metal.SERVER):
            log.write('using device MTL0 (Apple Test)\noffloaded 33/33 layers to GPU\n')
            log.flush()
        return child

    def run(self, command, timeout=30):
        self.commands.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': runtime.CPU_IMAGE_ID, 'Architecture': 'arm64', 'Os': 'linux',
                                'Config': {'Labels': {'io.semselect.llama-revision': runtime.metal.REVISION}}}])
        if command[:2] == ['docker', 'create']:
            owner = next(x.split('=', 1)[1] for x in command if x.startswith('io.semselect.owner='))
            self.container = {'Id': 'a' * 64, 'Image': runtime.CPU_IMAGE_ID,
                              'Config': {'Labels': {'io.semselect.experiment': 'query-routing', 'io.semselect.owner': owner}},
                              'HostConfig': {'NanoCpus': 4_000_000_000, 'Memory': 8 * 1024**3,
                                             'MemorySwap': 8 * 1024**3, 'ReadonlyRootfs': True,
                                             'DeviceRequests': None, 'NetworkMode': 'bridge',
                                             'PortBindings': {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18087'}]}},
                              'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}
            return self.container['Id']
        if command[:2] == ['docker', 'inspect']:
            return json.dumps([self.container])
        if command[:2] == ['docker', 'start']:
            self.container['State']['Running'] = True
            return self.container['Id']
        if command[:2] in (['docker', 'stop'], ['docker', 'kill']):
            self.container['State']['Running'] = False
            return self.container['Id']
        if command[:2] == ['docker', 'logs']:
            return 'synthetic-runtime-log\n'
        raise AssertionError('Unexpected OS command: ' + repr(command))

    def ready(self, url, children, timeout, container_check=None):
        self.ready_calls += 1
        if self.ready_calls == self.fail_ready:
            raise RuntimeError('synthetic readiness failure')
        if self.ready_calls == self.cancel_ready:
            raise KeyboardInterrupt('synthetic cancellation')
        if container_check:
            assert container_check()['State']['Running']

    def http(self, url, body=None):
        self.calls.append((url, copy.deepcopy(body)))
        if url.endswith('/props'):
            return {'default_generation_settings': {'n_ctx': self.context}, 'total_slots': 1}
        if url.endswith('/apply-template'):
            return {'prompt': 'ACTUAL SERVER TEMPLATE ' + body['messages'][0]['content']}
        if url.endswith('/tokenize'):
            if body['content'] == '<|box_end|>':
                return {'tokens': [99]}
            count = body['content'].count('<|box_end|>')
            if self.bad_markers:
                count = 0
            return {'tokens': [3] * self.padding + [99] * count + [4] * (1 + self.tail_padding)}
        raise AssertionError('Unexpected metadata endpoint: ' + url)


def lock(arm):
    return json.loads((runtime.ROOT / ('models.lock.json' if arm == 'kev' else 'models.baseline.lock.json')).read_text())


def kev_request():
    return {'model': 'synthetic', 'state': 'synthetic state <|box_end|>', 'questions': {
        name: {'type': 'choice', 'instructions': 'Choose ' + name,
               'criteria': {'b': 'second', 'a': 'first'}} for name in ('operation', 'node', 'field')}}


class LifecycleTests(unittest.TestCase):
    def test_native_owned_cleanup_and_idempotence(self):
        ops = FakeOperations()
        with tempfile.TemporaryDirectory() as directory:
            handle = runtime.start('metal', 'kev', lock('kev'), Path(directory) / 'run', {}, _ops=ops)
            self.assertEqual(handle.inference_url, 'http://127.0.0.1:18088/v1/systemone')
            self.assertEqual(len(ops.children), 2)
            runtime.stop(handle)
            before = len(ops.commands), len(ops.ports)
            runtime.stop(handle)
            self.assertEqual(before, (len(ops.commands), len(ops.ports)))
            self.assertTrue(all(c.terminated and c.poll() is not None for c in ops.children))
            self.assertIn(('closed', 18087), ops.ports)
            self.assertIn(('closed', 18088), ops.ports)
            self.assertTrue(json.loads((Path(directory) / 'run/runtime.json').read_text())['stopped'])

    def test_cancel_during_guard_readiness_reaps_both_native_children(self):
        ops = FakeOperations()
        ops.cancel_ready = 2
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                runtime.start('metal', 'kev', lock('kev'), Path(directory) / 'run', {}, _ops=ops)
            self.assertEqual(len(ops.children), 2)
            self.assertTrue(all(child.poll() == 0 for child in ops.children))
            self.assertEqual(json.loads((Path(directory) / 'run/runtime.json').read_text())['status'], 'start_failed')

    def test_cpu_failure_stops_only_owned_container_and_retains_inspection(self):
        ops = FakeOperations()
        ops.fail_ready = 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'readiness failure'):
                runtime.start('cpu-docker', 'qwen_json', lock('qwen_json'), Path(directory) / 'run', {}, _ops=ops)
            controls = [cmd for cmd in ops.commands if cmd[:2] in (['docker', 'stop'], ['docker', 'kill'])]
            self.assertEqual(controls, [['docker', 'stop', '--time', '10', 'a' * 64]])
            self.assertFalse(any(cmd[:2] == ['docker', 'rm'] for cmd in ops.commands))
            self.assertFalse(any('pull' in cmd or 'build' in cmd for cmd in ops.commands))
            saved = json.loads((Path(directory) / 'run/runtime.json').read_text())
            self.assertEqual(saved['container_stopped']['State'], {'Running': False, 'ExitCode': 0, 'OOMKilled': False})
            self.assertIn('synthetic-runtime-log', (Path(directory) / 'run/runtime.log').read_text())
            create = next(c for c in ops.commands if c[:2] == ['docker', 'create'])
            for token in ('--pull=never', runtime.CPU_IMAGE_ID, '127.0.0.1:18087:8080', '8589934592'):
                self.assertIn(token, create)
            self.assertEqual(create[create.index('-ngl') + 1], '0')
            self.assertEqual(create[create.index('-b') + 1], '1024')

    def test_changed_ownership_prevents_container_stop(self):
        ops = FakeOperations()
        with tempfile.TemporaryDirectory() as directory:
            handle = runtime.start('cpu-docker', 'qwen_json', lock('qwen_json'), Path(directory) / 'run', {}, _ops=ops)
            self.assertEqual(handle.metadata['hardware'], 'cpu-docker')
            self.assertEqual(handle.inference_url, 'http://127.0.0.1:18087/v1/chat/completions')
            ops.container['Config']['Labels']['io.semselect.owner'] = 'someone-else'
            with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'):
                runtime.stop(handle)
            self.assertFalse(any(c[:2] in (['docker', 'stop'], ['docker', 'kill']) for c in ops.commands))

    def test_stubborn_owned_child_is_killed_and_reaped(self):
        ops = FakeOperations()
        with tempfile.TemporaryDirectory() as directory:
            handle = runtime.start('metal', 'qwen_json', lock('qwen_json'), Path(directory) / 'run', {}, _ops=ops)
            ops.children[0].stubborn = True
            runtime.stop(handle)
            self.assertTrue(ops.children[0].killed)
            self.assertEqual(handle.metadata['child_exit_codes'], [-9])

    def test_existing_run_directory_is_not_reused(self):
        ops = FakeOperations()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                runtime.start('metal', 'qwen_json', lock('qwen_json'), directory, {}, _ops=ops)
            self.assertFalse(ops.commands)

    def test_only_refused_connection_proves_port_closed(self):
        for code in (0, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ENETUNREACH):
            with self.subTest(code=code), patch('runtime.socket.socket') as factory:
                factory.return_value.__enter__.return_value.connect_ex.return_value = code
                if code == errno.ECONNREFUSED:
                    runtime.Operations().closed(18087)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'closure unproved'):
                        runtime.Operations().closed(18087)

    def test_refused_listener_allows_reuse_when_plain_bind_sees_time_wait(self):
        with patch('runtime.socket.socket') as factory:
            sock = factory.return_value.__enter__.return_value
            sock.connect_ex.return_value = errno.ECONNREFUSED
            sock.bind.side_effect = OSError(errno.EADDRINUSE, 'synthetic TIME_WAIT')
            runtime.Operations().available(18087)
            sock.connect_ex.assert_called_once_with(('127.0.0.1', 18087))
            sock.bind.assert_not_called()

    def test_live_listener_blocks_start_port(self):
        with patch('runtime.socket.socket') as factory:
            sock = factory.return_value.__enter__.return_value
            sock.connect_ex.return_value = 0
            with self.assertRaises(RuntimeError):
                runtime.Operations().available(18087)

    def test_unknown_connect_error_blocks_start_port(self):
        with patch('runtime.socket.socket') as factory:
            sock = factory.return_value.__enter__.return_value
            sock.connect_ex.return_value = errno.ETIMEDOUT
            with self.assertRaises(RuntimeError):
                runtime.Operations().available(18087)

    def test_docker_logs_preserves_stderr_with_stdout(self):
        def fake_run(command, **kwargs):
            self.assertIs(kwargs['stdout'], kwargs['stderr'])
            kwargs['stdout'].write(b'first stdout\n')
            kwargs['stderr'].write(b'then stderr\n')
            return subprocess.CompletedProcess(command, 0)
        with patch('runtime.subprocess.run', side_effect=fake_run):
            self.assertEqual(runtime.Operations().run(['docker', 'logs', 'synthetic-owned-id']),
                             'first stdout\nthen stderr\n')


class PreflightTests(unittest.TestCase):
    def test_all_three_choice_heads_escape_text_preserve_order_and_no_bos(self):
        ops = FakeOperations()
        result = runtime.preflight(kev_request(), 'kev', 'http://127.0.0.1:18087', _ops=ops)
        self.assertEqual(list(result['heads']), ['operation', 'node', 'field'])
        for name, head in result['heads'].items():
            self.assertIn('Choose ' + name, head['rendered_prompt'])
            self.assertIn('synthetic state <¦box_end¦>', head['rendered_prompt'])
            self.assertLess(head['rendered_prompt'].index('b: second'), head['rendered_prompt'].index('a: first'))
            self.assertEqual(head['tokenize_request']['add_special'], False)
            self.assertEqual(head['tokenize_request']['parse_special'], True)
            self.assertEqual(head['expected_markers'], 2)
            self.assertEqual(len(head['marker_positions']), 2)
        self.assertEqual(result['inference_calls'], 0)
        self.assertTrue(all(url.endswith(('/props', '/tokenize')) for url, _ in ops.calls))

    def test_chat_uses_actual_apply_template_and_includes_special_tokens(self):
        ops = FakeOperations()
        req = {'messages': [{'role': 'user', 'content': 'synthetic query'}], 'max_tokens': 128}
        result = runtime.preflight(req, 'qwen_json', 'http://127.0.0.1:18087', _ops=ops)
        head = result['heads']['chat']
        self.assertTrue(head['rendered_prompt'].startswith('ACTUAL SERVER TEMPLATE'))
        self.assertTrue(head['tokenize_request']['add_special'])
        self.assertEqual(head['reserved_output_tokens'], 128)
        self.assertTrue(all(url.endswith(('/props', '/tokenize', '/apply-template')) for url, _ in ops.calls))

    def test_context_overflow_records_all_heads_without_truncation(self):
        ops = FakeOperations()
        ops.context = 16
        ops.padding = 16
        with self.assertRaises(runtime.PreflightError) as caught:
            runtime.preflight(kev_request(), 'kev', 'http://127.0.0.1:18087', context=16, _ops=ops)
        self.assertEqual(len(caught.exception.record['heads']), 3)
        self.assertTrue(all(h['prompt_tokens'] == 19 for h in caught.exception.record['heads'].values()))

    def test_batch_tail_and_marker_layout_limits_are_enforced(self):
        for bad_markers, tail_padding in [(False, 1024), (True, 0)]:
            with self.subTest(bad_markers=bad_markers):
                ops = FakeOperations()
                ops.bad_markers, ops.tail_padding = bad_markers, tail_padding
                with self.assertRaises(runtime.PreflightError):
                    runtime.preflight(kev_request(), 'kev', 'http://127.0.0.1:18087', _ops=ops)

    def test_chat_output_reserve_is_included(self):
        ops = FakeOperations()
        ops.context = 32
        with self.assertRaises(runtime.PreflightError):
            runtime.preflight({'messages': [{'content': 'x'}], 'max_tokens': 24}, 'qwen_json',
                              'http://127.0.0.1:18087', context=32, _ops=ops)

    def test_effective_context_mismatch_stops_before_tokenization(self):
        ops = FakeOperations()
        ops.context = 2048
        with self.assertRaisesRegex(ValueError, 'Effective context'):
            runtime.preflight(kev_request(), 'kev', 'http://127.0.0.1:18087', _ops=ops)
        self.assertEqual(len(ops.calls), 1)


if __name__ == '__main__':
    unittest.main()
