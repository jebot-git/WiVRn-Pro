"""Local Git fixtures only; run: python3 -m unittest discover -s <this directory>."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import intake


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.remote = self.root / "repo", self.root / "remote.git"
        self.repo.mkdir()
        intake.git(self.root, "init", "--bare", str(self.remote))
        intake.git(self.repo, "init")
        self.git("config", "user.name", "Intake Test")
        self.git("config", "user.email", "intake@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("remote", "add", "origin", str(self.remote))
        self.first = self.commit("first")
        self.a = intake.DEFAULT_PREFIX + "codec-a"
        self.b = intake.DEFAULT_PREFIX + "transport-b"
        self.alias = intake.DEFAULT_PREFIX + "mystery"
        self.workflow = intake.DEFAULT_PREFIX + "research-workflow"
        for name in (self.a, intake.REFERENCE):
            self.git("update-ref", "refs/heads/" + name, self.first)
        self.second = self.commit("second")
        for name in (self.b, self.alias, self.workflow):
            self.git("update-ref", "refs/heads/" + name, self.second)
        self.git("push", "--all", "origin")
        self.git("fetch", "origin")

    def git(self, *args):
        return intake.git(self.repo, *args)

    def commit(self, message):
        self.git("commit", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def snapshot(self, **kwargs):
        return intake.collect(self.repo, **kwargs)

    def test_pins_aliases_ancestry_and_advisory_classification(self):
        before = self.git("for-each-ref", "--format=%(refname) %(objectname)")
        data = self.snapshot(fetch=True)
        self.assertEqual(data["collection"]["source"], "remote")
        self.assertTrue(data["collection"]["graph_complete"])
        self.assertEqual(set(data["tip_aliases"][self.second]),
                         {self.b, self.alias, self.workflow})
        self.assertIn({"ancestor": self.first, "descendant": self.second}, data["ancestor_pairs"])
        rows = {row["name"]: row for row in data["branches"]}
        self.assertTrue(rows[self.alias]["review_needed"])
        self.assertEqual(rows[self.alias]["family"], "unknown")
        self.assertEqual(rows[self.a]["family"], "codec")
        self.assertFalse(rows[self.workflow]["candidate"])
        self.assertFalse(rows[intake.REFERENCE]["candidate"])
        for leaf, family in (("base", "reference"), ("integration", "reference"),
                             ("differential-fuzz", "validation")):
            self.assertEqual(intake.classify(intake.DEFAULT_PREFIX + leaf), (family, False, False))
        self.assertEqual(intake.classify(intake.DEFAULT_PREFIX + "udp-experiment"),
                         ("transport", False, True))
        self.assertEqual(before, self.git("for-each-ref", "--format=%(refname) %(objectname)"))

    def test_moving_and_new_remote_heads(self):
        previous = self.snapshot(fetch=True)
        third = self.commit("third")
        new = intake.DEFAULT_PREFIX + "new-experiment"
        self.git("update-ref", "refs/heads/" + self.a, third)
        self.git("update-ref", "refs/heads/" + new, third)
        self.git("push", "origin", self.a, new)
        data = self.snapshot(fetch=True, previous=previous)
        self.assertEqual(data["changes"]["new"], [new])
        self.assertEqual(data["changes"]["moved"],
                         [{"name": self.a, "previous_sha": self.first, "commit_sha": third}])

    def test_removal_requires_successful_discovery_and_fetch(self):
        previous = self.snapshot(fetch=True)
        self.git("push", "origin", "--delete", self.a)
        self.git("update-ref", "-d", "refs/remotes/origin/" + self.a)
        cached = self.snapshot(previous=previous)
        self.assertEqual(cached["changes"]["removed"], [])
        self.assertEqual(cached["changes"]["unconfirmed_missing"], [self.a])
        real_git = intake.git
        for stage in ("ls-remote", "fetch"):
            def fail(repo, *args, **kwargs):
                if args[0] == stage:
                    raise intake.GitError("simulated incomplete collection")
                return real_git(repo, *args, **kwargs)
            with self.subTest(stage=stage), patch.object(intake, "git", side_effect=fail):
                failed = self.snapshot(fetch=True, previous=previous)
            self.assertFalse(failed["collection"]["remote_discovery_complete"])
            self.assertEqual(failed["changes"]["removed"], [])
            self.assertEqual(failed["changes"]["unconfirmed_missing"], [self.a])
        current = self.snapshot(fetch=True, previous=failed)
        self.assertEqual(current["changes"]["removed"], [self.a])

    def test_stale_cache_cannot_move_authoritative_baseline(self):
        first = self.snapshot(fetch=True)
        # Advance the remote directly so the local origin/a cache remains at A.
        intake.git(self.remote, "update-ref", "refs/heads/" + self.a, self.second)
        current = self.snapshot(fetch=True, previous=first)
        self.assertEqual(current["changes"]["moved"],
                         [{"name": self.a, "previous_sha": self.first, "commit_sha": self.second}])
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/" + self.a), self.first)
        real_git = intake.git

        def fail(repo, *args, **kwargs):
            if args[0] == "fetch":
                raise intake.GitError("simulated fetch failure")
            return real_git(repo, *args, **kwargs)

        with patch.object(intake, "git", side_effect=fail):
            failed = self.snapshot(fetch=True, previous=current)
        self.assertEqual(failed["changes"]["new"], [])
        self.assertEqual(failed["changes"]["moved"], [])
        self.assertEqual(failed["comparison_baseline"], current["comparison_baseline"])
        self.assertIn({"name": self.a, "previous_sha": self.second, "commit_sha": self.first},
                      failed["changes"]["unconfirmed_moved"])
        intake.validate_snapshot(failed)
        restored = self.snapshot(fetch=True, previous=failed)
        self.assertEqual(restored["changes"]["new"], [])
        self.assertEqual(restored["changes"]["moved"], [])
        self.assertEqual(restored["comparison_baseline"], current["comparison_baseline"])

    def test_graph_bound_and_no_overwrite(self):
        data = self.snapshot(max_graph_commits=1)
        self.assertFalse(data["collection"]["graph_complete"])
        output = self.root / "receipt.json"
        intake.write_snapshot(output, data)
        original = output.read_bytes()
        with self.assertRaises(FileExistsError):
            intake.write_snapshot(output, data)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(json.loads(original), data)
        self.assertEqual(list(self.root.glob(".intake-*")), [])

    def test_malformed_previous_and_identity(self):
        valid = self.snapshot()
        for mutate in (
                lambda d: d.update(schema_version=True),
                lambda d: d["branches"][0].update(commit_sha="1234"),
                lambda d: d["branches"].append(d["branches"][0]),
                lambda d: d["collection"].update(remote_discovery_complete=True)):
            data = copy.deepcopy(valid)
            mutate(data)
            with self.assertRaises(ValueError):
                self.snapshot(previous=data)
        foreign = copy.deepcopy(valid)
        foreign["repository"]["remote_url"] = "https://github.com/other/repo"
        with self.assertRaisesRegex(ValueError, "different repository"):
            self.snapshot(previous=foreign)
        self.assertEqual(intake.identity("git@github.com:jebot-git/WiVRn-Pro.git"),
                         intake.identity("https://github.com/jebot-git/WiVRn-Pro"))
        self.assertEqual(intake.safe_url("https://user:secret@github.com/a/b.git?token=x"),
                         "https://github.com/a/b.git")
        self.assertEqual(intake.safe_url("token_like_secret@github.com:jebot-git/WiVRn-Pro.git"),
                         "github.com:jebot-git/WiVRn-Pro.git")


if __name__ == "__main__":
    unittest.main()
