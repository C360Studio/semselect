import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

import fixtures
import run as shared
import run_kevmlx
from run_kevmlx import KevCell
from test_kevmlx_requests import INPUTS, first_choice
from test_runner import Stub


class ScriptedRuntime:
    """kev.serve stand-in: requests go to a loopback stub; /v1/models counters are scripted per snapshot."""

    def __init__(self, url, snapshots):
        self.base, self.info, self.snapshots = url, {'command': ['kev.serve']}, list(snapshots)

    def start(self, deadline):
        return self

    def metrics(self):
        return self.snapshots.pop(0)

    def stop(self):
        self.info['cleanup_errors'] = []
        return self.info


def counters(hits=0, misses=0, requests=0):
    return {'batches': requests, 'batched_requests': requests, 'queued': 0, 'prefix_cache_hits': hits,
            'prefix_cache_misses': misses, 'prefix_cache_states': 0, 'prefix_cache_oom_retries': 0}


def run_cell(temp, cell, snapshots, references):
    jobs = run_kevmlx.kevmlx_requests.jobs_for(cell, INPUTS)
    with Stub(lambda path, body: (200, json.dumps(first_choice(body)).encode())) as stub:
        result = shared.run_cell(cell, Path(temp) / cell.id, jobs, run_kevmlx.reference_for(cell, INPUTS),
                                 lambda c, d: ScriptedRuntime(stub.url, snapshots), time.monotonic() + 120)
    return run_kevmlx.finish_cell(cell, Path(temp) / cell.id, result, references)


class CellTests(unittest.TestCase):
    def test_cells_ids_and_cache_settings(self):
        self.assertEqual([(c.id, c.concurrency, c.prefix_cache) for c in run_kevmlx.CELLS], [
            ('w1-kevmlx-1x1', 1, 0), ('w1-kevmlx-1x4', 4, 0), ('w1-kevmlx-1x8', 8, 0), ('w2-kevmlx-1x1', 1, 0),
            ('w2-kevmlx-1x4', 4, 0), ('w2-kevmlx-1x8', 8, 0), ('w2-kevmlx-cached-1x1', 1, 4)])
        self.assertEqual({c.arm for c in run_kevmlx.CELLS} & set(fixtures.ARM_MODEL), set())
        with self.assertRaisesRegex(ValueError, 'unknown cells'):
            run_kevmlx.select_cells(['w1'], ['w2-kevmlx-1x1'])

    def test_new_state_then_cached_state_cell(self):
        references = {}
        with tempfile.TemporaryDirectory() as temp:
            new = run_cell(temp, KevCell('w2', 1), [counters(), counters(requests=32), counters(requests=64)], references)
            cached = run_cell(temp, KevCell('w2', 1, split=True),
                              [counters(), counters(32, 32, 64), counters(64, 64, 128)], references)
            saved = json.loads((Path(temp) / 'w2-kevmlx-cached-1x1' / 'summary.json').read_text())
            journal = [json.loads(line) for line in (Path(temp) / 'w2-kevmlx-cached-1x1' / 'journal.jsonl').read_text().splitlines()]
        self.assertEqual((new['status'], new['cache_check']['ok'], new['label_agreement']['matched']), ('complete', True, 96))
        self.assertEqual(new['label_agreement']['reference_cell'], 'w2-kevmlx-1x1')
        self.assertIn('Q4_K_M', new['label_agreement_cross_runtime']['caveat'])
        self.assertEqual((cached['status'], cached['planned'], cached['planned_questions'], cached['valid_questions']),
                         ('complete', 64, 96, 96))
        self.assertEqual(cached['cache_check'], {'ok': True, 'expected': {'prefix_cache_hits': 32, 'prefix_cache_misses': 32},
                                                 'observed': {'prefix_cache_hits': 32, 'prefix_cache_misses': 32}})
        self.assertEqual((cached['split']['A']['planned'], cached['split']['B']['planned']), (32, 32))
        self.assertEqual((cached['split']['A']['valid_questions'], cached['split']['B']['valid_questions']), (32, 64))
        self.assertEqual((cached['label_agreement']['matched'], cached['label_agreement']['total']), (96, 96))
        self.assertEqual(cached['label_agreement_cross_runtime']['total'], 96)
        self.assertEqual(cached['kev_server']['latency_ms']['p50'], 812.4)
        self.assertEqual(saved['split'], cached['split'])
        measured = sorted((r for r in journal if r['phase'] == 'measured'), key=lambda r: r['sequence'])
        self.assertEqual([tuple(r['labels']) for r in measured[:2]], [('operation',), ('node', 'field')])
        readings = run_kevmlx.readings([new, cached])
        self.assertIsNotNone(readings['cached_state_cheaper']['result'])
        self.assertIsNone(readings['batches_usefully'][0]['result'])
        self.assertIn('Cached state is materially cheaper', run_kevmlx.render({'cells': [new, cached], 'runtime': 'kevmlx'}))

    def test_cache_accounting_that_differs_from_the_plan_stops_the_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_cell(temp, KevCell('w2', 1, split=True), [counters(), counters(32, 32, 64), counters(40, 56, 128)], {})
        self.assertEqual((result['status'], result['stop_reason']), ('stopped', 'prefix-cache accounting differs from the plan'))
        self.assertIsNone(result['label_agreement']['matched'])
        self.assertIsNone(run_kevmlx.cached_state([result])['result'])


class ValidateTests(unittest.TestCase):
    def test_validate_succeeds_offline(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(run_kevmlx.main(['--validate']), 0)
        text = stdout.getvalue()
        self.assertIn('byte-identical to the serial run 44/44', text)
        self.assertIn('byte-identical to request-manifest.json and the executed Metal run 32/32', text)
        self.assertIn('kevmlx: 7 cells, 716 requests (292 warmup, 424 measured), 648 measured questions; budget 45 min', text)


if __name__ == '__main__':
    unittest.main()
