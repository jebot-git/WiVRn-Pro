#!/usr/bin/env python3
"""Offline capture controls and read-only Android translation-unit validation."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
INTEGRATION = ROOT / 'validation/wivrn-codecs/integration'
OVERLAY = INTEGRATION / 'source-overlay'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--compile-commands', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    native = args.native_source.resolve()
    paths = ('client/scenes/stream.cpp', 'client/scenes/stream.h',
             'common/codec_test/selection_trace.h', 'common/codec_test/publication_ledger.h')
    core_path = HERE / 'publication-validation-20261009.json'
    core = json.loads(core_path.read_text())
    if core['status'] != 'pass' or any(sha(ROOT / name) != digest for name, digest in core['sources_sha256'].items()):
        raise ValueError('publication core differs from validated receipt')
    source_paths = [Path(__file__), core_path, args.compile_commands] + [OVERLAY / name for name in paths]
    source_paths += [INTEGRATION / name for name in ('native_compare.py', 'latency_policy.py', 'publication_trace.py')]
    source_paths += list((ROOT / 'validation/wivrn-codecs/tests').glob('test_*.py'))
    source_paths += [ROOT / 'prototypes/vr444/experiments/compositor_freshness/analyze.py']
    before = {str(p.resolve()): sha(p) for p in source_paths}
    native_before = {name: sha(native / name) if (native / name).exists() else None for name in paths}
    checks = []

    def run(name, command, cwd=ROOT):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=120)
        checks.append(dict(name=name, command=command, cwd=str(cwd), returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise ValueError(name + ' failed: ' + result.stderr[-6000:])

    run('full offline Python suite including capture/preflight/promotion adversaries',
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests'])
    database = json.loads(args.compile_commands.read_text())
    entry = next(e for e in database if Path(e['file']).resolve() == native / 'client/scenes/stream.cpp')
    tokens = entry.get('arguments') or shlex.split(entry['command'])
    command, skip = [], False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in ('-o', '-MF', '-MT', '-MQ'):
            skip = True
            continue
        if token in ('-c', '-MD', '-MMD'):
            continue
        command.append(token)
    if not any(token.startswith('-DWIVRN_CODEC_TESTS') for token in command):
        raise ValueError('expected actual codec-test Android compile entry')
    with tempfile.TemporaryDirectory(prefix='sub15-publication-vfs-') as folder:
        mapping = Path(folder) / 'overlay.json'
        mapping.write_text(json.dumps({'version': 0, 'roots': [
            {'type': 'file', 'name': str(native / name), 'external-contents': str(OVERLAY / name)} for name in paths]}))
        flags = ['-fsyntax-only', '-ivfsoverlay', str(mapping), '-fno-color-diagnostics']
        run('Android full stream translation unit with diagnostic code', command + flags, entry['directory'])
        production = [token for token in command if not token.startswith('-DWIVRN_CODEC_TESTS')]
        run('Android full stream translation unit with diagnostic macro disabled', production + flags, entry['directory'])
    if any(sha(name) != digest for name, digest in before.items()):
        raise ValueError('source changed during capture validation')
    if any((sha(native / name) if (native / name).exists() else None) != digest for name, digest in native_before.items()):
        raise ValueError('native source unexpectedly changed')
    result = dict(schema=1, status='pass', checks=checks, sources_sha256=before,
        native_sources_sha256=native_before, native_sources_modified=False,
        core_validation_sha256=sha(core_path), linked_build=False, headset_operations=False,
        device_commands_executed=False, measured_speedup=False, promotion_eligible=False,
        scope='Read-only Android VFS syntax and offline configuration/evidence checks; no live acquisition or build/install receipt.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': 'pass', 'checks': len(checks)}))


if __name__ == '__main__':
    main()
