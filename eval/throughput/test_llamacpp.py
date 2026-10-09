import ast
import unittest

import fixtures
import llamacpp
from fixtures import metal

EVIDENCE_LOG = fixtures.ROOT / 'docs/evidence/20261005T174941.125801Z-answerability-source-metal/kev.runtime.log'
LOCK = fixtures.load_lock(fixtures.LOCKS['kev'])


def metal_command_template():
    """scripts/metal.py's launch list; None marks computed (non-literal) elements."""
    tree = ast.parse((fixtures.ROOT / 'scripts/metal.py').read_text())
    run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
    assign = next(node for node in ast.walk(run) if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], 'id', None) == 'command')
    return [element.value if isinstance(element, ast.Constant) else None for element in assign.value.elts]


class CommandTests(unittest.TestCase):
    def test_one_slot_profile_is_metal_py_except_port(self):
        template = metal_command_template()
        ours = llamacpp.command(LOCK, 1, False)
        self.assertEqual(len(ours), len(template))
        for index, (theirs, mine) in enumerate(zip(template, ours)):
            if theirs is not None:
                self.assertEqual(mine, theirs, f'flag position {index}')
        self.assertEqual(ours[0], str(metal.SERVER))
        self.assertEqual(ours[ours.index('--port') + 1], str(llamacpp.PORT))

    def test_only_slots_context_unified_kv_and_logical_batch_change(self):
        one = llamacpp.command(LOCK, 1, False)
        for cell in fixtures.CELLS:
            other = llamacpp.command(LOCK, cell.slots, cell.kv_unified, n_batch=cell.n_batch)
            planned = {one.index('-c') + 1: str(4096 * cell.slots), one.index('-b') + 1: str(cell.n_batch),
                       one.index('-np') + 1: str(cell.slots)}
            changed = {i: b for i, (a, b) in enumerate(zip(one, other)) if a != b}
            self.assertEqual(changed, {i: value for i, value in planned.items() if one[i] != value}, cell.id)
            self.assertEqual(other[len(one):], ['-kvu'] if cell.kv_unified else [], cell.id)
        self.assertEqual(llamacpp.command(LOCK, 8, False, n_batch=4096)[one.index('-ub') - 1:one.index('-ub') + 2],
                         ['4096', '-ub', '512'])


class StartupTests(unittest.TestCase):
    def test_serial_evidence_log_reports_one_4096_token_slot(self):
        found = llamacpp.parse_startup(EVIDENCE_LOG.read_text())
        self.assertEqual({k: found[k] for k in ('n_seq_max', 'n_ctx', 'n_ctx_seq', 'n_slots', 'n_ctx_slot', 'slots_kv_unified')},
                         {'n_seq_max': 1, 'n_ctx': 4096, 'n_ctx_seq': 4096, 'n_slots': 1, 'n_ctx_slot': 4096,
                          'slots_kv_unified': False})
        self.assertEqual(found['kv_buffer_mib'], 128.0)
        llamacpp.check_startup(found, fixtures.Cell('w1', 'kev', 1, 1))
        with self.assertRaisesRegex(RuntimeError, 'n_slots'):
            llamacpp.check_startup(found, fixtures.Cell('w1', 'kev', 4, 4))

    def test_unified_kv_slot_addresses_the_whole_pool(self):
        log = '\n'.join([
            '0.01 I llama_context: n_seq_max             = 4', '0.01 I llama_context: n_ctx                 = 16384',
            '0.01 I llama_context: n_ctx_seq             = 16384', '0.01 I llama_context: n_batch               = 512',
            '0.01 I llama_context: n_ubatch              = 512', '0.01 I llama_context: kv_unified            = true',
            "0.01 I srv    load_model: initializing, n_slots = 4, n_ctx_slot = 16384, kv_unified = 'true'"])
        found = llamacpp.parse_startup(log)
        llamacpp.check_startup(found, fixtures.Cell('w2', 'kev', 4, 4, True))
        with self.assertRaisesRegex(RuntimeError, 'n_ctx_slot'):
            llamacpp.check_startup(found, fixtures.Cell('w2', 'kev', 4, 4))

    def test_lowered_logical_batch_is_refused(self):
        # A decision model runs in embedding mode, which sets n_batch = n_ubatch (common/common.cpp:1261-1266):
        # -b 4096 -ub 512 then starts with n_batch 512, which must not be measured as the b4096 cell.
        def log(n_batch):
            return '\n'.join([
                '0.01 I llama_context: n_seq_max             = 8', '0.01 I llama_context: n_ctx                 = 32768',
                f'0.01 I llama_context: n_batch               = {n_batch}', '0.01 I llama_context: n_ubatch              = 512',
                '0.01 I llama_context: kv_unified            = false',
                "0.01 I srv    load_model: initializing, n_slots = 8, n_ctx_slot = 4096, kv_unified = 'false'"])
        cell = next(c for c in fixtures.CELLS if c.id == 'w1-kev-8x8-b4096')
        llamacpp.check_startup(llamacpp.parse_startup(log(4096)), cell)
        with self.assertRaisesRegex(RuntimeError, r"'n_batch': \(512, 4096\)"):
            llamacpp.check_startup(llamacpp.parse_startup(log(512)), cell)


class MetricsTests(unittest.TestCase):
    def test_window_delta_rebuilds_busy_slots_from_lifetime_average(self):
        def text(prompt, cached, decodes, busy):
            return ('# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed\n'
                    '# TYPE llamacpp:prompt_tokens_total counter\n'
                    f'llamacpp:prompt_tokens_total {prompt}\nllamacpp:prompt_tokens_cached_total {cached}\n'
                    f'llamacpp:n_decode_total {decodes}\nllamacpp:n_busy_slots_per_decode {busy}\n')
        before, after = llamacpp.parse_metrics(text(1000, 50, 10, 1.5)), llamacpp.parse_metrics(text(3000, 150, 30, 2.5))
        delta = llamacpp.metrics_delta(before, after)
        self.assertEqual((delta['prompt_tokens_total'], delta['prompt_tokens_cached_total'], delta['n_decode_total']),
                         (2000, 100, 20))
        self.assertAlmostEqual(delta['busy_slots_per_decode'], 3.0)
        self.assertIsNone(llamacpp.metrics_delta(None, after))


if __name__ == '__main__':
    unittest.main()
