"""Budget-boundary tests; synthetic token counts do not measure model quality."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight


def char_tokens(text):
    # A deterministic tokenizer stand-in makes exact boundary inputs readable.
    # Live probe separately checks the real tokenizer's complete prompt IDs.
    return [1000 + ord(char) for char in text]


def request(state='', instructions='Pick', criteria=None):
    return {'state': state, 'questions': {'decision': {
        'type': 'choice', 'instructions': instructions,
        'criteria': criteria if criteria is not None else {'yes': 'Yes', 'no': 'No'},
    }}}


class PreflightTests(unittest.TestCase):
    def test_exact_context_boundary_and_one_token_overflow(self):
        body = request()
        base = preflight.check_request(body, char_tokens)['total_input_tokens']
        body['state'] = 'x' * (1024 - base)
        result = preflight.check_request(body, char_tokens)
        self.assertEqual(result['heads']['decision']['prompt_tokens'], 1024)
        self.assertFalse(result['heads']['decision']['truncated'])
        body['state'] += 'x'
        with self.assertRaisesRegex(ValueError, 'full prompt 1025'):
            preflight.check_request(body, char_tokens)

    def test_option_cap_includes_template_space_but_not_marker(self):
        body = request(criteria={'a': 'x' * 47, 'b': 'small'})
        result = preflight.check_request(body, char_tokens)
        self.assertEqual(result['heads']['decision']['option_text_tokens'][0], 48)
        body['questions']['decision']['criteria']['a'] += 'x'
        with self.assertRaisesRegex(ValueError, 'native option truncation'):
            preflight.check_request(body, char_tokens)

    def test_collective_option_budget_rejects_even_when_each_option_fits(self):
        # Five (marker + 48 text-token) options exceed the native head's
        # 240-token option allowance, causing additional silent shrinkage.
        body = request(criteria={str(index): 'x' * 47 for index in range(5)})
        with self.assertRaisesRegex(ValueError, 'to fit 256-token head'):
            preflight.check_request(body, char_tokens)

    def test_instruction_boundary_accounts_for_type_prefix_and_options(self):
        body = request(instructions='')
        head = preflight.check_request(body, char_tokens)['heads']['decision']
        remaining = head['instruction_limit'] - head['instruction_tokens']
        body['questions']['decision']['instructions'] = 'x' * remaining
        preflight.check_request(body, char_tokens)
        body['questions']['decision']['instructions'] += 'x'
        with self.assertRaisesRegex(ValueError, 'native instruction truncation'):
            preflight.check_request(body, char_tokens)

    def test_choice_descriptions_override_keys_without_reordering_or_mutation(self):
        body = request(criteria={'z_identifier': 'First meaning', 'a_identifier': None,
                                 'm_identifier': ''})
        original = copy.deepcopy(body)
        head = preflight.check_request(body, char_tokens)['heads']['decision']
        self.assertEqual([option['key'] for option in head['options']],
                         ['z_identifier', 'a_identifier', 'm_identifier'])
        self.assertEqual([option['text'] for option in head['options']],
                         ['First meaning', 'a_identifier', 'm_identifier'])
        self.assertNotIn('z_identifier', head['rendered_prompt'])
        self.assertEqual(head['tokens'].count(4), 3)
        self.assertEqual(head['tokens'][0], 2)
        self.assertEqual(head['tokens'].count(1), 3)
        self.assertEqual(body, original)

    def test_noul_has_false_true_order_and_score_has_zero_based_order(self):
        body = {'state': 'Evidence', 'questions': {
            'bool': {'type': 'noul', 'instructions': 'Supported?',
                     'criteria': {'true': 'Supported', 'false': 'Absent'}},
            'level': {'type': 'score', 'instructions': 'Strength?',
                      'criteria': ['None', 'Partial', 'Complete']},
        }}
        heads = preflight.check_request(body, char_tokens)['heads']
        self.assertEqual([option['key'] for option in heads['bool']['options']], ['false', 'true'])
        self.assertIn('<mask> Absent<mask> Supported', heads['bool']['rendered_prompt'])
        self.assertEqual([option['key'] for option in heads['level']['options']], ['0', '1', '2'])
        self.assertIn('<mask> None<mask> Partial<mask> Complete', heads['level']['rendered_prompt'])
        del body['questions']['bool']['criteria']
        boolean = preflight.check_request(body, char_tokens)['heads']['bool']
        self.assertIn('<mask> false<mask> true', boolean['rendered_prompt'])
        for levels in (['Only'], ['x'] * 11):
            body['questions']['level']['criteria'] = levels
            with self.assertRaisesRegex(ValueError, '2 to 10'):
                preflight.check_request(body, char_tokens)

    def test_context_limit_is_per_question_not_aggregate(self):
        body = request(state='x' * 550)
        body['questions']['second'] = copy.deepcopy(body['questions']['decision'])
        checked = preflight.check_request(body, char_tokens)
        self.assertGreater(checked['total_input_tokens'], 1024)
        self.assertTrue(all(head['prompt_tokens'] < 1024 for head in checked['heads'].values()))
        self.assertEqual(checked['inference_calls'], 0)

    def test_literal_control_tokens_rejected_but_html_and_newlines_preserved(self):
        body = request(state='<table>\ntext\n</table>')
        checked = preflight.check_request(body, char_tokens)
        self.assertIn(body['state'], checked['heads']['decision']['rendered_prompt'])
        for value in ('<mask>', '<eos>', '<bos>', '<unk>', '<start_of_turn>'):
            with self.subTest(value=value):
                body['state'] = 'quoted ' + value
                with self.assertRaisesRegex(ValueError, 'literal control token'):
                    preflight.check_request(body, char_tokens)

    def test_metadata_provenance_and_template_drift_fail_closed(self):
        metadata = json.loads((preflight.HERE / 'gguf-metadata.json').read_text())
        lock = json.loads((preflight.HERE / 'models.lock.json').read_text())
        preflight.verify_model_metadata(metadata, lock)
        for key, value in [('tokenizer.chat_template.systemone', 'different'),
                           ('modern-bert.decision.max_head_tokens', 512)]:
            changed = copy.deepcopy(metadata)
            changed[key] = value
            with self.assertRaisesRegex(ValueError, 'metadata changed'):
                preflight.verify_model_metadata(changed, lock)
        changed = copy.deepcopy(metadata)
        changed['_provenance']['model_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            preflight.verify_model_metadata(changed, lock)

    def test_bad_tokenizer_and_unsupported_input_cannot_pass(self):
        for bad in (None, [True], [-1], ['123'], []):
            with self.subTest(tokens=bad):
                with self.assertRaisesRegex(ValueError, 'tokenizer'):
                    preflight.check_request(request(state='text'), lambda _: bad)
        body = request()
        body['questions']['decision']['criteria']['yes'] = {'meaning': 'Yes'}
        with self.assertRaisesRegex(ValueError, 'requires text'):
            preflight.check_request(body, char_tokens)
        body = request()
        body['images'] = []
        with self.assertRaisesRegex(ValueError, 'text-only'):
            preflight.check_request(body, char_tokens)


if __name__ == '__main__':
    unittest.main()
