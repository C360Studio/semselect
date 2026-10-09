import argparse
import contextlib
import io
import json
from pathlib import Path
import platform
import tempfile
import time
import unittest

import fixtures
import run_sglang
from run_sglang import Probe
from test_runner import Stub


class FakeRuntime:
    def __init__(self, url, fail=None, cleanup_errors=()):
        self.base, self.fail, self.info = url, fail, {'ready': False}
        self.cleanup_errors = list(cleanup_errors)
        self.stopped = False

    def start(self, deadline):
        if self.fail:
            raise RuntimeError(self.fail)
        self.info['ready'] = True
        return self

    def metrics(self):
        return {}

    def stop(self):
        self.stopped = True
        self.info['cleanup_errors'] = self.cleanup_errors
        return self.info


def decisions(path, body):
    """Answer every question with its first option, in the /v1/decisions shape."""
    request = json.loads(body)
    answers = {}
    for question in request['questions']:
        names = [option['name'] for option in question['options']]
        probabilities = {name: (0.6 if i == 0 else 0.4 / (len(names) - 1)) for i, name in enumerate(names)}
        answers[question['id']] = {'type': 'choice', 'probabilities': probabilities, 'label_mass': 0.9, 'choice': names[0],
                                   'prompt_token_ids': [1, 2, 3], 'label_token_ids': list(range(32, 32 + len(names)))}
    usage = {'prompt_tokens': 100 * len(answers), 'total_tokens': 100 * len(answers), 'completion_tokens': 0}
    return 200, json.dumps({'object': 'decisions', 'model': request['model'], 'prompt_format_version': 1,
                            'answers': answers, 'usage': usage}).encode()


def probe_record(**changes):
    record = {'error': None, 'repeats': 2, 'runtime': {'ready': True, 'cleanup_errors': []},
              'requests': [{'status': 'ok'}, {'status': 'ok'}]}
    return {**record, **changes}


class ProbeRuleTests(unittest.TestCase):
    def test_pass_needs_ready_runtime_every_repeat_valid_and_clean_shutdown(self):
        self.assertTrue(run_sglang.probe_passed(probe_record()))
        failing = [probe_record(error='RuntimeError: Readiness timed out'),
                   probe_record(runtime={'ready': False, 'cleanup_errors': []}),
                   probe_record(runtime={'ready': True, 'cleanup_errors': ['port 30111 still accepts connections']}),
                   probe_record(requests=[{'status': 'ok'}, {'status': 'invalid'}]),
                   probe_record(requests=[{'status': 'ok'}, {'status': 'error'}]),
                   probe_record(requests=[{'status': 'ok'}]),
                   {key: value for key, value in probe_record().items() if key != 'runtime'}]
        for record in failing:
            self.assertFalse(run_sglang.probe_passed(record), record)

    def test_gated_cells_are_added_only_for_passing_probes(self):
        cells = [c for c in run_sglang.CELLS if c.workload == 'w1']
        plan = run_sglang.with_conditional(cells, run_sglang.CONDITIONAL, {'overlap': True, 'radix': False})
        ids = [c.id for c in plan]
        self.assertEqual(ids[ids.index('w1-qwen_decisions-8x8') + 1], 'w1-qwen_decisions-4x4-overlap')
        self.assertNotIn('w1-qwen_decisions-4x4-radix', ids)
        self.assertEqual(run_sglang.with_conditional(cells, run_sglang.CONDITIONAL, {}), cells)

    def test_probe_runs_one_fixture_twice_and_records_raw_output(self):
        job = run_sglang.sglang_requests.w1_jobs('qwen_decisions', fixtures.load_inputs(['w1']))[0]
        with tempfile.TemporaryDirectory() as temp, Stub(decisions) as stub:
            runtimes = []

            def make(probe, directory):
                runtimes.append(FakeRuntime(stub.url))
                (directory / 'runtime.log').write_text('line one\nline two\n')
                return runtimes[-1]
            record = run_sglang.run_probe(Probe('overlap'), Path(temp) / 'probe-overlap', job, make, time.monotonic() + 60)
            saved = json.loads((Path(temp) / 'probe-overlap' / 'probe.json').read_text())
        self.assertTrue(record['passed'])
        self.assertEqual(stub.hits[job.body], 2)
        self.assertEqual((record['dropped_flag'], record['gates']), ('--disable-overlap-schedule', 'w1-qwen_decisions-4x4-overlap'))
        self.assertTrue(all(row['response_bytes_base64'] for row in record['requests']))
        self.assertEqual(record['repeat_diagnostics'], {'labels_equal': True, 'max_probability_delta': 0.0})
        self.assertEqual((saved['log_tail'], runtimes[0].stopped), (['line one', 'line two'], True))

    def test_probe_failures_are_recorded_not_raised(self):
        job = run_sglang.sglang_requests.w1_jobs('qwen_decisions', fixtures.load_inputs(['w1']))[0]
        with tempfile.TemporaryDirectory() as temp, Stub(lambda path, body: (400, b'{"error": "radix"}')) as stub:
            refused = run_sglang.run_probe(Probe('radix'), Path(temp) / 'a', job, lambda p, d: FakeRuntime(stub.url),
                                           time.monotonic() + 60)
            crashed = run_sglang.run_probe(Probe('radix'), Path(temp) / 'b', job,
                                           lambda p, d: FakeRuntime(stub.url, fail='A service exited before readiness'),
                                           time.monotonic() + 60)
        self.assertFalse(refused['passed'])
        self.assertEqual([row['error_kind'] for row in refused['requests']], ['http_400', 'http_400'])
        self.assertFalse(crashed['passed'])
        self.assertIn('exited before readiness', crashed['error'])


class SelectionTests(unittest.TestCase):
    def test_default_plan(self):
        probes, cells, conditional = run_sglang.select(['w1', 'w2'], [])
        self.assertEqual([p.id for p in probes], ['probe-overlap', 'probe-radix'])
        self.assertEqual(len(cells), 15)
        self.assertEqual(sorted(c.id for c in conditional.values()), ['w1-qwen_decisions-4x4-overlap', 'w1-qwen_decisions-4x4-radix'])
        self.assertEqual(run_sglang.select(['w2'], [])[0], [])
        self.assertTrue(all(c.slots == c.concurrency for c in cells))

    def test_gated_cell_needs_its_probe_and_ids_must_exist(self):
        with self.assertRaisesRegex(ValueError, 'select the probe too'):
            run_sglang.select(['w1'], ['w1-qwen_decisions-4x4-radix'])
        with self.assertRaisesRegex(ValueError, 'unknown cells'):
            run_sglang.select(['w1'], ['w1-kev-1x1'])
        probes, cells, conditional = run_sglang.select(['w1'], ['probe-radix', 'w1-qwen_decisions-4x4-radix'])
        self.assertEqual(([p.id for p in probes], cells, list(conditional)), (['probe-radix'], [], ['radix']))


def journal_row(case_id, order, pass_number, sequence, label, status='ok'):
    return {'phase': 'measured', 'case_id': case_id, 'order': order, 'pass_number': pass_number, 'sequence': sequence,
            'status': status, 'labels': {'action': label}}


class AgreementTests(unittest.TestCase):
    def test_within_runtime_agreement_uses_measured_pass_one_of_the_1x1_cell(self):
        one = [journal_row('A', 'normal', 1, 0, 'allow'), journal_row('B', 'normal', 1, 1, None, 'invalid'),
               journal_row('B', 'normal', 2, 2, 'defer'), journal_row('A', 'normal', 2, 3, 'defer')]
        reference = {'cell': 'w1-qwen_decisions-1x1', 'path': 'x/journal.jsonl', 'sha256': '0' * 64,
                     'labels': {('A', 'normal'): {'action': 'allow'}, ('B', 'normal'): {'action': None}}}
        own = run_sglang.within_runtime(one, reference, ('action',), 'qwen_decisions')
        # Pass 2 flipped A and B was invalid in the reference: one match of four planned questions.
        self.assertEqual((own['matched'], own['total'], own['reference_valid_questions']), (1, 4, 1))
        missing = run_sglang.within_runtime(one, None, ('action',), 'qwen_decisions')
        self.assertEqual((missing['matched'], missing['total']), (None, 4))


@unittest.skipUnless(platform.system() == 'Darwin', 'provenance reads macOS sysctl keys')
class InvocationTests(unittest.TestCase):
    def invoke(self, ids, workloads):
        probes, cells, conditional = run_sglang.select(workloads, ids)
        verified = {'runtime': 'sglang-mlx', 'source_revision': 'test', 'model': {'quantization': 'test'}}
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / 'out'
        with Stub(decisions) as stub, contextlib.redirect_stdout(io.StringIO()):
            code = run_sglang.run_invocation(argparse.Namespace(output=output), probes, cells, conditional, workloads,
                                             make_runtime=lambda cell, directory: FakeRuntime(stub.url),
                                             verify=lambda: verified)
        return code, output

    def test_w2_decisions_cells_record_both_agreements_usage_and_the_report(self):
        code, output = self.invoke(['w2-qwen_decisions-1x1', 'w2-qwen_decisions-4x4'], ['w2'])
        summary = json.loads((output / 'summary.json').read_text())
        cell = json.loads((output / 'w2-qwen_decisions-4x4' / 'summary.json').read_text())
        usage = json.loads((output / 'w2-qwen_decisions-4x4' / 'usage.json').read_text())
        self.assertEqual((code, summary['status'], summary['runtime'], summary['probes']), (0, 'complete', 'sglang-mlx', []))
        # The shared harness protocol version, inherited from run.py.
        self.assertEqual(summary['protocol']['version'], run_sglang.run.PROTOCOL['version'])
        self.assertEqual((cell['planned'], cell['planned_questions'], cell['valid_questions']), (32, 96, 96))
        agreement = cell['label_agreement']
        self.assertEqual((agreement['matched'], agreement['total']), (96, 96))
        self.assertTrue(agreement['reference_path'].endswith('w2-qwen_decisions-1x1/journal.jsonl'))
        cross = cell['label_agreement_cross_runtime']
        self.assertEqual((cross['total'], cross['reference_arm']), (96, 'qwen_json'))
        self.assertIn('diagnostic only', cross['diagnostic'])
        self.assertEqual(cell['tokens_per_request']['prompt_tokens'], {'requests': 32, 'sum': 32 * 300, 'mean': 300.0})
        self.assertEqual(len(usage), 64)
        self.assertEqual({k: cell['profile'][k] for k in ('max_running_requests', 'max_total_tokens', 'n_ctx_total', 'context_length')},
                         {'max_running_requests': 4, 'max_total_tokens': 16384, 'n_ctx_total': 16384, 'context_length': 4096})
        one = json.loads((output / 'w2-qwen_decisions-1x1' / 'summary.json').read_text())
        self.assertEqual(one['profile']['max_total_tokens'], 8192)
        self.assertEqual(cell['request_mapping']['endpoint'].split(' -> ')[1], 'SGLang /v1/decisions on Qwen (SGLang does not serve Kev)')
        self.assertIn('| `w2-qwen_decisions-4x4` | 4 × 4 | no | 96 / 96 |', (output / 'report.md').read_text())
        self.assertTrue((output / 'source/eval/throughput/run_sglang.py').is_file())

    def test_passing_probe_adds_its_gated_cell(self):
        code, output = self.invoke(['probe-overlap', 'w1-qwen_decisions-1x1', 'w1-qwen_decisions-4x4-overlap'], ['w1'])
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual((code, summary['status']), (0, 'complete'))
        self.assertEqual(summary['planned_cells'], ['w1-qwen_decisions-1x1', 'w1-qwen_decisions-4x4-overlap'])
        self.assertEqual(summary['conditional_cells'], {'w1-qwen_decisions-4x4-overlap': 'added: probe-overlap passed'})
        gated = summary['cells'][1]
        self.assertEqual((gated['profile']['variant'], gated['profile']['dropped_flag']), ('overlap', '--disable-overlap-schedule'))
        self.assertEqual(gated['label_agreement']['matched'], gated['label_agreement']['total'])


def validate(*argv):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = run_sglang.main(['--validate', *argv])
    return code, stdout.getvalue()


W1_PLAN = ('w1 invocation: 9 cells, 1188 requests (396 warmup, 792 measured), 792 measured questions; '
           '2 probes x 2 requests; up to 2 probe-gated cells, 264 requests (176 measured); budget 45 min')
W2_PLAN = 'w2 invocation: 6 cells, 384 requests (192 warmup, 192 measured), 576 measured questions; budget 45 min'


class ValidateTests(unittest.TestCase):
    def test_validate_maps_every_request_offline(self):
        code, text = validate()
        self.assertEqual(code, 0)
        self.assertIn('W1 qwen_json 44/44', text)
        self.assertIn('W2 qwen_decisions 32/32', text)
        self.assertIn(W1_PLAN, text)
        self.assertIn(W2_PLAN, text)
        self.assertRegex(text, r'w2-qwen_decisions-8x8 +8 +8 +32768 +32 +32 +96')

    def test_each_workload_validates_as_its_own_invocation(self):
        code, w1 = validate('--workload', 'w1')
        self.assertEqual(code, 0)
        self.assertIn(W1_PLAN, w1)
        self.assertIn('probe-overlap', w1)
        self.assertNotIn('w2 invocation', w1)
        code, w2 = validate('--workload', 'w2')
        self.assertEqual(code, 0)
        self.assertIn(W2_PLAN, w2)
        self.assertNotIn('probe-', w2)
        self.assertNotIn('w1 invocation', w2)

    def test_run_requires_the_qwen_model(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_sglang.main(['--model', 'kev'])


if __name__ == '__main__':
    unittest.main()
