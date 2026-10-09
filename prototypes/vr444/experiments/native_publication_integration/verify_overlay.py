#!/usr/bin/env python3
"""Verify patch/overlay equivalence in disposable files, without native writes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
INTEGRATION = ROOT/'validation/wivrn-codecs/integration'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-out', type=Path, required=True,
                        help='New directory for the baseline file subset, not a complete native checkout')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((INTEGRATION/'overlay-manifest.json').read_text())
    overlay = INTEGRATION/'source-overlay'
    files = {str(p.relative_to(overlay)): p for p in overlay.rglob('*') if p.is_file()}
    if set(files) != set(manifest):
        raise ValueError('overlay population differs from manifest')
    inputs = [Path(__file__), INTEGRATION/'overlay-manifest.json', INTEGRATION/'integration.patch', INTEGRATION/'apply_overlay.py', *files.values()]
    before = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    baseline = args.baseline_out.resolve()
    baseline.mkdir(parents=True, exist_ok=False)
    for name, path in files.items():
        if sha(path) != manifest[name]['after_sha256']:
            raise ValueError('overlay after hash mismatch: '+name)
        target = baseline/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    commands = []

    def run(command, cwd):
        r = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)
        commands.append(dict(argv=command, cwd=str(cwd), returncode=r.returncode, stdout=r.stdout, stderr=r.stderr))
        if r.returncode:
            raise ValueError('parity operation failed: '+r.stderr)

    patch = str(INTEGRATION/'integration.patch')
    run(['git', 'apply', '-R', patch], baseline)
    for name, item in manifest.items():
        target = baseline/name
        if name.endswith('.spv'):
            target.unlink()  # Text patch deliberately excludes frozen binaries.
        if (sha(target) if target.exists() else None) != item['before_sha256']:
            raise ValueError('reconstructed baseline hash mismatch: '+name)
    with tempfile.TemporaryDirectory(prefix='publication-patch-') as folder:
        destination = Path(folder)
        shutil.copytree(baseline, destination, dirs_exist_ok=True)
        run(['git', 'apply', '--check', patch], destination)
        run(['git', 'apply', patch], destination)
        for name, item in manifest.items():
            if not name.endswith('.spv') and sha(destination/name) != item['after_sha256']:
                raise ValueError('patch/overlay mismatch: '+name)
    with tempfile.TemporaryDirectory(prefix='publication-overlay-') as folder:
        destination = Path(folder)
        shutil.copytree(baseline, destination, dirs_exist_ok=True)
        run(['python3', str(INTEGRATION/'apply_overlay.py'), str(destination), '--apply'], destination)
        if any(sha(destination/name) != item['after_sha256'] for name, item in manifest.items()):
            raise ValueError('overlay apply differs')
    if any(sha(ROOT/name) != value for name, value in before.items()):
        raise ValueError('input changed during verification')
    result = dict(schema=1, status='pass', overlay_files=len(manifest),
                  text_patch_files=sum(not n.endswith('.spv') for n in manifest),
                  baseline_subset=str(baseline), baseline_is_full_native_checkout=False,
                  sources_sha256=before, commands=commands, native_sources_modified=False,
                  linked_build=False, device_operations=False, promotion_eligible=False,
                  scope='Exact overlay population, before/after hashes and bidirectional text patch/application equivalence only.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(dict(status='pass', overlay_files=len(manifest))))


if __name__ == '__main__':
    main()
