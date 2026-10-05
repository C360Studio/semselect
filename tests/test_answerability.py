"""Behavior and evidence-integrity checks for the teaching experiment."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import answerability as experiment


def fixture():
    return {
        'version': 1, 'scope': 'teaching/development-only',
        'instructions': 'Use only eligible evidence. Defer if it does not answer the question.',
        'categories': {'allow': 'Evidence contains the requested answer', 'defer': 'Evidence is insufficient'},
        'cases': [{
            'id': 'case-id-never-send', 'family': 'family-never-send', 'title': 'title-never-send',
            'input': {'question': 'What is the filing deadline?', 'audience': 'members', 'required_fact': None,
                      'passages': [{'id': 'p1', 'text': 'File within thirty days.',
                                    'metadata': {'audience': 'all', 'status': 'current'}}], 'facts': []},
            'gold': {'action': 'allow', 'reason': 'gold-reason-never-send',
                     'support': [{'passage_id': 'p1', 'quote': 'thirty days'}], 'missing': None},
            'origin': {'kind': 'constructed', 'references': [], 'note': 'origin-never-send'},
        }],
    }


def successful_response(choice='allow'):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'route': choice})}}]}


class FixtureTests(unittest.TestCase):
    def test_valid_fixture_and_duplicate_id_rejection(self):
        data = fixture()
        self.assertIs(experiment.validate_dataset(data), data)
        data['cases'].append(copy.deepcopy(data['cases'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate case ID'):
            experiment.validate_dataset(data)

    def test_source_support_must_be_verbatim_in_named_passage(self):
        for mutation in ('invented quote', 'unknown passage'):
            data = fixture()
            support = data['cases'][0]['gold']['support'][0]
            support['quote' if mutation == 'invented quote' else 'passage_id'] = mutation
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'support quote'):
                experiment.validate_dataset(data)

    def test_source_origin_requires_full_commit_and_content_hash(self):
        data = fixture()
        origin = data['cases'][0]['origin']
        origin['kind'] = 'source_mutation'
        with self.assertRaisesRegex(ValueError, 'pinned references'):
            experiment.validate_dataset(data)
        origin['references'] = [{'repository': 'example/repo', 'revision': 'a' * 40,
                                 'path': 'README.md', 'sha256': 'b' * 64, 'note': 'adapted'}]
        experiment.validate_dataset(data)
        origin['references'][0]['revision'] = 'main'
        with self.assertRaisesRegex(ValueError, 'full commit'):
            experiment.validate_dataset(data)

    def test_instruction_limit_is_utf8_bytes_and_input_is_allowlisted(self):
        data = fixture()
        data['instructions'] = 'é' * 513
        with self.assertRaisesRegex(ValueError, '1024 UTF-8'):
            experiment.validate_dataset(data)
        data = fixture()
        data['cases'][0]['input']['expected_answer'] = 'allow'
        with self.assertRaisesRegex(ValueError, 'only question'):
            experiment.validate_dataset(data)


class CommonGateTests(unittest.TestCase):
    def test_filters_wrong_audience_and_superseded_facts_before_resolution(self):
        inp = fixture()['cases'][0]['input']
        inp['required_fact'] = 'deadline'
        inp['passages'] += [
            {'id': 'private', 'text': 'File within ninety days.', 'metadata': {'audience': 'staff', 'status': 'current'}},
            {'id': 'old', 'text': 'File within sixty days.', 'metadata': {'audience': 'all', 'status': 'superseded'}},
        ]
        inp['facts'] = [{'key': 'deadline', 'value': '90', 'passage_id': 'private'},
                        {'key': 'deadline', 'value': '60', 'passage_id': 'old'}]
        filtered, action, reason = experiment.common_gate(inp)
        self.assertEqual(action, 'defer')
        self.assertEqual(reason, 'missing required fact')
        self.assertEqual(filtered['facts'], [])
        self.assertEqual([p['id'] for p in filtered['passages']], ['p1'])

    def test_exact_fact_is_allow_missing_and_conflicting_values_defer(self):
        inp = fixture()['cases'][0]['input']
        inp['required_fact'] = 'deadline'
        fact = {'key': 'deadline', 'value': '30', 'passage_id': 'p1'}
        for facts, expected in (([], 'defer'), ([fact], 'allow'), ([fact, fact], 'allow'),
                                ([fact, dict(fact, value='60')], 'defer')):
            inp['facts'] = facts
            self.assertEqual(experiment.common_gate(inp)[1], expected)
        inp['passages'][0]['metadata']['status'] = 'superseded'
        self.assertEqual(experiment.common_gate(inp)[1:], ('defer', 'no eligible current evidence'))

    def test_unrequested_facts_do_not_short_circuit_semantic_judgment(self):
        inp = fixture()['cases'][0]['input']
        inp['facts'] = [{'key': 'unrelated', 'value': 'true', 'passage_id': 'p1'}]
        self.assertIsNone(experiment.common_gate(inp)[1])

    def test_request_does_not_contain_gold_case_labels_or_provenance(self):
        data = fixture()
        for arm in ('qwen_json', 'kev'):
            prepared = experiment.prepare(data, data['cases'][0], arm, 'model', 'normal')
            serialized = json.dumps(prepared['request'])
            self.assertIn('File within thirty days.', serialized)
            self.assertNotIn('never-send', serialized)
            self.assertNotIn('support', serialized)
            if arm == 'qwen_json':
                self.assertTrue(prepared['request']['cache_prompt'])
                self.assertFalse(prepared['request']['chat_template_kwargs']['enable_thinking'])

    def test_composite_reversal_changes_evidence_and_candidate_order_only(self):
        data = fixture()
        inp = data['cases'][0]['input']
        inp['passages'].append({'id': 'p2', 'text': 'No weekends excluded.',
                                'metadata': {'audience': 'all', 'status': 'current'}})
        inp['facts'] = [{'key': 'a', 'value': '1', 'passage_id': 'p1'},
                        {'key': 'b', 'value': '2', 'passage_id': 'p2'}]
        prepared = experiment.prepare(data, data['cases'][0], 'kev', 'model', 'reverse')
        self.assertEqual(prepared['candidates'], ['defer', 'allow'])
        self.assertEqual([p['id'] for p in prepared['filtered_input']['passages']], ['p2', 'p1'])
        self.assertEqual([f['key'] for f in prepared['filtered_input']['facts']], ['b', 'a'])
        self.assertEqual([p['id'] for p in inp['passages']], ['p1', 'p2'])


class DecisionEvidenceTests(unittest.TestCase):
    def test_common_code_avoids_model_calls_and_cannot_be_overridden(self):
        data = fixture()
        data['cases'][0]['input']['passages'] = []
        for arm in experiment.ARMS:
            prepared = experiment.prepare(data, data['cases'][0], arm, 'model', 'normal')
            with patch.object(experiment, 'http_call') as call:
                row = experiment.decide(prepared, arm, 'http://unused', 1)
            call.assert_not_called()
            self.assertEqual(row['action'], 'defer')
            self.assertEqual(row['status'], 'code')
            self.assertIsNone(row['request'])

    def test_control_allows_unresolved_without_claiming_semantic_classification(self):
        data = fixture()
        with patch.object(experiment, 'http_call') as call:
            row = experiment.decide(experiment.prepare(data, data['cases'][0], 'no_added_gate', '', 'normal'),
                                    'no_added_gate', '', 1)
        call.assert_not_called()
        self.assertEqual(row['action'], 'allow')
        self.assertFalse(row['model_called'])

    def test_malformed_json_body_is_preserved_and_deferred(self):
        data = fixture()
        response = Mock()
        response.status = 200
        response.read.return_value = b'{broken JSON'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        with patch.object(experiment.urllib.request, 'urlopen', return_value=response):
            row = experiment.decide(experiment.prepare(data, data['cases'][0], 'qwen_json', 'model', 'normal'),
                                    'qwen_json', 'http://127.0.0.1:1', 1)
        self.assertEqual(row['status'], 'invalid')
        self.assertEqual(row['action'], 'defer')
        self.assertIsNone(row['choice'])
        self.assertEqual(row['http']['body_text'], '{broken JSON')

    def test_error_response_body_preserved_without_counting_fallback_correct(self):
        data = fixture()
        error = urllib.error.HTTPError('http://unused', 503, 'unavailable', {}, io.BytesIO(b'{"error":"overloaded"}'))
        with patch.object(experiment.urllib.request, 'urlopen', side_effect=error):
            row = experiment.decide(experiment.prepare(data, data['cases'][0], 'qwen_json', 'model', 'normal'),
                                    'qwen_json', 'http://unused', 1)
        row.update(expected='defer', case_id='case', trial=1, order='normal')
        summary = experiment.metrics([row])
        self.assertEqual(row['http']['status'], 503)
        self.assertEqual(row['http']['body_text'], '{"error":"overloaded"}')
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['correct'], 0)
        self.assertEqual(summary['valid'], 0)
        self.assertEqual(summary['failure_fallback_deferrals'], 1)

    def test_invalid_warmup_saved_once_without_retry(self):
        data = fixture()
        result = {'rows': [], 'warmups': []}
        malformed = {'choices': []}
        save = Mock()
        with patch.object(experiment, 'http_call', return_value=malformed) as call:
            with self.assertRaisesRegex(RuntimeError, 'warmup failed'):
                experiment.measure_arm(result, data, 'qwen_json', 'model', 'http://unused', 2, 1, save)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['warmups'][0]['response'], malformed)
        self.assertEqual(result['warmups'][0]['status'], 'invalid')
        self.assertTrue(save.called)

    def test_primary_summary_keeps_correlated_views_separate_and_failures_in_denominator(self):
        rows = []
        data = fixture()
        with patch.object(experiment, 'http_call', return_value=successful_response()):
            valid = experiment.decide(experiment.prepare(data, data['cases'][0], 'qwen_json', 'model', 'normal'),
                                      'qwen_json', 'http://unused', 1)
        for trial in (1, 2):
            for order in ('normal', 'reverse'):
                rows.append(dict(valid, arm='qwen_json', trial=trial, order=order, case_id='a', expected='allow'))
        rows.append(dict(valid, arm='qwen_json', trial=1, order='normal', case_id='b', expected='defer',
                         status='invalid', action='defer', choice=None))
        summary = experiment.summarize(rows)['qwen_json']
        primary = summary['primary']['whole_set']
        self.assertEqual(primary['total'], 2)
        self.assertEqual(primary['correct'], 1)
        self.assertEqual(primary['accuracy_including_failures'], .5)
        self.assertEqual(summary['all_correlated_views']['whole_set']['total'], 5)
        self.assertEqual(summary['all_correlated_views']['whole_set']['order_comparison']['valid_pairs'], 2)

    def test_startup_or_warmup_failure_keeps_unattempted_cases_in_denominators(self):
        result = {'rows': []}
        experiment.record_unattempted(result, fixture(), 2)
        self.assertEqual(len(result['rows']), 12)  # Three arms, two orders, two trials.
        primary = experiment.summarize(result['rows'])['qwen_json']['primary']['whole_set']
        self.assertEqual(primary['total'], 1)
        self.assertEqual(primary['correct'], 0)
        self.assertEqual(primary['unattempted'], 1)
        self.assertIsNone(primary['decision_ms']['p50'])
        experiment.record_unattempted(result, fixture(), 2)
        self.assertEqual(len(result['rows']), 12)


class CleanupTests(unittest.TestCase):
    def test_one_stop_failure_does_not_skip_other_owned_process_or_closure_checks(self):
        guard, runtime = Mock(pid=11), Mock(pid=12)
        guard.poll.return_value = None
        runtime.poll.return_value = 0
        calls = []
        def stop(children):
            calls.append(children[0].pid)
            if children[0] is guard:
                raise PermissionError('test signal denial')
        info = {'status': 'passed'}
        with patch.object(experiment.metal, 'stop', side_effect=stop), \
                patch.object(experiment, 'port_is_listening', side_effect=[False, True]):
            experiment.cleanup([('runtime', runtime), ('guard', guard)], info)
        self.assertEqual(calls, [11, 12])
        self.assertEqual(info['status'], 'failed')
        self.assertFalse(info['runtime_stopped'])
        self.assertTrue(info['ports_closed'][str(experiment.RUNTIME_PORT)])
        self.assertFalse(info['ports_closed'][str(experiment.GUARD_PORT)])
        self.assertGreaterEqual(len(info['cleanup_errors']), 3)


if __name__ == '__main__':
    unittest.main()
