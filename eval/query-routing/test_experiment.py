"""Preparation/grading boundaries; these tests never infer held-out labels."""
import unittest
from unittest.mock import patch
import copy
import experiment as e


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.protocol = e.load('protocol.json')
        self.training = e.load('training.json')

    def test_model_and_driver_requests_exclude_gold_and_review_metadata(self):
        case = {'id': 'probe', 'input': {'query': 'Count devices'}, 'gold': {'sentinel': 'DO_NOT_SEND_GOLD'}, 'review': 'DO_NOT_SEND_REVIEW'}
        for backend in ('qwen_json', 'kev'):
            text = e.encoded(e.build_request(case, self.training, self.protocol, backend)).decode()
            self.assertNotIn('DO_NOT_SEND', text)
            self.assertIn('Count devices', text)
        self.assertNotIn('DO_NOT_SEND', e.encoded(e.driver_input(case, self.training)).decode())

    def test_same_state_and_complete_fixed_choices_for_both_models(self):
        case = {'input': {'query': 'Count devices'}}
        chat = e.build_request(case, self.training, self.protocol, 'qwen_json')
        kev = e.build_request(case, self.training, self.protocol, 'kev')
        self.assertEqual(chat['messages'][1]['content'], kev['state'])
        for key in e.FIELDS:
            self.assertEqual(chat['response_format']['json_schema']['schema']['properties'][key]['enum'], list(kev['questions'][key]['criteria']))
        reversed_request = e.build_request(case, self.training, self.protocol, 'kev', True)
        self.assertEqual(list(reversed_request['questions']['node']['criteria']), list(reversed(self.protocol['nodes'])))

    def test_wrong_or_extra_binding_is_not_hidden_by_right_intent(self):
        expected = {'path_intent': True, 'path_start_node': 'sensor-17'}
        self.assertFalse(e.exact({'path_intent': True, 'path_start_node': 'pump-42'}, expected))
        self.assertFalse(e.exact(dict(expected, use_embeddings=True), expected))
        self.assertFalse(e.exact(dict(expected, path_intent=1), expected))
        self.assertFalse(e.exact({'limit': 0}, {}))
        self.assertTrue(e.exact({'use_embeddings': False, 'path_start_node': '', 'time_range': None}, {}))

    def test_decoder_preserves_missing_binding_and_rejects_incompatible_fields(self):
        self.assertEqual(e.decode_selection({'operation': 'path', 'node': 'none', 'field': 'none'}, self.protocol), {'path_intent': True})
        for selection in ({'operation': 'similarity', 'node': 'sensor-17', 'field': 'none'},
                          {'operation': 'avg', 'node': 'none', 'field': 'none'},
                          {'operation': 'zone', 'node': 'pump-42', 'field': 'none'}):
            with self.assertRaises(ValueError):
                e.decode_selection(selection, self.protocol)

    def test_all_fixture_tuples_and_request_sizes_validate(self):
        _, _, counts, manifest = e.validate()
        self.assertEqual(counts['heldout.json'], 32)
        self.assertEqual(len(manifest), 200)

    def test_empty_freeze_is_rejected(self):
        with patch.object(e, 'load', return_value={'files': {}}):
            with self.assertRaises(ValueError):
                e.verify_freeze()

    def test_gold_boolean_cannot_be_numeric_one(self):
        original_load = e.load
        def tampered(name):
            value = original_load(name)
            if name == 'heldout.json':
                value = copy.deepcopy(value)
                value['cases'][6]['gold']['options']['path_intent'] = 1
            return value
        with patch.object(e, 'load', side_effect=tampered):
            with self.assertRaises(ValueError):
                e.validate()

    def test_mismatched_runtime_locks_rejected(self):
        original_read = e.Path.read_text
        def tampered(path, *args, **kwargs):
            text = original_read(path, *args, **kwargs)
            if path.name == 'models.baseline.lock.json':
                value = e.json.loads(text)
                value['runtime_revision'] = '0' * 40
                return e.json.dumps(value)
            return text
        with patch.object(e.Path, 'read_text', tampered):
            with self.assertRaises(ValueError):
                e.validate()


if __name__ == '__main__':
    unittest.main()
