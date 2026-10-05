"""Evidence integrity checks for the evaluation-only scoring comparison."""
import copy
import math
from pathlib import Path
import sys
import subprocess
import socket
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import compare_scoring as scoring


class ScoreEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ids = {'billing': 32, 'unknown': 33}
        self.response = {
            'truncated': False, 'stop_type': 'limit', 'tokens': [32],
            'generation_settings': {'post_sampling_probs': False},
            'completion_probabilities': [{'top_logprobs': [
                {'id': 32, 'logprob': math.log(.2)},
                {'id': 33, 'logprob': math.log(.1)},
                {'id': 999, 'logprob': math.log(.7)},
            ]}],
        }

    def test_normalizes_only_complete_labels_and_preserves_outside_mass(self):
        result = scoring.validate_score(self.response, self.ids)
        self.assertEqual(result['choice'], 'billing')
        self.assertAlmostEqual(result['probabilities']['billing'], 2 / 3)
        self.assertAlmostEqual(result['label_mass'], .3)
        self.assertIsNone(result['confidence'])

    def test_missing_candidate_must_not_be_treated_as_zero(self):
        del self.response['completion_probabilities'][0]['top_logprobs'][1]
        with self.assertRaisesRegex(ValueError, 'every candidate'):
            scoring.validate_score(self.response, self.ids)

    def test_rejects_duplicate_nonfinite_and_post_sampling_scores(self):
        bad = []
        duplicate = copy.deepcopy(self.response)
        duplicate['completion_probabilities'][0]['top_logprobs'].append({'id': 32, 'logprob': -2})
        bad.append(duplicate)
        for value in (float('nan'), float('inf'), True, .1):
            response = copy.deepcopy(self.response)
            response['completion_probabilities'][0]['top_logprobs'][0]['logprob'] = value
            bad.append(response)
        response = copy.deepcopy(self.response)
        response['generation_settings']['post_sampling_probs'] = True
        bad.append(response)
        for response in bad:
            with self.subTest(response=response), self.assertRaises(ValueError):
                scoring.validate_score(response, self.ids)

    def test_rejects_truncation_multiple_tokens_and_nonmaximal_choice(self):
        for key, value in [('truncated', True), ('stop_type', 'none'), ('tokens', [32, 33]), ('tokens', [33])]:
            response = dict(self.response, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                scoring.validate_score(response, self.ids)

    def test_checks_tokenization_at_answer_position(self):
        self.assertEqual(scoring.check_label_tokens([1, 2], [[1, 2, 32], [1, 2, 33]]), [32, 33])
        for appended in ([[1, 9, 32]], [[1, 2, 32, 33]], [[1, 2, 32], [1, 2, 32]]):
            with self.subTest(appended=appended), self.assertRaises(ValueError):
                scoring.check_label_tokens([1, 2], appended)

    def test_failed_calls_remain_in_summary_and_brier_uses_valid_only(self):
        rows = [dict(id='1/a', trial=1, mode='token_scores', order='normal', status='ok',
                     choice='billing', expected='billing', probabilities={'billing': .75, 'unknown': .25},
                     pmax=.75, latency_ms=10),
                dict(id='1/a', trial=1, mode='token_scores', order='reverse', status='invalid',
                     expected='billing', latency_ms=20)]
        summary = scoring.summaries(rows, 'unknown')['token_scores']
        self.assertEqual(summary['accuracy'], .5)
        self.assertEqual(summary['brier_multiclass_sum_valid_only'], .125)
        self.assertEqual(summary['order_comparison']['valid_pairs'], 0)

    def test_invalid_warmup_preserves_response_and_failure(self):
        dataset = {'instructions': 'Choose a route', 'unknown_label': 'unknown',
                   'categories': {'billing': 'Payments', 'unknown': 'Other'},
                   'cases': [{'id': 'a', 'text': 'Refund please', 'expected': 'billing'}]}
        malformed = {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}
        prepared = {'request': {}, 'label_token_ids': self.ids}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(scoring, 'prepare_score', side_effect=lambda *_: copy.deepcopy(prepared)), \
                    patch.object(scoring, 'post', return_value=malformed), self.assertRaises(ValueError):
                scoring.compare(Path(temp), dataset, 'model', 1)
            result = scoring.json.loads((Path(temp) / 'comparison.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['warmup']), 1)
        self.assertEqual(result['warmup'][0]['response'], malformed)
        self.assertEqual(result['warmup'][0]['status'], 'invalid')


class CleanupEvidenceTests(unittest.TestCase):
    def test_closed_server_in_time_wait_is_not_a_live_listener(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        client = socket.create_connection(('127.0.0.1', port), timeout=1)
        connection, _ = listener.accept()
        connection.close()  # Active close leaves the server-side port in TIME_WAIT.
        self.assertEqual(client.recv(1), b'')
        client.close()
        listener.close()
        info = {'status': 'passed'}
        with tempfile.TemporaryDirectory() as temp, patch.object(scoring, 'PORT', port):
            scoring.cleanup_runtime(info, None, None, Path(temp))
        self.assertTrue(info['port_closed'])
        self.assertEqual(info['status'], 'passed')

    def test_cleanup_error_fails_run_but_still_attempts_removal(self):
        calls = []
        def command(args):
            calls.append(args)
            if 'stop' in args:
                raise subprocess.CalledProcessError(1, args)
            if 'inspect' in args:
                return '[{"State":{"Running":false,"ExitCode":0}}]'
            if 'memory.peak' in args[-1]:
                return '100'
            return ''
        info = {'status': 'passed'}
        with tempfile.TemporaryDirectory() as temp, patch.object(scoring, 'command', side_effect=command), \
                patch.object(scoring.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                patch.object(scoring, 'port_is_listening', return_value=False):
            scoring.cleanup_runtime(info, None, 'owned-container', Path(temp))
        self.assertEqual(info['status'], 'failed')
        self.assertTrue(info['cleanup_errors'])
        self.assertTrue(any('rm' in args for args in calls))

    def test_native_stop_failure_and_open_port_are_preserved(self):
        from unittest.mock import Mock
        child = Mock()
        child.poll.return_value = None
        info = {'status': 'passed'}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(scoring.metal, 'stop', side_effect=OSError('stop denied')), \
                patch.object(scoring, 'port_is_listening', return_value=True):
            scoring.cleanup_runtime(info, child, None, Path(temp))
        self.assertEqual(info['status'], 'failed')
        self.assertFalse(info['port_closed'])
        self.assertFalse(info['runtime_stopped'])


if __name__ == '__main__':
    unittest.main()
