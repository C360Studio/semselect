from pathlib import Path
import unittest

import kevmlx_runtime
import kevmlx_setup
from run_kevmlx import KevCell

LAYOUT = kevmlx_setup.Layout(Path('/tmp/kev-layout'))
ADAPTER = '/tmp/kev-layout/hf/hub/models--jaredpalmer--kev-4b/snapshots/6cfce5c2fa4b4bd64026336ab649c5ca78857d52'
HEAD = {'lora': 16, 'temperature': 1.234567, 'base': kevmlx_setup.BASE.repo, 'base_revision': kevmlx_setup.BASE.revision}


def models_body(prefix_cache=0, dtype='bfloat16', hits=0, misses=0, batches=0, requests=0):
    """GET /v1/models as serve.py:298-312 builds it for an MLX server."""
    card = {'description': 'Kev pointer head on Qwen/Qwen3.5-4B-Base, serving ... at temperature 1.23',
            'release_date': '2026-10-01', 'run': ADAPTER, 'base': 'Qwen/Qwen3.5-4B-Base', 'lora': 16, 'device': 'mps',
            'backend': 'mlx', 'dtype': dtype, 'temperature': 1.234567, 'max_state_tokens': 65536, 'truncate_states': False,
            'cuda_graphs': None,
            'prefix_cache': {'size': prefix_cache, 'min_state_tokens': 0, 'max_tokens': 65536, 'hits': hits,
                             'misses': misses, 'cached_states': min(prefix_cache, misses), 'oom_retries': 0},
            'batches': {'count': batches, 'requests': requests, 'queued': 0}}
    return {'models': [{'name': name, **card} for name in ('kev-latest', 'jev-latest')]}


class LaunchTests(unittest.TestCase):
    def test_command_serves_the_pinned_local_adapter_on_loopback(self):
        self.assertEqual(kevmlx_runtime.command(LAYOUT), [
            '/tmp/kev-layout/venv/bin/python', '-I', '-m', 'kev.serve', '--run', ADAPTER, '--fallback', ADAPTER,
            '--host', '127.0.0.1', '--port', '18096'])
        self.assertNotIn(kevmlx_runtime.PORT, kevmlx_runtime.RESERVED_PORTS)
        for port in (18086, 18087, 18088, 8084, 8085, 30101):
            with self.assertRaisesRegex(ValueError, 'belongs to another runtime'):
                kevmlx_runtime.command(LAYOUT, port)

    def test_environment_pins_every_kev_knob_and_keeps_the_hub_offline(self):
        inherited = {'PATH': '/usr/bin', 'HOME': '/Users/x', 'KEV_API_KEY': 'secret', 'KEV_DATE_FACTS': '1',
                     'KEV_PREFIX_CACHE': '64', 'KEV_TEMPERATURE': '1.0', 'HF_TOKEN': 'hf_x', 'HF_HUB_OFFLINE': '0',
                     'HUGGINGFACE_HUB_CACHE': '/elsewhere', 'PYTHONPATH': '/x', 'MLX_METAL_FAST': '1'}
        for cell, cache in ((KevCell('w2', 8), '0'), (KevCell('w2', 1, split=True), '4')):
            env = kevmlx_runtime.environment(LAYOUT, cell.prefix_cache, inherited)
            self.assertEqual({k: v for k, v in env.items() if k.startswith('KEV_')},
                             {'KEV_BACKEND': 'mlx', 'KEV_DTYPE': 'bf16', 'KEV_DATE_FACTS': '0', 'KEV_TRUNCATE_STATES': '0',
                              'KEV_PREFIX_CACHE': cache})
            self.assertEqual((env['HF_HUB_OFFLINE'], env['TRANSFORMERS_OFFLINE'], env['HF_HUB_CACHE'], env['HF_HOME']),
                             ('1', '1', '/tmp/kev-layout/hf/hub', '/tmp/kev-layout/hf'))
            for dropped in ('HF_TOKEN', 'HUGGINGFACE_HUB_CACHE', 'PYTHONPATH', 'MLX_METAL_FAST', 'KEV_API_KEY', 'KEV_TEMPERATURE'):
                self.assertNotIn(dropped, env)
            self.assertEqual((env['PATH'], env['PYTHONUNBUFFERED']), ('/usr/bin', '1'))


class StartupTests(unittest.TestCase):
    LOG = ('loading...\n'
           f'serving {ADAPTER} ({ADAPTER}) on mps via mlx (bfloat16) 127.0.0.1:18096; states over 65,536 tokens refused (422)\n'
           'INFO:     Uvicorn running on http://127.0.0.1:18096 (Press CTRL+C to quit)\n')

    def test_startup_line_and_model_card_match_the_cell(self):
        startup = kevmlx_runtime.parse_startup(self.LOG)
        self.assertEqual({k: startup[k] for k in ('run', 'device', 'backend', 'dtype', 'port', 'max_state')},
                         {'run': ADAPTER, 'device': 'mps', 'backend': 'mlx', 'dtype': 'bfloat16', 'port': 18096,
                          'max_state': 65536})
        self.assertEqual(startup['uvicorn'], 'http://127.0.0.1:18096')
        card = kevmlx_runtime.check_card(models_body(4), kevmlx_runtime.expected_card(LAYOUT, 4, HEAD))
        kevmlx_runtime.check_startup(startup, card, 18096)

    def test_wrong_precision_or_cache_size_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "'dtype': \\('float32', 'bfloat16'\\)"):
            kevmlx_runtime.check_card(models_body(0, dtype='float32'), kevmlx_runtime.expected_card(LAYOUT, 0, HEAD))
        with self.assertRaisesRegex(RuntimeError, "'prefix_cache_size': \\(4, 0\\)"):
            kevmlx_runtime.check_card(models_body(4), kevmlx_runtime.expected_card(LAYOUT, 0, HEAD))
        with self.assertRaisesRegex(RuntimeError, 'temperature'):
            kevmlx_runtime.check_card(models_body(0), kevmlx_runtime.expected_card(LAYOUT, 0, dict(HEAD, temperature=1.0)))

    def test_counters_window(self):
        before = kevmlx_runtime.counters(models_body(4, hits=32, misses=32, batches=64, requests=64))
        after = kevmlx_runtime.counters(models_body(4, hits=64, misses=64, batches=100, requests=128))
        delta = kevmlx_runtime.counters_delta(before, after)
        self.assertEqual({k: delta[k] for k in ('prefix_cache_hits', 'prefix_cache_misses', 'batches', 'batched_requests')},
                         {'prefix_cache_hits': 32, 'prefix_cache_misses': 32, 'batches': 36, 'batched_requests': 64})
        self.assertAlmostEqual(delta['requests_per_batch'], 64 / 36)
        self.assertIsNone(kevmlx_runtime.counters_delta({'error': 'URLError'}, after))


if __name__ == '__main__':
    unittest.main()
