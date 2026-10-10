"""Offline tests for the step-3 packet serializer (no model, no stack)."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'tier1-capture' / 'make_packets.py'
spec = importlib.util.spec_from_file_location('make_packets', MODULE)
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

FAM = 'fam'
IDS = {
    'repo': f'semselect.semsource.code.{FAM}.repo.{FAM}',
    'file': f'semselect.semsource.golang.{FAM}.file.a-go',
    'fn': f'semselect.semsource.golang.{FAM}.function.a-go-New',
    'doc': f'semselect.semsource.web.{FAM}.doc.README-md',
    'c1': f'semselect.semsource.web.{FAM}.chunk.README-md-0001',
    'c2': f'semselect.semsource.web.{FAM}.chunk.README-md-0002',
    'c3': f'semselect.semsource.web.{FAM}.chunk.README-md-0003',
    'c4': f'semselect.semsource.web.{FAM}.chunk.README-md-0004',
    'c5': f'semselect.semsource.web.{FAM}.chunk.README-md-0005',
}


def tr(predicate, obj):
    return {'subject': 's', 'predicate': predicate, 'object': obj, 'source': 'semsource',
            'timestamp': '2026-10-10T00:00:00Z', 'confidence': 1.0}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')


def make_capture(root, fn_body, chunk_bodies, gzip_directed=False):
    """A synthetic hydration capture with one code function, one doc and five passages."""
    ev = root / 'evidence'
    (ev / 'hydration').mkdir(parents=True)
    (ev / 'structural').mkdir()
    (ev / 'mutualknn').mkdir()
    (ev / 'summary.json').write_text(json.dumps({
        'family': FAM, 'split': 'development', 'commit': 'c' * 40, 'identity_profile': 'explicit-only',
        'partition_hash': 'p' * 64, 'evidence': 'docs/evidence/x'}))
    (ev / 'family.json').write_text(json.dumps({'family': {'repo': 'https://example.org/fam'}}))
    (ev / 'hydration' / 'hydration.json').write_text(json.dumps({
        'entities': 9, 'with_body_handle': 6, 'bodies_fetched': 6,
        'checks': {'all_states_parse': True, 'all_handles_resolved': True, 'handles_name_object_store': True}}))
    types = {'repo': ('repo', 'code'), 'file': ('file', 'golang'), 'fn': ('function', 'golang'),
             'doc': ('doc', 'web'), **{k: ('chunk', 'web') for k in ('c1', 'c2', 'c3', 'c4', 'c5')}}
    write_jsonl(ev / 'structural' / 'entities.jsonl', [
        {'entity_id': IDS[k], 'type': t, 'domain': d, 'community_by_level': {'0': 'x'}} for k, (t, d) in types.items()])
    edges = [(IDS['repo'], IDS['file'], 'code.structure.contains'), (IDS['file'], IDS['repo'], 'code.structure.belongs'),
             (IDS['file'], IDS['fn'], 'code.structure.contains'), (IDS['fn'], IDS['file'], 'code.structure.belongs')]
    edges += [(IDS[k], IDS['doc'], 'code.structure.belongs') for k in ('c1', 'c2', 'c3', 'c4', 'c5')]
    write_jsonl(ev / 'structural' / 'explicit_edges.jsonl', [
        {'from': a, 'to': b, 'predicate': p, 'from_in_entity_states': True, 'to_in_entity_states': True,
         'in_incoming_index': True, 'same_community_level0': True} for a, b, p in edges])
    write_jsonl(ev / 'mutualknn' / 'mutual_pairs.jsonl', [{'a': IDS['fn'], 'b': IDS['c1']}])
    directed = [{'entity_id': IDS['fn'], 'status': 'ok', 'similar': [{'entity_id': IDS['c1'], 'similarity': 0.81},
                                                                      {'entity_id': IDS['c2'], 'similarity': 0.79}]},
                {'entity_id': IDS['c1'], 'status': 'ok', 'similar': [{'entity_id': IDS['fn'], 'similarity': 0.81}]}]
    if gzip_directed:
        import gzip
        with gzip.open(ev / 'mutualknn' / 'directed.jsonl.gz', 'wt', encoding='utf-8') as f:
            f.writelines(json.dumps(r) + '\n' for r in directed)
    else:
        write_jsonl(ev / 'mutualknn' / 'directed.jsonl', directed)
    bodies = {'k-fn': fn_body}
    bodies.update({f'k-{k}': chunk_bodies[k] for k in chunk_bodies})
    states = [
        {'entity_id': IDS['repo'], 'state': {'id': IDS['repo'], 'triples': [tr('dc.terms.title', FAM), tr('code.artifact.language', 'golang')]}},
        {'entity_id': IDS['file'], 'state': {'id': IDS['file'], 'triples': [
            tr('dc.terms.title', 'a.go'), tr('code.artifact.path', 'a.go'), tr('code.artifact.hash', 'deadbeef00112233'),
            tr('code.metric.start-line', 1), tr('code.metric.end-line', 40)]}},
        {'entity_id': IDS['fn'], 'body_key': 'k-fn', 'body_store': 'objectstore', 'state': {'id': IDS['fn'], 'triples': [
            tr('dc.terms.title', 'New'), tr('code.artifact.path', 'a.go'), tr('code.metric.start-line', 10),
            tr('code.metric.end-line', 10 + fn_body.count('\n')), tr('code.doc.signature', 'func New() *T'),
            tr('code.artifact.visibility', 'public'), tr('code.body.store', 'objectstore'), tr('code.body.key', 'k-fn')]}},
        {'entity_id': IDS['doc'], 'state': {'id': IDS['doc'], 'triples': [
            tr('dc.terms.title', 'README'), tr('source.doc.file-path', 'README.md'), tr('source.doc.file-hash', 'f' * 64),
            tr('source.doc.chunk-count', 5)]}},
    ]
    for i, k in enumerate(('c1', 'c2', 'c3', 'c4', 'c5'), 1):
        states.append({'entity_id': IDS[k], 'body_key': f'k-{k}', 'body_store': 'objectstore', 'state': {'id': IDS[k], 'triples': [
            tr('dc.terms.title', f'README § S{i}'), tr('source.doc.file-path', 'README.md'), tr('source.doc.chunk-index', i - 1),
            tr('source.doc.section', f'S{i}'), tr('source.doc.body-store', 'objectstore'), tr('source.doc.body-key', f'k-{k}')]}})
    write_jsonl(ev / 'hydration' / 'entity_states.jsonl', states)
    write_jsonl(ev / 'hydration' / 'bodies.jsonl', [
        {'store': 'objectstore', 'key': k, 'entities': [], 'bytes': len(v.encode()), 'sha256': sha(v.encode()),
         'valid_utf8': True, 'text': v} for k, v in bodies.items()])
    return ev


def make_selection(root, ev, rows):
    sel = {'family': FAM, 'split': 'development', 'step': 'step 2', 'inputs': {
        'identity_profile': 'explicit-only',
        'entities_jsonl_sha256': sha((ev / 'structural' / 'entities.jsonl').read_bytes()),
        'mutual_pairs_jsonl_sha256': sha((ev / 'mutualknn' / 'mutual_pairs.jsonl').read_bytes()),
        'partition_hash': 'p' * 64}, 'selected': rows}
    path = root / 'selection.json'
    path.write_text(json.dumps(sel))
    return path


def row(rank, a, b, sim, crosses=True):
    return {'rank': rank, 'a': a, 'b': b, 'similarity': sim, 'crosses_partition': crosses,
            'hash': sha(f'{a}\n{b}'.encode())}


def read_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l]


def run_main(args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return mp.main(args)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def small(self):
        chunks = {k: f'Section {k}\n' + 'words about guards and limits.\n' * 5 for k in ('c1', 'c2', 'c3', 'c4', 'c5')}
        ev = make_capture(self.root, 'func New() *T {\n\treturn &T{}\n}\n', chunks)
        rows = [row(1, IDS['fn'], IDS['c1'], 0.81), row(2, IDS['fn'], IDS['c2'], 0.79),
                row(3, IDS['fn'], IDS['c3'], 0.78), row(4, IDS['fn'], IDS['c4'], 0.77),
                row(5, IDS['fn'], IDS['c5'], 0.76), row(6, IDS['repo'], IDS['c1'], 0.75, crosses=False)]
        return ev, make_selection(self.root, ev, rows)

    def test_packets_fit_and_carry_sources(self):
        ev, sel = self.small()
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)]), 0)
        pairs = read_jsonl(out / 'pairs.jsonl')
        self.assertEqual([p['rank'] for p in pairs], [1, 2, 3, 4, 5, 6])
        first = pairs[0]
        state = first['request']['state']
        self.assertLessEqual(len(state.encode()), mp.STATE_BUDGET)
        self.assertIn('[A] function | New | golang | a.go:10-13 | visibility public | file hash deadbeef0011', state)
        self.assertIn('  links in (1): file:a-go contains', state)
        self.assertIn("[B] chunk | 'README § S1' | web | README.md | passage 1 | section S1 | file hash ffffffffffff", state)
        self.assertIn('func New() *T {', state)
        self.assertIn('A lists B at neighbour rank 1 of 2; B lists A at rank 1 of 1', state)
        self.assertEqual(first['context'], {'rank_a_to_b': 1, 'rank_b_to_a': 1, 'explicit_link': []})
        self.assertEqual([s['truncated'] for s in first['sources']], [False, False])
        self.assertEqual(first['sources'][1]['revision_of'], 'parent')
        (key, q), = first['request']['questions'].items()
        self.assertTrue(key.startswith('pair-'))
        self.assertEqual(set(q['criteria']), {'keep', 'suppress', 'defer'})
        self.assertLessEqual(len(q['instructions'].encode()), mp.INSTRUCTION_LIMIT)
        repo_pair = pairs[5]
        self.assertIn('source: none (no verbatim body for this entity)', repo_pair['request']['state'])
        self.assertIn('contains (1): file:a-go', repo_pair['request']['state'])
        self.assertIn('Same explicit-structure group: yes', repo_pair['request']['state'])
        self.assertIsNone(repo_pair['sources'][0]['body_key'])
        index = json.loads((out / 'index.json').read_text())
        self.assertEqual(index['pairs']['packets'], 6)
        self.assertEqual(index['pairs']['sources_without_body'], 1)
        self.assertEqual(index['evidence']['entities_jsonl_sha256'], json.loads(sel.read_text())['inputs']['entities_jsonl_sha256'])

    def test_bundles_split_at_four_questions(self):
        ev, sel = self.small()
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)]), 0)
        bundles = read_jsonl(out / 'bundles.jsonl')
        fn = [b for b in bundles if b['entity_id'] == IDS['fn']]
        self.assertEqual([(b['part'], b['parts'], len(b['request']['questions'])) for b in fn], [(1, 2, 4), (2, 2, 1)])
        self.assertEqual(fn[0]['neighbours'], 5)
        self.assertEqual([q['rank'] for q in fn[0]['questions']], [1, 2, 3, 4])
        for b in bundles:
            self.assertLessEqual(len(b['request']['state'].encode()), mp.STATE_BUDGET)
            for key, q in b['request']['questions'].items():
                self.assertTrue(key.startswith('nb-'))
                self.assertLessEqual(len(q['instructions'].encode()), mp.INSTRUCTION_LIMIT)
                if '[B] chunk' in q['instructions']:
                    self.assertIn('Excerpt: Section', q['instructions'])
                elif '[B] repo' in q['instructions']:
                    self.assertIn('Excerpt: (no verbatim body)', q['instructions'])
                else:
                    self.assertIn('Excerpt: func New() *T { return &T{} }', q['instructions'])
        c1 = [b for b in bundles if b['entity_id'] == IDS['c1']]
        self.assertEqual(len(c1), 1)
        self.assertEqual(c1[0]['neighbours'], 2)
        index = json.loads((out / 'index.json').read_text())
        self.assertEqual(index['bundles'], {**index['bundles'], 'requests': 8, 'entities': 7, 'split_entities': 1, 'questions': 12})

    def test_large_bodies_are_bounded_with_omitted_metadata(self):
        fn_body = ''.join(f'\tline {i:04d} of a long function body with some padding text\n' for i in range(200))
        chunks = {k: ''.join(f'paragraph {i} of passage {k} with enough prose to matter here\n' for i in range(150))
                  for k in ('c1', 'c2', 'c3', 'c4', 'c5')}
        ev = make_capture(self.root, fn_body, chunks)
        sel = make_selection(self.root, ev, [row(1, IDS['fn'], IDS['c1'], 0.8)])
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)]), 0)
        p, = read_jsonl(out / 'pairs.jsonl')
        state = p['request']['state']
        self.assertLessEqual(len(state.encode()), mp.STATE_BUDGET)
        self.assertGreater(len(state.encode()), mp.STATE_BUDGET - 400)
        for s in p['sources']:
            self.assertTrue(s['truncated'])
            self.assertGreater(s['omitted_bytes'], 0)
            self.assertEqual(s['included_bytes'] + s['omitted_bytes'], s['body_bytes'])
        self.assertIn('omitted after line', state)
        self.assertIn('lines 10-', state)
        self.assertTrue(all(line == '' or not line.startswith('\tline') or line.endswith('text') for line in state.splitlines()))
        bundles = read_jsonl(out / 'bundles.jsonl')
        fnb = next(b for b in bundles if b['entity_id'] == IDS['fn'])
        q, = fnb['request']['questions'].values()
        self.assertLessEqual(len(q['instructions'].encode()), mp.INSTRUCTION_LIMIT)
        self.assertGreater(fnb['questions'][0]['excerpt_omitted_bytes'], 0)
        self.assertLessEqual(fnb['questions'][0]['excerpt_bytes'], mp.DESCRIPTION_LIMIT)

    def test_output_is_deterministic(self):
        ev, sel = self.small()
        a, b = self.root / 'a', self.root / 'b'
        run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(a)])
        run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(b)])
        for name in ('pairs.jsonl', 'bundles.jsonl'):
            self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_refuses_capture_that_does_not_reproduce_the_frozen_inputs(self):
        ev, sel = self.small()
        data = json.loads(sel.read_text())
        data['inputs']['mutual_pairs_jsonl_sha256'] = '0' * 64
        sel.write_text(json.dumps(data))
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)]), 2)
        self.assertFalse(out.exists())

    def test_refuses_existing_output_and_reads_gzipped_directed(self):
        chunks = {k: 'text\n' for k in ('c1', 'c2', 'c3', 'c4', 'c5')}
        ev = make_capture(self.root, 'body\n', chunks, gzip_directed=True)
        sel = make_selection(self.root, ev, [row(1, IDS['fn'], IDS['c1'], 0.8)])
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)]), 0)
        p, = read_jsonl(out / 'pairs.jsonl')
        self.assertEqual(p['context']['rank_a_to_b'], 1)
        with self.assertRaises(SystemExit):
            run_main(['--selection', str(sel), '--evidence', str(ev), '--out', str(out)])

    def test_frozen_capture_supplies_ranks_and_drift_is_recorded(self):
        ev, sel = self.small()
        import shutil
        hyd = self.root / 'hydration-capture'
        shutil.copytree(ev, hyd)
        # The hydration capture's embedding replay moved: one similarity changed, one pair appeared.
        directed = read_jsonl(hyd / 'mutualknn' / 'directed.jsonl')
        directed[0]['similar'] = [{'entity_id': IDS['c2'], 'similarity': 0.84}, {'entity_id': IDS['c1'], 'similarity': 0.80}]
        write_jsonl(hyd / 'mutualknn' / 'directed.jsonl', directed)
        write_jsonl(hyd / 'mutualknn' / 'mutual_pairs.jsonl', [{'a': IDS['fn'], 'b': IDS['c1']}, {'a': IDS['fn'], 'b': IDS['c2']}])
        out = self.root / 'out'
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(hyd), '--frozen', str(ev), '--out', str(out)]), 0)
        pairs = read_jsonl(out / 'pairs.jsonl')
        self.assertEqual(pairs[0]['context']['rank_a_to_b'], 1, 'ranks come from the frozen capture')
        self.assertIn('A lists B at neighbour rank 1 of 2', pairs[0]['request']['state'])
        drift = json.loads((out / 'index.json').read_text())['evidence']['mutual_pairs_drift']
        self.assertFalse(drift['equal'])
        self.assertEqual((drift['mutual_pairs_frozen'], drift['mutual_pairs_hydration'], drift['pairs_only_hydration']), (1, 2, 1))
        self.assertAlmostEqual(drift['max_similarity_delta'], 0.05, places=6)
        self.assertEqual(drift['entities_with_moved_similarity'], 3)
        self.assertEqual(drift['most_moved_entities'][0], IDS['fn'])
        # An entity-set or partition difference between the two captures is refused.
        bad = self.root / 'bad-capture'
        shutil.copytree(ev, bad)
        rows = read_jsonl(bad / 'structural' / 'entities.jsonl')
        write_jsonl(bad / 'structural' / 'entities.jsonl', rows[:-1])
        self.assertEqual(run_main(['--selection', str(sel), '--evidence', str(bad), '--frozen', str(ev), '--out', str(self.root / 'out2')]), 2)

    def test_bound_respects_lines_and_utf8(self):
        text = 'first line\nsecond line\nthird line\n'
        kept, omitted, lines = mp.bound(text, 25)
        self.assertEqual(kept, 'first line\nsecond line')
        self.assertEqual(omitted, len(text.encode()) - len(kept.encode()))
        self.assertEqual(lines, 2)
        kept, omitted, _ = mp.bound('é' * 50, 7)
        self.assertEqual(kept, 'é' * 3)
        self.assertEqual(omitted, 100 - 6)
        self.assertEqual(mp.bound('abc', 10), ('abc', 0, 1))
        self.assertEqual(mp.allocate([10, 10], 100), [10, 10])
        self.assertEqual(mp.allocate([100, 10], 60), [50, 10])
        self.assertEqual(mp.allocate([100, 100], 61), [31, 30])


if __name__ == '__main__':
    unittest.main()
