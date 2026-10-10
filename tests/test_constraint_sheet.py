"""Offline tests for the step-4 constraint catalogue, validator and index."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'tier1-capture' / 'constraint_sheet.py'
spec = importlib.util.spec_from_file_location('constraint_sheet', MODULE)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

FAM = 'fam'
ID = {
    'repo': f'semselect.semsource.code.{FAM}.repo.{FAM}',
    'fileA': f'semselect.semsource.golang.{FAM}.file.a-go',
    'fnA': f'semselect.semsource.golang.{FAM}.function.a-go-New',
    'cfgA': f'semselect.semsource.golang.{FAM}.struct.a-go-Config',
    'fileB': f'semselect.semsource.golang.{FAM}.file.b-go',
    'fnB': f'semselect.semsource.golang.{FAM}.function.b-go-Validate',
    'cfgB': f'semselect.semsource.golang.{FAM}.struct.b-go-Config',
    'doc': f'semselect.semsource.web.{FAM}.doc.README-md',
    'c1': f'semselect.semsource.web.{FAM}.chunk.README-md-0001',
    'c2': f'semselect.semsource.web.{FAM}.chunk.README-md-0002',
}
FILES = {'a.go': 'package x\n\ntype Config struct{}\n\nfunc New() {}\n', 'b.go': 'package x\n\ntype Config struct{}\n\nfunc Validate() {}\n',
         'README.md': '# fam\n\nNew builds it.\n\n## Other\n\nValidate checks it.\n'}


def tr(p, o):
    return {'subject': 's', 'predicate': p, 'object': o, 'source': 'semsource', 'timestamp': 't', 'confidence': 1.0}


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(root):
    """Evidence root with one frozen+hydration capture, a selection, a workspace."""
    ev_root = root / 'evidence'
    cap = ev_root / f'20261010T000000Z-legacy-tier1-capture-{FAM}-explicit-only'
    (cap / 'hydration').mkdir(parents=True)
    (cap / 'structural').mkdir()
    (cap / 'mutualknn').mkdir()
    ws = root / 'ws' / FAM
    for name, text in FILES.items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text)
    files = list(FILES)
    listing = ''.join(f'{sha(FILES[p].encode())}  {p}\n' for p in files)
    (cap / 'family.json').write_text(json.dumps({'family': {'repo': 'https://example.org/fam', 'commit': 'c' * 40,
                                                            'files': files, 'workspace_sha256': sha(listing.encode())}}))
    (cap / 'summary.json').write_text(json.dumps({'family': FAM, 'split': 'development', 'partition_hash': 'p' * 64}))
    (cap / 'hydration' / 'hydration.json').write_text(json.dumps({'checks': {'ok': True}}))
    states = [
        (ID['repo'], [tr('dc.terms.title', FAM)], None),
        (ID['fileA'], [tr('dc.terms.title', 'a.go'), tr('code.artifact.path', 'a.go'), tr('code.metric.start-line', 1), tr('code.metric.end-line', 5)], None),
        (ID['fnA'], [tr('dc.terms.title', 'New'), tr('code.artifact.path', 'a.go'), tr('code.metric.start-line', 5), tr('code.metric.end-line', 5), tr('code.doc.signature', 'func New()')], 'k1'),
        (ID['cfgA'], [tr('dc.terms.title', 'Config'), tr('code.artifact.path', 'a.go'), tr('code.metric.start-line', 3), tr('code.metric.end-line', 3)], 'k2'),
        (ID['fileB'], [tr('dc.terms.title', 'b.go'), tr('code.artifact.path', 'b.go'), tr('code.metric.start-line', 1), tr('code.metric.end-line', 5)], None),
        (ID['fnB'], [tr('dc.terms.title', 'Validate'), tr('code.artifact.path', 'b.go'), tr('code.metric.start-line', 5), tr('code.metric.end-line', 5)], 'k3'),
        (ID['cfgB'], [tr('dc.terms.title', 'Config'), tr('code.artifact.path', 'b.go'), tr('code.metric.start-line', 3), tr('code.metric.end-line', 3)], 'k4'),
        (ID['doc'], [tr('dc.terms.title', 'fam'), tr('source.doc.file-path', 'README.md')], None),
        (ID['c1'], [tr('dc.terms.title', 'fam § fam'), tr('source.doc.file-path', 'README.md'), tr('source.doc.chunk-index', 0), tr('source.doc.section', 'fam')], 'k5'),
        (ID['c2'], [tr('dc.terms.title', 'fam § Other'), tr('source.doc.file-path', 'README.md'), tr('source.doc.chunk-index', 1), tr('source.doc.section', 'Other')], 'k6'),
    ]
    write_jsonl(cap / 'hydration' / 'entity_states.jsonl', [
        {'entity_id': i, 'body_key': k, 'state': {'id': i, 'triples': t}} for i, t, k in states])
    edges = [(ID['repo'], ID['fileA'], 'code.structure.contains'), (ID['fileA'], ID['fnA'], 'code.structure.contains'),
             (ID['fnA'], ID['fileA'], 'code.structure.belongs'), (ID['fileA'], ID['cfgA'], 'code.structure.contains'),
             (ID['fileB'], ID['fnB'], 'code.structure.contains'), (ID['fileB'], ID['cfgB'], 'code.structure.contains'),
             (ID['c1'], ID['doc'], 'code.structure.belongs'), (ID['c2'], ID['doc'], 'code.structure.belongs'),
             (ID['fnB'], ID['cfgB'], 'code.relationship.parameter')]
    write_jsonl(cap / 'structural' / 'explicit_edges.jsonl', [{'from': a, 'to': b, 'predicate': p} for a, b, p in edges])
    write_jsonl(cap / 'mutualknn' / 'mutual_pairs.jsonl', [{'a': ID['cfgA'], 'b': ID['cfgB']}, {'a': ID['fnA'], 'b': ID['c1']}])
    sel = root / 'selections'
    sel.mkdir()
    (sel / f'{FAM}.json').write_text(json.dumps({'family': FAM, 'split': 'development', 'evidence': cap.name,
                                                 'selected': [{'a': ID['fnA'], 'b': ID['c1']}]}))
    con = root / 'constraints'
    con.mkdir()
    return {'evidence_root': ev_root, 'selections_dir': sel, 'constraints_dir': con, 'workspaces': root / 'ws'}


def constraint(i, polarity, a, b, evidence=('a.go:5', 'README.md:3'), rationale='Because the source shows it.'):
    return {'id': f'{"pos" if polarity == "positive" else "neg"}-{i:02d}', 'polarity': polarity, 'a': a, 'b': b,
            'evidence': list(evidence), 'rationale': rationale}


def good_sheet(cat_sha):
    pos_pairs = [(ID['fnA'], ID['c1']), (ID['fnB'], ID['c2']), (ID['cfgA'], ID['c1']), (ID['cfgB'], ID['c2']),
                 (ID['fnA'], ID['cfgB']), (ID['fnB'], ID['cfgA']), (ID['fileA'], ID['c1']), (ID['fileB'], ID['c2']),
                 (ID['fnA'], ID['cfgA']), (ID['fnB'], ID['cfgB'])]
    neg_pairs = [(ID['cfgA'], ID['cfgB']), (ID['fnA'], ID['fnB']), (ID['fileA'], ID['fileB']), (ID['c1'], ID['c2']),
                 (ID['fnA'], ID['c2']), (ID['fnB'], ID['c1']), (ID['cfgA'], ID['c2']), (ID['cfgB'], ID['c1']),
                 (ID['fileA'], ID['c2']), (ID['fileB'], ID['c1'])]
    rows = [constraint(i + 1, 'positive', a, b) for i, (a, b) in enumerate(pos_pairs)]
    rows += [constraint(i + 1, 'negative', a, b) for i, (a, b) in enumerate(neg_pairs)]
    return {'provenance': cs.PROVENANCE, 'step': cs.STEP, 'family': FAM, 'split': 'development',
            'annotator': {'role': 'A', 'kind': 'llm', 'model': 'test', 'method': 'full-source'},
            'catalogue_sha256': cat_sha, 'constraints': rows}


class ConstraintSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dirs = build(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.cat_path, n = cs.write_catalogue(FAM, self.dirs['evidence_root'], self.dirs['constraints_dir'])
        self.assertEqual(n, 10)
        self.cat_sha = cs.sha256_file(self.cat_path)

    def tearDown(self):
        self.tmp.cleanup()

    def write_sheet(self, sheet, name=f'{FAM}.json'):
        (self.dirs['constraints_dir'] / name).write_text(json.dumps(sheet))

    def problems(self, role='A'):
        return cs.validate(FAM, **self.dirs)[role]

    def test_catalogue_has_no_community_or_neighbour_fields(self):
        rows = [json.loads(l) for l in self.cat_path.read_text().splitlines()]
        self.assertEqual(set(rows[0]), {'entity_id', 'type', 'domain', 'path', 'title', 'section', 'signature',
                                        'start_line', 'end_line', 'chunk_index', 'has_body'})
        self.assertEqual([r['path'] for r in rows][:3], ['README.md', 'README.md', 'README.md'])
        fn = next(r for r in rows if r['entity_id'] == ID['fnA'])
        self.assertEqual((fn['type'], fn['start_line'], fn['signature'], fn['has_body']), ('function', 5, 'func New()', True))

    def test_passages_carry_verbatim_bodies(self):
        cap = next(self.dirs['evidence_root'].glob('*'))
        (cap / 'hydration' / 'bodies.jsonl').write_text(json.dumps({'key': 'k5', 'sha256': 'x5', 'text': 'New builds it.'}) + '\n'
                                                        + json.dumps({'key': 'k6', 'sha256': 'x6', 'text': 'Validate checks it.'}) + '\n')
        out, n = cs.write_passages(FAM, self.dirs['evidence_root'], self.dirs['constraints_dir'])
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual(n, 2)
        self.assertEqual([(r['entity_id'], r['chunk_index'], r['text']) for r in rows],
                         [(ID['c1'], 0, 'New builds it.'), (ID['c2'], 1, 'Validate checks it.')])
        self.assertNotIn('community', json.dumps(rows))

    def test_workspace_hash_is_recomputed(self):
        fam = cs.Family(FAM, **self.dirs)
        ok, detail = fam.workspace_check()
        self.assertTrue(ok, detail)
        (self.dirs['workspaces'] / FAM / 'a.go').write_text('changed\n')
        self.assertFalse(cs.Family(FAM, **self.dirs).workspace_check()[0])

    def test_good_sheet_validates(self):
        self.write_sheet(good_sheet(self.cat_sha))
        self.assertEqual(self.problems(), [])

    def test_rules_are_enforced(self):
        sheet = good_sheet(self.cat_sha)
        sheet['constraints'][0]['a'], sheet['constraints'][0]['b'] = ID['fileA'], ID['fnA']   # containment
        sheet['constraints'][1]['a'] = ID['repo']                                           # repo
        sheet['constraints'][2]['evidence'] = ['zz.go:1']                                   # not a family file
        sheet['constraints'][3]['evidence'] = ['a.go:4-9']                                  # beyond 5 lines
        sheet['constraints'][4]['polarity'] = 'negative'                                    # id/polarity mismatch
        sheet['constraints'][10]['a'], sheet['constraints'][10]['b'] = ID['c1'], ID['cfgA']  # duplicates pos-03 (unordered)
        self.write_sheet(sheet)
        joined = '\n'.join(self.problems())
        for needle in ('trivial containment', 'repo entity', 'not a family file', 'outside the file (5 lines)',
                       'does not match the id', 'duplicates pos-03'):
            self.assertIn(needle, joined)

    def test_cross_file_positives_minimum(self):
        sheet = good_sheet(self.cat_sha)
        for i, row in enumerate(sheet['constraints'][:10]):
            row['a'], row['b'] = (ID['fnA'], ID['cfgA']) if i % 2 else (ID['fnB'], ID['cfgB'])  # all same-file
        self.write_sheet(sheet)
        self.assertTrue(any('join different files' in p for p in self.problems()))

    def test_index_flags_and_review_merge(self):
        self.write_sheet(good_sheet(self.cat_sha))
        with contextlib.redirect_stdout(io.StringIO()):
            out = cs.build_index(**self.dirs)
        row = out['families'][0]
        self.assertEqual(row['proposed'], {'positive': 10, 'negative': 10})
        self.assertEqual(row['outside_review_set'], 19)       # pos-01 is the selected pair
        self.assertEqual(row['outside_mutual_candidates'], 18)  # pos-01 and neg-01 are mutual pairs
        self.assertEqual(row['with_explicit_edge'], 1)          # pos-10 fnB/cfgB parameter edge
        self.assertEqual(row['review'], 'awaiting annotator B')
        sheet_sha = cs.sha256_file(self.dirs['constraints_dir'] / f'{FAM}.json')
        verdicts = [{'id': i, 'verdict': 'disagree' if i in ('pos-09', 'neg-03') else 'agree', 'rationale': 'checked'}
                    for i in cs.expected_ids()]
        self.write_sheet({'provenance': cs.PROVENANCE, 'step': cs.STEP, 'family': FAM, 'split': 'development',
                          'annotator': {'role': 'B', 'kind': 'llm', 'model': 'codex-test', 'method': 'full-source'},
                          'sheet_sha256': sheet_sha, 'verdicts': verdicts}, name=f'{FAM}.review.json')
        self.assertEqual(self.problems('B'), [])
        with contextlib.redirect_stdout(io.StringIO()):
            out = cs.build_index(**self.dirs)
        row = out['families'][0]
        self.assertEqual(row['agreed'], {'positive': 9, 'negative': 9})
        self.assertEqual([d['id'] for d in row['dropped']], ['pos-09', 'neg-03'])
        final = json.loads((self.dirs['constraints_dir'] / f'{FAM}.final.json').read_text())
        self.assertEqual(len(final['constraints']), 18)
        self.assertIn('flags', final['constraints'][0])
        self.assertEqual(out['totals'], {'positive': 9, 'negative': 9})

    def test_review_must_cover_every_id_and_match_the_sheet(self):
        self.write_sheet(good_sheet(self.cat_sha))
        self.write_sheet({'family': FAM, 'annotator': {'role': 'B', 'model': 'x', 'method': 'full-source'},
                          'sheet_sha256': '0' * 64, 'verdicts': [{'id': 'pos-01', 'verdict': 'maybe', 'rationale': ''}]},
                         name=f'{FAM}.review.json')
        joined = '\n'.join(self.problems('B'))
        for needle in ('sheet_sha256', 'pos-02: 0 verdicts', "verdict 'maybe'", 'rationale missing'):
            self.assertIn(needle, joined)


if __name__ == '__main__':
    unittest.main()
