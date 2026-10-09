import argparse
import contextlib
import io
import json
from pathlib import Path
import platform
import tempfile
import time
import unittest
from unittest import mock

import fixtures
import report
import run
from test_runner import Stub, answer, jobs

REFERENCE = {'path': 'reference.json', 'sha256': '0' * 64, 'arm': 'kev', 'view': 'test'}


class FakeRuntime:
    def __init__(self, url, fail=False):
        self.base, self.fail, self.info = url, fail, {}
        self.started = self.stopped = False
        self.scrapes = 0

    def start(self, deadline):
        self.started = True
        if self.fail:
            raise RuntimeError('Full Metal offload was not confirmed; inspect runtime log')
        return self

    def metrics(self):
        self.scrapes += 1
        return {'prompt_tokens_total': 100.0 * self.scrapes, 'n_decode_total': 10.0 * self.scrapes,
                'n_busy_slots_per_decode': 2.0}

    def stop(self):
        self.stopped = True
        self.info['cleanup_errors'] = []
        return self.info


def journal(directory):
    return [json.loads(line) for line in (directory / 'journal.jsonl').read_text().splitlines()]


def scripted(pattern):
    """Answer requests in arrival order: 'e' is HTTP 500, 'o' a valid answer; valid answers after the script."""
    calls = iter(pattern)
    return lambda path, body: (500, b'{}') if next(calls, 'o') == 'e' else answer('allow')


def run_scripted(pattern, cases):
    """One client, so arrival order is warmup (cases requests) then two measured passes."""
    cell = fixtures.Cell('w1', 'kev', 1, 1)
    with tempfile.TemporaryDirectory() as temp, Stub(scripted(pattern)) as stub:
        result = run.run_cell(cell, Path(temp) / cell.id, jobs(cases), REFERENCE, lambda c, d: FakeRuntime(stub.url),
                              time.monotonic() + 60)
        lines = journal(Path(temp) / cell.id)
    return result, lines


class CellTests(unittest.TestCase):
    def test_complete_cell_journals_warmup_and_measured_passes(self):
        cell = fixtures.Cell('w1', 'kev', 2, 2)
        with tempfile.TemporaryDirectory() as temp, Stub(lambda path, body: answer('allow')) as stub:
            runtime = FakeRuntime(stub.url)
            result = run.run_cell(cell, Path(temp) / cell.id, jobs(3), REFERENCE, lambda c, d: runtime,
                                  time.monotonic() + 60)
            lines = journal(Path(temp) / cell.id)
            saved = json.loads((Path(temp) / cell.id / 'summary.json').read_text())
            requests = json.loads((Path(temp) / cell.id / 'requests.json').read_text())
        self.assertTrue(runtime.stopped)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(saved['cell'], 'w1-kev-2x2')
        self.assertEqual([r['phase'] for r in lines].count('warmup'), 3)
        # The journal is in completion order; sequence is the planned position.
        measured = sorted((r for r in lines if r['phase'] == 'measured'), key=lambda r: r['sequence'])
        self.assertEqual([r['pass_number'] for r in measured], [1, 1, 1, 2, 2, 2])
        self.assertEqual([r['case_id'] for r in measured], ['C00', 'C01', 'C02', 'C02', 'C01', 'C00'])
        self.assertEqual((result['planned'], result['valid_questions'], result['warmup']['ok']), (6, 6, 3))
        self.assertEqual(result['label_agreement']['matched'], 6)
        self.assertEqual(result['label_agreement']['reference_sha256'], '0' * 64)
        self.assertEqual(result['metrics']['measured_delta']['prompt_tokens_total'], 100.0)
        self.assertEqual(len(requests), 3)

    def test_exhausted_budget_never_starts_a_runtime_and_keeps_denominators(self):
        cell = fixtures.Cell('w2', 'kev', 4, 1)
        with tempfile.TemporaryDirectory() as temp:
            def refuse(*_):
                raise AssertionError('runtime must not start without budget')
            result = run.run_cell(cell, Path(temp) / cell.id, jobs(4), REFERENCE, refuse, time.monotonic() - 1)
            lines = journal(Path(temp) / cell.id)
        self.assertEqual((result['status'], result['stop_reason']), ('stopped', 'budget'))
        self.assertEqual((result['planned'], result['not_run']), (4, 4))
        self.assertEqual([r['status'] for r in lines], ['not_run'] * 4)
        self.assertEqual(result['label_agreement']['total'], 4)

    def test_startup_failure_stops_runtime_and_records_not_run(self):
        cell = fixtures.Cell('w1', 'qwen_json', 8, 8)
        runtime = FakeRuntime('http://127.0.0.1:9', fail=True)
        with tempfile.TemporaryDirectory() as temp:
            result = run.run_cell(cell, Path(temp) / cell.id, jobs(2), REFERENCE, lambda c, d: runtime, time.monotonic() + 60)
        self.assertTrue(runtime.stopped)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Full Metal offload', result['stop_reason'])
        self.assertEqual((result['planned'], result['not_run'], result['decisions_per_s']), (4, 4, None))

    # Protocol amendment 1 (2026-10-09): warmup errors are recorded but never stop a cell.
    def test_warmup_errors_do_not_stop_the_cell(self):
        result, lines = run_scripted('eeeoeeo', 7)  # five warmup errors, three of them consecutive
        self.assertEqual((result['status'], result['stop_reason'], result['protocol_version']), ('complete', None, 2))
        self.assertEqual(result['warmup'], {'planned': 7, 'attempted': 7, 'ok': 2, 'errors': 5})
        self.assertEqual([r['status'] for r in lines if r['phase'] == 'warmup'].count('error'), 5)
        self.assertEqual((result['planned'], result['valid'], result['not_run'], result['errors']['total']), (14, 14, 0, 0))

    def test_three_consecutive_measured_errors_still_stop_the_cell(self):
        result, lines = run_scripted('eoeo' + 'eee', 4)
        self.assertEqual((result['status'], result['stop_reason']), ('stopped', 'three consecutive runtime errors'))
        self.assertEqual(result['warmup'], {'planned': 4, 'attempted': 4, 'ok': 2, 'errors': 2})
        measured = sorted((r for r in lines if r['phase'] == 'measured'), key=lambda r: r['sequence'])
        self.assertEqual([r['status'] for r in measured], ['error'] * 3 + ['not_run'] * 5)
        self.assertEqual((result['valid'], result['not_run']), (0, 5))
        # A stopped cell with no valid request renders with its stop reason and warmup counts.
        markdown = report.render([{'runtime': 'llamacpp', 'model': 'kev', 'protocol': run.PROTOCOL, 'cells': [result]}])
        self.assertIn('| `w1-kev-1x1` | 1 × 1 | no | 0 / 8 | 0.00 |', markdown)
        self.assertIn('| 2 / 2 / 4 / 4 | stopped (three consecutive runtime errors) |', markdown)

    def test_consecutive_count_does_not_carry_over_from_warmup(self):
        # Two trailing warmup errors plus two leading measured errors would be four if the count carried over.
        result, _ = run_scripted('ooee' + 'ee', 4)
        self.assertEqual((result['status'], result['stop_reason']), ('complete', None))
        self.assertEqual((result['warmup']['errors'], result['errors']['total'], result['valid'], result['not_run']),
                         (2, 2, 6, 0))


class SelectionTests(unittest.TestCase):
    def test_cells_are_filtered_by_model_workload_and_id(self):
        self.assertEqual(len(run.select_cells('kev', ['w1', 'w2'], [])), 8)
        self.assertEqual(len(run.select_cells('qwen', ['w1', 'w2'], [])), 9)
        self.assertEqual([c.id for c in run.select_cells('kev', ['w2'], ['w2-kev-4x4-kvu'])], ['w2-kev-4x4-kvu'])
        with self.assertRaisesRegex(ValueError, 'unknown cells'):
            run.select_cells('qwen', ['w2'], ['w2-kev-1x1'])


def kev_answers(path, body):
    """Answer every Choice head with its first candidate, in the native response shape."""
    questions = json.loads(body)['questions']
    answers = {}
    for key, question in questions.items():
        options = list(question['criteria'])
        probabilities = {o: (0.5 if i == 0 else 0.5 / (len(options) - 1)) for i, o in enumerate(options)}
        answers[key] = {'type': 'choice', 'choice': options[0], 'probabilities': probabilities, 'confidence': 0.5}
    return 200, json.dumps({'model': 'semselect-kev-4b', 'answers': answers, 'usage': {'input_tokens': 3600}}).encode()


@unittest.skipUnless(platform.system() == 'Darwin', 'provenance reads macOS sysctl keys')
class InvocationTests(unittest.TestCase):
    def test_run_writes_provenance_summary_and_report_without_inference(self):
        lock = fixtures.load_lock(fixtures.LOCKS['kev'])
        with tempfile.TemporaryDirectory() as temp, Stub(kev_answers) as stub, \
                mock.patch.object(run.llamacpp, 'verify_runtime', return_value=(lock, {'runtime_revision': 'test'})), \
                mock.patch.object(run.llamacpp, 'Runtime', lambda cell, lock, directory: FakeRuntime(stub.url)):
            output = Path(temp) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                code = run.run(argparse.Namespace(model='kev', output=output), run.select_cells('kev', ['w2'], ['w2-kev-4x1']), ['w2'])
            summary = json.loads((output / 'summary.json').read_text())
            cell = json.loads((output / 'w2-kev-4x1' / 'summary.json').read_text())
            report_text = (output / 'report.md').read_text()
            copied = (output / 'source/eval/throughput/run.py').is_file() and (output / 'working-tree.diff').is_file()
        self.assertEqual((code, summary['status'], summary['stop_reason']), (0, 'complete', None))
        self.assertEqual((cell['planned'], cell['planned_questions'], cell['valid_questions']), (32, 96, 96))
        self.assertEqual(cell['label_agreement']['total'], 96)
        self.assertEqual(cell['label_agreement']['reference_path'], 'docs/evidence/20261006-query-routing/metal.tar.gz!metal/result.json')
        self.assertEqual(cell['provenance']['datasets']['w2']['sha256'], fixtures.routing.digest(fixtures.ROUTING_DIR / 'heldout.json'))
        self.assertEqual(cell['tokens_per_request']['input_tokens']['sum'], 32 * 3600)
        self.assertTrue(copied)
        self.assertIn('| `w2-kev-4x1` | 4 × 1 | no | 96 / 96 |', report_text)


class ValidateTests(unittest.TestCase):
    def test_validate_exits_nonzero_on_a_frozen_hash_mismatch(self):
        stderr = io.StringIO()
        with mock.patch.object(fixtures, 'W1_DATASET_SHA256', '0' * 64), contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(['--validate', '--workload', 'w1']), 1)
        self.assertIn('dataset changed since freezing', stderr.getvalue())

    def test_validate_succeeds_offline(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(run.main(['--validate', '--model', 'kev']), 0)
        self.assertIn('kev: 8 cells, 716 requests (292 warmup, 424 measured)', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
