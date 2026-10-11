"""Offline tests for the fixture freeze record and its verification."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'tier1-capture' / 'freeze.py'
spec = importlib.util.spec_from_file_location('freeze', MODULE)
fz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fz)


class FreezeTests(unittest.TestCase):
    def test_checksum_file_is_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'a.txt').write_text('alpha\n')
            (d / 'SHA256SUMS').write_text(f"{fz.sha256_file(d / 'a.txt')}  a.txt\n")
            self.assertEqual(fz.check_sums(d), (1, []))
            (d / 'a.txt').write_text('beta\n')
            self.assertEqual(fz.check_sums(d), (1, ['a.txt']))

    def test_committed_freeze_is_intact(self):
        if not fz.FREEZE.exists():
            self.skipTest('no freeze.json yet')
        self.assertEqual(fz.verify(fz.FREEZE), [])

    def test_drift_is_reported(self):
        if not fz.FREEZE.exists():
            self.skipTest('no freeze.json yet')
        data = json.loads(fz.FREEZE.read_text())
        data['families'][0]['capture']['sha256sums'] = '0' * 64
        data['contracts']['queries/PROMPT.md'] = '1' * 64
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'freeze.json'
            p.write_text(json.dumps(data))
            problems = fz.verify(p)
        self.assertTrue(any('sha256sums' in x for x in problems), problems)
        self.assertTrue(any('queries/PROMPT.md' in x for x in problems), problems)


if __name__ == '__main__':
    unittest.main()
