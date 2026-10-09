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

    def test_only_slots_context_and_unified_kv_change(self):
        one = llamacpp.command(LOCK, 1, False)
        for slots, unified in ((4, False), (4, True), (8, False)):
            other = llamacpp.command(LOCK, slots, unified)
            self.assertEqual(other[:len(one)][one.index('-c') + 1], str(4096 * slots))
            self.assertEqual(other[:len(one)][one.index('-np') + 1], str(slots))
            changed = [i for i, (a, b) in enumerate(zip(one, other)) if a != b]
            self.assertEqual(changed, [one.index('-c') + 1, one.index('-np') + 1])
            self.assertEqual(other[len(one):], ['-kvu'] if unified else [])


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
            '0.01 I llama_context: n_ctx_seq             = 16384', '0.01 I llama_context: kv_unified            = true',
            "0.01 I srv    load_model: initializing, n_slots = 4, n_ctx_slot = 16384, kv_unified = 'true'"])
        found = llamacpp.parse_startup(log)
        llamacpp.check_startup(found, fixtures.Cell('w2', 'kev', 4, 4, True))
        with self.assertRaisesRegex(RuntimeError, 'n_ctx_slot'):
            llamacpp.check_startup(found, fixtures.Cell('w2', 'kev', 4, 4))


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
