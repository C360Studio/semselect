import unittest

import fixtures
import report


def cell(workload, arm, slots, concurrency, rate, p95, matched, kv_unified=False, status='complete', n_batch=None):
    profile = {'slots': slots, 'concurrency': concurrency, 'kv_unified': kv_unified}
    if n_batch is not None:  # summaries written before amendment 2 have no n_batch
        profile.update(n_batch=n_batch, n_ubatch=512)
    return {'cell': fixtures.Cell(workload, arm, slots, concurrency, kv_unified, n_batch or 512).id, 'workload': workload,
            'arm': arm, 'status': status, 'stop_reason': None, 'profile': profile,
            'decisions_per_s': rate, 'requests_per_s': rate, 'valid_questions': 88, 'planned_questions': 88,
            'request_ms': {'p50': p95 / 2, 'p95': p95}, 'label_agreement': {'matched': matched, 'total': 88},
            'metrics': {'measured_delta': {'prompt_tokens_total': 1000.0, 'prompt_tokens_cached_total': 0.0,
                                           'busy_slots_per_decode': float(slots)}}}


class ReadingTests(unittest.TestCase):
    def test_batching_reading_applies_all_three_thresholds(self):
        cells = [cell('w1', 'kev', 1, 1, 1.0, 1000, 80), cell('w1', 'kev', 8, 8, 2.0, 3000, 78),
                 cell('w1', 'qwen_json', 1, 1, 2.0, 500, 80), cell('w1', 'qwen_json', 8, 8, 5.0, 600, 77),
                 cell('w1', 'qwen_score', 1, 1, 2.0, 500, 80), cell('w1', 'qwen_score', 8, 8, 3.9, 400, 80)]
        result = {(r['workload'], r['arm']): r for r in report.readings(cells)['batches_usefully']}
        self.assertTrue(result[('w1', 'kev')]['result'])          # exactly at 2x, 3x and a drop of 2
        self.assertFalse(result[('w1', 'qwen_json')]['result'])   # agreement fell by 3
        self.assertFalse(result[('w1', 'qwen_score')]['result'])  # 1.95x is below 2x

    def test_incomplete_cells_are_not_evaluable(self):
        cells = [cell('w1', 'kev', 1, 1, 1.0, 1000, 80), cell('w1', 'kev', 8, 8, 9.0, 1000, 80, status='stopped')]
        reading = report.readings(cells)['batches_usefully'][0]
        self.assertIsNone(reading['result'])
        self.assertIn('not evaluable', reading['reason'])
        self.assertIsNone(report.readings(cells)['kev_shared_state_advantage']['result'])

    def test_shared_state_compares_best_cells(self):
        planned = [c for c in fixtures.CELLS if c.workload == 'w2']
        cells = [cell('w2', c.arm, c.slots, c.concurrency, 1.0 + c.slots / 10 if c.arm == 'kev' else 1.5, 1000, 90,
                      c.kv_unified) for c in planned]
        shared = report.readings(cells)['kev_shared_state_advantage']
        self.assertTrue(shared['result'])
        self.assertEqual((shared['kev_best'], shared['qwen_best']), ('w2-kev-8x8', 'w2-qwen_json-1x1'))
        markdown = report.render([{'runtime': 'llamacpp', 'model': 'kev', 'status': 'complete', 'cells': cells}])
        self.assertIn('| `w2-kev-4x4-kvu` | 4 × 4 | yes | 88 / 88 |', markdown)
        self.assertIn('Kev shared-state advantage: **yes**', markdown)

    def test_stopped_cell_without_valid_requests_renders(self):
        # The shape of w1-kev-8x8 in run 20261009T132345.224681Z-llamacpp-kev: stopped in warmup, nothing measured.
        stopped = dict(cell('w1', 'kev', 8, 8, None, 0, 0, status='stopped'), valid_questions=0,
                       stop_reason='three consecutive runtime errors', request_ms={'p50': None, 'p95': None, 'samples': 0},
                       warmup={'planned': 44, 'attempted': 35, 'ok': 30, 'errors': 5},
                       metrics={'measured_delta': {'prompt_tokens_total': 0.0, 'prompt_tokens_cached_total': 0.0,
                                                   'busy_slots_per_decode': None}})
        original = {'runtime': 'llamacpp', 'model': 'kev', 'run_id': 'original', 'protocol': {'question': 'q'},
                    'cells': [cell('w1', 'kev', 1, 1, 1.0, 1000, 88), stopped]}
        rerun = {'runtime': 'llamacpp', 'model': 'kev', 'run_id': 'rerun', 'protocol': {'version': 2}, 'cells': []}
        markdown = report.render([original, rerun])
        self.assertIn('| `w1-kev-8x8` | 8 × 8 | no | 0 / 88 | — | — | — | — | 0 / 88 | 0 / 0 | — '
                      '| 30 / 5 / 35 / 44 | stopped (three consecutive runtime errors) |', markdown)
        self.assertIn('| W1 kev batches usefully | original / original | — | — | — | not evaluable |', markdown)
        self.assertIn('run original, protocol version 1', markdown)
        self.assertIn('run rerun, protocol version 2', markdown)


def run_summary(run_id, version, cells):
    protocol = {'question': 'q'} if version == 1 else {'version': version}
    return {'runtime': 'llamacpp', 'model': 'kev', 'run_id': run_id, 'protocol': protocol, 'cells': cells}


class PairingTests(unittest.TestCase):
    """The same cell ID in several runs: highest protocol version, then latest start, and name the run."""

    def test_completed_rerun_replaces_the_stopped_original_cell(self):
        # Run 20261009T132345.224681Z-llamacpp-kev (protocol 1) stopped w1-kev-8x8; the rerun is protocol 2.
        stopped = dict(cell('w1', 'kev', 8, 8, None, 0, 0, status='stopped'), valid_questions=0,
                       stop_reason='three consecutive runtime errors', started_at='2026-10-09T13:28:31+00:00')
        original = run_summary('original', 1, [dict(cell('w1', 'kev', 1, 1, 1.0, 1000, 88),
                                                     started_at='2026-10-09T13:23:45+00:00'), stopped])
        rerun = run_summary('rerun', 2, [dict(cell('w1', 'kev', 8, 8, 2.5, 2000, 87, n_batch=512),
                                              protocol_version=2, started_at='2026-10-09T15:00:00+00:00')])
        for order in ([original, rerun], [rerun, original]):
            cells, superseded = report.preferred(order)
            reading = report.readings(cells)['batches_usefully'][0]
            self.assertEqual(reading['runs'], ('original', 'rerun'))
            self.assertTrue(reading['result'])
            self.assertAlmostEqual(reading['speedup'], 2.5)
            self.assertEqual([(c['cell'], c['run'], c['status']) for c in superseded],
                             [('w1-kev-8x8', 'original', 'stopped')])
        markdown = report.render([original, rerun])
        self.assertIn('| W1 kev batches usefully | original / rerun | 2.50 | 2.00 | 1 | yes |', markdown)
        self.assertIn('| `w1-kev-1x1` | original | 1 | 2026-10-09T13:23:45+00:00 | — |', markdown)
        self.assertIn('| `w1-kev-8x8` | rerun | 2 | 2026-10-09T15:00:00+00:00 '
                      '| original (protocol version 1, stopped) |', markdown)
        # The stopped original stays in the report, as recorded, under the superseded cells.
        superseded_section = markdown.split('### Superseded cells', 1)[1]
        self.assertIn('stopped (three consecutive runtime errors) |', superseded_section)

    def test_same_version_prefers_the_later_start(self):
        early = run_summary('early', 3, [dict(cell('w1', 'kev', 8, 8, 1.0, 1000, 80), protocol_version=3,
                                              started_at='2026-10-09T15:00:00.000001+00:00')])
        late = run_summary('late', 3, [dict(cell('w1', 'kev', 8, 8, 2.0, 1000, 80), protocol_version=3,
                                            started_at='2026-10-09T16:00:00.000001+00:00')])
        for order in ([early, late], [late, early]):
            cells, superseded = report.preferred(order)
            self.assertEqual(([c['run'] for c in cells], [c['run'] for c in superseded]), (['late'], ['early']))

    def test_batch_diagnostic_cell_is_not_the_8x8_reading(self):
        cells = [cell('w1', 'qwen_json', 1, 1, 2.0, 500, 80, n_batch=512),
                 cell('w1', 'qwen_json', 8, 8, 9.0, 600, 80, n_batch=4096),
                 cell('w1', 'qwen_json', 8, 8, 2.0, 600, 80, n_batch=512)]
        self.assertEqual(cells[1]['cell'], 'w1-qwen_json-8x8-b4096')
        reading = report.readings(cells)['batches_usefully'][0]
        self.assertAlmostEqual(reading['speedup'], 1.0)
        self.assertFalse(reading['result'])


if __name__ == '__main__':
    unittest.main()
