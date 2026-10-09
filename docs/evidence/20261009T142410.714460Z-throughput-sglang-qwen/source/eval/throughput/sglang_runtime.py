"""Owned SGLang MLX server lifecycle for one throughput cell (native Metal through MLX).

The launch is the 2026-10-05 cache-disabled probe's command, flag for flag
(docs/evidence/20261005T154252Z-sglang-metal-cache-disabled/probe.json), with
four changes: --max-running-requests N, a token pool of max(8192, 4096 x N) so each
running request can hold a full 4096-token context (as llama.cpp's -c 4096*N gives
each slot), this runner's port and served model name, and, in the two re-probe
variants only, one degraded flag dropped. Before any
launch the venv's SGLang source and the model files are checked against the
probe's provenance.json; a mismatch refuses the run.

SGLang serves no /metrics here (the probe did not pass --enable-metrics), so the
scheduler's own "Prefill batch" log lines are the per-cell token and batching record.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import tarfile
import time
import urllib.request

import fixtures
import compare_scoring
import metal

ROOT = fixtures.ROOT
require = fixtures.require
SGLANG = ROOT / '.sglang'
VENV = SGLANG / 'venv'
VENV_PYTHON = VENV / 'bin/python'
SITE_PACKAGES = VENV / 'lib/python3.12/site-packages'
MODEL_DIR = SGLANG / 'models/Qwen3.5-4B-4bit'
SOURCE_ARCHIVE = SGLANG / 'source-efb62ce.tar.gz'
EVIDENCE = ROOT / 'docs/evidence/20261005T154252Z-sglang-metal-cache-disabled'
PROVENANCE = EVIDENCE / 'provenance.json'
# Digest listed for provenance.json in the evidence directory's sha256.json (test_sglang_runtime checks they agree).
PROVENANCE_SHA256 = '748c3faf8e5f772f1e3e18140d0dae842222d57eb639eb2fec15a45c1cc2ff5b'
DEPENDENCY_FREEZE = EVIDENCE / 'dependencies.txt'
PROBE_RECORD = EVIDENCE / 'probe.json'
SOURCE_REVISION = 'efb62ce269b499123e2d1c89005ee4cea8c31098'
QUANTIZATION = 'mlx affine 4-bit, group 64'
# Outside 18086-18088, 8084, 8085 (llama.cpp and guard runs) and 30101 (the probe).
PORT = 30111
BASE = f'http://127.0.0.1:{PORT}'
SERVED_MODEL = 'qwen35-4b-mlx'
CONTEXT_LENGTH = 4096
# The probe's pool; unchanged at one running request.
PROBE_POOL_TOKENS = 8192
# SGLang's default as the probe's server reported it; not passed, but verified at startup.
CHUNKED_PREFILL_SIZE = 4096
READY_SECONDS = 150
TERM_WAIT_SECONDS = 15
KILL_WAIT_SECONDS = 5
MAX_INFO_BYTES = 1 << 20
# Re-probe variants: each drops exactly one flag the probe needed.
VARIANT_DROPS = {'': None, 'overlap': '--disable-overlap-schedule', 'radix': '--disable-radix-cache'}
# probe.json launch_environment, exactly; inherited SGLANG_* variables are stripped.
ENVIRONMENT = {'SGLANG_USE_MLX': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
               'HF_HOME': str(SGLANG / 'hf'), 'XDG_CACHE_HOME': str(SGLANG / 'cache'),
               'SGLANG_MLX_CACHE_LIMIT_GB': '1'}
MLX_RUNNER = 'Initializing MlxModelRunner for end-to-end MLX inference'
STARTUP = {
    'max_total_num_tokens': r'max_total_num_tokens=(\d+), chunked_prefill_size=',
    'chunked_prefill_size': r'chunked_prefill_size=(\d+), max_prefill_tokens=',
    'max_prefill_tokens': r'max_prefill_tokens=(\d+), max_running_requests=',
    'max_running_requests': r'max_prefill_tokens=\d+, max_running_requests=(\d+), context_len=',
    'context_len': r'max_running_requests=\d+, context_len=(\d+)',
    'available_gpu_mem_gb': r'context_len=\d+, available_gpu_mem=([\d.]+) GB',
    'mlx_pool_max_total_num_tokens': r'MLX stub: initialized minimal pools \(max_total_num_tokens=(\d+)',
    'mlx_pool_max_running_requests': r'MLX stub: initialized minimal pools \(max_total_num_tokens=\d+, max_running_requests=(\d+)',
    'mlx_model_load_s': r'MLX model loaded in ([\d.]+)s',
    'mlx_buffer_cache_limit_gb': r'MLX buffer cache limit set to ([\d.]+) GB',
    'wired_memory_limit_gb': r'Wired memory limit set to ([\d.]+) GB',
    'tree_cache': r'Tree cache initialized: (.*)',
}
PREFILL = re.compile(r'Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+),'
                     r'.*?#running-req: (\d+), #queue-req: (\d+)')
RECORDED_ARGS = ('model_path', 'served_model_name', 'host', 'port', 'context_length', 'max_total_tokens',
                 'max_running_requests', 'mem_fraction_static', 'grammar_backend', 'mamba_radix_cache_strategy',
                 'disable_overlap_schedule', 'disable_radix_cache', 'disable_cuda_graph', 'mlx_enable_sampling',
                 'chunked_prefill_size', 'max_prefill_tokens', 'schedule_policy', 'enable_mixed_chunk', 'page_size',
                 'random_seed', 'enable_deterministic_inference', 'enable_metrics', 'enable_cache_report',
                 'decode_log_interval', 'log_level', 'version')


def relative(path):
    """Repository-relative where possible; an --output directory may live elsewhere."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def pool_tokens(running):
    """--max-total-tokens: 4096 tokens per running request, never below the probe's 8192."""
    return max(PROBE_POOL_TOKENS, CONTEXT_LENGTH * running)


def command(running, variant='', port=PORT, python=VENV_PYTHON, model=MODEL_DIR):
    """probe.json launch_arguments in their order; only the port, served name, N and the pool differ."""
    launch = [str(python), '-m', 'sglang.launch_server', '--model-path', str(model),
              '--served-model-name', SERVED_MODEL, '--host', '127.0.0.1', '--port', str(port),
              '--disable-cuda-graph', '--mlx-enable-sampling', '--context-length', str(CONTEXT_LENGTH),
              '--max-total-tokens', str(pool_tokens(running)), '--max-running-requests', str(running),
              '--mem-fraction-static', '0.5', '--grammar-backend', 'llguidance',
              '--mamba-radix-cache-strategy', 'no_buffer', '--disable-overlap-schedule', '--disable-radix-cache']
    drop = VARIANT_DROPS[variant]
    return [argument for argument in launch if argument != drop]


def environment(inherited=None):
    inherited = os.environ if inherited is None else inherited
    return {**{k: v for k, v in inherited.items() if not k.startswith('SGLANG_')}, **ENVIRONMENT}


# --- Provenance: refuse before launch on any mismatch ----------------------------------------

def load_provenance(path=PROVENANCE, expected=PROVENANCE_SHA256):
    raw = Path(path).read_bytes()
    require(fixtures.digest(raw) == expected, 'SGLang probe provenance changed: ' + str(path))
    data = json.loads(raw)
    require(data['source_revision'] == SOURCE_REVISION, 'probe provenance names another SGLang revision')
    require(data['model_quantization'] == {'group_size': 64, 'bits': 4, 'mode': 'affine'},
            'probe provenance names another quantization')
    return data


def verify_source(provenance, sglang_dir=SGLANG, site_packages=SITE_PACKAGES, archive=SOURCE_ARCHIVE):
    """The venv imports sglang from the extracted pinned tree, and that tree equals the archive."""
    revision = provenance['source_revision']
    tree = Path(sglang_dir) / f'sglang-{revision}'
    dist_info = sorted(Path(site_packages).glob('sglang-*.dist-info'))
    require(len(dist_info) == 1, f'expected one sglang installation in {site_packages}, found {len(dist_info)}')
    url = json.loads((dist_info[0] / 'direct_url.json').read_text())
    require(url == {'url': (tree / 'python').as_uri(), 'dir_info': {'editable': True}},
            f'venv sglang is not the editable pinned tree {tree.name}: {url}')
    finders = sorted(Path(site_packages).glob('__editable___sglang_*_finder.py'))
    require(len(finders) == 1, 'expected one sglang editable finder')
    mapping = re.search(r"MAPPING: dict\[str, str\] = \{'sglang': '([^']+)'\}", finders[0].read_text())
    require(mapping is not None and mapping[1] == str(tree / 'python/sglang'),
            'venv sglang import mapping does not point at the pinned tree')
    require(metal.sha256(Path(archive)) == provenance['source_tar_sha256'], 'SGLang source archive checksum mismatch')
    verified = set()
    with tarfile.open(archive) as bundle:
        for member in bundle:
            top, _, name = member.name.partition('/')
            # Only the importable package; python/pyproject.toml was replaced as the Apple guide instructs.
            if top != tree.name or not member.isfile() or not name.startswith('python/sglang/'):
                continue
            on_disk = tree / name
            require(on_disk.is_file() and on_disk.read_bytes() == bundle.extractfile(member).read(),
                    'SGLang source differs from the pinned archive: ' + name)
            verified.add(name)
    require(verified, 'no python/sglang files in the source archive')
    # Recorded, not refused: an install step may have written files the archive lacks.
    extra = sorted(str(p.relative_to(tree)) for p in (tree / 'python/sglang').rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts and str(p.relative_to(tree)) not in verified)
    return {'source_repository': provenance['source_repository'], 'source_revision': revision,
            'source_tar_sha256': provenance['source_tar_sha256'], 'source_archive': relative(archive),
            'source_files_verified': len(verified), 'source_files_not_in_archive': extra,
            'editable_install': url['url']}


def verify_model(provenance, model_dir=MODEL_DIR):
    """Every recorded file present with its byte count and SHA-256; no unlisted top-level file."""
    model_dir = Path(model_dir)
    listed = {entry['name'] for entry in provenance['files']}
    for entry in provenance['files']:
        path = model_dir / entry['name']
        require(path.is_file(), 'pinned model file missing: ' + entry['name'])
        require(path.stat().st_size == entry['bytes'], 'pinned model file size mismatch: ' + entry['name'])
        require(metal.sha256(path) == entry['sha256'], 'pinned model file checksum mismatch: ' + entry['name'])
    # The download's .cache/ directory holds hub metadata, not weights.
    unlisted = sorted(p.name for p in model_dir.iterdir() if p.is_file() and p.name not in listed)
    require(not unlisted, f'unlisted files in the pinned model directory: {unlisted}')
    return {'repository': provenance['model_repository'], 'revision': provenance['model_revision'],
            'quantization': QUANTIZATION, 'quantization_config': provenance['model_quantization'],
            'path': relative(model_dir), 'files': provenance['files'],
            'provenance_limitation': provenance['model_provenance_limitation']}


def verify_runtime(provenance_path=PROVENANCE, sglang_dir=SGLANG, site_packages=SITE_PACKAGES,
                   archive=SOURCE_ARCHIVE, model_dir=MODEL_DIR, freeze=DEPENDENCY_FREEZE):
    """Source revision, model bytes and the recorded environment, checked before any launch."""
    provenance = load_provenance(provenance_path)
    python = re.search(r'version_info = (\S+)', (Path(sglang_dir) / 'venv/pyvenv.cfg').read_text())
    expected_python = re.search(r'(\d+\.\d+\.\d+)', provenance['python'])[1]
    require(python is not None and python[1] == expected_python, 'venv Python differs from the probe')
    return {'runtime': 'sglang-mlx', **verify_source(provenance, sglang_dir, site_packages, archive),
            'python': provenance['python'], 'source_configuration': provenance['source_configuration'],
            'dependency_freeze': {'path': relative(freeze), 'sha256': metal.sha256(Path(freeze)),
                                  'compared_with_venv': False},
            'model': verify_model(provenance, model_dir),
            'probe_evidence': {'provenance': relative(provenance_path), 'sha256': PROVENANCE_SHA256,
                               'launch_record': relative(PROBE_RECORD)}}


# --- Startup layout and log-derived counters ------------------------------------------------

def parse_startup(log):
    found = {'mlx_runner': MLX_RUNNER in log}
    for key, pattern in STARTUP.items():
        match = re.search(pattern, log)
        if match:
            value = match[1]
            found[key] = int(value) if value.isdigit() else float(value) if re.fullmatch(r'[\d.]+', value) else value
    if 'context_len' in found:
        # Per-request context limit; run.run_cell records it as n_ctx_per_slot_observed.
        found['n_ctx_slot'] = found['context_len']
    return found


def check_startup(found, cell):
    """The scheduler must report the layout the cell claims to measure, on the MLX runner."""
    expected = {'mlx_runner': True, 'max_running_requests': cell.slots, 'context_len': CONTEXT_LENGTH,
                'max_total_num_tokens': pool_tokens(cell.slots)}
    wrong = {key: (found.get(key), value) for key, value in expected.items() if found.get(key) != value}
    if wrong:
        raise RuntimeError(f'SGLang scheduler layout differs from the cell (observed, expected): {wrong}')


def check_server_args(info, cell, port=PORT):
    """Server-reported arguments, so a flag SGLang silently overrides cannot pass as a variant."""
    expected = {'served_model_name': SERVED_MODEL, 'port': port, 'max_running_requests': cell.slots,
                'context_length': CONTEXT_LENGTH, 'max_total_tokens': pool_tokens(cell.slots),
                'chunked_prefill_size': CHUNKED_PREFILL_SIZE,
                'mamba_radix_cache_strategy': 'no_buffer', 'mlx_enable_sampling': True, 'grammar_backend': 'llguidance',
                'disable_overlap_schedule': cell.variant != 'overlap', 'disable_radix_cache': cell.variant != 'radix'}
    wrong = {key: (info.get(key), value) for key, value in expected.items() if info.get(key) != value}
    if wrong:
        raise RuntimeError(f'SGLang server arguments differ from the cell (observed, expected): {wrong}')


def log_counters(data):
    """Counters over complete 'Prefill batch' lines of a log byte range.

    #new-token is prompt tokens computed and #cached-token prefix tokens reused, the
    same split as llama.cpp's prompt_tokens_total / prompt_tokens_cached_total."""
    text = data.decode('utf-8', errors='replace') if isinstance(data, bytes) else data
    counters = {'prompt_tokens_total': 0, 'prompt_tokens_cached_total': 0, 'prefill_batches_total': 0,
                'prefill_sequences_total': 0, 'max_sequences_in_one_prefill_batch': 0, 'max_queue_requests': 0}
    for line in text.split('\n'):
        match = PREFILL.search(line)
        if match:
            seqs, new, cached, _, queued = (int(value) for value in match.groups())
            counters['prompt_tokens_total'] += new
            counters['prompt_tokens_cached_total'] += cached
            counters['prefill_batches_total'] += 1
            counters['prefill_sequences_total'] += seqs
            counters['max_sequences_in_one_prefill_batch'] = max(counters['max_sequences_in_one_prefill_batch'], seqs)
            counters['max_queue_requests'] = max(counters['max_queue_requests'], queued)
    return counters


def window(log_bytes, before, after):
    """Prefill counters between two metrics() snapshots, with sequences per prefill batch."""
    if not (before and after and 'log_bytes' in before and 'log_bytes' in after):
        return None
    counters = log_counters(log_bytes[before['log_bytes']:after['log_bytes']])
    batches = counters['prefill_batches_total']
    counters['sequences_per_prefill_batch'] = counters['prefill_sequences_total'] / batches if batches else None
    counters['source'] = 'runtime.log "Prefill batch" lines (SGLang serves no /metrics without --enable-metrics)'
    return counters


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Runtime:
    """One owned SGLang server process group. The caller must call stop(), which never raises."""

    def __init__(self, cell, cell_dir, port=PORT, launch=None, env=None):
        self.cell, self.cell_dir, self.port = cell, Path(cell_dir), port
        self.base = f'http://127.0.0.1:{port}'
        self.child = None
        self.command = launch or command(cell.slots, cell.variant, port)
        self.environment = environment() if env is None else env
        self.info = {'command': self.command, 'endpoint_base': self.base, 'variant': cell.variant,
                     'dropped_flag': VARIANT_DROPS[cell.variant],
                     'environment': {key: self.environment.get(key) for key in ENVIRONMENT},
                     'stripped_environment': sorted(k for k in os.environ if k.startswith('SGLANG_')),
                     'guard': 'not applicable: clients call the SGLang server directly', 'ready': False}
        self.started = time.monotonic()
        self.usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read(MAX_INFO_BYTES))

    def start(self, deadline):
        require(not compare_scoring.port_is_listening(self.port), f'port {self.port} already has a listener')
        log_path = self.cell_dir / 'runtime.log'
        with log_path.open('x') as log:
            # New session, as the probe did: shutdown signals the whole owned process group.
            self.child = subprocess.Popen(self.command, env=self.environment, cwd=ROOT, stdout=log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
        self.info['pid'] = self.child.pid
        metal.ready(self.base + '/health', [self.child], timeout=max(1, min(READY_SECONDS, deadline - time.monotonic())))
        self.info.update(ready=True, startup_seconds=time.monotonic() - self.started)
        server_info = self.get('/get_server_info')
        models = self.get('/v1/models')
        for name, value in (('server-info.json', server_info), ('models.json', models)):
            (self.cell_dir / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
        self.info['server_args'] = {key: server_info.get(key) for key in RECORDED_ARGS}
        self.info['models'] = models
        self.info['startup'] = parse_startup(log_path.read_text(errors='replace'))
        check_startup(self.info['startup'], self.cell)
        check_server_args(server_info, self.cell, self.port)
        return self

    def metrics(self):
        """Cumulative prefill counters from complete log lines, and the byte offset they cover."""
        try:
            data = (self.cell_dir / 'runtime.log').read_bytes()
        except OSError as exc:
            return {'error': f'{type(exc).__name__}: {exc}'}
        end = data.rfind(b'\n') + 1
        return {**log_counters(data[:end]), 'log_bytes': end}

    def stop(self):
        errors = []
        if self.child is not None:
            sigkill = False
            try:
                try:
                    os.killpg(self.child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.child.wait(timeout=TERM_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    sigkill = True
                    try:
                        os.killpg(self.child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.child.wait(timeout=KILL_WAIT_SECONDS)
                # The server's SIGQUIT handler kills its own tree, itself included (probe
                # shutdown-analysis.json); make sure no scheduler or detokenizer outlived it.
                settle = time.monotonic() + KILL_WAIT_SECONDS
                while group_alive(self.child.pid) and time.monotonic() < settle:
                    if not sigkill:
                        sigkill = True
                        try:
                            os.killpg(self.child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            break
                    time.sleep(0.1)
                if group_alive(self.child.pid):
                    errors.append('processes remain in the owned process group')
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f'stop: {type(exc).__name__}: {exc}')
            self.info.update(exit_code=self.child.poll(), sigkill_sent=sigkill)
            if self.child.poll() is None:
                errors.append('owned runtime is still running')
        try:
            if compare_scoring.port_is_listening(self.port):
                errors.append(f'port {self.port} still accepts connections')
        except OSError as exc:
            errors.append(f'port closure unproved: {exc}')
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.info.update(
            cleanup_errors=errors, wall_seconds=time.monotonic() - self.started,
            child_cpu_seconds=(after.ru_utime + after.ru_stime) - (self.usage_before.ru_utime + self.usage_before.ru_stime),
            # Reaped children only; SGLang's scheduler, which holds the MLX weights, is the
            # server's child and is killed by the server itself, so this likely misses it.
            largest_child_peak_rss_bytes_so_far=after.ru_maxrss)
        return self.info
