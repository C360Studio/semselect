"""Freeze and environment checks. Missing measurements always fail closed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def source_hashes():
    files = [p for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'wheels' not in p.parts and p.suffix != '.pyc']
    for name in ('models.baseline.lock.json', 'docs/evidence/20261005-synthesis-acquisition/provenance/embedding-files.json', 'eval/query-routing/training.json', 'eval/query-routing/driver/main.go', 'eval/query-routing/driver/go.mod', 'eval/query-routing/driver/go.sum', 'eval/query-routing/driver/source-pins.json'):
        files.append(REPO/name)
    return {str(p.relative_to(REPO)): digest(p) for p in sorted(files)}


def verify_sources(manifest):
    if not manifest or manifest != source_hashes():
        raise ValueError('implementation/fixture/reference source differs from preparation freeze')


def sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def verify_environment(env, arm, cpu_probe=False):
    """Require measured runtime provenance; a model name is not an artifact pin."""
    required = ('model_revision', 'artifacts', 'dependencies', 'runtime_sha256', 'architecture',
                'precision', 'readiness_seconds', 'idle_memory_bytes', 'peak_memory_bytes',
                'peak_includes_startup', 'cpu_time_seconds', 'oom', 'startup_log_sha256',
                'background_contention', 'cleanup', 'verified')
    if any(k not in env for k in required) or env['verified'] is not True:
        raise ValueError('environment record missing measured provenance')
    if not re.fullmatch('[a-f0-9]{40,64}', env['model_revision']) or not sha(env['runtime_sha256']) or not sha(env['startup_log_sha256']):
        raise ValueError('runtime/model revision is not immutable')
    if not env['artifacts'] or any(not sha(v) for v in env['artifacts'].values()) or not env['dependencies']:
        raise ValueError('artifact hashes and exact dependencies required')
    if any(not isinstance(v, str) or not v or any(c in v for c in '<>*~^') for v in env['dependencies'].values()):
        raise ValueError('dependencies must be exact versions')
    if env['oom'] is not False or env['peak_includes_startup'] is not True:
        raise ValueError('OOM or unmeasured load/warmup memory')
    if arm in ('gliclass', 'deberta', 'embeddings') or cpu_probe:
        if env['architecture'] != 'linux/arm64' or env.get('cpu_only') is not True or env.get('cpus') != 4 or env.get('threads') != 4 or env.get('memory_limit_bytes') != 4294967296 or env.get('query_batch_size') != 1:
            raise ValueError('CPU Docker limits differ from protocol')
        if not re.fullmatch('sha256:[a-f0-9]{64}', env.get('container_digest', '')):
            raise ValueError('container digest required')
        if arm in ('gliclass', 'deberta') and env['precision'] != 'FP32':
            raise ValueError('specialist precision must be FP32')
        if arm == 'deberta' and env.get('pair_batch_size') != 9:
            raise ValueError('DeBERTa pairs must use frozen batch size nine')
        if arm in ('gliclass', 'deberta'):
            model = read(ROOT/'locks'/f'{arm}.json')
            if env['model_revision'] != model['revision'] or env['artifacts'] != {name: info['sha256'] for name, info in model['files'].items()}:
                raise ValueError('specialist artifacts differ from frozen model lock')
            dependencies = read(ROOT/'locks/dependencies.json')['direct_versions']
            if any(env['dependencies'].get(name) != version for name, version in dependencies.items()):
                raise ValueError('specialist dependencies differ from frozen direct pins')
        elif arm == 'embeddings':
            model = read(ROOT/'locks/models.json')['embeddings']
            lock = read((ROOT/'locks'/model['artifact_lock']).resolve())
            expected = {name: info['sha256'] for name, info in lock['files'].items()} if isinstance(lock['files'], dict) else {row['path']: row['sha256'] for row in lock['files']}
            if env['model_revision'] != model['revision'] or env['artifacts'] != expected or env['precision'].lower() in ('unknown', 'unverified'):
                raise ValueError('embedding artifact/precision identity unresolved')
    if arm == 'qwen':
        if (not cpu_probe and env['architecture'] != 'darwin/arm64-metal') or env.get('thinking') is not False:
            raise ValueError('primary Qwen evaluation requires Metal with thinking disabled')
        if type(env.get('prefix_cache_disabled_verified')) is not bool:
            raise ValueError('record verified cache disabling or disclose the timing confound')
        lock = read(REPO/'models.baseline.lock.json')
        if env['model_revision'] != lock['revision'] or env['artifacts'].get(lock['filename']) != lock['sha256'] or env.get('runtime_revision') != lock['runtime_revision']:
            raise ValueError('Qwen differs from existing model/runtime lock')
    elif arm not in ('gliclass', 'deberta', 'embeddings'):
        raise ValueError('unknown learned arm')
    return env


def verify_tokens(records, requests, arm, environment=None):
    indexed = {}
    for record in records:
        key = record['payload_sha256']
        if key in indexed and indexed[key] != record:
            raise ValueError('conflicting token records')
        indexed[key] = record
    for request in requests:
        key = request['payload_sha256']
        row = indexed.get(key)
        if not row or row.get('arm') != arm or row.get('full_input') is not True or row.get('truncated') is not False or row.get('verified') is not True:
            raise ValueError('missing full-input token preflight: '+key)
        count = row.get('tokens')
        limit = 4096 if arm == 'qwen' else 512
        if type(count) is not int or not 0 < count <= limit:
            raise ValueError('input exceeds frozen context bound')
        if arm == 'qwen' and count + 64 > limit:
            raise ValueError('Qwen context lacks output reserve')
        if not sha(row.get('tokenizer_sha256')):
            raise ValueError('tokenizer provenance missing')
        if environment is not None:
            identity = row.get('identity', {})
            if identity.get('revision') != environment['model_revision']:
                raise ValueError('token preflight used another model revision')
            # The identity's digest must bind the complete artifact manifest, not
            # an arbitrary caller-supplied string that merely looks like a hash.
            from transport import encoded
            if hashlib.sha256(encoded(identity)).hexdigest() != row['tokenizer_sha256']:
                raise ValueError('tokenizer identity digest mismatch')
            artifacts = identity.get('artifacts', {})
            actual = {k: v['sha256'] if isinstance(v, dict) else v for k, v in artifacts.items()}
            if actual != environment['artifacts']:
                raise ValueError('tokenizer artifact manifest differs from loaded runtime')
    return True
