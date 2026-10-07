"""Failure-path tests for owned cleanup, strict runtime contracts and prelaunch gates."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import lifecycle as lc

CID = 'c' * 64
NONCE = 'nonce'


def owned(running=True):
    return {'Id': CID, 'Config': {'Labels': {lc.LABEL: NONCE}}, 'State': {'Running': running}}


class CleanupTests(unittest.TestCase):
    def test_missing_cidfile_still_stops_only_label_verified_owned_container(self):
        with tempfile.TemporaryDirectory() as temp:
            process = Mock(returncode=0)
            with patch.object(lc, 'inspect_owned', side_effect=[owned(), owned(False)]), patch.object(lc.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stderr='')) as run:
                result = lc.cleanup_owned(Path(temp), 'owned-name', NONCE, process)
            self.assertTrue(result['container_shutdown_verified'])
            self.assertTrue(result['cli_reaped'])
            self.assertEqual(run.call_args.args[0], ['docker', 'stop', '--time', '2', CID])

    def test_container_stop_failure_is_retained_even_when_cli_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            process = Mock(returncode=1)
            with patch.object(lc, 'inspect_owned', return_value=owned()), patch.object(lc.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker stop', 15)):
                result = lc.cleanup_owned(Path(temp), 'owned-name', NONCE, process)
            self.assertFalse(result['container_shutdown_verified'])
            self.assertTrue(result['cli_reaped'])
            self.assertIn('TimeoutExpired', result['error'])
            process.wait.assert_called_once_with(timeout=15)

    def test_wrong_ownership_label_never_stops_container(self):
        with tempfile.TemporaryDirectory() as temp:
            foreign = owned(); foreign['Config']['Labels'][lc.LABEL] = 'another-owner'
            with patch.object(lc.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps([foreign]))) as run:
                result = lc.cleanup_owned(Path(temp), 'collision', NONCE, Mock(returncode=1))
            self.assertFalse(result['container_shutdown_verified'])
            self.assertEqual(len(run.call_args_list), 1)
            self.assertEqual(run.call_args.args[0], ['docker', 'inspect', 'collision'])

    def test_cli_reap_failure_does_not_erase_verified_container_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            process = Mock(); process.wait.side_effect = subprocess.TimeoutExpired('docker', 15)
            with patch.object(lc, 'inspect_owned', return_value=None):
                result = lc.cleanup_owned(Path(temp), 'owned', NONCE, process)
            self.assertTrue(result['container_shutdown_verified'])
            self.assertFalse(result['cli_reaped'])
            self.assertIn('cli_reap_error', result)

    def test_prior_missing_cleanup_record_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); prior = root / 'prior'; prior.mkdir()
            lc.e.save_new(prior / 'launch.json', {})
            with self.assertRaisesRegex(RuntimeError, 'no final cleanup'):
                lc.verify_previous(root, root / 'current')
            lc.e.save_new(prior / 'result.json', {'cleanup': {'container_shutdown_verified': True, 'cli_reaped': False}})
            with self.assertRaisesRegex(RuntimeError, 'shutdown unverified'):
                lc.verify_previous(root, root / 'current')


class RuntimeContractTests(unittest.TestCase):
    def test_threads_alias_context_slots_are_checked(self):
        props = {'default_generation_settings': {'n_ctx': 4096}, 'total_slots': 1, 'model_alias': 'small'}
        models = {'data': [{'id': 'small'}]}; model = {'alias': 'small'}
        log = 'system_info: n_threads = 4 (n_threads_batch = 4) / 4'
        self.assertEqual(lc.verify_ready(props, models, model, log)['batch_threads'], 4)
        for wrong in ('n_threads = 8 (n_threads_batch = 4)', ''):
            with self.assertRaisesRegex(ValueError, 'threads'):
                lc.verify_ready(props, models, model, wrong)
        with self.assertRaisesRegex(ValueError, 'alias'):
            lc.verify_ready(props, {'data': [{'id': 'other'}]}, model, log)
        props['total_slots'] = 2
        with self.assertRaisesRegex(ValueError, 'slot'):
            lc.verify_ready(props, models, model, log)

    def test_artifact_hash_is_streamed_and_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bytes'; path.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
            self.assertEqual(lc.file_sha(path), hashlib.sha256(path.read_bytes()).hexdigest())


class ExecutionFailureTests(unittest.TestCase):
    def execute(self, evaluator, stage='development'):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp); log = run / 'log'; log.write_text('')
            with log.open('a') as stream, patch.object(lc.subprocess, 'Popen') as launch:
                record = lc.execute(run, 'qwen35_2b', stage, stream, log, evaluator)
            saved = lc.e.read(run / 'lifecycle' / ('qwen35_2b-' + stage) / 'result.json')
            self.assertEqual(saved, record)
            launch.assert_not_called()
            return record

    def evaluator(self, **kw):
        return SimpleNamespace(barrier=Mock(), previous_stop=Mock(return_value=None), remaining=Mock(return_value=1800), **kw)

    def test_review_or_stage_barrier_failure_prevents_model_launch_and_persists(self):
        evaluator = self.evaluator(); evaluator.barrier.side_effect = ValueError('independent review missing')
        record = self.execute(evaluator)
        self.assertFalse(record['success'])
        self.assertIn('independent review missing', record['error'])
        self.assertTrue(record['cleanup']['container_shutdown_verified'])

    def test_prior_stop_marks_all_stage_jobs_unattempted_without_launch(self):
        evaluator = self.evaluator(run_stage=Mock(return_value={'valid': 0}))
        evaluator.previous_stop.return_value = 'three consecutive runtime failures'
        record = self.execute(evaluator)
        self.assertTrue(record['success'])
        self.assertEqual(record['unattempted_reason'], evaluator.previous_stop.return_value)
        self.assertEqual(evaluator.run_stage.call_args.kwargs, {'unavailable': 'three consecutive runtime failures'})

    def test_exhausted_budget_cannot_launch_fresh_model(self):
        evaluator = self.evaluator(run_stage=Mock(return_value={'valid': 0})); evaluator.remaining.return_value = 0
        self.assertEqual(self.execute(evaluator)['unattempted_reason'], 'inference budget exhausted')

    def test_failed_load_prerequisites_prevent_model_launch(self):
        evaluator = self.evaluator(report=Mock(return_value={'arms': {'qwen35_2b': {'load_status': 'skipped: prerequisites failed'}}}))
        record = self.execute(evaluator, stage='load')
        self.assertFalse(record['success'])
        self.assertIn('prerequisites', record['error'])

    def test_nested_cleanup_exception_still_saves_result(self):
        evaluator = self.evaluator(); evaluator.barrier.side_effect = ValueError('initial failure')
        with patch.object(lc, 'cleanup_owned', side_effect=RuntimeError('cleanup failed too')):
            record = self.execute(evaluator)
        self.assertFalse(record['success'])
        self.assertIn('initial failure', record['error'])
        self.assertIn('cleanup failed too', record['cleanup']['error'])


class LifecycleIntegrationTests(unittest.TestCase):
    def test_runtime_failure_still_captures_final_resources_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp); artifact = run / 'small.gguf'; artifact.write_bytes(b'gguf')
            model = {'artifact_path': str(artifact), 'filename': artifact.name, 'size_bytes': 4,
                     'sha256': lc.file_sha(artifact), 'revision': 'a' * 40, 'alias': 'small',
                     'runtime_revision': 'b' * 40}
            runtime = {'image_id': 'sha256:' + 'd' * 64, 'runtime_revision': model['runtime_revision'],
                       'runtime_sha256': 'e' * 64, 'dependencies': {'llama.cpp': model['runtime_revision']}}
            evaluator = SimpleNamespace(barrier=Mock(), previous_stop=Mock(return_value=None),
                                        remaining=Mock(return_value=1800), model=Mock(return_value=model),
                                        runtime_lock=Mock(return_value=runtime),
                                        run_stage=Mock(side_effect=RuntimeError('inference interrupted')))
            log = run / 'log'; log.write_text('system_info: n_threads = 4 (n_threads_batch = 4) / 4')
            observed = {}
            process = Mock(pid=123, returncode=0); process.poll.return_value = None
            def launch(args, **kwargs):
                observed['args'] = args
                Path(args[args.index('--cidfile') + 1]).write_text(CID)
                return process
            def command(args):
                if args[:3] == ['docker', 'image', 'inspect']:
                    return json.dumps([{'Id': runtime['image_id'], 'Architecture': 'arm64', 'Os': 'linux',
                                        'Config': {'Labels': {'io.semselect.llama-revision': model['runtime_revision']}}}])
                if args[:2] == ['docker', 'exec']:
                    return runtime['runtime_sha256'] + '  /opt/llama/llama-server'
                return ''
            def capture(env):
                return {'verified': True, 'oom': False, 'running': True, 'current_memory_bytes': 100,
                        'peak_memory_bytes': 150, 'cpu_time_seconds': .2,
                        'inspect': {'HostConfig': {'CpusetCpus': '0-3'},
                                    'Config': {'Cmd': observed['args'][observed['args'].index(runtime['image_id']) + 1:], 'Env': []},
                                    'Mounts': [{'Destination': '/model/' + artifact.name, 'RW': False, 'Source': str(artifact)}]}}
            def http(url):
                if url.endswith('/health'): return {'status': 'ok'}
                if url.endswith('/props'): return {'default_generation_settings': {'n_ctx': 4096}, 'total_slots': 1, 'model_alias': 'small'}
                return {'data': [{'id': 'small'}]}
            cleanup = {'container_shutdown_verified': True, 'cli_reaped': True}
            with log.open('a') as stream, patch.object(lc.subprocess, 'Popen', side_effect=launch), patch.object(lc, 'command', side_effect=command), patch.object(lc, 'get_json', side_effect=http), patch.object(lc.resources, 'capture', side_effect=capture) as resource_capture, patch.object(lc, 'token_records', return_value=run / 'tokens.json'), patch.object(lc, 'cleanup_owned', return_value=cleanup) as clean:
                record = lc.execute(run, 'qwen35_2b', 'development', stream, log, evaluator)
            self.assertFalse(record['success'])
            self.assertIn('inference interrupted', record['error'])
            self.assertEqual(record['final_resources']['peak_memory_bytes'], 150)
            self.assertEqual(resource_capture.call_count, 2)
            clean.assert_called_once()
            self.assertIs(clean.call_args.args[-1], process)
            environment = lc.e.read(run / 'lifecycle/qwen35_2b-development/environment.json')
            self.assertEqual(environment['container_id'], CID)
            self.assertTrue(environment['verified'])
            self.assertFalse(environment['prefix_cache_disabled_verified'])
            self.assertEqual(lc.e.read(run / 'lifecycle/qwen35_2b-development/result.json'), record)
            self.assertEqual(lc.file_sha(log), record['log_sha256'])

    def test_existing_token_file_from_other_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp); model = {'revision': 'a' * 40, 'filename': 'small.gguf', 'sha256': 'b' * 64}
            runtime = {'runtime_revision': 'c' * 40}
            # No jobs bypass only per-request checks; even extra cached rows must bind the current runtime.
            lc.e.save_new(run / 'tokens-qwen35_2b.json', [{'payload_sha256': 'd' * 64, 'identity': {'runtime_revision': 'wrong'}}])
            evaluator = SimpleNamespace(requests_for_arm=Mock(return_value=[]))
            with self.assertRaisesRegex(ValueError, 'runtime identity'):
                lc.token_records(run, 'qwen35_2b', model, runtime, evaluator, 'http://127.0.0.1:18095')


if __name__ == '__main__':
    unittest.main()
