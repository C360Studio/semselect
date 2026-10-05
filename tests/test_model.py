"""Offline tests for exact artifact acquisition; no weights or network required."""

import copy
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('model', ROOT / 'scripts/model.py')
model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model)


class ModelLockTests(unittest.TestCase):
    def setUp(self):
        self.lock = {
            'repository': 'example/model-GGUF', 'revision': 'a' * 40,
            'filename': 'model-Q4_K_M.gguf', 'size_bytes': 4,
            'sha256': hashlib.sha256(b'GGUF').hexdigest(), 'alias': 'test-model',
            'license': 'Apache-2.0', 'runtime_revision': 'b' * 40,
        }

    def read_lock(self, lock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock.json'
            path.write_text(json.dumps(lock))
            return model.load_lock(path)

    def test_accepts_exact_pins_and_both_project_locks(self):
        self.assertEqual(self.read_lock(self.lock), self.lock)
        for filename in ('models.lock.json', 'models.baseline.lock.json'):
            self.assertTrue(model.load_lock(ROOT / filename)['filename'].endswith('.gguf'))

    def test_rejects_mutable_or_malformed_pins(self):
        for key, values in {
            'revision': ['main', 'v1', 'a' * 39, '../main', None],
            'sha256': ['00', 'z' * 64, None],
            'size_bytes': [0, -1, True, 4.0, '4'],
            'runtime_revision': ['main', 'a' * 39],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    lock = copy.deepcopy(self.lock)
                    lock[key] = value
                    with self.assertRaises(ValueError):
                        self.read_lock(lock)

    def test_rejects_path_traversal_and_url_injection(self):
        for key, values in {
            'filename': ['../model.gguf', '/tmp/model.gguf', 'sub/model.gguf', 'sub\\model.gguf', 'model.gguf?download=true', 'model.gguf#part', '.gguf'],
            'repository': ['../model', 'owner/../model', 'https://other/model', 'owner/model?x=1', 'owner/model#main'],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    lock = copy.deepcopy(self.lock)
                    lock[key] = value
                    with self.assertRaises(ValueError):
                        self.read_lock(lock)

    def test_missing_fields_are_clear_validation_errors(self):
        for key in ('repository', 'revision', 'filename', 'sha256', 'size_bytes'):
            lock = copy.deepcopy(self.lock)
            del lock[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.read_lock(lock)

    def test_verification_rejects_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.lock['filename']
            self.assertFalse(model.verify(path, self.lock))
            path.write_bytes(b'GGUF')
            self.assertTrue(model.verify(path, self.lock))
            path.write_bytes(b'FAIL')
            self.assertFalse(model.verify(path, self.lock))
            path.write_bytes(b'GGUF-extra')
            self.assertFalse(model.verify(path, self.lock))

    def test_alternate_lock_cli_verifies_selected_artifact_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'models').mkdir()
            (root / 'models' / self.lock['filename']).write_bytes(b'GGUF')
            alternate = root / 'baseline.json'
            alternate.write_text(json.dumps(self.lock))
            stdout = io.StringIO()
            with patch.object(model, 'ROOT', root), patch('sys.argv', ['model.py', '--lock', str(alternate), '--verify']), patch.object(model.subprocess, 'run') as download, redirect_stdout(stdout):
                model.main()
            self.assertIn(self.lock['sha256'], stdout.getvalue())
            download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
