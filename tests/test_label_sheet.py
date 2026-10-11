"""Offline tests for the step-3 label sheet validator and index."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'tier1-capture' / 'label_sheet.py'
spec = importlib.util.spec_from_file_location('label_sheet', MODULE)
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

STATE1 = 'Community objective: group things.\n\n[A] function | New | golang | a.go:10-13\n  source (31 bytes, complete):\nfunc New() *T {\n\treturn &T{}\n}\n[B] chunk | \'README § S1\' | web\n  source (20 bytes, complete):\nNew builds the guard.'
STATE2 = 'Community objective: group things.\n\n[A] repo | fam | code | (no path)\n  source: none (no verbatim body for this entity)\n[B] chunk | \'README § S2\' | web\n  source (12 bytes, complete):\nMIT License.'
H1, H2 = hashlib.sha256(b'1').hexdigest(), hashlib.sha256(b'2').hexdigest()


def make_packets(root, family='fam'):
    d = root / 'packets' / family
    d.mkdir(parents=True)
    rows = [{'rank': 1, 'pair_hash': H1, 'a': 'x', 'b': 'y', 'request': {'state': STATE1}},
            {'rank': 2, 'pair_hash': H2, 'a': 'x', 'b': 'z', 'request': {'state': STATE2}}]
    (d / 'pairs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    sha = hashlib.sha256((d / 'pairs.jsonl').read_bytes()).hexdigest()
    (d / 'index.json').write_text(json.dumps({'family': family, 'split': 'development', 'pairs': {'sha256': sha, 'packets': 2}}))
    return sha


def sheet(sha, role='A', labels=None, family='fam'):
    labels = labels if labels is not None else [
        {'pair_hash': H1, 'rank': 1, 'label': 'keep', 'evidence': ['func New() *T {', 'New builds the guard.'],
         'rationale': 'The passage documents the function.'},
        {'pair_hash': H2, 'rank': 2, 'label': 'defer', 'evidence': ['source: none (no verbatim body for this entity)'],
         'rationale': 'The repo side has no source; the licence line settles nothing.'}]
    return {'provenance': ls.PROVENANCE, 'step': 'step 3 labels', 'family': family, 'split': 'development',
            'annotator': {'role': role, 'kind': 'llm', 'model': 'test-model', 'method': 'packet-only'},
            'packets_sha256': sha, 'labels': labels}


class LabelSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sha = make_packets(self.root)
        (self.root / 'labels').mkdir()
        self.packets = self.root / 'packets'
        self.labels = self.root / 'labels'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        (self.labels / name).write_text(json.dumps(data))

    def test_valid_sheet_has_no_problems(self):
        self.write('fam.json', sheet(self.sha))
        self.assertEqual(ls.validate('fam', self.packets, self.labels), {'A': [], 'B': None})

    def test_quote_must_be_verbatim_but_whitespace_may_differ(self):
        rows = sheet(self.sha)['labels']
        rows[0]['evidence'] = ['func New() *T {  return &T{} }']  # newlines squashed: fine
        self.write('fam.json', sheet(self.sha, labels=rows))
        self.assertEqual(ls.validate('fam', self.packets, self.labels)['A'], [])
        rows[0]['evidence'] = ['func New() *T { return nil }']
        self.write('fam.json', sheet(self.sha, labels=rows))
        problems = ls.validate('fam', self.packets, self.labels)['A']
        self.assertEqual(len(problems), 1)
        self.assertIn('not found verbatim', problems[0])

    def test_coverage_rank_label_and_hash_are_checked(self):
        rows = sheet(self.sha)['labels']
        rows[1]['label'] = 'maybe'
        rows.append(dict(rows[0]))
        del rows[1]['rank']
        data = sheet(self.sha, labels=rows)
        data['packets_sha256'] = '0' * 64
        self.write('fam.json', data)
        problems = ls.validate('fam', self.packets, self.labels)['A']
        joined = '\n'.join(problems)
        for needle in ('packets_sha256', 'labelled 2 times', "label 'maybe'", 'rank None should be 2'):
            self.assertIn(needle, joined)
        self.write('fam.json', sheet(self.sha, labels=rows[:1]))
        self.assertIn('is not labelled', '\n'.join(ls.validate('fam', self.packets, self.labels)['A']))

    def test_index_awaits_b_then_merges(self):
        self.write('fam.json', sheet(self.sha))
        with contextlib.redirect_stdout(io.StringIO()):
            out = ls.build_index(self.packets, self.labels)
        self.assertEqual(out['families'][0]['review'], 'awaiting annotator B')
        self.assertEqual(out['totals'], {'keep': 1, 'defer': 1})
        self.assertEqual(out['reviewed_families'], 0)
        b_rows = sheet(self.sha)['labels']
        b_rows[0]['label'] = 'suppress'
        b_rows[0]['review_note'] = 'kept my label after reading A'
        self.write('fam.review.json', sheet(self.sha, role='B', labels=b_rows))
        with contextlib.redirect_stdout(io.StringIO()):
            out = ls.build_index(self.packets, self.labels)
        row = out['families'][0]
        self.assertEqual((row['agreed'], row['disagreed'], row['b_changed_after_reading_a']), (1, 1, 1))
        self.assertEqual(row['confusion'], {'defer/defer': 1, 'keep/suppress': 1})
        self.assertEqual(row['final_counts'], {'defer': 2})
        final = json.loads((self.labels / 'fam.final.json').read_text())
        self.assertEqual([f['final'] for f in final['labels']], ['defer', 'defer'])
        self.assertEqual(out['totals'], {'defer': 2})
        self.assertEqual(out['reviewed_families'], 1)

    def test_index_refuses_invalid_sheets(self):
        rows = sheet(self.sha)['labels']
        rows[0]['evidence'] = ['nope']
        self.write('fam.json', sheet(self.sha, labels=rows))
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            ls.build_index(self.packets, self.labels)

    def test_cli_validate_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ls.main(['--packets-dir', str(self.packets), '--labels-dir', str(self.labels), 'validate', 'fam']), 1)
            self.write('fam.json', sheet(self.sha))
            self.assertEqual(ls.main(['--packets-dir', str(self.packets), '--labels-dir', str(self.labels), 'validate', 'fam']), 0)


if __name__ == '__main__':
    unittest.main()
