#!/usr/bin/env python3
"""Fetch upstream and prepare an ordinary merge in an isolated review worktree."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run(args):
    pin = json.loads((ROOT / "fork/upstream.json").read_text())
    remote = git("remote", "get-url", "upstream", check=False)
    if remote.returncode:
        git("remote", "add", "upstream", pin["url"])
    elif remote.stdout.strip() != pin["url"]:
        raise ValueError("upstream URL differs from fork/upstream.json; inspect it before fetching")
    if not args.no_fetch:
        git("fetch", "--no-tags", "upstream",
            f"refs/heads/{pin['branch']}:refs/remotes/upstream/{pin['branch']}")
    target = args.ref or f"refs/remotes/upstream/{pin['branch']}"
    commit = git("rev-parse", "--verify", "--end-of-options", target + "^{commit}").stdout.strip()
    for ref in ("HEAD", commit):
        if git("merge-base", "--is-ancestor", pin["import_commit"], ref, check=False).returncode:
            raise ValueError(f"{ref} does not descend from the recorded WiVRn import")
    ahead = int(git("rev-list", "--count", f"HEAD..{commit}").stdout)
    base = git("merge-base", "HEAD", commit).stdout.strip()
    print(f"Upstream target: {commit}\nLast common ancestor: {base}\nUpstream commits to review: {ahead}")
    if args.command == "check":
        print(git("log", "--oneline", "--max-count=20", f"HEAD..{commit}").stdout, end="")
        return 2 if args.fail_if_behind and ahead else 0
    if not ahead:
        print("Already contains this upstream revision; no worktree created.")
        return 0
    if git("status", "--porcelain").stdout:
        raise ValueError("Commit or stash source worktree changes before preparing a merge")
    path = Path(args.worktree).expanduser().resolve()
    if path.exists():
        raise ValueError("Review worktree path already exists; nothing changed")
    branch = args.branch or f"sync/upstream-{commit[:12]}"
    git("check-ref-format", "--branch", branch)
    git("worktree", "add", "-b", branch, str(path), "HEAD")
    result = git("merge", "--no-commit", "--no-ff", commit, cwd=path, check=False)
    print(result.stdout + result.stderr, end="")
    print(f"Review worktree: {path}\nBranch: {branch}")
    if result.returncode:
        print("Merge stopped. Resolve conflicts in the review worktree; the original branch is unchanged.")
        return 1
    print("Merge staged, not committed. Review fork policy, run fork/check-policy.py and fork/test-offline.sh,\n"
          "build server with QSV OFF and ON and the Quest Pro APK, then commit the merge there.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "prepare"))
    parser.add_argument("--ref", help="Fetched upstream revision (default upstream/master)")
    parser.add_argument("--no-fetch", action="store_true", help="Use already fetched refs")
    parser.add_argument("--fail-if-behind", action="store_true", help="check exits 2 when an update is available")
    parser.add_argument("--worktree", help="New path for the review checkout (required for prepare)")
    parser.add_argument("--branch", help="New review branch name")
    args = parser.parse_args()
    if args.command == "prepare" and not args.worktree:
        parser.error("prepare needs --worktree")
    try:
        return run(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(getattr(error, "stderr", None) or str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
