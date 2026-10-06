import json
from pathlib import Path
import tempfile
import unittest

import audit_runtime as audit


class AuditTests(unittest.TestCase):
    def test_prefix_cache_distinguished_from_processing_progress(self):
        log = '''I llama_context: n_ctx = 4096
I llama_context: n_ctx_seq = 4096
I llama_context: n_batch = 512
I llama_context: n_ubatch = 512
W embeddings enabled: setting n_batch = n_ubatch = 512
I load_tensors: offloaded 33/33 layers to GPU
I slot operator(): id 0 | task 1 | new prompt, n_ctx_slot = 4096, n_keep = 0, task.n_tokens = 1000
I slot operator(): id 0 | task 1 | cached n_tokens = 0, memory_seq_rm [0, end)
I slot operator(): id 0 | task 1 | cached n_tokens = 512, memory_seq_rm [512, end)
I slot print_timing: id 0 | task 1 | prompt eval time = 2000.2 ms / 1000 tokens
I slot release: id 0 | task 1 | stop processing: n_tokens = 1010, truncated = 0
I slot operator(): id 0 | task 2 | new prompt, n_ctx_slot = 4096, n_keep = 0, task.n_tokens = 1000
I slot operator(): id 0 | task 2 | cached n_tokens = 200, memory_seq_rm [200, end)
I slot print_timing: id 0 | task 2 | prompt eval time = 1700.2 ms / 800 tokens
I slot release: id 0 | task 2 | stop processing: n_tokens = 1010, truncated = 1
I slot operator(): id 0 | task 3 | new prompt, n_ctx_slot = 4096, n_keep = 0, task.n_tokens = 900
'''
        result = audit.log_observations(log)
        self.assertEqual(result['effective_batch'], [512])
        self.assertTrue(result['embedding_batch_clamp_logged'])
        self.assertEqual(result['offload'], [{'offloaded': 33, 'total': 33}])
        self.assertEqual(result['cache']['observed_tasks'], 3)
        self.assertEqual(result['cache']['first_cache_positive_tasks'], 1)
        self.assertEqual(result['cache']['first_cache_zero_tasks'], 1)
        self.assertEqual(result['cache']['full_prompt_eval_tasks'], 1)
        self.assertEqual(result['cache']['release_unobserved_tasks'], 1)
        self.assertEqual(result['cache']['truncated_tasks'], 1)

    def test_token_budget_uses_actual_clamped_batch(self):
        head = {'prompt_tokens': 100, 'reserved_output_tokens': 1, 'decision_tail_tokens': 80,
                'tokenization': {'tokens': [1] * 20 + [99] + [1] * 59 + [99] + [1] * 19},
                'marker_positions': [20, 80], 'expected_markers': 2}
        checks = [{'id': 'synthetic', 'view': 'normal', 'check': {'heads': {'operation': head}, 'fits': True,
                                                              'marker_tokenization': {'tokens': [99]}}}]
        limits = {'effective_context_per_sequence': [4096], 'effective_batch': [64]}
        result = audit.token_budgets(checks, limits)
        self.assertFalse(result['actual_limits_verified'])
        self.assertTrue(result['all_saved_preflights_claim_fit'])
        self.assertIn('decision tail exceeds actual batch', result['actual_limit_violations'][0]['reasons'])
        limits['effective_batch'] = [512]
        self.assertTrue(audit.token_budgets(checks, limits)['actual_limits_verified'])
        head['marker_positions'] = [21, 80]
        self.assertFalse(audit.token_budgets(checks, limits)['actual_limits_verified'])

    def test_saved_prompt_count_checked_against_tokens(self):
        checks = [{'id': 'synthetic', 'view': 'normal', 'check': {'fits': True, 'heads': {
            'chat': {'prompt_tokens': 1, 'reserved_output_tokens': 128, 'tokenization': {'tokens': [1] * 100}}}}}]
        limits = {'effective_context_per_sequence': [4096], 'effective_batch': [1024]}
        result = audit.token_budgets(checks, limits)
        self.assertFalse(result['actual_limits_verified'])
        self.assertEqual(result['max_prompt_tokens'], 100)

    def test_missing_actual_context_remains_unproved(self):
        checks = [{'id': 'synthetic', 'view': 'normal', 'check': {'fits': True, 'heads': {
            'chat': {'prompt_tokens': 1, 'reserved_output_tokens': 128, 'tokenization': {'tokens': [1]}}}}}]
        self.assertFalse(audit.token_budgets(checks, {'effective_context_per_sequence': [], 'effective_batch': [512]})['actual_limits_verified'])

    def test_limits_are_not_peaks_and_pending_cleanup_is_unproved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = {'hardware': 'cpu-docker', 'arm': 'kev', 'runtime_command': ['server', '-b', '1024'],
                        'container_created': {'HostConfig': {'NanoCpus': 4_000_000_000, 'Memory': 8589934592,
                                                            'MemorySwap': 8589934592}, 'State': {'Running': True}}}
            (root / 'runtime.start.json').write_text(json.dumps(metadata))
            (root / 'runtime.log').write_text('I llama_context: n_batch = 512\n')
            result = audit.runtime_audit(root)
            self.assertIsNone(result['resources']['observed_peak_memory_bytes'])
            self.assertEqual(result['resources']['container_memory_limit_bytes'], 8589934592)
            self.assertFalse(result['cleanup']['confirmed'])
            self.assertEqual(result['requested_effective_discrepancies'], [{'setting': 'batch', 'requested': 1024, 'logged_effective': [512]}])

    def test_native_usage_compares_sum_of_all_three_question_prompts(self):
        preflights = [{'request_sha256': 'synthetic-sha', 'check': {'heads': {
            name: {'tokenization': {'tokens': [1] * n}} for name, n in [('operation', 10), ('node', 20), ('field', 30)]}}}]
        row = {'arm': 'kev', 'id': 'synthetic', 'view': 'normal', 'status': 'ok', 'request_sha256': 'synthetic-sha',
               'http_status': 200, 'response': {'usage': {'input_tokens': 60}}}
        result = audit.response_audit({'rows': [row]}, 'kev', preflights)
        self.assertEqual(result['usage_matches_preflight'], 1)
        self.assertFalse(result['usage_unverified_or_mismatched'])
        row['response']['usage']['input_tokens'] = 30
        result = audit.response_audit({'rows': [row]}, 'kev', preflights)
        self.assertEqual(result['usage_matches_preflight'], 0)
        self.assertEqual(result['usage_unverified_or_mismatched'][0]['preflight_total_input_tokens'], 60)


if __name__ == '__main__':
    unittest.main()
