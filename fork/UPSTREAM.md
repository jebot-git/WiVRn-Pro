# Keeping the fork current

Official remote: https://github.com/WiVRn/WiVRn.git, branch `master`.
The import is WiVRn v26.9, `bbc6e4cc36c355fa6180980abd231673dc15115d`, recorded
in `upstream.json`. Source lives at the same paths as upstream so ordinary Git
merges can carry future fixes.

The historical nested import was moved to the repository root in `e9b43ba2`.
The one-time ancestry merge `4e7a0bb3` records the exact already-imported release;
it used the `ours` strategy solely to join that original history. **Future
updates must use normal merges**, never `-s ours` or blanket conflict resolution.
Do not squash away the upstream ancestry. Keep fork policy changes in small,
reviewable commits and retain upstream licenses.

```sh
python3 fork/sync-upstream.py check
python3 fork/sync-upstream.py prepare --worktree ../wivrn-upstream-review
# --no-fetch uses cached refs; --ref selects a previously fetched commit/tag.
```

The helper verifies the official remote and common release ancestry. It requires
a clean source checkout, creates a new branch/worktree, and runs
`git merge --no-commit --no-ff`. It never switches or merges into the working
branch. Conflicts remain in the review checkout; no source changes are discarded.
`check --fail-if-behind` exits 2 when fixes await review. The weekly GitHub workflow
reports that status in its job summary; it does not auto-publish or merge code.
Scheduled workflows run on the hosted repository's default branch.

In the review worktree:

1. Resolve changes to packets, session lifetime, stage mapping, encoder/Vulkan
   code, Android metadata and Monado patches carefully. New Arc IDs require
   explicit review of `common/fork_policy.h`; unknown GPUs are rejected.
2. Run `fork/check-policy.py`, `fork/test-offline.sh`, and
   `cargo test --locked --manifest-path server/shora/Cargo.toml`.
3. Build the server with QSV OFF and ON, the Quest Pro APK, and the optional
   dashboard when changing its code. Compare PC/ARM protocol constants using
   `fork/check-android-protocol.py .cxx/Debug/<id>/arm64-v8a/compile_commands.json`.
4. Check the generated service units and a staged CMake installation. Verify the
   private SolarXR patch still applies to the newly pinned `monado-rev`.
5. Review the complete diff, then commit the merge. Record the merged upstream
   revision in the commit message. `git merge-base HEAD upstream/master` provides
   the new integration baseline; the original import pin remains provenance.
6. Merge the tested review branch into `wivrn-native`. Deploy only in a separate,
   explicitly authorized hardware-testing window. Roll back with a revert of the
   merge/feature commits or the previous installation; preserve user profiles.

To abandon an unfinished review, use `git merge --abort` **inside that review
worktree**. Inspect its status before removing it. The helper never cleans or
force-deletes worktrees. Offline tests exercise clean merges, conflict preservation,
dirty-source refusal, remote validation and already-merged revisions.

Shora is vendored separately under `server/shora`; update its source/protocol and
Cargo.lock as a reviewed change, retain licenses, and rerun replay/reconnect tests.
There is no automatic overwrite from the local Shora development checkout.

WayVR is pinned separately at the commit in `wayvr-import.json`, with its upstream
Cargo.lock, under `server/wayvr/upstream`. `patches/wayvr/0001-managed-worker.patch`
records the small source adaptations (supervised restart, Qt dashboard launch,
build identity and offline profile test hook). `fork/check-wayvr-source.py` checks
all 723 imported files against recorded hashes and reverse-checks that patch.

For a WayVR update, use a review branch, export the desired official upstream
commit into the import directory, apply/rebase the recorded patch, and update the
upstream/fork hashes and commit pin. Never overwrite the integration patch with a
fresh source export. Build OpenXR-only, run the supervisor, profile, Qt D-Bus and
staged-package tests, and record any schema changes before merging. Keep its Cargo
workspace separate from Shora; the two currently use different OpenXR crate ABIs.
No AppImage download or replacement is part of this workflow.
