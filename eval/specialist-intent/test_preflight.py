import hashlib
import unittest

from adapters import build_request, encoded
from preflight import encoder_inputs, preflight_request
from test_adapters import PROTOCOL


class Tokenizer:
    """Behavioral double: never an actual-tokenization evidence source."""
    def __init__(self, lengths):
        self.lengths = lengths
        self.calls = []

    def __call__(self, *texts, **kwargs):
        self.calls.append((texts, kwargs))
        assert kwargs['truncation'] is False
        assert kwargs['add_special_tokens'] is True
        assert kwargs['padding'] is False
        return {'input_ids': [[1] * n for n in self.lengths], 'attention_mask': [[1] * n for n in self.lengths]}


def formatter(query, labels, *, prompt):
    return ''.join('<<LABEL>>' + label for label in labels) + '<<SEP>>' + prompt + query


class PreflightTests(unittest.TestCase):
    def test_embedding_preflight_requires_actual_runtime_audit_and_no_truncation(self):
        class Tokenizer:
            def no_truncation(self):
                self.untruncated = True
            def no_padding(self):
                pass
            def encode(self, text, add_special_tokens):
                self.assert_special = add_special_tokens
                return type('Encoding', (), {'ids': list(range(len(text)))})()
        tokenizer = Tokenizer()
        request = {'arm': 'embeddings', 'payload': {'input': ['x'*512]}}
        identity = {'runtime_tokenization_audit': {'verbatim_with_special_tokens': True}}
        record = preflight_request(request, tokenizer, identity=identity)
        self.assertEqual(record['tokens'], 512)
        self.assertTrue(tokenizer.untruncated)
        self.assertTrue(tokenizer.assert_special)
        request['payload']['input'] = ['x'*513]
        with self.assertRaises(ValueError):
            preflight_request(request, tokenizer, identity=identity)
        with self.assertRaises(ValueError):
            preflight_request(request, tokenizer, identity={})
    def request(self, arm='gliclass'):
        return build_request('Find similar entities.', arm, protocol=PROTOCOL)

    def test_gliclass_full_combined_input_exact_boundary(self):
        request = self.request()
        tokenizer = Tokenizer([512])
        record = preflight_request(request, tokenizer, formatter=formatter, identity={'test_double': True})
        text = tokenizer.calls[0][0][0][0]
        self.assertIn(request['payload']['query'], text)
        self.assertIn(request['payload']['instruction'], text)
        for candidate in request['payload']['candidates']:
            self.assertIn(candidate['label'], text)
        self.assertEqual(record['tokens'], 512)
        self.assertEqual(record['payload_sha256'], hashlib.sha256(encoded(request['payload'])).hexdigest())
        self.assertEqual(len(record['tokenizer_sha256']), 64)
        self.assertFalse(record['truncated'])

    def test_combined_overflow_rejected_not_query_only_checked(self):
        with self.assertRaisesRegex(ValueError, '512'):
            encoder_inputs(self.request()['payload'], Tokenizer([513]), formatter=formatter)

    def test_structural_label_injection_is_encoding_error_not_silent_remap(self):
        request = self.request()
        request['payload']['query'] = 'Find <<LABEL>>new class'
        tokenizer = Tokenizer([20])
        with self.assertRaisesRegex(ValueError, 'reserved marker'):
            encoder_inputs(request['payload'], tokenizer, formatter=formatter)
        self.assertEqual(tokenizer.calls, [])

    def test_deberta_all_nine_full_pairs_include_instruction_and_special_tokens(self):
        request = self.request('deberta')
        tokenizer = Tokenizer([300, 301, 302, 303, 304, 305, 306, 307, 512])
        record = preflight_request(request, tokenizer, identity={'test_double': True})
        premises, hypotheses = tokenizer.calls[0][0]
        self.assertEqual(len(premises), 9)
        self.assertEqual(len(hypotheses), 9)
        self.assertTrue(all(request['payload']['instruction'] in p for p in premises))
        self.assertTrue(all(request['payload']['query'] in p for p in premises))
        self.assertEqual(record['max_pair_tokens'], 512)
        self.assertEqual(record['input_lengths'][-1], 512)
        with self.assertRaisesRegex(ValueError, '512'):
            encoder_inputs(request['payload'], Tokenizer([300] * 8 + [513]))

    def test_batch_mismatch_and_duplicate_candidates_rejected(self):
        request = self.request('deberta')
        with self.assertRaises(ValueError):
            encoder_inputs(request['payload'], Tokenizer([5]))
        request['payload']['candidates'][1]['id'] = request['payload']['candidates'][0]['id']
        with self.assertRaises(ValueError):
            encoder_inputs(request['payload'], Tokenizer([5] * 9))

    def test_no_verified_record_without_identity(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            preflight_request(self.request(), Tokenizer([5]), formatter=formatter)

    def test_qwen_renders_actual_runtime_template_and_reserves_output(self):
        request = self.request('qwen')
        calls = []
        def transport(url, body, timeout):
            calls.append((url, body))
            return {'prompt': 'rendered'} if url.endswith('/apply-template') else {'tokens': [1] * 4032}
        record = preflight_request(request, identity={'model_sha256': 'test_double'},
                                   qwen_url='http://127.0.0.1:8090', transport=transport)
        self.assertEqual(record['tokens'], 4032)
        self.assertFalse(calls[0][1]['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(calls[1][1], {'content': 'rendered', 'add_special': True, 'parse_special': True})
        with self.assertRaisesRegex(ValueError, 'reserve'):
            preflight_request(request, identity={'test_double': True}, qwen_url='http://127.0.0.1:8090',
                              transport=lambda url, body, timeout: {'prompt': 'rendered'} if url.endswith('/apply-template') else {'tokens': [1] * 4033})


if __name__ == '__main__':
    unittest.main()
