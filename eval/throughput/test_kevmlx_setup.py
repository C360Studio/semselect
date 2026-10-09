import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

import fixtures
import kevmlx_setup
from kevmlx_setup import Pin, SetupError

GIB = 1 << 30


class Usage:
    def __init__(self, free):
        self.free = free

    def __call__(self, path):
        return self


class Recorder:
    """Stands in for Commands: records argv instead of running anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, env, timeout=None, recorded=()):
        self.calls.append([str(a) for a in argv])
        raise AssertionError('no command may run in this test')


class PinTests(unittest.TestCase):
    def test_pins_are_the_upstream_facts(self):
        files = kevmlx_setup.BASE.files
        self.assertEqual(sum(files[name][0] for name in files if name.endswith('.safetensors')), 9_319_828_056)
        self.assertEqual(kevmlx_setup.ADAPTER.files['adapter_model.safetensors'][0], 129_924_032)
        self.assertEqual(kevmlx_setup.ADAPTER.files['head.pt'][0], 5_249_791)
        self.assertEqual(fixtures.load_lock(fixtures.LOCKS['kev'])['conversion_source_revision'], kevmlx_setup.ADAPTER.revision)
        for pin in (kevmlx_setup.ADAPTER, kevmlx_setup.BASE):
            self.assertEqual(kevmlx_setup.expected_files(pin), set(pin.files), pin.repo)
        self.assertEqual(kevmlx_setup.EXPECTED_HEAD['base_revision'], kevmlx_setup.BASE.revision)

    def test_git_blob_sha1_matches_git(self):
        with tempfile.TemporaryDirectory() as temp:
            empty, hello = Path(temp) / 'empty', Path(temp) / 'hello'
            empty.write_bytes(b'')
            hello.write_bytes(b'hello\n')
            self.assertEqual(kevmlx_setup.git_blob_sha1(empty), kevmlx_setup.SOURCE_BLOBS['kev/__init__.py'])
            self.assertEqual(kevmlx_setup.git_blob_sha1(hello), 'ce013625030ba8dba906f756967f9e9ca394464a')


class DiskGuardTests(unittest.TestCase):
    def test_guard_refuses_below_12_gib(self):
        with self.assertRaisesRegex(SetupError, r'11\.0 GiB free .* at least 12 GiB'):
            kevmlx_setup.require_free_space('/', usage=Usage(11 * GIB))
        self.assertEqual(kevmlx_setup.require_free_space('/', usage=Usage(12 * GIB)), 12 * GIB)

    def test_base_is_never_fetched_when_disk_is_short(self):
        run = Recorder()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SetupError, 'needs at least 12 GiB'):
                kevmlx_setup.fetch_base(kevmlx_setup.Layout(Path(temp)), run, usage=Usage(12 * GIB - 1))
        self.assertEqual(run.calls, [])


def pin_for(directory, lfs=('w.safetensors',)):
    files = {}
    for path in sorted(p for p in Path(directory).rglob('*') if p.is_file()):
        name = str(path.relative_to(directory))
        algorithm = 'sha256' if name in lfs else 'git-sha1'
        digest = kevmlx_setup.sha256_file(path) if algorithm == 'sha256' else kevmlx_setup.git_blob_sha1(path)
        files[name] = (path.stat().st_size, algorithm, digest)
    return Pin('test/model', '0' * 40, 'apache-2.0', files)


class ProvenanceTests(unittest.TestCase):
    def snapshot(self, temp):
        directory = Path(temp) / 'snapshot'
        directory.mkdir()
        (directory / 'config.json').write_text('{"a": 1}\n')
        (directory / 'w.safetensors').write_bytes(bytes(range(256)) * 64)
        return directory

    def test_snapshot_checksum_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = self.snapshot(temp)
            pin = pin_for(directory)
            rows = kevmlx_setup.check_snapshot(directory, pin)
            self.assertEqual([r['path'] for r in rows], ['config.json', 'w.safetensors'])
            self.assertEqual(rows[1]['sha256'], hashlib.sha256(bytes(range(256)) * 64).hexdigest())
            data = bytearray((directory / 'w.safetensors').read_bytes())
            data[100] ^= 1  # same size, one bit changed
            (directory / 'w.safetensors').write_bytes(bytes(data))
            with self.assertRaisesRegex(SetupError, r'w\.safetensors: sha256 .* differs from the pinned'):
                kevmlx_setup.check_snapshot(directory, pin)
            (directory / 'config.json').write_text('{"a": 2}\n')
            with self.assertRaisesRegex(SetupError, r'config\.json: git-sha1 .* differs from the pinned'):
                kevmlx_setup.check_snapshot(directory, pin)

    def test_missing_or_extra_files_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = self.snapshot(temp)
            pin = pin_for(directory)
            (directory / 'extra.json').write_text('{}')
            with self.assertRaisesRegex(SetupError, r"unexpected \['extra.json'\]"):
                kevmlx_setup.check_snapshot(directory, pin)
            (directory / 'extra.json').unlink()
            (directory / 'config.json').unlink()
            with self.assertRaisesRegex(SetupError, r"missing \['config.json'\]"):
                kevmlx_setup.check_snapshot(directory, pin)

    def test_recorded_provenance_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = self.snapshot(temp)
            rows = kevmlx_setup.check_snapshot(directory, pin_for(directory))
            kevmlx_setup.check_recorded(rows, json.loads(json.dumps(rows)), 'adapter')
            tampered = json.loads(json.dumps(rows))
            tampered[0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(SetupError, r"adapter: \['config.json'\] differ from provenance"):
                kevmlx_setup.check_recorded(rows, tampered, 'adapter')

    def test_verify_refuses_missing_or_foreign_provenance_before_running_anything(self):
        run = Recorder()
        with tempfile.TemporaryDirectory() as temp:
            layout = kevmlx_setup.Layout(Path(temp))
            with self.assertRaisesRegex(SetupError, 'missing'):
                kevmlx_setup.verify(layout, run)
            layout.provenance.write_text(json.dumps({'pins': dict(kevmlx_setup.pins(), base='Qwen/other@main')}))
            with self.assertRaisesRegex(SetupError, 'other pins'):
                kevmlx_setup.verify(layout, run)
            layout.provenance.write_text(json.dumps({'pins': kevmlx_setup.pins(),
                                                     'kev': {'archive': {'sha256': '0' * 64}}}))
            layout.archive.parent.mkdir(parents=True)
            layout.archive.write_bytes(b'not the recorded archive')
            with self.assertRaisesRegex(SetupError, 'SHA-256 differs from provenance'):
                kevmlx_setup.verify(layout, run)
        self.assertEqual(run.calls, [])


class SourceTests(unittest.TestCase):
    def test_extraction_takes_only_pinned_files_inside_the_root(self):
        payload = b'print("pinned")\n'
        blob = hashlib.sha1(b'blob %d\0' % len(payload) + payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(kevmlx_setup, 'SOURCE_BLOBS', {'kev/serve.py': blob}):
            layout = kevmlx_setup.Layout(Path(temp) / 'kev-root')
            layout.archive.parent.mkdir(parents=True)
            with tarfile.open(layout.archive, 'w:gz') as bundle:
                for name, data in ((f'{kevmlx_setup.ARCHIVE_TOP}/kev/serve.py', payload),
                                   (f'{kevmlx_setup.ARCHIVE_TOP}/evals/big.jsonl', b'{}\n'),
                                   ('../escape.py', b'x')):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
            self.assertEqual(kevmlx_setup.extract_source(layout), 1)
            extracted = sorted(str(p.relative_to(layout.root)) for p in layout.root.rglob('*') if p.is_file())
            self.assertEqual(extracted, [f'downloads/{kevmlx_setup.ARCHIVE_TOP}.tar.gz', f'{kevmlx_setup.ARCHIVE_TOP}/kev/serve.py'])
            self.assertFalse((Path(temp) / 'escape.py').exists())
            (layout.source / 'kev/serve.py').write_bytes(b'print("changed")\n')
            with self.assertRaisesRegex(SetupError, 'differs from Kev'):
                kevmlx_setup.check_source(layout.source)


if __name__ == '__main__':
    unittest.main()
