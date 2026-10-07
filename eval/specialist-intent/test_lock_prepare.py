import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('specialist_lock_prepare', Path(__file__).parent/'locks/prepare.py')
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class WheelLockTests(unittest.TestCase):
    def test_vendored_metadata_does_not_replace_distribution_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pins = json.loads((prepare.ROOT/'dependencies.json').read_text())['direct_versions']
            for name, version in pins.items():
                with zipfile.ZipFile(root/(name+'.whl'), 'w') as wheel:
                    wheel.writestr(f'{name}.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
                    wheel.writestr('vendor/fake.dist-info/METADATA', 'Name: fake\nVersion: 999\n')
            prepare.wheel_lock(root, root/'requirements.lock.txt')
            content = (root/'requirements.lock.txt').read_text()
            self.assertNotIn('fake==', content)
            self.assertEqual(len(content.splitlines()), len(pins))
