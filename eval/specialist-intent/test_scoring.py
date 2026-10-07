import copy
import unittest

import scoring as s
from evidence import ROOT, read


def case(ident='D001', operation='count', query='Count pumps'):
    return {'id': ident, 'family': 'family-'+ident, 'stratum': operation,
            'input': {'query': query, 'catalog': {'nodes': ['pump-1'], 'zones': [], 'fields': ['pressure']}},
            'gold': {'operation': operation, 'readiness': 'valid_plan' if operation != 'no_override' else 'no_override',
                     'options': {'aggregation_type': operation} if operation == 'count' else {},
                     'executable': operation != 'no_override'}}


def row(ident='D001', operation='count', status='selected', **kw):
    return {'id': ident, 'operation': operation, 'status': status, 'http_ms': 100, 'elapsed_ms': 101, **kw}


class ScoringTests(unittest.TestCase):
    def test_partial_denominators_and_no_override_not_defer(self):
        cases = [case('D001', 'no_override'), case('D002'), case('D003'), case('D004')]
        result = s.grade(cases, [row(operation='no_override'), row('D002', status='deferred'), row('D003', status='error')])
        self.assertEqual((result['raw_correct'], result['accepted_correct'], result['errors'], result['unattempted']), (2, 1, 1, 1))
        self.assertEqual((result['total'], result['accepted'], result['deferred'], result['no_override']), (4, 1, 1, 1))
        self.assertEqual(result['complete_errors'], 3)

    def test_missing_binding_never_executable(self):
        c = case(operation='path', query='Trace graph connections from that device')
        c['gold'].update(readiness='needs_binding', options={'path_intent': True}, executable=False)
        result = s.grade([c], [row(operation='path')])
        self.assertEqual(result['accepted_correct'], 1)
        self.assertEqual(result['complete_correct'], 1)
        self.assertEqual(result['executable_correct'], 0)
        self.assertEqual(result['needs_binding'], 1)

    def test_wrong_specialized_route_is_separate(self):
        result = s.grade([case(operation='no_override')], [row()])
        self.assertEqual(result['wrong_specialized'], 1)
        self.assertEqual(result['wrong_accepted'], 1)

    def test_threshold_boundary_and_unfiltered(self):
        scores = {op: .01 for op in read(ROOT/'protocol.json')['operations']}
        scores['count'] = .6
        scores['sum'] = .4
        r = row(scores=scores)
        self.assertEqual(s.accept(r, {'threshold': .6, 'margin': .1})['status'], 'selected')
        self.assertEqual(s.accept(r, {'threshold': .7})['status'], 'deferred')
        self.assertEqual(s.accept(r, {'unfiltered': True})['status'], 'selected')
        scores['path'] = float('nan')
        self.assertEqual(s.accept(r, {})['status'], 'error')

    def test_development_only_and_fixed_ties(self):
        cases = [case(f'D{i:03d}') for i in range(60)]
        rows = [row(c['id']) for c in cases]
        winner = s.select_model(cases, [{'variant': 0, 'rows': rows}, {'variant': 1, 'rows': rows}])
        self.assertEqual(winner['variant'], 0)
        self.assertTrue(winner['eligible'])
        with self.assertRaises(ValueError):
            s.select_model([case(f'H{i:03d}') for i in range(60)], [])

    def test_no_retrospective_specialist_choice(self):
        metric = s.grade([case()], [row()])
        choices = {'gliclass': {'eligible': True, 'metrics': metric, 'variant': 0}, 'deberta': {'eligible': True, 'metrics': metric, 'variant': 1}}
        self.assertEqual(s.nominate(choices)['arm'], 'gliclass')
        choices['deberta']['metrics'] = dict(metric, median_ms=50)
        self.assertEqual(s.nominate(choices)['arm'], 'deberta')

    def test_gate_requires_both_baselines_and_all_latency_samples(self):
        p = read(ROOT/'protocol.json')
        n = dict(total=120, valid=120, raw_correct=115, accepted_correct=110, wrong_accepted=0,
                 wrong_specialized=0, invented_binding=0, complete_errors=10, executable_correct=60,
                 latency_samples=120, median_ms=100, p95_ms=150)
        b = dict(n, accepted_correct=98, complete_errors=22, executable_correct=54)
        q = dict(n)
        resource = {'verified': True, 'architecture': 'linux/arm64', 'cpu_only': True, 'peak_includes_startup': True, 'oom': False, 'peak_memory_bytes': 1000000000, 'readiness_seconds': 10}
        variants = {v: {'complete': True, 'changes': 0, 'new_wrong_specialized': 0} for v in ('reverse', 'remap')}
        load = {'planned': 200, 'valid': 200, 'errors': 0, 'latency_samples': 200, 'concurrency': 2, 'elapsed_seconds': 20, 'p95_ms': 200}
        self.assertEqual(s.gates(n, b, b, q, resource, variants, load, p)['verdict'], 'prototype one backend')
        self.assertFalse(s.gates(n, b, n, q, resource, variants, load, p)['checks']['added_value'])
        self.assertFalse(s.gates(dict(n, latency_samples=119), b, b, q, resource, variants, load, p)['checks']['cpu_service_cost'])
        self.assertEqual(s.gates(n, b, None, q, resource, variants, load, p)['verdict'], 'inconclusive')

    def test_duplicate_rows_rejected(self):
        with self.assertRaises(ValueError):
            s.grade([case()], [row(), row()])

    def test_clustered_pairing_and_intervals(self):
        cases = [case('D001'), case('D002')]
        cases[1]['family'] = cases[0]['family']
        a = s.grade(cases, [row('D001'), row('D002')])
        b = s.grade(cases, [])
        paired = s.paired(a, b, replicates=30)
        self.assertEqual(paired['net'], 2)
        self.assertEqual(paired['paired_difference_interval'], [1, 1])
        self.assertGreater(s.wilson(0, 40)[1], 0)

    def test_native_output_scored_separately_from_binder(self):
        c = case(operation='avg', query='Average pressure')
        c['gold']['options'] = {'aggregation_type': 'avg', 'aggregation_field': 'pressure'}
        result = s.grade([c], [row(operation='avg', native_options={'aggregation_type': 'avg', 'aggregation_field': 'of'})])
        self.assertEqual(result['complete_correct'], 1)
        self.assertEqual(result['native_complete_correct'], 0)

    def test_repeated_families_use_clustered_proportion_intervals(self):
        cases = [case('D001'), case('D002')]
        cases[1]['family'] = cases[0]['family']
        metrics = s.grade(cases, [row('D001')])
        self.assertEqual(metrics['interval_method'], 'family-cluster percentile bootstrap')
        self.assertEqual(metrics['intervals']['raw_correct'], [.5, .5])


if __name__ == '__main__':
    unittest.main()
