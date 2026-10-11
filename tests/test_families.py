"""Offline tests for families.py prepare: the generalization export path, which
fetches a public repository at its pinned commit instead of reading a sibling
checkout. A local bare repository stands in for the public one."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'legacy-count' / 'families.py'
spec = importlib.util.spec_from_file_location('families', MODULE)
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_public_repo(root):
    repo = root / 'public'
    repo.mkdir()
    git('init', '-q', '-b', 'main', cwd=repo)
    git('config', 'user.email', 't@example.org', cwd=repo)
    git('config', 'user.name', 'T', cwd=repo)
    files = {'LICENSE': 'Apache License\nVersion 2.0\n', 'README.md': '# pub\n',
             'micro/service.go': 'package micro\n', 'micro/service_test.go': 'package micro\n',
             'other/x.go': 'package other\n'}
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git('add', '.', cwd=repo)
    git('commit', '-q', '-m', 'one', cwd=repo)
    return repo, git('rev-parse', 'HEAD', cwd=repo), files


class FamiliesPrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo, self.sha, self.files = make_public_repo(self.root)
        self.spec_path = self.root / 'input.json'
        self.write_spec(self.sha)

    def tearDown(self):
        self.tmp.cleanup()

    def write_spec(self, commit):
        self.spec_path.write_text(json.dumps({
            'repositories': {}, 'families': [],
            'generalization': {
                'repositories': {'pub': {'url': f'file://{self.repo}', 'commit': commit,
                                         'license': 'Apache-2.0', 'license_file': 'LICENSE'}},
                'families': [{'id': 'pub-micro', 'repository': 'pub', 'include': ['micro', 'README.md'],
                              'exclude': ['*_test.go']}],
                'ast_languages': ['go', 'java']}}))

    def prepare(self, generalization):
        args = argparse.Namespace(input=str(self.spec_path), src_root=str(self.root / 'src'),
                                  ws_root=str(self.root / 'semselect-families'), repos_root=str(self.root),
                                  config_out=str(self.root / 'tier0.json'),
                                  manifest_out=str(self.root / 'manifest.json'), generalization=generalization)
        with contextlib.redirect_stdout(io.StringIO()):
            fp.prepare(args)
        return (json.loads((self.root / 'manifest.json').read_text()), json.loads((self.root / 'tier0.json').read_text()))

    def test_generalization_family_is_fetched_and_exported(self):
        manifest, config = self.prepare(True)
        self.assertEqual(manifest['repositories']['pub']['commit'], self.sha)
        self.assertTrue(manifest['repositories']['pub']['public'])
        self.assertEqual(manifest['repositories']['pub']['license_file_first_line'], 'Apache License')
        fam = manifest['families'][0]
        self.assertEqual((fam['id'], fam['split'], fam['commit']), ('pub-micro', 'generalization', self.sha))
        paths = [e['path'] for e in fam['files']]
        self.assertEqual(sorted(paths), ['README.md', 'micro/service.go'])
        listing = ''.join(f"{hashlib.sha256(self.files[p].encode()).hexdigest()}  {p}\n" for p in paths)
        self.assertEqual(fam['workspace_sha256'], hashlib.sha256(listing.encode()).hexdigest())
        ws = self.root / 'semselect-families' / 'pub-micro'
        self.assertEqual(sorted(str(p.relative_to(ws)) for p in ws.rglob('*') if p.is_file()),
                         ['README.md', 'micro/service.go'])
        ast = next(s for s in config['sources'] if s['type'] == 'ast')
        self.assertEqual(ast['languages'], ['go', 'java'])
        # A second run reuses the fetched clone and the extracted tree.
        again, _ = self.prepare(True)
        self.assertEqual(again['families'], manifest['families'])

    def test_without_the_flag_nothing_public_is_touched_and_the_workspace_is_kept(self):
        self.prepare(True)
        manifest, config = self.prepare(False)
        self.assertEqual(manifest['families'], [])
        self.assertEqual(manifest['repositories'], {})
        self.assertTrue((self.root / 'semselect-families' / 'pub-micro' / 'micro' / 'service.go').exists())

    def test_public_repository_needs_a_full_commit_hash(self):
        self.write_spec(self.sha[:12])
        with self.assertRaises(SystemExit):
            self.prepare(True)


if __name__ == '__main__':
    unittest.main()
