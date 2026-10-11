"""Offline tests for the step-5 retrieval-query validator and index."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TIER1 = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'tier1-capture'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qs = load('query_sheet', TIER1 / 'query_sheet.py')
cs = qs.cs
tc = load('constraint_fixture', ROOT / 'tests' / 'test_constraint_sheet.py')  # the synthetic capture builder
ID, FAM = tc.ID, tc.FAM


def query(i, kind, sets, question='How does New relate to Validate?', answer='New builds it; Validate checks it.',
          evidence=('a.go:5', 'b.go:5', 'README.md:3'), rationale='Each side carries half of the answer.'):
    return {'id': f'q-{i:02d}', 'kind': kind, 'question': question, 'answer': answer,
            'gold_sets': [list(s) for s in sets], 'evidence': list(evidence), 'rationale': rationale}


def good_sheet(cat_sha, passages_sha=None):
    rows = [query(1, 'complementary', [[ID['fnA'], ID['fnB']]]),
            query(2, 'complementary', [[ID['fnA'], ID['c2']], [ID['cfgA'], ID['c2']]], evidence=('a.go:5', 'README.md:7')),
            query(3, 'complementary', [[ID['cfgA'], ID['fnB']]]),
            query(4, 'complementary', [[ID['cfgB'], ID['c1']]], evidence=('b.go:3', 'README.md:3')),
            query(5, 'precise', [[ID['fnA']]], evidence=('a.go:5',)),
            query(6, 'precise', [[ID['c1']]], evidence=('README.md:3',))]
    return {'provenance': qs.PROVENANCE, 'step': qs.STEP, 'family': FAM, 'split': 'development',
            'annotator': {'role': 'A', 'kind': 'llm', 'model': 'test', 'method': 'full-source'},
            'catalogue_sha256': cat_sha, 'passages_sha256': passages_sha, 'queries': rows}


def review(sheet_sha, verdicts):
    return {'provenance': qs.PROVENANCE, 'step': qs.STEP, 'family': FAM, 'split': 'development',
            'annotator': {'role': 'B', 'kind': 'llm', 'model': 'test-b', 'method': 'full-source'},
            'sheet_sha256': sheet_sha, 'verdicts': verdicts}


class QuerySheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dirs = tc.build(self.root)
        self.dirs['queries_dir'] = self.root / 'queries'
        self.dirs['queries_dir'].mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.cat_path, _ = cs.write_catalogue(FAM, self.dirs['evidence_root'], self.dirs['constraints_dir'])
        self.cat_sha = cs.sha256_file(self.cat_path)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, value):
        path = self.dirs['queries_dir'] / name
        path.write_text(json.dumps(value))
        return path

    def problems(self, role='A'):
        return qs.validate(FAM, **self.dirs)[role]

    def test_good_sheet_validates(self):
        self.write(f'{FAM}.json', good_sheet(self.cat_sha))
        self.assertEqual(self.problems(), [])

    def test_rules_are_enforced(self):
        sheet = good_sheet(self.cat_sha)
        sheet['queries'][4]['gold_sets'] = [[ID['fileA']]]                       # container, no body
        sheet['queries'][5]['gold_sets'] = [[ID['c1'], ID['c2']]]                # precise with two members
        sheet['queries'][0]['gold_sets'] = [[ID['fnA'], ID['cfgA']]]             # primary set inside one file
        sheet['queries'][1]['question'] = f'What does {ID["fnA"]} do?'           # entity ID in the question
        sheet['queries'][2]['question'] = 'What does a.go do?'                   # file path in the question
        sheet['queries'][3]['gold_sets'] = [[ID['cfgB'], ID['c1']], [ID['cfgB'], ID['c1'], ID['fnB']]]  # not minimal
        sheet['queries'][3]['kind'] = 'precise'                                  # kind must follow the id
        sheet['queries'][2]['evidence'] = ['a.go:5']                             # b.go of the primary set not cited
        self.write(f'{FAM}.json', sheet)
        text = '\n'.join(self.problems())
        for needle in ('container or carries no text', 'exactly one member', 'primary gold set of a complementary query must span',
                       'names an entity ID', 'names the file a.go', 'contains another', "kind 'precise' must be 'complementary'",
                       'cite every file of the primary gold set'):
            self.assertIn(needle, text)

    def test_alternative_set_may_sit_in_one_file(self):
        sheet = good_sheet(self.cat_sha)
        sheet['queries'][1]['gold_sets'] = [[ID['fnA'], ID['c2']], [ID['fnA'], ID['cfgA']]]   # alternative inside a.go
        self.write(f'{FAM}.json', sheet)
        self.assertEqual(self.problems(), [])

    def test_primary_sets_differ_and_spread(self):
        sheet = good_sheet(self.cat_sha)
        sheet['queries'][2]['gold_sets'] = [[ID['fnB'], ID['fnA']]]              # same set as q-01, other order
        self.write(f'{FAM}.json', sheet)
        self.assertIn('q-03: the primary gold set repeats that of q-01', self.problems())
        sheet = good_sheet(self.cat_sha)
        for q in sheet['queries']:                                               # everything from a.go and b.go only
            q['gold_sets'] = [[ID['fnA'], ID['fnB']]] if q['kind'] == 'complementary' else [[ID['fnA']]]
        self.write(f'{FAM}.json', sheet)
        self.assertTrue(any('at least 3 needed (the family has 3 that carry text)' in p for p in self.problems()))

    def test_passages_hash_is_required_when_the_family_has_passages(self):
        cap = self.dirs['evidence_root'] / f'20261010T000000Z-legacy-tier1-capture-{FAM}-explicit-only'
        tc.write_jsonl(cap / 'hydration' / 'bodies.jsonl', [{'key': 'k5', 'sha256': 'x', 'text': 'New builds it.'},
                                                            {'key': 'k6', 'sha256': 'y', 'text': 'Validate checks it.'}])
        with contextlib.redirect_stdout(io.StringIO()):
            passages, n = cs.write_passages(FAM, self.dirs['evidence_root'], self.dirs['constraints_dir'])
        self.assertEqual(n, 2)
        self.write(f'{FAM}.json', good_sheet(self.cat_sha))
        self.assertIn('passages_sha256 does not match the passages file', self.problems())
        self.write(f'{FAM}.json', good_sheet(self.cat_sha, cs.sha256_file(passages)))
        self.assertEqual(self.problems(), [])

    def test_index_flags_and_review_merge(self):
        cap = self.dirs['evidence_root'] / f'20261010T000000Z-legacy-tier1-capture-{FAM}-explicit-only'
        side = {k: ('X' if k in ('fileA', 'fnA', 'cfgA', 'c1', 'doc', 'repo') else 'Y') for k in ID}
        (cap / 'structural' / 'partition.json').write_text(json.dumps(
            {'hash': 'h' * 64, 'entity_to_community': {'0': {ID[k]: side[k] for k in ID}}}))
        (self.dirs['constraints_dir'] / f'{FAM}.final.json').write_text(json.dumps(
            {'constraints': [{'a': ID['fnA'], 'b': ID['fnB'], 'polarity': 'negative'}]}))
        sheet_path = self.write(f'{FAM}.json', good_sheet(self.cat_sha))
        with contextlib.redirect_stdout(io.StringIO()):
            out = qs.build_index(**self.dirs)
        row = out['families'][0]
        self.assertEqual(row['proposed'], {'complementary': 4, 'precise': 2})
        self.assertEqual(row['review'], 'awaiting annotator B')
        self.assertEqual(row['primary_set_spans_communities'], 4)        # every complementary primary set crosses X/Y
        self.assertEqual(row['alternative_sets'], 1)
        verdicts = [{'id': f'q-{i:02d}', 'verdict': 'agree', 'rationale': 'ok'} for i in range(1, 7)]
        verdicts[2] = {'id': 'q-03', 'verdict': 'disagree', 'rationale': 'cfgA alone answers it'}
        verdicts[0]['additional_sets'] = [[ID['cfgA'], ID['cfgB']]]
        self.write(f'{FAM}.review.json', review(cs.sha256_file(sheet_path), verdicts))
        self.assertEqual(self.problems('B'), [])
        with contextlib.redirect_stdout(io.StringIO()):
            out = qs.build_index(**self.dirs)
        row = out['families'][0]
        self.assertEqual(row['agreed'], {'complementary': 3, 'precise': 2})
        self.assertEqual([d['id'] for d in row['dropped']], ['q-03'])
        self.assertEqual(row['b_additional_sets'], 1)
        self.assertEqual(out['totals'], {'complementary': 3, 'precise': 2, 'development': 5})
        final = json.loads((self.dirs['queries_dir'] / f'{FAM}.final.json').read_text())
        self.assertEqual([q['id'] for q in final['queries']], ['q-01', 'q-02', 'q-04', 'q-05', 'q-06'])
        q1 = final['queries'][0]
        self.assertEqual(q1['flags'][0], {'members': 2, 'files': 2, 'communities_spanned': 2, 'explicit_edges_among_members': 0,
                                          'mutual_pairs_among_members': 0, 'constraint_pairs': {'negative': 1}})
        self.assertEqual(q1['b_additional_sets'], [[ID['cfgA'], ID['cfgB']]])
        self.assertEqual(final['partition_hash'], 'h' * 64)

    def test_review_rules(self):
        sheet_path = self.write(f'{FAM}.json', good_sheet(self.cat_sha))
        verdicts = [{'id': f'q-{i:02d}', 'verdict': 'agree', 'rationale': 'ok'} for i in range(1, 6)]   # q-06 missing
        verdicts[0]['additional_sets'] = [[ID['fnA'], ID['fnB']], [ID['fileA'], ID['fnB']]]           # repeats A's; container
        verdicts[4]['additional_sets'] = [[ID['fnA'], ID['fnB']]]                                      # two members for precise
        self.write(f'{FAM}.review.json', review('0' * 64, verdicts))
        text = '\n'.join(self.problems('B'))
        for needle in ("sheet_sha256 does not match", 'q-06: 0 verdicts', "repeats one of A's gold sets",
                       'container or carries no text', 'exactly one member'):
            self.assertIn(needle, text)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                qs.build_index(**self.dirs)


if __name__ == '__main__':
    unittest.main()
