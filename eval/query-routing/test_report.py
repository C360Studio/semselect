import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import experiment as e
import report
import runner


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code, self.model = self.root/'code.json', self.root/'model.json'
        execution = {'design_freeze_sha256': e.digest(e.ROOT/'freeze.json')}
        cases = e.load('heldout.json')['cases']
        def rows(arms, views):
            return [{'arm': a, 'view': v, 'id': c['id'], 'status': 'ok', 'options': c['gold']['options']}
                    for a in arms for v in views for c in cases]
        self.code.write_text(json.dumps({'status': 'complete', 'execution': execution,
            'rows': rows(runner.ARMS, ['primary'])+rows(runner.ARMS[1:], ['normal', 'reverse'])}))
        self.model.write_text(json.dumps({'status': 'complete', 'execution': execution, 'hardware': 'metal',
            'runtimes': [], 'rows': rows(runner.MODEL_ARMS, ['normal', 'reverse'])}))

    def test_source_hash_describes_the_bytes_actually_graded(self):
        expected = hashlib.sha256(self.model.read_bytes()).hexdigest()
        original_text, original_bytes = Path.read_text, Path.read_bytes
        changed = False
        def read(method, path, *args, **kwargs):
            nonlocal changed
            content = method(path, *args, **kwargs)
            if path == self.model and not changed:
                changed = True
                path.write_bytes(b'{"later_checkpoint": true}')
            return content
        with patch.object(Path, 'read_text', lambda path, *a, **k: read(original_text,path,*a,**k)), \
             patch.object(Path, 'read_bytes', lambda path, *a, **k: read(original_bytes,path,*a,**k)):
            result = report.summarize(self.code, [self.model])
        self.assertEqual(result['sources'][str(self.model)], expected)
        self.assertEqual(result['primary']['metal/kev']['exact'], 32)

    def test_rejects_results_from_a_different_design(self):
        value = json.loads(self.model.read_bytes())
        value['execution']['design_freeze_sha256'] = '0'*64
        self.model.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'design'):
            report.summarize(self.code, [self.model])

    def test_reports_raw_changes_when_both_tuples_are_invalid(self):
        value = json.loads(self.model.read_bytes())
        for row in value['rows']:
            if row['arm'] == 'kev' and row['id'] == 'R01':
                row.update(status='invalid', options=None, response={'answers': {
                    'operation': {'choice': 'count' if row['view'] == 'normal' else 'no_override'},
                    'node': {'choice': 'pump-42'}, 'field': {'choice': 'none'}}})
        self.model.write_text(json.dumps(value))
        result = report.summarize(self.code, [self.model])['sensitivity']['metal/kev']
        self.assertEqual(result['changed_options'], [])
        self.assertEqual(result['changed_raw_selections'], ['R01'])
        self.assertEqual(result['validity_changed'], [])

    def test_hardware_agreement_exposes_different_invalid_selections(self):
        value = json.loads(self.model.read_bytes())
        for row in value['rows']:
            if row['arm'] == 'kev' and row['id'] == 'R01' and row['view'] == 'normal':
                row.update(status='invalid', options=None, response={'answers': {
                    'operation': {'choice': 'count'}, 'node': {'choice': 'pump-42'}, 'field': {'choice': 'none'}}})
        self.model.write_text(json.dumps(value))
        value['hardware'] = 'cpu-docker'
        cpu = self.root/'cpu.json'
        for row in value['rows']:
            if row['arm'] == 'kev' and row['id'] == 'R01' and row['view'] == 'normal':
                row['response']['answers']['operation']['choice'] = 'no_override'
        cpu.write_text(json.dumps(value))
        result = report.summarize(self.code, [self.model,cpu])['hardware_agreement']['kev/normal']
        self.assertEqual(result['different_raw_selection_ids'], ['R01'])
        self.assertEqual(result['different_status_ids'], [])


if __name__ == '__main__':
    unittest.main()
