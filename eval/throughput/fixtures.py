"""Frozen throughput workloads: verified inputs, unchanged request builders, reference labels.

Nothing here writes a prompt, label or candidate. Requests come from the builders
that produced the serial evidence; this module selects, verifies and orders them.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))
ROUTING_DIR = ROOT / 'eval/query-routing'
if str(ROUTING_DIR) not in sys.path:
    # Appended, not prepended: query-routing has its own runner.py and report.py.
    sys.path.append(str(ROUTING_DIR))

import answerability  # noqa: E402
import compare_scoring  # noqa: E402
import evaluate  # noqa: E402
import experiment as routing  # noqa: E402
import metal  # noqa: E402
from model import load_lock  # noqa: E402


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded under a distinct name so it cannot be confused with this directory's runner.py.
routing_runner = _module('query_routing_runner', ROUTING_DIR / 'runner.py')

require = answerability.require
FIELDS = routing.FIELDS
W1_QUESTIONS = ('action',)
W1_DATASET = ROOT / 'eval/answerability/heldout/cases.json'
# Same digest as Taskfile.yml answerability:source:validate (test_fixtures checks they agree).
W1_DATASET_SHA256 = '74066d02a4fcff48b8363478dcbc22f9e08bc6cef5be668c859a7a50ee536594'
W1_MODEL_ROUTED_CASES = 22
W1_REFERENCE = ROOT / 'docs/evidence/20261005T174941.125801Z-answerability-source-metal/comparison.json'
W1_REFERENCE_SHA256 = '22c405872621264ba7e73b0349c34595c8343ddad191f073a31595c91b83b606'
W2_CASES = 32
# Neither execution.json nor heldout.json holds model selections; the executed Metal
# run's per-case rows are in the committed evidence archive.
W2_REFERENCE_ARCHIVE = ROOT / 'docs/evidence/20261006-query-routing/metal.tar.gz'
W2_REFERENCE_MANIFEST = ROOT / 'docs/evidence/20261006-query-routing/MANIFEST.json'
W2_REFERENCE_MEMBER = 'metal/result.json'
W2_REFERENCE_SHA256 = 'e46d230e04f9231b3a5787a1a7c2acf916cba549e16824ce86aab540f6051b8c'
LOCKS = {'kev': ROOT / 'models.lock.json', 'qwen': ROOT / 'models.baseline.lock.json'}
ARM_MODEL = {'kev': 'kev', 'qwen_json': 'qwen', 'qwen_score': 'qwen'}
# One-token scoring never ran serially on the answerability pilot; its labels are
# compared with the same model's serial JSON labels (a cross-format reference).
REFERENCE_ARM = {'kev': 'kev', 'qwen_json': 'qwen_json', 'qwen_score': 'qwen_json'}
PATHS = {'kev': '/v1/systemone', 'qwen_json': '/v1/chat/completions', 'qwen_score': '/completion'}
CONTEXT_PER_SLOT = 4096
# scripts/metal.py's -b and -ub. Only the Amendment 2 cells raise the logical batch (-b);
# the physical batch (-ub) stays 512 so per-step compute is unchanged.
BATCH = 512
UBATCH = 512
WARMUP_PASSES = 1
MEASURED_PASSES = {'w1': 2, 'w2': 1}


@dataclass(frozen=True)
class Cell:
    workload: str
    arm: str
    slots: int
    concurrency: int
    kv_unified: bool = False
    n_batch: int = BATCH

    @property
    def id(self):
        return (f'{self.workload}-{self.arm}-{self.slots}x{self.concurrency}' + ('-kvu' if self.kv_unified else '')
                + (f'-b{self.n_batch}' if self.n_batch != BATCH else ''))

    @property
    def n_ubatch(self):
        return UBATCH

    @property
    def model(self):
        return ARM_MODEL[self.arm]

    @property
    def measured_passes(self):
        return MEASURED_PASSES[self.workload]

    @property
    def n_ctx_total(self):
        return CONTEXT_PER_SLOT * self.slots

    @property
    def n_ctx_per_slot(self):
        # Unified KV lets every slot address the whole pool (src/llama-context.cpp:294-296).
        return self.n_ctx_total if self.kv_unified else CONTEXT_PER_SLOT


CELLS = (
    Cell('w1', 'kev', 1, 1), Cell('w1', 'kev', 4, 4), Cell('w1', 'kev', 8, 8),
    Cell('w1', 'kev', 8, 8, n_batch=4096),  # Amendment 2
    Cell('w1', 'qwen_json', 1, 1), Cell('w1', 'qwen_json', 4, 4), Cell('w1', 'qwen_json', 8, 8),
    Cell('w1', 'qwen_json', 8, 8, n_batch=4096),  # Amendment 2
    Cell('w1', 'qwen_score', 1, 1), Cell('w1', 'qwen_score', 4, 4), Cell('w1', 'qwen_score', 8, 8),
    Cell('w2', 'kev', 1, 1), Cell('w2', 'kev', 4, 1), Cell('w2', 'kev', 4, 4),
    Cell('w2', 'kev', 4, 4, True), Cell('w2', 'kev', 8, 8),
    Cell('w2', 'qwen_json', 1, 1), Cell('w2', 'qwen_json', 4, 4), Cell('w2', 'qwen_json', 8, 8),
)


@dataclass(frozen=True)
class Job:
    """One HTTP request: exact body bytes, the questions it answers and their reference labels.

    qwen_score jobs carry score_input until the live runtime tokenizes them."""
    case_id: str
    order: str
    path: str
    body: bytes | None
    questions: tuple[str, ...]
    reference: dict[str, Any]
    parse: Callable[[Any], dict[str, Any]] | None
    score_input: tuple[str, tuple[str, ...]] | None = None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


# --- W1: independent answerability decisions -------------------------------------------------

def load_w1(path=None):
    path = path or W1_DATASET
    dataset = answerability.load_dataset(path)
    answerability.verify_frozen_dataset(path, dataset, W1_DATASET_SHA256)
    routed = [case for case in dataset['cases'] if answerability.common_gate(case['input'])[1] is None]
    require(len(routed) == W1_MODEL_ROUTED_CASES, 'unexpected count of cases the code precheck leaves unresolved')
    return dataset


def load_w1_reference(path=None):
    path, expected = path or W1_REFERENCE, W1_REFERENCE_SHA256
    raw = Path(path).read_bytes()
    require(digest(raw) == expected, 'W1 reference evidence changed: ' + str(path))
    data = json.loads(raw)
    require(data.get('dataset_sha256') == W1_DATASET_SHA256, 'W1 reference used a different dataset')
    labels, requests, later = {}, {}, {}
    for row in data['rows']:
        if row['arm'] not in ('kev', 'qwen_json') or row['code_action'] is not None:
            continue
        key = (row['arm'], row['case_id'], row['order'])
        label = row['action'] if row['status'] == 'ok' else None
        if row['trial'] == 1:
            labels[key], requests[key] = label, row['request']
        else:
            later[key] = label
    return {'path': relative(path), 'sha256': expected, 'labels': labels, 'requests': requests,
            'trial': 1, 'later_trial_disagreements': sum(later[k] != v for k, v in labels.items() if k in later),
            'model_sha256': {runtime['arm']: runtime['model']['sha256'] for runtime in data['runtimes']}}


def _w1_parser(arm, candidates):
    validate = evaluate.validate_native if arm == 'kev' else evaluate.validate_baseline
    return lambda response: {'action': validate(response, candidates)['choice']}


def _score_parser(label_token_ids):
    return lambda response: {'action': compare_scoring.validate_score(response, label_token_ids)['choice']}


def w1_jobs(arm, dataset, reference, alias):
    """One pass in file order over model-routed cases, normal then reverse candidates."""
    require(arm in PATHS, 'unknown W1 arm')
    jobs = []
    for case in dataset['cases']:
        if answerability.common_gate(case['input'])[1] is not None:
            continue
        for order in ('normal', 'reverse'):
            label = reference['labels'].get((REFERENCE_ARM[arm], case['id'], order))
            if arm == 'qwen_score':
                prepared = answerability.prepare(dataset, case, 'no_added_gate', alias, order)
                # Same evidence text that prepare() hands the Kev and Qwen JSON builders.
                text = json.dumps(prepared['filtered_input'], ensure_ascii=False)
                jobs.append(Job(case['id'], order, PATHS[arm], None, W1_QUESTIONS, {'action': label}, None,
                                (text, tuple(prepared['candidates']))))
                continue
            prepared = answerability.prepare(dataset, case, arm, alias, order)
            # Serialized exactly as answerability.http_call sent it.
            jobs.append(Job(case['id'], order, PATHS[arm], json.dumps(prepared['request']).encode(),
                            W1_QUESTIONS, {'action': label}, _w1_parser(arm, prepared['candidates'])))
    return jobs


def finalize_score_jobs(jobs, dataset, score=None):
    """Tokenize one-token scoring requests through the live runtime, as compare_scoring does.

    prepare_score posts to compare_scoring.BASE; the runtime listens on that port."""
    score = score or compare_scoring.prepare_score
    finished = []
    for job in jobs:
        if job.score_input is None:
            finished.append(job)
            continue
        text, candidates = job.score_input
        prepared = score(dataset, {'text': text}, list(candidates))
        finished.append(dataclasses.replace(job, body=json.dumps(prepared['request']).encode(),
                                            parse=_score_parser(prepared['label_token_ids'])))
    return finished


def traversal(jobs, trial):
    """Trial 1 keeps file order; even trials reverse case order, as the serial run did."""
    if trial % 2:
        return list(jobs)
    groups = {}
    for job in jobs:
        groups.setdefault(job.case_id, []).append(job)
    return [job for case_id in reversed(list(groups)) for job in groups[case_id]]


# --- W2: bundled questions over one shared state ---------------------------------------------

def load_w2():
    """Verify the frozen design (file hashes, request manifest) and its executed binding."""
    freeze = routing.verify_freeze()
    execution = routing.load('execution.json')
    require(execution.get('design_freeze_sha256') == routing.digest(ROUTING_DIR / 'freeze.json'),
            'execution.json does not bind the frozen design')
    cases = routing.load('heldout.json')['cases']
    require(len(cases) == W2_CASES, 'unexpected held-out routing cohort size')
    return {'protocol': routing.load('protocol.json'), 'training': routing.load('training.json'),
            'cases': cases, 'execution': execution, 'freeze_sha256': routing.digest(ROUTING_DIR / 'freeze.json'),
            'heldout_sha256': freeze['files']['heldout.json']}


def raw_selection(arm, response):
    """The three selections as the model returned them, before tuple validation.

    Tuple-invalid answers still carry labels, so they remain usable as a reference."""
    try:
        if arm == 'kev':
            return {key: response['answers'][key]['choice'] for key in FIELDS}
        content = routing_runner.strict_json(response['choices'][0]['message']['content'])
        return {key: content[key] for key in FIELDS}
    except (KeyError, TypeError, IndexError, ValueError):
        return None


def load_w2_reference(archive=None):
    archive, manifest, expected = archive or W2_REFERENCE_ARCHIVE, W2_REFERENCE_MANIFEST, W2_REFERENCE_SHA256
    listing = json.loads(Path(manifest).read_text())['archives'][Path(archive).name]
    require(metal.sha256(Path(archive)) == listing['sha256'], 'W2 evidence archive changed: ' + str(archive))
    with tarfile.open(archive) as bundle:
        raw = bundle.extractfile(W2_REFERENCE_MEMBER).read()
    require(digest(raw) == expected == listing['members'][W2_REFERENCE_MEMBER]['sha256'],
            'W2 reference rows changed: ' + W2_REFERENCE_MEMBER)
    data = json.loads(raw)
    labels, request_sha256 = {}, {}
    for row in data['rows']:
        if row['view'] == 'normal':
            labels[(row['arm'], row['id'])] = raw_selection(row['arm'], row.get('response'))
            request_sha256[(row['arm'], row['id'])] = row['request_sha256']
    return {'path': relative(archive) + '!' + W2_REFERENCE_MEMBER, 'sha256': expected, 'labels': labels,
            'request_sha256': request_sha256, 'execution': data['execution'],
            'runtime_command': {r['arm']: r.get('runtime_command') for r in data['runtimes']},
            'model_sha256': {r['arm']: r['model']['sha256'] for r in data['runtimes']}}


def _w2_parser(arm, request, protocol):
    allowed = {'operation': list(protocol['operations']), 'node': protocol['nodes'], 'field': protocol['fields']}

    def parse(response):
        require(isinstance(response, dict), 'response must be an object')
        if arm == 'kev':
            answers = response.get('answers')
            require(isinstance(answers, dict), 'missing native answers')
            labels = {}
            for key in FIELDS:
                # Heads are answered independently, so each is valid or invalid on its own.
                try:
                    labels[key] = evaluate.validate_native(
                        {'answers': {'route': answers.get(key)}}, list(request['questions'][key]['criteria']))['choice']
                except ValueError:
                    labels[key] = None
            return labels
        choices = response.get('choices')
        require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                and choices[0].get('finish_reason') == 'stop', 'expected one normally finished JSON answer')
        message = choices[0].get('message')
        require(isinstance(message, dict) and isinstance(message.get('content'), str), 'expected a textual JSON message')
        selection = routing_runner.strict_json(message['content'])
        require(isinstance(selection, dict) and set(selection) == set(FIELDS), 'expected exactly operation, node and field')
        return {key: selection[key] if selection[key] in allowed[key] else None for key in FIELDS}
    return parse


def w2_jobs(arm, w2, reference):
    """Primary normal-order view; bytes verified against the manifest and the executed run."""
    require(arm in ('kev', 'qwen_json'), 'unknown W2 arm')
    jobs = []
    for case in w2['cases']:
        request, body = routing_runner.request_for(case, arm, 'normal', w2['protocol'], w2['training'])
        require(digest(body) == reference['request_sha256'][(arm, case['id'])],
                'request differs from the executed Metal run: ' + case['id'])
        labels = reference['labels'].get((arm, case['id'])) or {}
        jobs.append(Job(case['id'], 'normal', PATHS[arm], body, FIELDS, {key: labels.get(key) for key in FIELDS},
                        _w2_parser(arm, request, w2['protocol'])))
    return jobs


# --- Shared ---------------------------------------------------------------------------------

def load_inputs(workloads):
    """Verify every frozen input a run or --validate needs, before any model launch."""
    locks = {model: load_lock(path) for model, path in LOCKS.items()}
    for lock in locks.values():
        require(lock['runtime_revision'] == metal.REVISION, 'model lock and scripts/metal.py runtime revisions differ')
    inputs = {'locks': locks, 'lock_sha256': {model: metal.sha256(path) for model, path in LOCKS.items()}}
    if 'w1' in workloads:
        inputs['w1'] = load_w1()
        inputs['w1_reference'] = load_w1_reference()
        for arm, model in (('kev', 'kev'), ('qwen_json', 'qwen')):
            require(inputs['w1_reference']['model_sha256'][arm] == locks[model]['sha256'],
                    'W1 reference ran a different model pin: ' + arm)
    if 'w2' in workloads:
        inputs['w2'] = load_w2()
        inputs['w2_reference'] = load_w2_reference()
        require(inputs['w2_reference']['execution'] == inputs['w2']['execution'],
                'W2 reference was not produced under the current execution.json')
        for arm, model in (('kev', 'kev'), ('qwen_json', 'qwen')):
            require(inputs['w2_reference']['model_sha256'][arm] == locks[model]['sha256'],
                    'W2 reference ran a different model pin: ' + arm)
    return inputs


def jobs_for(cell, inputs):
    """Offline jobs for one pass; qwen_score still needs finalize_score_jobs at run time."""
    alias = inputs['locks'][cell.model]['alias']
    if cell.workload == 'w1':
        return w1_jobs(cell.arm, inputs['w1'], inputs['w1_reference'], alias)
    return w2_jobs(cell.arm, inputs['w2'], inputs['w2_reference'])


def reference_for(cell, inputs):
    reference = inputs['w1_reference' if cell.workload == 'w1' else 'w2_reference']
    return {'path': reference['path'], 'sha256': reference['sha256'], 'arm': REFERENCE_ARM[cell.arm],
            'view': 'trial 1, both candidate orders' if cell.workload == 'w1' else 'normal order'}
