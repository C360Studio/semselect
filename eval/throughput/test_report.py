import unittest

import fixtures
import report


def cell(workload, arm, slots, concurrency, rate, p95, matched, kv_unified=False, status='complete'):
    return {'cell': f'{workload}-{arm}-{slots}x{concurrency}' + ('-kvu' if kv_unified else ''), 'workload': workload,
            'arm': arm, 'status': status, 'stop_reason': None,
            'profile': {'slots': slots, 'concurrency': concurrency, 'kv_unified': kv_unified},
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


if __name__ == '__main__':
    unittest.main()
