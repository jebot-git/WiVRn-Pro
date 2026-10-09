#!/usr/bin/env python3
"""Host recorder correctness and optional read-only native pacer syntax receipt."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
OVERLAY = ROOT / 'validation/wivrn-codecs/integration/source-overlay'
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--native-source', type=Path)
    parser.add_argument('--compile-commands', type=Path)
    args = parser.parse_args()
    if bool(args.native_source) != bool(args.compile_commands):
        parser.error('native source and cached compile database are required together')
    checks = []
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
    def run(name, command, cwd=ROOT):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
        checks.append(dict(name=name, command=command, cwd=str(cwd), returncode=result.returncode,
                           stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(name + ' failed: ' + result.stderr[-4000:])
        return result.stdout
    compiler = shutil.which('clang++')
    if not compiler:
        raise RuntimeError('clang++ is required')
    with tempfile.TemporaryDirectory(prefix='sub15-pacer-check-') as name:
        temporary = Path(name)
        executable = temporary / 'recorder-test'
        run('ASan/UBSan recorder compile', [compiler, '-std=c++20', '-DWIVRN_CODEC_TESTS=1', '-pthread',
            '-Wall', '-Wextra', '-Werror', '-pedantic', '-O1', '-g', '-fsanitize=address,undefined',
            '-fno-omit-frame-pointer', '-I' + str(OVERLAY / 'server/compositor'),
            str(HERE / 'pacer_trace_test.cpp'), '-o', str(executable)])
        run('Recorder disabled/overflow/concurrent-close checks', [str(executable)])
        fixture = temporary / 'fixtures.jsonl'
        run('Serialize 512 independent predictor fixture snapshots', [str(executable), '--fixtures', str(fixture)])
        spec = importlib.util.spec_from_file_location('pacer_validator', HERE / 'analyze.py')
        analyzer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(analyzer)
        analysis = analyzer.evaluate([json.loads(line) for line in fixture.read_text().splitlines()])
        if analysis['status'] != 'pass':
            raise RuntimeError(str(analysis))
        checks.append(dict(name='Independent Python exact arithmetic validation of C++ serialization',
                           returncode=0, result=analysis, fixture_sha256=sha(fixture)))
        run('Offline Python regression suite', ['python3', '-m', 'unittest', 'discover',
                                               '-s', 'validation/wivrn-codecs/tests'])
        if args.native_source:
            native = args.native_source.resolve()
            paths = ('server/compositor/pacer.cpp', 'server/compositor/pacer.h', 'server/compositor/codec_pacer_trace.h')
            mapping = temporary / 'overlay.json'
            mapping.write_text(json.dumps(dict(version=0, roots=[dict(type='file', name=str(native / path),
                **{'external-contents': str(OVERLAY / path)}) for path in paths])))
            database = json.loads(args.compile_commands.read_text())
            entry = next(e for e in database if Path(e['file']) == native / paths[0])
            tokens = entry.get('arguments') or shlex.split(entry['command'])
            command, skip = [compiler], False
            for token in tokens[1:]:
                if skip:
                    skip = False
                    continue
                if token in ('-o', '-MF', '-MT', '-MQ'):
                    skip = True
                    continue
                if token in ('-c', '-MD', '-MMD'):
                    continue
                command.append(token)
            command += ['-fsyntax-only', '-ivfsoverlay', str(mapping), '-fno-color-diagnostics', '-Wno-unknown-warning-option']
            run('Host full pacer TU via read-only Clang VFS (cached GCC flags)', command, entry['directory'])
            checks[-1].update(compile_commands_sha256=sha(args.compile_commands),
                             virtual_sources_sha256={p: sha(OVERLAY / p) for p in paths},
                             underlying_sources_sha256={p: sha(native / p) for p in paths if (native / p).exists()})
    files = [Path(__file__), HERE / 'pacer_trace_test.cpp', HERE / 'analyze.py']
    files += [OVERLAY / ('server/compositor/' + p) for p in ('pacer.cpp', 'pacer.h', 'codec_pacer_trace.h')]
    files += [ROOT / ('validation/wivrn-codecs/' + p) for p in (
        'tests/test_pacer_trace.py', 'tests/test_native_compare.py', 'tests/test_latency_policy.py',
        'integration/native_compare.py', 'integration/latency_policy.py')]
    receipt = dict(schema=1, status='pass', checks=checks,
                   sources_sha256={str(p.relative_to(ROOT)): sha(p) for p in files},
                   native_sources_modified=False, linked_native_build=False, native_predictor_executed=False,
                   headset_operations=False, measured_latency_claim=False,
                   scope='Recorder correctness, independent arithmetic replay and translation-unit syntax only.')
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(status='pass', checks=len(checks))))


if __name__ == '__main__':
    main()
