import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import fixtures
import sglang_runtime
from run_sglang import Cell

PROBE = json.loads(sglang_runtime.PROBE_RECORD.read_text())
PROBE_LOG = (sglang_runtime.EVIDENCE / 'server.log').read_text()
PROBE_INFO = PROBE['requests'][0]['response']
# The probe ran from the same checkout layout; its root is the prefix of its interpreter path.
PROBE_ROOT = PROBE['launch_arguments'][0].removesuffix('/.sglang/venv/bin/python')


def rooted(values, root):
    return [value.replace(root, '<root>') for value in values]


class PinTests(unittest.TestCase):
    def test_provenance_pin_matches_the_evidence_checksum_list(self):
        listed = json.loads((sglang_runtime.EVIDENCE / 'sha256.json').read_text())
        self.assertEqual(listed['provenance.json'], sglang_runtime.PROVENANCE_SHA256)
        provenance = sglang_runtime.load_provenance()
        self.assertEqual(provenance['source_revision'], sglang_runtime.SOURCE_REVISION)
        self.assertEqual(provenance['model_revision'], '0e7ffd5c629ef7719d4cbc04069232580bfa9d9c')

    def test_changed_provenance_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / 'provenance.json'
            changed.write_bytes(sglang_runtime.PROVENANCE.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'provenance changed'):
                sglang_runtime.load_provenance(changed)


class CommandTests(unittest.TestCase):
    def test_one_running_request_is_the_probe_launch_except_port_and_served_name(self):
        theirs = rooted(PROBE['launch_arguments'], PROBE_ROOT)
        ours = rooted(sglang_runtime.command(1), str(fixtures.ROOT))
        self.assertEqual(len(ours), len(theirs))
        changed = [i for i, (a, b) in enumerate(zip(theirs, ours)) if a != b]
        self.assertEqual([theirs[i - 1] for i in changed], ['--served-model-name', '--port'])
        self.assertEqual(ours[ours.index('--port') + 1], str(sglang_runtime.PORT))
        self.assertNotIn(sglang_runtime.PORT, (18086, 18087, 18088, 8084, 8085, 30101))
        for flag in ('--disable-cuda-graph', '--mlx-enable-sampling', '--disable-overlap-schedule', '--disable-radix-cache'):
            self.assertIn(flag, ours)
        self.assertEqual(ours[ours.index('--mamba-radix-cache-strategy') + 1], 'no_buffer')
        self.assertEqual((ours[ours.index('--context-length') + 1], ours[ours.index('--max-total-tokens') + 1]), ('4096', '8192'))

    def test_pool_holds_4096_tokens_per_running_request_and_never_less_than_the_probe(self):
        self.assertEqual({n: sglang_runtime.pool_tokens(n) for n in (1, 2, 4, 8)}, {1: 8192, 2: 8192, 4: 16384, 8: 32768})

    def test_only_running_requests_and_the_pool_change_across_cells(self):
        one = sglang_runtime.command(1)
        for running, pool in ((4, '16384'), (8, '32768')):
            other = sglang_runtime.command(running)
            changed = [i for i, (a, b) in enumerate(zip(one, other)) if a != b]
            self.assertEqual(changed, [one.index('--max-total-tokens') + 1, one.index('--max-running-requests') + 1])
            self.assertEqual([other[i] for i in changed], [pool, str(running)])
            self.assertEqual(other[other.index('--context-length') + 1], '4096')
            self.assertNotIn('--chunked-prefill-size', other)

    def test_each_variant_drops_exactly_its_flag(self):
        base = sglang_runtime.command(4)
        for variant, flag in (('overlap', '--disable-overlap-schedule'), ('radix', '--disable-radix-cache')):
            dropped = sglang_runtime.command(4, variant)
            self.assertEqual(dropped, [argument for argument in base if argument != flag])
            self.assertEqual(len(dropped), len(base) - 1)

    def test_environment_is_the_probe_environment_without_inherited_sglang_settings(self):
        env = sglang_runtime.environment({'PATH': '/bin', 'SGLANG_ENABLE_TORCH_COMPILE': '1', 'SGLANG_USE_MLX': '0'})
        theirs = {k: v.replace(PROBE_ROOT, '<root>') for k, v in PROBE['launch_environment'].items()}
        ours = {k: env[k].replace(str(fixtures.ROOT), '<root>') for k in theirs}
        self.assertEqual(ours, theirs)
        self.assertEqual(env['PATH'], '/bin')
        self.assertNotIn('SGLANG_ENABLE_TORCH_COMPILE', env)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class ModelChecksumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = Path(self.temp.name) / 'model'
        files = {'config.json': b'{"quantization": {"bits": 4}}', 'model.safetensors': b'\x00weights\x01'}
        self.provenance = {'model_repository': 'example/model', 'model_revision': 'abc', 'model_quantization': {},
                           'model_provenance_limitation': 'test',
                           'files': [{'name': n, 'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()} for n, d in files.items()]}
        for name, data in files.items():
            write(self.model / name, data)
        write(self.model / '.cache/huggingface/download/meta', b'hub metadata')

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_files_pass(self):
        record = sglang_runtime.verify_model(self.provenance, self.model)
        self.assertEqual(record['quantization'], 'mlx affine 4-bit, group 64')

    def test_changed_bytes_of_the_same_size_are_refused(self):
        write(self.model / 'model.safetensors', b'\x00weightz\x01')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch: model.safetensors'):
            sglang_runtime.verify_model(self.provenance, self.model)

    def test_missing_resized_or_unlisted_files_are_refused(self):
        write(self.model / 'extra.safetensors', b'x')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            sglang_runtime.verify_model(self.provenance, self.model)
        (self.model / 'extra.safetensors').unlink()
        write(self.model / 'config.json', b'{}')
        with self.assertRaisesRegex(ValueError, 'size mismatch: config.json'):
            sglang_runtime.verify_model(self.provenance, self.model)
        (self.model / 'config.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing: config.json'):
            sglang_runtime.verify_model(self.provenance, self.model)


class SourceRevisionTests(unittest.TestCase):
    REVISION = 'f' * 40

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.sglang, self.site = root / '.sglang', root / 'site-packages'
        self.tree = self.sglang / f'sglang-{self.REVISION}'
        files = {'python/sglang/__init__.py': b'version = "dev"\n', 'python/sglang/srt/server.py': b'serve()\n',
                 'python/pyproject.toml': b'[project]\n', 'README.md': b'readme\n'}
        self.archive = root / 'source.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as bundle:
            for name, data in files.items():
                info = tarfile.TarInfo(f'{self.tree.name}/{name}')
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
        for name, data in files.items():
            write(self.tree / name, data)
        # The Apple guide replaces pyproject.toml; only python/sglang/ must equal the archive.
        write(self.tree / 'python/pyproject.toml', b'[project]\nname = "sglang"\n')
        write(self.tree / 'python/sglang/__pycache__/server.cpython-312.pyc', b'compiled')
        self.dist = self.site / 'sglang-0.0.0.dev0.dist-info'
        self.point_at(self.tree)
        self.provenance = {'source_repository': 'https://github.com/sgl-project/sglang', 'source_revision': self.REVISION,
                           'source_tar_sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}

    def point_at(self, tree):
        write(self.dist / 'direct_url.json', json.dumps({'url': (tree / 'python').as_uri(), 'dir_info': {'editable': True}}).encode())
        write(self.site / '__editable___sglang_0_0_0_dev0_finder.py',
              f"MAPPING: dict[str, str] = {{'sglang': '{tree / 'python/sglang'}'}}\n".encode())

    def tearDown(self):
        self.temp.cleanup()

    def verify(self):
        return sglang_runtime.verify_source(self.provenance, self.sglang, self.site, self.archive)

    def test_pinned_tree_passes(self):
        record = self.verify()
        self.assertEqual((record['source_revision'], record['source_files_verified']), (self.REVISION, 2))

    def test_changed_package_file_is_refused(self):
        write(self.tree / 'python/sglang/srt/server.py', b'serve(patched=True)\n')
        with self.assertRaisesRegex(ValueError, 'differs from the pinned archive: python/sglang/srt/server.py'):
            self.verify()

    def test_venv_installed_from_another_revision_is_refused(self):
        other = self.sglang / f'sglang-{"0" * 40}'
        self.point_at(other)
        with self.assertRaisesRegex(ValueError, 'not the editable pinned tree'):
            self.verify()

    def test_archive_checksum_mismatch_is_refused(self):
        self.provenance['source_tar_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'archive checksum mismatch'):
            self.verify()


class StartupTests(unittest.TestCase):
    def test_probe_log_reports_one_running_request_on_the_mlx_runner(self):
        found = sglang_runtime.parse_startup(PROBE_LOG)
        self.assertEqual({k: found[k] for k in ('mlx_runner', 'max_running_requests', 'context_len', 'max_total_num_tokens',
                                                'mlx_pool_max_running_requests', 'n_ctx_slot')},
                         {'mlx_runner': True, 'max_running_requests': 1, 'context_len': 4096, 'max_total_num_tokens': 8192,
                          'mlx_pool_max_running_requests': 1, 'n_ctx_slot': 4096})
        self.assertEqual(found['mlx_model_load_s'], 1.93)
        sglang_runtime.check_startup(found, Cell('w1', 'qwen_decisions', 1, 1))
        with self.assertRaisesRegex(RuntimeError, 'max_running_requests'):
            sglang_runtime.check_startup(found, Cell('w1', 'qwen_decisions', 4, 4))
        with self.assertRaisesRegex(RuntimeError, 'mlx_runner'):
            sglang_runtime.check_startup(dict(found, mlx_runner=False), Cell('w1', 'qwen_decisions', 1, 1))

    def test_server_arguments_must_show_the_variant_flag_dropped(self):
        info = dict(PROBE_INFO, served_model_name=sglang_runtime.SERVED_MODEL)
        sglang_runtime.check_server_args(info, Cell('w1', 'qwen_json', 1, 1), port=30101)
        four = dict(info, max_running_requests=4, max_total_tokens=16384)
        sglang_runtime.check_server_args(four, Cell('w1', 'qwen_json', 4, 4), port=30101)
        with self.assertRaisesRegex(RuntimeError, r"\{'disable_radix_cache': \(True, False\)\}$"):
            sglang_runtime.check_server_args(four, Cell('w1', 'qwen_json', 4, 4, 'radix'), port=30101)
        # The probe's fixed pool is refused for a 4-request cell, as is a changed chunked-prefill size.
        with self.assertRaisesRegex(RuntimeError, 'max_total_tokens.: .8192, 16384'):
            sglang_runtime.check_server_args(dict(info, max_running_requests=4), Cell('w1', 'qwen_json', 4, 4), port=30101)
        with self.assertRaisesRegex(RuntimeError, 'chunked_prefill_size'):
            sglang_runtime.check_server_args(dict(four, chunked_prefill_size=8192), Cell('w1', 'qwen_json', 4, 4), port=30101)
        with self.assertRaisesRegex(RuntimeError, 'served_model_name'):
            sglang_runtime.check_server_args(PROBE_INFO, Cell('w1', 'qwen_json', 1, 1), port=30101)

    def test_prefill_log_lines_reproduce_the_probe_token_total(self):
        counters = sglang_runtime.log_counters(PROBE_LOG)
        self.assertEqual(counters, {'prompt_tokens_total': 641, 'prompt_tokens_cached_total': 0, 'prefill_batches_total': 14,
                                    'prefill_sequences_total': 14, 'max_sequences_in_one_prefill_batch': 1,
                                    'max_queue_requests': 2})
        # Two startup prefills (6 + 1 tokens) precede the probe's eight calls, which summary.json totals at 634.
        summary = json.loads((sglang_runtime.EVIDENCE / 'summary.json').read_text())
        self.assertEqual(counters['prompt_tokens_total'] - 7, summary['token_usage']['total_prompt_tokens'])

    def test_window_counts_only_lines_between_snapshots(self):
        data = PROBE_LOG.encode()
        start = data.index(b'POST /v1/score')
        end = data.index(b'SIGTERM received')
        before, after = {'log_bytes': data.rfind(b'\n', 0, start) + 1}, {'log_bytes': data.rfind(b'\n', 0, end) + 1}
        counters = sglang_runtime.window(data, before, after)
        # From the first score response: one more score, two decisions bundles of three prefills, two replays.
        self.assertEqual((counters['prefill_batches_total'], counters['sequences_per_prefill_batch']), (9, 1.0))
        self.assertIsNone(sglang_runtime.window(data, None, after))


STUB_SERVER = textwrap.dedent('''
    import json, os, signal, subprocess, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port, mode = int(sys.argv[1]), sys.argv[2]
    print('Initializing MlxModelRunner for end-to-end MLX inference', flush=True)
    print('max_total_num_tokens=8192, chunked_prefill_size=4096, max_prefill_tokens=16384, '
          'max_running_requests=1, context_len=4096, available_gpu_mem=6.31 GB', flush=True)
    if mode == 'ignore-term':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode == 'orphan':
        # A scheduler-like child in the same process group that outlives the server.
        subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
    info = {'served_model_name': 'qwen35-4b-mlx', 'port': port, 'max_running_requests': 1, 'context_length': 4096,
            'max_total_tokens': 8192, 'mamba_radix_cache_strategy': 'no_buffer', 'mlx_enable_sampling': True,
            'grammar_backend': 'llguidance', 'disable_overlap_schedule': True, 'disable_radix_cache': True,
            'chunked_prefill_size': 4096}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'/health': {}, '/get_server_info': info,
                               '/v1/models': {'object': 'list', 'data': [{'id': 'qwen35-4b-mlx'}]}}[self.path]).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
''')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LifecycleTests(unittest.TestCase):
    """An owned stub process stands in for SGLang: readiness, records, bounded shutdown, port closure."""

    def launch(self, mode):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        script = write(Path(temp.name) / 'stub' / 'server.py', STUB_SERVER.encode())
        cell_dir = Path(temp.name) / 'cell'
        cell_dir.mkdir()
        port = free_port()
        runtime = sglang_runtime.Runtime(Cell('w1', 'qwen_json', 1, 1), cell_dir, port=port,
                                         launch=[sys.executable, str(script), str(port), mode], env=dict(os.environ))
        return runtime, cell_dir

    def test_ready_runtime_records_server_info_and_stops_cleanly(self):
        runtime, cell_dir = self.launch('plain')
        try:
            runtime.start(time.monotonic() + 30)
            self.assertTrue(runtime.info['ready'])
            self.assertEqual(json.loads((cell_dir / 'models.json').read_text())['data'][0]['id'], 'qwen35-4b-mlx')
            self.assertEqual(runtime.info['server_args']['disable_radix_cache'], True)
            self.assertEqual(runtime.info['startup']['max_running_requests'], 1)
            self.assertEqual(runtime.metrics()['prefill_batches_total'], 0)
        finally:
            info = runtime.stop()
        self.assertEqual((info['cleanup_errors'], info['sigkill_sent']), ([], False))
        self.assertIsNotNone(info['exit_code'])

    def test_term_is_escalated_to_kill_after_the_bounded_wait(self):
        runtime, _ = self.launch('ignore-term')
        with mock.patch.object(sglang_runtime, 'TERM_WAIT_SECONDS', 0.5):
            try:
                runtime.start(time.monotonic() + 30)
            finally:
                info = runtime.stop()
        self.assertEqual((info['cleanup_errors'], info['sigkill_sent'], info['exit_code']), ([], True, -9))

    def test_group_members_that_outlive_the_server_are_killed(self):
        runtime, _ = self.launch('orphan')
        try:
            runtime.start(time.monotonic() + 30)
        finally:
            info = runtime.stop()
        self.assertEqual((info['cleanup_errors'], info['sigkill_sent']), ([], True))
        self.assertFalse(sglang_runtime.group_alive(runtime.child.pid))

    def test_occupied_port_is_refused_before_launch(self):
        runtime, _ = self.launch('plain')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', runtime.port))
            sock.listen()
            with self.assertRaisesRegex(ValueError, 'already has a listener'):
                runtime.start(time.monotonic() + 30)
        self.assertIsNone(runtime.child)


if __name__ == '__main__':
    unittest.main()
