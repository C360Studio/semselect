"""Lifecycle checks use real owned child processes, not model inference."""
from pathlib import Path
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import metal


class MetalLifecycleTests(unittest.TestCase):
    def test_native_mtl_device_log_confirms_full_offload(self):
        log = ('llama_prepare_model_devices: using device MTL0 (Apple M3 Pro) (unknown id) - 28753 MiB free\n'
               'load_tensors: offloaded 33/33 layers to GPU\n')
        self.assertEqual(metal.metal_offload(log), 'offloaded 33/33 layers to GPU')
        for bad in (log.replace('33/33', '0/33'), log.replace('33/33', '32/33'),
                    log.replace('MTL0', 'CUDA0'), 'Metal available, CPU-only inference'):
            with self.assertRaises(RuntimeError):
                metal.metal_offload(bad)

    def child(self):
        child = subprocess.Popen([sys.executable, '-u', '-c',
                                  'import time; print("ready", flush=True); time.sleep(60)'],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(metal.stop, [child])
        self.addCleanup(child.stdout.close)
        # Child explicitly acknowledges startup before any lifecycle assertion.
        self.assertTrue(select.select([child.stdout], [], [], 5)[0])
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        return child

    def test_shutdown_stops_only_owned_children(self):
        owned, other = self.child(), self.child()
        metal.stop([owned])
        self.assertIsNotNone(owned.poll())
        self.assertIsNone(other.poll())
        metal.stop([owned])  # Already-reaped children are safe to clean up again.

    def test_readiness_fails_when_service_exits(self):
        child = self.child()
        metal.stop([child])
        with self.assertRaisesRegex(RuntimeError, 'exited before readiness'):
            metal.ready('http://127.0.0.1:1/health', [child], timeout=1)

    def test_occupied_port_is_rejected_before_launch(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, 'is in use'):
                metal.available(listener.getsockname()[1])

    def test_build_timeout_stops_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            stopped = Path(directory) / 'stopped'
            grandchild = ('import signal,time,pathlib,sys; '
                          f'signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path({str(stopped)!r}).touch(), sys.exit(0))); '
                          'print("ready", flush=True); time.sleep(30)')
            parent = ('import subprocess,sys,time; '
                      f'p=subprocess.Popen([sys.executable,"-u","-c",{grandchild!r}], stdout=subprocess.PIPE, text=True); '
                      'p.stdout.readline(); print(p.pid, flush=True); time.sleep(30)')
            popen = subprocess.Popen
            streams = []
            descendant_ids = []

            def started(*args, **kwargs):
                child = popen(*args, **kwargs, stdout=subprocess.PIPE, text=True)
                streams.append(child.stdout)
                self.assertTrue(select.select([child.stdout], [], [], 5)[0])
                descendant_ids.append(int(child.stdout.readline()))  # Signal handler is ready.
                return child

            try:
                with patch.object(metal.subprocess, 'Popen', side_effect=started):
                    try:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            metal.build_command([sys.executable, '-u', '-c', parent], timeout=0.01)
                    except PermissionError as exc:
                        self.skipTest(f'Environment prohibits owned process-group signals: {exc}')
                deadline = time.monotonic() + 2
                while not stopped.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(stopped.exists(), 'Build descendant never received termination')
            finally:
                for pid in descendant_ids:
                    try:
                        os.kill(pid, 15)  # Explicitly owned scratch child, also after a sandbox skip.
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 2
                while not stopped.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                for stream in streams:
                    stream.close()

    def test_runtime_integrity_includes_libraries_and_symlink_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'llama-server').write_bytes(b'executable')
            library = root / 'libllama.dylib'
            library.write_bytes(b'original library')
            alias = root / 'libllama.0.dylib'
            alias.symlink_to(library.name)
            with patch.object(metal, 'SERVER', root / 'llama-server'):
                original = metal.runtime_hashes()
                library.write_bytes(b'changed library')
                self.assertNotEqual(original, metal.runtime_hashes())
                library.write_bytes(b'original library')
                alias.unlink()
                alias.symlink_to('llama-server')
                self.assertNotEqual(original, metal.runtime_hashes())


if __name__ == '__main__':
    unittest.main()
