import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runner
import evidence as e
from test_scoring import case


class RunnerTests(unittest.TestCase):
    def test_frozen_subset_interface(self):
        self.assertEqual(len(runner.selected_ids('sensitivity')), 24)
        self.assertEqual(len(runner.selected_ids('feasibility')), 12)

    def test_unavailable_arm_preserves_every_planned_case(self):
        jobs = [{'id': 'D001', 'case': case(), 'configuration': {'arm': 'keyword'}, 'phase': 'measured'}]
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            with patch.object(runner, 'barrier'), patch.object(runner, 'make_jobs', return_value=jobs):
                completion = runner.run_stage(run, 'development', 'code', unavailable='setup failed')
            rows = e.read(run/'development/code/rows.json')
            self.assertEqual(rows[0]['status'], 'unattempted')
            self.assertEqual(completion['valid'], 0)

    def test_three_runtime_failures_stop_remaining_work(self):
        jobs = [{'id': f'D{i}', 'case': case(), 'configuration': {'arm': 'keyword', 'threshold': .7}, 'phase': 'measured'} for i in range(5)]
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            with patch.object(runner, 'barrier'), patch.object(runner, 'make_jobs', return_value=jobs), patch.object(runner.baselines, 'classify_code', return_value={'status': 'error', 'elapsed_ms': 10}) as invoke:
                completion = runner.run_stage(run, 'development', 'code')
            self.assertEqual(invoke.call_count, 3)
            self.assertEqual(completion['stop_reason'], 'three consecutive runtime failures')
            self.assertEqual(e.read(run/'development/code/rows.json')[-1]['status'], 'unattempted')

    def test_interruption_keeps_inflight_attempt_as_error(self):
        jobs = [{'id': f'D{i}', 'case': case(), 'configuration': {'arm': 'keyword', 'threshold': .7}, 'phase': 'measured'} for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            with patch.object(runner, 'barrier'), patch.object(runner, 'make_jobs', return_value=jobs), patch.object(runner.baselines, 'classify_code', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    runner.run_stage(run, 'development', 'code')
            rows = e.read(run/'development/code/rows.json')
            self.assertEqual(rows[0]['status'], 'error')
            self.assertEqual(rows[1]['status'], 'unattempted')

    def test_token_preflight_does_not_accept_estimates_or_truncation(self):
        key = 'a'*64
        record = {'payload_sha256': key, 'arm': 'deberta', 'full_input': True, 'truncated': False, 'verified': True, 'tokens': 512, 'tokenizer_sha256': 'b'*64}
        e.verify_tokens([record], [{'payload_sha256': key}], 'deberta')
        for change in ({'tokens': 513}, {'full_input': False}, {'truncated': True}, {'verified': False}):
            with self.assertRaises(ValueError):
                e.verify_tokens([dict(record, **change)], [{'payload_sha256': key}], 'deberta')

    def test_budget_includes_warmup_and_previous_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            for stage in ('development', 'primary'):
                path = run/stage/'gliclass'
                path.mkdir(parents=True)
                e.save_new(path/'rows.json', [{'phase': 'warmup', 'elapsed_ms': 1000}, {'phase': 'measured', 'elapsed_ms': 2000}])
            self.assertEqual(runner.budget_remaining(run, 'gliclass'), 1794)

    def test_embedding_bridge_uses_actual_adapter_contract(self):
        response = {'model': 'Snowflake/snowflake-arctic-embed-s', 'data': [{'index': 0, 'embedding': [1., 0.]}]}
        completed = type('Completed', (), {'returncode': 0, 'stdout': json.dumps({'status': 'received', 'response': response, 'http_ms': 1}).encode()})()
        with patch.object(runner.subprocess, 'run', return_value=completed):
            result = runner.embedding_http('http://127.0.0.1:8081/embed', 'a query', 1)
        self.assertEqual(result['status'], 'received')
        self.assertEqual(result['vector'], [1., 0.])

    def test_fatal_failure_persists_across_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            path = run/'development/gliclass'
            path.mkdir(parents=True)
            e.save_new(path/'completion.json', {'stop_reason': 'fatal input/mapping failure'})
            self.assertEqual(runner.previous_stop(run, 'gliclass'), 'fatal input/mapping failure')

    def test_decoding_failure_retains_http_evidence(self):
        data = {'status': 'received', 'response': {'arm': 'gliclass', 'truncated': True}, 'response_base64': 'e30=', 'response_sha256': 'a'*64, 'http_ms': 50}
        completed = type('Completed', (), {'returncode': 0, 'stdout': json.dumps(data).encode()})()
        req = runner.request(case(), 'gliclass')
        with patch.object(runner.subprocess, 'run', return_value=completed):
            result = runner.http_call('http://127.0.0.1:8099/classify', req, 'gliclass', 10)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['fatal'])
        self.assertEqual(result['response_sha256'], data['response_sha256'])
        self.assertEqual(result['response'], data['response'])

    def test_results_cannot_contaminate_source_freeze(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            runner.prepare(e.ROOT/'results'/'invalid')

    def test_all_unavailable_end_to_end_stays_inconclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)/'run'
            runner.prepare(run)
            for arm in runner.ARMS:
                runner.run_stage(run, 'development', arm, unavailable='synthetic offline compatibility failure')
            runner.select(run)
            # A synthetic review fixture exercises the barrier without claiming
            # an actual reviewer approved an unmeasured experiment.
            e.save_new(run/'review.json', {'freeze_sha256': e.digest(run/'freeze.json'), 'reviewer': 'synthetic test fixture', 'independent': True, 'fixture_labels_verified': True, 'payloads_verified': True, 'scoring_verified': True})
            for arm in runner.ARMS:
                runner.run_stage(run, 'primary', arm, unavailable='synthetic offline compatibility failure')
            for arm in runner.MODELS:
                runner.run_stage(run, 'sensitivity', arm, unavailable='synthetic offline compatibility failure')
            result = runner.report(run)
            self.assertEqual(result['verdict'], 'inconclusive')
            self.assertEqual(result['metrics']['gliclass']['total'], 120)
            self.assertEqual(result['metrics']['gliclass']['unattempted'], 120)
            self.assertEqual(result['metrics']['gliclass']['accepted_correct'], 0)
            self.assertIn('inconclusive', runner.render_report(result))


if __name__ == '__main__':
    unittest.main()
