#!/usr/bin/env python3
"""Host/sanitizer and optional read-only Android syntax checks for schema 3."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
OVERLAY = ROOT / 'validation/wivrn-codecs/integration/source-overlay'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--native-source', type=Path)
    parser.add_argument('--compile-commands', type=Path)
    args = parser.parse_args()
    if bool(args.native_source) != bool(args.compile_commands):
        parser.error('native source and compile commands must be supplied together')
    checks = []

    def run(name, command, cwd=ROOT):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=120)
        checks.append(dict(name=name, command=command, cwd=str(cwd), returncode=result.returncode,
                           stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(name + ' failed: ' + result.stderr[-4000:])
        return result.stdout

    test = Path(__file__).with_name('render_attempt_test.cpp')
    with tempfile.TemporaryDirectory(prefix='sub15-attempt-validation-') as folder:
        temporary = Path(folder)
        compiler = shutil.which('clang++')
        if not compiler:
            raise RuntimeError('clang++ is required for sanitizer checks')
        executable = temporary / 'attempt-test'
        run('host ASan/UBSan compile', [compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror', '-pedantic',
            '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(OVERLAY / 'common'), str(test), '-o', str(executable)])
        # Existing environment supports ASan/UBSan; LSan is disabled under ptrace.
        import os
        old = os.environ.get('ASAN_OPTIONS')
        os.environ['ASAN_OPTIONS'] = 'detect_leaks=0'
        try:
            run('host ASan/UBSan state-machine checks', [str(executable)])
            fixtures = [json.loads(line) for line in run('C++ fixture serialization', [str(executable), '--fixtures']).splitlines()]
        finally:
            if old is None:
                os.environ.pop('ASAN_OPTIONS')
            else:
                os.environ['ASAN_OPTIONS'] = old
        path = ROOT / 'validation/wivrn-codecs/integration/candidate_trace.py'
        spec = importlib.util.spec_from_file_location('attempt_validation', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous = None
        for fixture in fixtures:
            module._attempt_accounting(fixture, previous)
            previous = fixture
        checks.append(dict(name='independent Python accounting of C++ fixtures', records=len(fixtures), returncode=0))

        if args.native_source:
            native = args.native_source.resolve()
            database = json.loads(args.compile_commands.read_text())
            entry = next(e for e in database if Path(e['file']) == native / 'client/scenes/stream.cpp')
            paths = ('client/scenes/stream.cpp', 'client/scenes/stream.h', 'common/codec_test/selection_trace.h')
            mapping = temporary / 'overlay.json'
            mapping.write_text(json.dumps({'version': 0, 'roots': [
                {'type': 'file', 'name': str(native / name), 'external-contents': str(OVERLAY / name)} for name in paths]}))
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
            command += ['-fsyntax-only', '-ivfsoverlay', str(mapping), '-fno-color-diagnostics']
            run('Android full stream translation-unit syntax via read-only VFS', command, entry['directory'])
            checks[-1]['compile_commands_sha256'] = sha(args.compile_commands)
            checks[-1]['virtual_replacements_sha256'] = {name: sha(OVERLAY / name) for name in paths}

    sources = [Path(__file__), test, OVERLAY / 'common/codec_test/selection_trace.h',
               OVERLAY / 'client/scenes/stream.cpp', OVERLAY / 'client/scenes/stream.h',
               ROOT / 'validation/wivrn-codecs/integration/candidate_trace.py',
               ROOT / 'validation/wivrn-codecs/integration/latency_policy.py',
               ROOT / 'validation/wivrn-codecs/tests/test_candidate_trace.py',
               ROOT / 'validation/wivrn-codecs/tests/test_latency_policy.py']
    receipt = dict(schema=1, status='pass', checks=checks,
                   sources_sha256={str(p.relative_to(ROOT)): sha(p) for p in sources},
                   native_sources_modified=False, linked_build=False, headset_operations=False,
                   optical_or_latency_claim=False, scope='Offline correctness and syntax only; no complete process-tail evidence.')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'checks': len(checks)}))


if __name__ == '__main__':
    main()
