import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
import unittest
from unittest import mock

import fixtures
from fixtures import compare_scoring
import runner
from test_runner import Stub

INPUTS = fixtures.load_inputs(['w1', 'w2'])


class FrozenInputTests(unittest.TestCase):
    def test_dataset_digest_matches_taskfile(self):
        taskfile = (fixtures.ROOT / 'Taskfile.yml').read_text()
        block = taskfile.split('answerability:source:validate:', 1)[1].split('\n  answerability:', 1)[0]
        self.assertEqual(re.search(r'--expected-dataset-sha256 ([0-9a-f]{64})', block)[1], fixtures.W1_DATASET_SHA256)

    def test_changed_dataset_is_rejected(self):
        original = fixtures.W1_DATASET.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / 'cases.json'
            changed.write_bytes(original + b'\n')  # still valid JSON, different bytes
            with self.assertRaisesRegex(ValueError, 'dataset changed'):
                fixtures.load_w1(changed)

    def test_changed_reference_is_rejected(self):
        original = fixtures.W1_REFERENCE.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / 'comparison.json'
            changed.write_bytes(original.replace(b'"defer"', b'"allow"', 1))
            with self.assertRaisesRegex(ValueError, 'W1 reference evidence changed'):
                fixtures.load_w1_reference(changed)

    def test_changed_w2_reference_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'metal.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(fixtures.W1_REFERENCE, arcname=fixtures.W2_REFERENCE_MEMBER)
            with self.assertRaisesRegex(ValueError, 'W2 evidence archive changed'):
                fixtures.load_w2_reference(archive)

    def test_reference_labels_cover_every_planned_request(self):
        self.assertEqual(INPUTS['w1_reference']['later_trial_disagreements'], 0)
        for cell in fixtures.CELLS:
            for job in fixtures.jobs_for(cell, INPUTS):
                self.assertTrue(all(label is not None for label in job.reference.values()), cell.id)


class RequestIdentityTests(unittest.TestCase):
    def test_w1_bodies_are_byte_identical_to_the_serial_run(self):
        reference = INPUTS['w1_reference']
        for arm in ('kev', 'qwen_json'):
            jobs = fixtures.w1_jobs(arm, INPUTS['w1'], reference, INPUTS['locks'][fixtures.ARM_MODEL[arm]]['alias'])
            self.assertEqual(len(jobs), 44)
            for job in jobs:
                saved = reference['requests'][(arm, job.case_id, job.order)]
                self.assertEqual(job.body, json.dumps(saved).encode(), (arm, job.case_id, job.order))
        sampled = json.loads(fixtures.w1_jobs('qwen_json', INPUTS['w1'], reference, 'qwen3.5-4b')[0].body)
        self.assertEqual((sampled['cache_prompt'], sampled['seed']), (True, 0))

    def test_w2_bodies_are_byte_identical_to_the_executed_run(self):
        with tarfile.open(fixtures.W2_REFERENCE_ARCHIVE) as bundle:
            rows = json.loads(bundle.extractfile(fixtures.W2_REFERENCE_MEMBER).read())['rows']
        manifest = {(r['id'], r['backend'], r['order']): r['sha256']
                    for r in json.loads((fixtures.ROUTING_DIR / 'request-manifest.json').read_text())}
        for arm in ('kev', 'qwen_json'):
            saved = {r['id']: r for r in rows if r['arm'] == arm and r['view'] == 'normal'}
            for job in fixtures.w2_jobs(arm, INPUTS['w2'], INPUTS['w2_reference']):
                digest = hashlib.sha256(job.body).hexdigest()
                self.assertEqual(digest, saved[job.case_id]['request_sha256'])
                self.assertEqual(digest, manifest[(job.case_id, arm, 'normal')])
                self.assertEqual(json.loads(job.body), saved[job.case_id]['request'])

    def test_one_token_scoring_uses_the_unchanged_builder_and_the_same_evidence(self):
        seen = []

        def respond(path, body):
            payload = json.loads(body)
            if path == '/apply-template':
                seen.append(payload['messages'])
                return 200, json.dumps({'prompt': 'P'}).encode()
            return 200, json.dumps({'tokens': [ord(c) for c in payload['content']]}).encode()
        reference = INPUTS['w1_reference']
        jobs = fixtures.w1_jobs('qwen_score', INPUTS['w1'], reference, 'qwen3.5-4b')[:2]
        self.assertTrue(all(job.body is None for job in jobs))
        with Stub(respond) as stub, mock.patch.object(compare_scoring, 'BASE', stub.url):
            finished = fixtures.finalize_score_jobs(jobs, INPUTS['w1'])
        for job, messages in zip(finished, seen):
            request = json.loads(job.body)
            self.assertEqual(request['prompt'], [ord('P')])
            self.assertEqual({k: request[k] for k in ('n_predict', 'n_probs', 'post_sampling_probs', 'cache_prompt', 'grammar')},
                             {'n_predict': 1, 'n_probs': 256, 'post_sampling_probs': False, 'cache_prompt': False,
                              'grammar': 'root ::= "A" | "B"'})
            evidence = reference['requests'][('qwen_json', job.case_id, job.order)]['messages'][1]['content']
            self.assertEqual(messages[1]['content'], evidence)
        self.assertEqual([j.order for j in finished], ['normal', 'reverse'])


class PlanTests(unittest.TestCase):
    def test_even_trials_reverse_case_order_only(self):
        jobs = fixtures.w1_jobs('kev', INPUTS['w1'], INPUTS['w1_reference'], 'semselect-kev-4b')
        second = fixtures.traversal(jobs, 2)
        self.assertEqual(fixtures.traversal(jobs, 1), jobs)
        self.assertEqual([(j.case_id, j.order) for j in second[:2]], [(jobs[-1].case_id, 'normal'), (jobs[-1].case_id, 'reverse')])
        self.assertEqual(sorted(id(j) for j in second), sorted(id(j) for j in jobs))

    def test_cells_are_the_frozen_plan(self):
        self.assertEqual([(c.id, c.n_ctx_total, c.n_ctx_per_slot) for c in fixtures.CELLS if c.workload == 'w2' and c.arm == 'kev'],
                         [('w2-kev-1x1', 4096, 4096), ('w2-kev-4x1', 16384, 4096), ('w2-kev-4x4', 16384, 4096),
                          ('w2-kev-4x4-kvu', 16384, 16384), ('w2-kev-8x8', 32768, 4096)])
        self.assertEqual(len(fixtures.CELLS), 17)
        self.assertEqual({c.id for c in fixtures.CELLS if c.workload == 'w1'},
                         {f'w1-{arm}-{n}x{n}' for arm in ('kev', 'qwen_json', 'qwen_score') for n in (1, 4, 8)})


def native(choice, candidates):
    share = 0.5 / (len(candidates) - 1)
    probabilities = {c: (0.5 if c == choice else share) for c in candidates}
    return {'type': 'choice', 'choice': choice, 'probabilities': probabilities, 'confidence': 0.5}


class AgreementTests(unittest.TestCase):
    """Real workload parsers, a loopback stub and the frozen reference labels."""

    def test_w1_agreement_counts_labels_equal_to_the_serial_reference(self):
        jobs = fixtures.w1_jobs('kev', INPUTS['w1'], INPUTS['w1_reference'], 'semselect-kev-4b')[:4]
        body = json.dumps({'answers': {'route': native('allow', ['allow', 'defer'])}, 'usage': {'input_tokens': 9}}).encode()
        with Stub(lambda path, response: (200, body)) as stub:
            rows = [runner.call(stub.url, job, 5) for job in jobs]
        for job, row in zip(jobs, rows):
            self.assertEqual((row['status'], row['labels'], row['tokens']), ('ok', {'action': 'allow'}, {'input_tokens': 9}))
            self.assertEqual(row['matched'], int(job.reference['action'] == 'allow'))

    def test_w2_kev_heads_are_validated_and_matched_independently(self):
        job = fixtures.w2_jobs('kev', INPUTS['w2'], INPUTS['w2_reference'])[0]
        criteria = {k: list(q['criteria']) for k, q in json.loads(job.body)['questions'].items()}
        other_field = next(f for f in criteria['field'] if f != job.reference['field'])
        answers = {'operation': native(job.reference['operation'], criteria['operation']),
                   'node': native('not-a-node', criteria['node']), 'field': native(other_field, criteria['field'])}
        with Stub(lambda path, body: (200, json.dumps({'answers': answers}).encode())) as stub:
            row = runner.call(stub.url, job, 5)
        self.assertEqual((row['status'], row['valid_questions'], row['matched']), ('invalid', 2, 1))
        self.assertIsNone(row['labels']['node'])

    def test_w2_qwen_compound_answer_needs_exactly_three_fields(self):
        job = fixtures.w2_jobs('qwen_json', INPUTS['w2'], INPUTS['w2_reference'])[0]
        def content(value):
            return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
                               'timings': {'prompt_n': 1090, 'cache_n': 0}}).encode()
        complete = content(job.reference)
        partial = content({'operation': job.reference['operation'], 'node': job.reference['node']})
        with Stub(lambda path, body: (200, complete)) as stub:
            whole = runner.call(stub.url, job, 5)
        with Stub(lambda path, body: (200, partial)) as stub:
            missing = runner.call(stub.url, job, 5)
        self.assertEqual((whole['status'], whole['matched'], whole['tokens']), ('ok', 3, {'prompt_processed': 1090, 'prompt_cached': 0}))
        self.assertEqual((missing['status'], missing['valid_questions'], missing['matched']), ('invalid', 0, 0))


if __name__ == '__main__':
    unittest.main()
