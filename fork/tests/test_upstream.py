# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'sync-upstream.py'

class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wivrn-sync-test-')
        self.root = Path(self.tmp.name) / 'source'
        self.root.mkdir()
        self.git('init', '-b', 'fork')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Offline test')
        (self.root / 'file').write_text('base\n')
        self.commit('base')
        base = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '-b', 'upstream-fixture')
        (self.root / 'file').write_text('upstream\n')
        self.commit('upstream fix')
        self.target = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', 'fork')
        (self.root / 'fork').mkdir()
        (self.root / 'fork/upstream.json').write_text(json.dumps(dict(url=str(self.root), branch='upstream-fixture', import_commit=base)))
        self.commit('fork metadata')
        spec = importlib.util.spec_from_file_location('upstream', SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.mod.ROOT = self.root
        # The wrapper's default argument is bound at import time.
        self.mod.git.__kwdefaults__['cwd'] = self.root
        self.args = SimpleNamespace(command='prepare', no_fetch=True, ref=self.target,
                                    fail_if_behind=False, branch='sync/test',
                                    worktree=str(Path(self.tmp.name) / 'review'))
    def tearDown(self):
        self.tmp.cleanup()
    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL)
    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-m', message)
    def test_clean_merge_is_staged_only_in_review_worktree(self):
        before = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.mod.run(self.args), 0)
        review = Path(self.args.worktree)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual((self.root / 'file').read_text(), 'base\n')
        self.assertEqual((review / 'file').read_text(), 'upstream\n')
        merged = subprocess.check_output(['git','rev-parse','MERGE_HEAD'],cwd=review,text=True).strip()
        self.assertEqual(merged, self.target)
    def test_conflict_preserved_and_source_untouched(self):
        (self.root / 'file').write_text('fork edit\n')
        self.commit('local edit')
        self.assertEqual(self.mod.run(self.args), 1)
        self.assertIn('<<<<<<<', (Path(self.args.worktree)/'file').read_text())
        self.assertEqual((self.root/'file').read_text(), 'fork edit\n')
    def test_dirty_source_refused(self):
        (self.root / 'file').write_text('uncommitted\n')
        with self.assertRaises(ValueError): self.mod.run(self.args)
        self.assertFalse(Path(self.args.worktree).exists())
    def test_wrong_remote_refused(self):
        self.git('remote','add','upstream','https://example.invalid/unexpected')
        with self.assertRaises(ValueError): self.mod.run(self.args)
    def test_check_and_already_merged(self):
        self.args.command='check'
        self.args.fail_if_behind=True
        self.assertEqual(self.mod.run(self.args), 2)
        self.args.ref=self.git('rev-parse','HEAD').strip()
        self.assertEqual(self.mod.run(self.args), 0)
        self.args.command='prepare'
        self.assertEqual(self.mod.run(self.args), 0)
        self.assertFalse(Path(self.args.worktree).exists())
