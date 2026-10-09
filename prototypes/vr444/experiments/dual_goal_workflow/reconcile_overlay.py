#!/usr/bin/env python3
"""Regenerate a combined overlay from a pinned prior baseline and added files.

The retained native tree is read only, and supplies before bytes only for newly
introduced paths. Existing overlay baselines always come from the verified
reconstructed subset, never the retained tree's already-patched source.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
INTEGRATION = ROOT/'validation/wivrn-codecs/integration'


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior-commit', required=True)
    p.add_argument('--baseline-subset', type=Path, required=True)
    p.add_argument('--native-baseline', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    args = p.parse_args()
    if args.receipt.exists():
        raise ValueError('receipt already exists')
    prior_commit = subprocess.check_output(['git', 'rev-parse', args.prior_commit+'^{commit}'], cwd=ROOT, text=True).strip()
    manifest_data = subprocess.check_output(['git', 'show', prior_commit+':validation/wivrn-codecs/integration/overlay-manifest.json'], cwd=ROOT)
    prior = json.loads(manifest_data)
    overlay = INTEGRATION/'source-overlay'
    after = {str(p.relative_to(overlay)): p.read_bytes() for p in sorted(overlay.rglob('*')) if p.is_file()}
    if not set(prior).issubset(after):
        raise ValueError('previous overlay entries were removed')
    before = {}
    for name in after:
        baseline = args.baseline_subset if name in prior else args.native_baseline
        path = baseline/name
        if path.is_symlink() or not path.resolve().is_relative_to(baseline.resolve()):
            raise ValueError('baseline path escapes or is a symlink: '+name)
        before[name] = path.read_bytes() if path.is_file() else None
        if name in prior and digest(before[name]) != prior[name]['before_sha256']:
            raise ValueError('prior before bytes differ: '+name)
        if name.endswith('.spv') and before[name] is not None:
            raise ValueError('frozen shader already existed in baseline: '+name)
    with tempfile.TemporaryDirectory(prefix='dual-goal-overlay-') as folder:
        tree = Path(folder)
        def git(*argv):
            return subprocess.check_output(['git', *argv], cwd=tree)
        git('init', '-q')
        for name, data in before.items():
            if data is not None:
                path = tree/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        git('add', '.')
        git('-c', 'user.name=Codex', '-c', 'user.email=codex@local.invalid', 'commit', '-qm', 'Verified overlay baseline')
        for name, data in after.items():
            if not name.endswith('.spv'):
                path = tree/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        git('add', '.')
        patch = git('diff', '--cached', '--no-ext-diff', '--binary')
    manifest = {name: {'before_sha256': digest(before[name]), 'after_sha256': digest(data)} for name, data in after.items()}
    # Reject concurrent edits, then publish derived files together for parity verification.
    if any((overlay/name).read_bytes() != data for name, data in after.items()):
        raise ValueError('overlay changed during reconciliation')
    (INTEGRATION/'integration.patch').write_bytes(patch)
    (INTEGRATION/'overlay-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    receipt = dict(schema=1, prior_commit=prior_commit, prior_manifest_sha256=digest(manifest_data),
        baseline_subset=str(args.baseline_subset.resolve()), native_baseline=str(args.native_baseline.resolve()),
        added_baseline_sha256={name: digest(data) for name, data in before.items() if name not in prior},
        files=len(manifest), patch_sha256=digest(patch),
        manifest_sha256=digest((INTEGRATION/'overlay-manifest.json').read_bytes()),
        native_baseline_modified=False, parity_verification_required=True)
    with args.receipt.open('x') as stream:
        json.dump(receipt, stream, indent=2); stream.write('\n')
    print(json.dumps(dict(files=len(manifest), receipt=str(args.receipt))))


if __name__ == '__main__':
    main()
