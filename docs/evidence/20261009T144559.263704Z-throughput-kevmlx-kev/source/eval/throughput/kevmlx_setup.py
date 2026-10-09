#!/usr/bin/env python3
"""Pinned Kev MLX serving environment under .kev/ for the throughput screen.

    python3 eval/throughput/kevmlx_setup.py            fetch, install and record (network; idempotent)
    python3 eval/throughput/kevmlx_setup.py --verify   re-check every recorded byte; no network

It fetches only what kev.serve needs: the Kev source archive at one commit (only the
package, pyproject.toml, README.md, LICENSE and .python-version are extracted), the
`serve` extra's dependencies into a uv venv, the Kev-4B adapter snapshot (about 160 MB)
and the Qwen3.5-4B-Base snapshot (about 9.34 GB). Hub files are selected with
kev.checkpoint.resolve_run's patterns (checkpoint.py:45) plus LICENSE and README.md for
attribution, and each is checked against the Hub's own digest at the pinned revision
(SHA-256 for LFS files, git blob SHA-1 otherwise). Every file's SHA-256 and size, the
license strings and the commands run are written to .kev/provenance.json.

Refuses to fetch the base with less than 12 GiB free, and refuses to run while a
Metal build or measurement holds .native/operation.lock.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
KEV_DIR = ROOT / '.kev'
OPERATION_LOCK = ROOT / '.native/operation.lock'  # scripts/metal.py CACHE / 'operation.lock'

KEV_REPOSITORY = 'https://github.com/jaredpalmer/kev'
KEV_COMMIT = '5e42a7a03f28134853dd3ff77461457e921e5ec1'
KEV_COMMIT_DATE = '2026-10-05T20:43:29Z'
KEV_LICENSE = 'Apache-2.0'
ARCHIVE_URL = f'{KEV_REPOSITORY}/archive/{KEV_COMMIT}.tar.gz'
ARCHIVE_TOP = f'kev-{KEV_COMMIT}'
ARCHIVE_MAX_BYTES = 1 << 30
# Git blob SHA-1 of every extracted file, from the GitHub tree at KEV_COMMIT. GitHub does not
# promise byte-stable archives, so the archive's SHA-256 is recorded on first fetch; these pins
# bind the extracted (and installed) bytes to the commit.
SOURCE_BLOBS = {
    '.python-version': '24ee5b1be9961e38a503c8e764b7385dbb6ba124',
    'LICENSE': 'e1ece45b775283628888a36bc75f56078cee9de0',
    'README.md': 'f74193a24470441aa6a832ed534e944d31edecf2',
    'pyproject.toml': '846d5028036c01f879bd1432facab6311261c87e',
    'kev/__init__.py': 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391',
    'kev/anchors.py': '3eefaf52de3b0d8a840f50300b0a3987c5d73ae0',
    'kev/api.py': '0096b136e3fad80ce7c25a5dfec33644990d7aca',
    'kev/autoresearch.py': '9068b56f947947d6b9fc5860a9daf222d317aad3',
    'kev/benchmark.py': 'ada8cc188c0ef3b5eb9fab24cf21cdcce4b2d50c',
    'kev/budget.py': 'ae0835f20baae6f5218a57737c86c520e2bcb680',
    'kev/calibrate.py': 'fc8dcee8c7926125d28c950390aae6a61188e7c5',
    'kev/checkpoint.py': '272462473b5c9dddfeae25499efea319c831fa19',
    'kev/compare.py': 'dbeea3f09d2f1b68e204687b17319a8c43c5a0e6',
    'kev/composition.py': '2f354418936ddb4278939a89ffd1c86510968e51',
    'kev/contrastive.py': '5b0ca1be6b86a122012f784e6b8a78a611162aa7',
    'kev/cuda_graphs.py': '6d6478288fba8894f070eeb5b3466c47eaa0d9dc',
    'kev/data.py': 'a81ea0e2d40059ba304778c33d9eca5ec3178a11',
    'kev/device.py': 'f01f7026b7bac8a5ad598ddf50bb8f607ee455c6',
    'kev/evaluate.py': '703f5f593c88b1699ee76d84f6ef966b7e670534',
    'kev/experiment.py': '1a9eee5fee8c06a2a358a96e62a57a8e6b46931b',
    'kev/full_ft.py': '5784a213b31ceb4f25cf29bde7337df8ae8daac5',
    'kev/fused_qwen35.py': '1df371fe2252dfc6c07da5cfd05e1ffa443a7425',
    'kev/jev.py': 'a4621bde460cee8fb4f3ddf462c1333d854db356',
    'kev/metrics.py': '6c5aacf46aa8e43c0666b47d51e940058771da09',
    'kev/mirror.py': '2358a391d817cc627f487c839222edcf5e41b3d2',
    'kev/mlx_model.py': '0042e36bbfeaec3db5106934d0b66dc151d6ec72',
    'kev/model.py': '1fffa118c8dbac8b3c6c1d2be39d6cd6ce8e9326',
    'kev/plot.py': '83ba7fc0b29ad75c06b877c46a002a3ea22746ef',
    'kev/predictors.py': '0d01683bb92b8c673501fc7d9dd3077433ecbf34',
    'kev/publish.py': '9714e1577f507547a610066ecb06ff386006ae8d',
    'kev/rounds.py': '49cde9bfdeca19e6f5f39b518fe050f2ba40a578',
    'kev/serve.py': 'c2789e3824c0d0ffcb9537e1e04246d036fd2ec9',
    'kev/shared_prefix.py': '14dd3ee815360a1a70abc1a448eb61e7531363e1',
    'kev/study_v3.py': '77218cd4fc8548c12146ecab6ac43e3c7180ae9c',
    'kev/suite.py': '2a49b69630186c1ef652e4ce52c27c9348a8dec2',
    'kev/train.py': 'f72dcc4f776b23d7195135bec61452ffaab2c465',
    'kev/transfer_v9.py': '923a8d0cbefa139ed72ec483ff3f38c4299da7c0',
}
PYTHON = '3.13'  # Kev's .python-version; pyproject requires >=3.12,<3.14
# Kev's dependency ranges are open; resolve them as of the day this protocol was designed.
EXCLUDE_NEWER = '2026-10-09T00:00:00Z'
# kev.checkpoint.resolve_run's allow_patterns (checkpoint.py:45) plus the license and model card.
ALLOW_PATTERNS = ('*.json', '*.safetensors', '*.pt', '*.txt', '*.jinja', 'LICENSE', 'README.md')
MIN_FREE_BEFORE_BASE = 12 << 30


@dataclass(frozen=True)
class Pin:
    """One Hub snapshot: path -> (bytes, algorithm, digest) from the Hub tree API at `revision`."""
    repo: str
    revision: str
    license: str
    files: dict


ADAPTER = Pin('jaredpalmer/kev-4b', '6cfce5c2fa4b4bd64026336ab649c5ca78857d52', 'apache-2.0', {
    'README.md': (24981, 'git-sha1', '20ab650c92dbec5f963efaa32cb6c2979f4c39cb'),
    'adapter_config.json': (1271, 'git-sha1', 'ea5c33529f3e0e9521f52b42485d44a23d333ba7'),
    'adapter_model.safetensors': (129924032, 'sha256', '90e817356246e7f18bfa7ca3d31794cd4fbeb3332a66a84cb51d9ceae925f2b2'),
    'added_tokens.json': (707, 'git-sha1', 'b54f9135e44c1e81047e8d05cb027af8bc039eed'),
    'head.pt': (5249791, 'sha256', 'dd633435998ecc751ac538717a3742e32149500fabf7d7276287dbf0693f347c'),
    'merges.txt': (1671853, 'git-sha1', '31349551d90c7606f325fe0f11bbb8bd5fa0d7c7'),
    'provenance.json': (4265, 'git-sha1', '5c34f812efe4ad2983c4db3fe3c50f243ed420f1'),
    'result.json': (80553, 'git-sha1', '1a18548b8d47bd0260021fdd7c30237c17f27bb1'),
    'special_tokens_map.json': (616, 'git-sha1', '17305b3603dfb19ccc0f658ec2cd2cd3adff4a58'),
    'tokenizer.json': (19989325, 'sha256', '06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523'),
    'tokenizer_config.json': (1128, 'git-sha1', 'fa833096a2f03c2ff89094eb1ba7716fbd98b102'),
    'training_config.json': (1845, 'git-sha1', '2abc7a274e9ba1cb7290bc51ff20a6df4dbb4886'),
    'training_metrics.json': (324, 'git-sha1', '8f551b88fa7f46a5201d7b0d4d281ffcec6940d8'),
    'vocab.json': (2776833, 'git-sha1', '4783fe10ac3adce15ac8f358ef5462739852c569'),
})
BASE = Pin('Qwen/Qwen3.5-4B-Base', '1001bb4d826a52d1f399e183466143f4da7b741b', 'apache-2.0', {
    'LICENSE': (11343, 'git-sha1', '1d5180a42f1c3383ba7c7bd0a50f0837ef0168df'),
    'README.md': (3722, 'git-sha1', 'f66751fce75ca423e0993107b38d4c5408f677fb'),
    'config.json': (3161, 'git-sha1', '557d961b205319c6a7da5f757f565b69b3967b7d'),
    'merges.txt': (3353259, 'git-sha1', 'a494e019ca1502219fd0128658b979e5f05ae8e8'),
    'model.safetensors-00001-of-00002.safetensors':
        (5329398712, 'sha256', 'df547074dce70532a0493e5433152bd17a65efb89088cfabc2e7e2371a93d712'),
    'model.safetensors-00002-of-00002.safetensors':
        (3990429344, 'sha256', '590fbaac095dd31db886c322d9d2f7df47777966391acf306ddddc3e4e3a15ef'),
    'model.safetensors.index.json': (76196, 'git-sha1', '7586335c0c85f13864338166a651bc2afbf49849'),
    'preprocessor_config.json': (390, 'git-sha1', '2ea84a437d448ff71b08df68fdd949d5cc4ebb64'),
    'tokenizer.json': (12807196, 'sha256', 'fe000e3ed39ed12b8d2481d527d44f93c65d37e87645d2dcc80d1bf9d50d2927'),
    'tokenizer_config.json': (16713, 'git-sha1', 'ae8d254e44c51d0cb0907bcb221f18efca829d3e'),
    'video_preprocessor_config.json': (386, 'git-sha1', '37900b3ff9295e1aa7e211378466356b52e64e55'),
    'vocab.json': (6722759, 'git-sha1', '0aa0ce0658d60ac4a5d609f4eadb0e8e43514176'),
})
# head.pt must point at the pinned base, or kev.checkpoint would resolve another one (checkpoint.py:238,258).
EXPECTED_HEAD = {'base': BASE.repo, 'base_revision': BASE.revision, 'lora': 16, 'option_isolation': False}
HEAD_KEYS = ('base', 'base_revision', 'lora', 'head_dim', 'option_isolation', 'special_embeddings',
             'weights_dtype', 'temperature', 'lora_placement')
PACKAGES = ('mlx', 'mlx-lm', 'mlx-metal', 'torch', 'transformers', 'huggingface-hub', 'tokenizers', 'safetensors',
            'fastapi', 'uvicorn', 'pydantic', 'numpy')

# Run inside .kev/venv with -I. Prints one JSON line on stdout; progress bars go to stderr.
SNAPSHOT_CODE = '''import json, sys
from huggingface_hub import HfApi, snapshot_download
repo, revision, cache, patterns = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
info = HfApi().model_info(repo, revision=revision)
card = info.card_data.to_dict() if info.card_data else {}
path = snapshot_download(repo, revision=revision, cache_dir=cache, allow_patterns=patterns)
print(json.dumps({"path": path, "sha": info.sha, "license": card.get("license"),
                  "license_tags": sorted(t for t in info.tags or [] if t.startswith("license:"))}))
'''
# The same torch.load call kev.checkpoint.read_meta makes (checkpoint.py:76-77).
HEAD_CODE = '''import json, sys, torch
meta = torch.load(sys.argv[1], map_location="cpu", weights_only=True)
print(json.dumps({k: meta.get(k) for k in json.loads(sys.argv[2])}, default=float))
'''


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Layout:
    root: Path = KEV_DIR

    @property
    def archive(self):
        return self.root / 'downloads' / f'{ARCHIVE_TOP}.tar.gz'

    @property
    def source(self):
        return self.root / ARCHIVE_TOP

    @property
    def venv(self):
        return self.root / 'venv'

    @property
    def python(self):
        return self.venv / 'bin' / 'python'

    @property
    def site_package(self):
        return self.venv / 'lib' / f'python{PYTHON}' / 'site-packages' / 'kev'

    @property
    def freeze(self):
        return self.root / 'requirements.freeze.txt'

    @property
    def hf_home(self):
        return self.root / 'hf'

    @property
    def hub(self):
        return self.hf_home / 'hub'

    @property
    def uv_cache(self):
        return self.root / 'uv-cache'

    @property
    def provenance(self):
        return self.root / 'provenance.json'

    def snapshot(self, pin):
        # huggingface_hub's cache layout: models--{org}--{name}/snapshots/{commit}
        return self.hub / ('models--' + pin.repo.replace('/', '--')) / 'snapshots' / pin.revision


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise SetupError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def git_blob_sha1(path):
    digest = hashlib.sha1(b'blob %d\0' % os.path.getsize(path))
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def tree_bytes(path):
    """Bytes on disk under path, symlinks not followed (the Hub cache links snapshots to blobs)."""
    path = Path(path)
    if not path.exists():
        return 0
    return sum(os.lstat(Path(base) / name).st_size for base, _, names in os.walk(path) for name in names)


def require_free_space(path, minimum=MIN_FREE_BEFORE_BASE, usage=shutil.disk_usage):
    free = usage(path).free
    require(free >= minimum, f'{free / 2**30:.1f} GiB free at {path}; the base snapshot '
                             f'({sum(f[0] for f in BASE.files.values()) / 1e9:.2f} GB) needs at least '
                             f'{minimum / 2**30:.0f} GiB free before it is fetched')
    return free


def expected_files(pin, patterns=ALLOW_PATTERNS):
    """The pinned files huggingface_hub's fnmatch filter selects."""
    return {path for path in pin.files if any(fnmatch.fnmatch(path, p) for p in patterns)}


def check_snapshot(directory, pin):
    """Every file the patterns select is present with its pinned size and Hub digest, and nothing else.
    -> [{path, bytes, sha256, pinned}] for provenance."""
    directory = Path(directory)
    require(directory.is_dir(), f'{pin.repo}@{pin.revision} snapshot missing: {directory}')
    present = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}
    wanted = expected_files(pin)
    require(present == wanted, f'{pin.repo} snapshot files differ: missing {sorted(wanted - present)}, '
                               f'unexpected {sorted(present - wanted)}')
    rows = []
    for path in sorted(wanted):
        size, algorithm, pinned = pin.files[path]
        actual_size = (directory / path).stat().st_size
        require(actual_size == size, f'{pin.repo}/{path}: {actual_size} bytes, pinned {size}')
        sha256 = sha256_file(directory / path)
        actual = sha256 if algorithm == 'sha256' else git_blob_sha1(directory / path)
        require(actual == pinned, f'{pin.repo}/{path}: {algorithm} {actual} differs from the pinned {pinned}')
        rows.append({'path': path, 'bytes': size, 'sha256': sha256, 'pinned': f'{algorithm}:{pinned}'})
    return rows


def check_recorded(rows, recorded, name):
    """Freshly hashed rows (check_snapshot) against the rows provenance recorded at setup."""
    new, old = {row['path']: row for row in rows}, {row['path']: row for row in recorded}
    require(set(new) == set(old), f'{name}: files differ from provenance')
    changed = sorted(path for path in new if new[path] != old[path])
    require(not changed, f'{name}: {changed} differ from provenance (size or SHA-256)')


def check_source(directory, prefix=''):
    """Pinned git blob SHA-1s; with a prefix only that subtree is compared and extra .py files are refused."""
    directory = Path(directory)
    wanted = {path[len(prefix):]: blob for path, blob in SOURCE_BLOBS.items() if path.startswith(prefix)}
    if prefix:
        present = {str(p.relative_to(directory)) for p in directory.rglob('*.py') if '__pycache__' not in p.parts}
        require(present == set(wanted), f'{directory}: package files differ from the pinned commit: '
                                        f'missing {sorted(set(wanted) - present)}, extra {sorted(present - set(wanted))}')
    for path, blob in wanted.items():
        require((directory / path).is_file(), f'{directory / path} missing')
        actual = git_blob_sha1(directory / path)
        require(actual == blob, f'{directory / path}: git blob {actual} differs from Kev {KEV_COMMIT[:12]} ({blob})')
    return len(wanted)


def fetch_archive(layout, opener=urllib.request.urlopen, timeout=60):
    if layout.archive.exists():
        return False
    layout.archive.parent.mkdir(parents=True, exist_ok=True)
    partial = layout.archive.with_name(layout.archive.name + '.part')
    total = 0
    with opener(ARCHIVE_URL, timeout=timeout) as response, partial.open('wb') as out:
        while block := response.read(1 << 20):
            total += len(block)
            require(total <= ARCHIVE_MAX_BYTES, f'Kev archive exceeds {ARCHIVE_MAX_BYTES} bytes')
            out.write(block)
    partial.replace(layout.archive)
    return True


def extract_source(layout):
    """Only the pinned files, with tarfile's 'data' filter (no links, devices or paths outside root)."""
    with tarfile.open(layout.archive) as bundle:
        members = [m for m in bundle.getmembers()
                   if m.isfile() and m.name.startswith(ARCHIVE_TOP + '/') and m.name[len(ARCHIVE_TOP) + 1:] in SOURCE_BLOBS]
        found = {m.name[len(ARCHIVE_TOP) + 1:] for m in members}
        require(found == set(SOURCE_BLOBS), f'Kev archive lacks {sorted(set(SOURCE_BLOBS) - found)}')
        bundle.extractall(layout.root, members=members, filter='data')
    return check_source(layout.source)


def clean_env(extra):
    """Inherited environment without uv, Hub, Python-path or virtualenv settings, plus `extra`."""
    drop = ('UV_', 'HF_', 'HUGGING', 'TRANSFORMERS_', 'PYTHON', 'VIRTUAL_ENV', 'CONDA_')
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env.update(extra)
    return env


class Commands:
    """Runs and records every external command: argv, the settings it was given, seconds, exit code."""

    def __init__(self):
        self.log = []

    def __call__(self, argv, env, timeout=3 * 3600, recorded=()):
        argv = [str(a) for a in argv]
        started, at = time.monotonic(), now()
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
        self.log.append({'argv': argv, 'env': {k: env[k] for k in recorded if k in env}, 'started_at': at,
                         'seconds': round(time.monotonic() - started, 1), 'returncode': result.returncode})
        require(result.returncode == 0, f'{" ".join(argv[:4])} failed ({result.returncode}): {result.stderr[-4000:]}')
        return result.stdout


def uv():
    found = shutil.which('uv')
    require(found, 'uv is required (https://docs.astral.sh/uv/)')
    return found


def uv_env(layout):
    return clean_env({'UV_CACHE_DIR': str(layout.uv_cache), 'UV_PYTHON_DOWNLOADS': 'never'})


def hub_env(layout):
    return clean_env({'HF_HOME': str(layout.hf_home), 'HF_HUB_CACHE': str(layout.hub), 'HF_HUB_DISABLE_TELEMETRY': '1'})


def python_version(layout, run):
    version = run([layout.python, '-I', '-c', 'import sys; print(sys.version.split()[0])'], clean_env({})).strip()
    require(version.startswith(PYTHON + '.'), f'.kev/venv runs Python {version}, expected {PYTHON}')
    return version


def freeze_text(layout, run):
    return run([uv(), 'pip', 'freeze', '--python', layout.python], uv_env(layout), recorded=('UV_CACHE_DIR',))


def package_versions(text):
    versions = {}
    for line in text.splitlines():
        name, separator, version = line.partition('==')
        if separator and name.strip().lower() in PACKAGES:
            versions[name.strip().lower()] = version.strip()
    return versions


def read_head(layout, run):
    out = run([layout.python, '-I', '-c', HEAD_CODE, layout.snapshot(ADAPTER) / 'head.pt', json.dumps(HEAD_KEYS)],
              clean_env({}))
    meta = json.loads(out.strip().splitlines()[-1])
    wrong = {k: (meta.get(k), v) for k, v in EXPECTED_HEAD.items() if meta.get(k) != v}
    require(not wrong, f'head.pt does not point at the pinned base (found, expected): {wrong}')
    return meta


def fetch_snapshot(layout, pin, run):
    out = run([layout.python, '-I', '-c', SNAPSHOT_CODE, pin.repo, pin.revision, layout.hub, json.dumps(ALLOW_PATTERNS)],
              hub_env(layout), recorded=('HF_HOME', 'HF_HUB_CACHE', 'HF_HUB_DISABLE_TELEMETRY'))
    found = json.loads(out.strip().splitlines()[-1])
    require(Path(found['path']).resolve() == layout.snapshot(pin).resolve(), f'{pin.repo} landed at {found["path"]}')
    require(found['sha'] == pin.revision, f'{pin.repo}: Hub resolved {found["sha"]}, pinned {pin.revision}')
    require(found['license'] == pin.license, f'{pin.repo}: license {found["license"]!r}, expected {pin.license!r}')
    return {'license': found['license'], 'license_tags': found['license_tags']}


def fetch_base(layout, run, usage=shutil.disk_usage):
    """The disk guard comes first: nothing is fetched with less than MIN_FREE_BEFORE_BASE free. -> (free bytes, licenses)."""
    free = require_free_space(layout.root, usage=usage)
    return free, fetch_snapshot(layout, BASE, run)


def present_rows(layout, pin):
    """check_snapshot's rows when the snapshot is already complete and pinned, else None (fetch it)."""
    try:
        return check_snapshot(layout.snapshot(pin), pin)
    except (SetupError, OSError):
        return None


def load_provenance(layout):
    return json.loads(layout.provenance.read_text()) if layout.provenance.exists() else None


def save_provenance(layout, record):
    temporary = layout.provenance.with_name(layout.provenance.name + '.tmp')
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(layout.provenance)


def pins():
    return {'kev_commit': KEV_COMMIT, 'adapter': f'{ADAPTER.repo}@{ADAPTER.revision}', 'base': f'{BASE.repo}@{BASE.revision}',
            'python': PYTHON, 'exclude_newer': EXCLUDE_NEWER, 'allow_patterns': list(ALLOW_PATTERNS)}


def setup(layout, run=None, opener=urllib.request.urlopen, usage=shutil.disk_usage):
    """Idempotent: every step checks the bytes it would produce before doing anything."""
    run = run or Commands()
    layout.root.mkdir(parents=True, exist_ok=True)
    previous = load_provenance(layout) or {}
    require(not previous or previous.get('pins') == pins(), '.kev/provenance.json was made for other pins; '
                                                           'move .kev aside and set up again')
    record = {'kind': 'kev-mlx-serving-environment', 'pins': pins(), 'created_at': previous.get('created_at') or now(),
              'host': {'platform': platform.platform(), 'machine': platform.machine(), 'setup_python': sys.version.split()[0]}}
    # 1. Source at the pinned commit.
    started, at = time.monotonic(), now()
    if fetch_archive(layout, opener):
        run.log.append({'argv': ['GET', ARCHIVE_URL, '->', str(layout.archive.relative_to(layout.root))], 'env': {},
                        'started_at': at, 'seconds': round(time.monotonic() - started, 1), 'returncode': 0})
    archive_sha256 = sha256_file(layout.archive)
    recorded = (previous.get('kev') or {}).get('archive', {}).get('sha256')
    require(recorded in (None, archive_sha256), f'Kev archive SHA-256 {archive_sha256} differs from the recorded {recorded}')
    try:
        verified = check_source(layout.source)
    except (SetupError, OSError):
        verified = extract_source(layout)
    record['kev'] = {'repository': KEV_REPOSITORY, 'commit': KEV_COMMIT, 'commit_date': KEV_COMMIT_DATE,
                     'license': KEV_LICENSE, 'archive_url': ARCHIVE_URL,
                     'archive': {'path': str(layout.archive.relative_to(layout.root)), 'bytes': layout.archive.stat().st_size,
                                 'sha256': archive_sha256},
                     'source': str(layout.source.relative_to(layout.root)), 'pinned_blobs_verified': verified}
    # 2. Python environment.
    if not layout.python.exists():
        run([uv(), 'venv', '--python', PYTHON, '--no-python-downloads', layout.venv], uv_env(layout), recorded=('UV_CACHE_DIR',))
    version = python_version(layout, run)
    uv_version = run([uv(), '--version'], uv_env(layout)).strip()
    requirement = f'kev[serve] @ {layout.source.resolve().as_uri()}'
    run([uv(), 'pip', 'install', '--python', layout.python, '--exclude-newer', EXCLUDE_NEWER, requirement],
        uv_env(layout), recorded=('UV_CACHE_DIR', 'UV_PYTHON_DOWNLOADS'))
    installed = check_source(layout.site_package, prefix='kev/')
    freeze = freeze_text(layout, run)
    layout.freeze.write_text(freeze)
    record['python'] = {'uv': uv_version, 'requested': PYTHON, 'version': version, 'venv': 'venv'}
    record['install'] = {'requirement': requirement, 'extra': 'serve', 'exclude_newer': EXCLUDE_NEWER,
                         'installed_package_files_verified': installed,
                         'freeze': {'path': layout.freeze.name, 'sha256': sha256_file(layout.freeze),
                                    'lines': len(freeze.splitlines())},
                         'packages': package_versions(freeze)}
    # 3. Adapter, then the base behind the disk guard. HF is used offline afterwards.
    licenses, free_before_base = {}, None
    for name, pin in (('adapter', ADAPTER), ('base', BASE)):
        rows = present_rows(layout, pin)  # each file is hashed once per setup run
        if rows is None:
            if pin is BASE:
                free_before_base, licenses[name] = fetch_base(layout, run, usage)
            else:
                licenses[name] = fetch_snapshot(layout, pin, run)
            rows = check_snapshot(layout.snapshot(pin), pin)
        previous_pin = previous.get(name) or {}
        record[name] = {'repository': pin.repo, 'revision': pin.revision, 'license': pin.license,
                        'hub_license': licenses.get(name) or previous_pin.get('hub_license'),
                        'snapshot': str(layout.snapshot(pin).relative_to(layout.root)), 'files': rows}
        record[name]['bytes'] = sum(row['bytes'] for row in record[name]['files'])
    record['head_meta'] = read_head(layout, run)
    record['disk'] = {'minimum_free_before_base_bytes': MIN_FREE_BEFORE_BASE,
                      'free_before_base_bytes': free_before_base or (previous.get('disk') or {}).get('free_before_base_bytes'),
                      'free_after_bytes': usage(layout.root).free,
                      'bytes': {'archive': layout.archive.stat().st_size, 'source': tree_bytes(layout.source),
                                'venv': tree_bytes(layout.venv), 'hf': tree_bytes(layout.hf_home),
                                'uv_cache': tree_bytes(layout.uv_cache), 'total': tree_bytes(layout.root)}}
    record['commands'] = (previous.get('commands') or []) + run.log
    record['updated_at'] = now()
    save_provenance(layout, record)
    return record


def verify(layout, run=None):
    """Offline: every recorded byte, the pinned digests, the installed package and the dependency freeze."""
    run = run or Commands()
    record = load_provenance(layout)
    require(record is not None, f'{layout.provenance} missing; run python3 eval/throughput/kevmlx_setup.py')
    require(record.get('pins') == pins(), f'{layout.provenance} records other pins than this harness')
    archive = record['kev']['archive']
    require(layout.archive.is_file() and sha256_file(layout.archive) == archive['sha256'],
            f'{layout.archive}: SHA-256 differs from provenance')
    check_source(layout.source)
    check_source(layout.site_package, prefix='kev/')
    require(record['python']['version'] == python_version(layout, run), '.kev/venv Python version changed')
    freeze = freeze_text(layout, run)
    require(sha256_file(layout.freeze) == record['install']['freeze']['sha256'] and layout.freeze.read_text() == freeze,
            'installed packages differ from the recorded dependency freeze')
    for name, pin in (('adapter', ADAPTER), ('base', BASE)):
        check_recorded(check_snapshot(layout.snapshot(pin), pin), record[name]['files'], name)
    require(read_head(layout, run) == record['head_meta'], 'head.pt metadata differs from provenance')
    return dict(record, verified_at=now(), provenance_sha256=sha256_file(layout.provenance))


def operation_lock(path=OPERATION_LOCK):
    path.parent.mkdir(exist_ok=True)
    handle = path.open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SetupError(f'a Metal build or measurement holds {path}; run this afterwards') from None
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verify', action='store_true', help='Re-check every recorded byte offline; no network')
    args = parser.parse_args(argv)
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('the Kev MLX runtime targets native Apple Silicon')
    layout = Layout()
    try:
        with operation_lock():
            if args.verify:
                record = verify(layout)
                print(f'kevmlx setup --verify OK: Kev {KEV_COMMIT[:12]}, {ADAPTER.repo}@{ADAPTER.revision[:12]} '
                      f'({len(record["adapter"]["files"])} files), {BASE.repo}@{BASE.revision[:12]} '
                      f'({len(record["base"]["files"])} files), Python {record["python"]["version"]}, '
                      f'freeze sha256 {record["install"]["freeze"]["sha256"][:12]}')
                return 0
            record = setup(layout)
            sizes = record['disk']['bytes']
            print(f'kevmlx setup complete: {layout.provenance}\n'
                  f'  adapter {record["adapter"]["bytes"]:,} bytes, base {record["base"]["bytes"]:,} bytes, '
                  f'.kev total {sizes["total"]:,} bytes; {record["disk"]["free_after_bytes"] / 2**30:.1f} GiB free')
            return 0
    except (SetupError, OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        print(f'kevmlx setup FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
