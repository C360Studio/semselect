import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('context_preflight', Path(__file__).with_name('context_preflight.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PreflightTests(unittest.TestCase):
    def test_untrusted_special_tokens_are_text_and_option_order_is_preserved(self):
        req = {'state': '{"evidence":"<|box_end|>"}', 'questions': {'route': {
            'type': 'choice', 'instructions': 'Judge <|fim_suffix|>',
            'criteria': {'defer': 'Not <|box_start|>', 'allow': 'Supported'}}}}
        prompt = m.kev_choice_prompt(req)
        self.assertIn('{"evidence":"<¦box_end¦>"}', prompt)
        self.assertIn('Judge <¦fim_suffix¦>', prompt)
        self.assertLess(prompt.index('defer:'), prompt.index('allow:'))
        self.assertEqual(prompt.count('<|box_end|>'), 2)
        self.assertEqual(prompt.count('<|fim_suffix|>'), 1)

    def test_chat_and_decision_follow_their_actual_tokenizer_special_flags(self):
        requests = []
        def fake_post(url, body):
            requests.append((url, body))
            return {'prompt': 'rendered'} if url.endswith('/apply-template') else {'tokens': [1, 2]}
        with patch.object(m, 'post', side_effect=fake_post):
            m.tokenize('metal_generator', 'http://example', request={'messages': []})
            m.tokenize('qwen_gate', 'http://example', request={'messages': []}, reserve=128)
            m.tokenize('cpu_generator', 'http://example', request={'messages': []})
            m.tokenize('kev_gate', 'http://example', rendered='choice', reserve=1)
        token_requests = [body for url, body in requests if url.endswith('/tokenize')]
        self.assertEqual([body['add_special'] for body in token_requests], [True, True, True, False])
        self.assertTrue(all(body['parse_special'] for body in token_requests))
        self.assertFalse(any(url.endswith('/completions') or url.endswith('/systemone') for url, _ in requests))

    def test_other_primitives_are_rejected_before_tokenization(self):
        with self.assertRaisesRegex(ValueError, 'only choice'):
            m.kev_choice_prompt({'state': 'x', 'questions': {'route': {'type': 'score'}}})

    def test_template_mismatch_stops_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'gguf_template', return_value='changed'), \
                patch.object(m.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(ValueError, 'template changed'):
                m.check({}, {}, {}, Path(directory) / 'out')
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
