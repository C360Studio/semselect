from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import supervise


class SupervisorTests(unittest.TestCase):
    def test_setup_two_attempts_then_rejects_third(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for _ in range(2):
                result = supervise.supervise(root, 'gliclass', 'setup', [sys.executable, '-c', 'print("ready")'])
                self.assertEqual(result['exit_code'], 0)
                self.assertTrue(result['shutdown_verified'])
            with self.assertRaises(ValueError):
                supervise.supervise(root, 'gliclass', 'setup', [sys.executable, '-c', 'pass'])

    def test_process_deadline_reaps_owned_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(supervise.LIMITS, runtime=.05):
            result = supervise.supervise(Path(directory), 'gliclass', 'runtime', [sys.executable, '-c', 'import threading; threading.Event().wait(10)'])
            self.assertEqual(result['stop_reason'], 'deadline exhausted')
            self.assertTrue(result['shutdown_verified'])
            self.assertLess(result['elapsed_seconds'], 3)

    def test_docker_cleanup_requires_observed_stopped_state(self):
        with tempfile.TemporaryDirectory() as directory:
            cidfile = Path(directory)/'container.cid'
            cidfile.write_text('a'*64)
            result = type('Result', (), {'returncode': 0, 'stdout': b'[{"State":{"Running":false}}]', 'stderr': b''})()
            with patch.object(supervise.subprocess, 'run', return_value=result) as invoke:
                observation = supervise.cleanup_container(cidfile)
            self.assertTrue(observation['container_shutdown_verified'])
            self.assertEqual(invoke.call_args_list[0].args[0], ['docker', 'stop', '--time', '2', 'a'*64])
            result.stdout = b'[{"State":{"Running":true}}]'
            with patch.object(supervise.subprocess, 'run', return_value=result):
                self.assertFalse(supervise.cleanup_container(cidfile)['container_shutdown_verified'])

    def test_docker_cli_absence_is_not_container_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'cidfile'):
                supervise.supervise(Path(directory), 'gliclass', 'runtime', ['docker', 'run', 'fake-image'])


if __name__ == '__main__':
    unittest.main()
