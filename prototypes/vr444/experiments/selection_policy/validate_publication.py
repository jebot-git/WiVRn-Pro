#!/usr/bin/env python3
"""Validate immutable publication prefixes without device or native operations."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
INTEGRATION = ROOT / 'validation/wivrn-codecs/integration'
HEADER = INTEGRATION / 'source-overlay/common/codec_test/publication_ledger.h'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.build_dir.mkdir(parents=True, exist_ok=False)
    compiler = shutil.which('clang++')
    if not compiler:
        raise ValueError('clang++ is required')
    compiler = str(Path(compiler).absolute()) # Preserve C++ driver invocation.
    sources = [Path(__file__), HERE / 'publication_ledger_test.cpp', HEADER,
        HEADER.with_name('selection_trace.h'), INTEGRATION / 'publication_trace.py',
        INTEGRATION / 'candidate_trace.py', INTEGRATION / 'selection_replay.py',
        ROOT / 'validation/wivrn-codecs/tests/test_publication_trace.py',
        ROOT / 'validation/wivrn-codecs/tests/test_candidate_trace.py']
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    checks = []
    environment = os.environ | {'ASAN_OPTIONS': 'detect_leaks=0'}

    def run(name, command, **kwargs):
        result = subprocess.run(command, cwd=ROOT, env=environment, text=True,
            stderr=subprocess.PIPE, timeout=120, **({'stdout': subprocess.PIPE} | kwargs))
        checks.append(dict(name=name, command=command, returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise ValueError(name + ' failed: ' + result.stderr[-4000:])
        return result.stdout

    version = run('compiler version', [compiler, '--version'])
    executable_hashes = {}
    for sanitizer in ('address,undefined', 'thread'):
        executable = args.build_dir.resolve() / ('publication-' + sanitizer.replace(',', '-'))
        run(sanitizer + ' compile', [compiler, '-std=c++20', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pedantic',
            '-fno-omit-frame-pointer', '-fsanitize=' + sanitizer, '-pthread',
            '-I' + str(INTEGRATION / 'source-overlay/common'), str(HERE / 'publication_ledger_test.cpp'), '-o', str(executable)])
        run(sanitizer + ' bounds/reset/concurrent prefix checks', [str(executable)])
        executable_hashes[sanitizer] = sha(executable)
    executable = args.build_dir.resolve() / 'publication-address-undefined'
    emitted = [json.loads(line) for line in run('C++ JSON fixture', [str(executable), '--fixture']).splitlines()]
    sys.path.insert(0, str(ROOT / 'validation/wivrn-codecs/tests'))
    spec = importlib.util.spec_from_file_location('publication_test_fixture', ROOT / 'validation/wivrn-codecs/tests/test_publication_trace.py')
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    expected, traces, presented = test.fixture()
    if emitted != expected:
        raise ValueError('independent Python fixture differs from C++ serialization')
    evaluated = test.module.evaluate(emitted, traces, presented)
    if evaluated['status'] != 'pass':
        raise ValueError('C++ fixture does not pass independent history/presentation checks')
    checks.append(dict(name='independent Python reconstruction and presentation join of C++ output', returncode=0, result=evaluated))
    abrupt = args.build_dir / 'abrupt.jsonl'
    with abrupt.open('x') as stream:
        run('explicit checkpoint flush survives _Exit without destructors', [str(executable), '--abrupt-fixture'], stdout=stream)
    if test.module.load(abrupt) != expected:
        raise ValueError('abrupt-exit output is not the exact exported prefix')
    run('full Python offline suite', [sys.executable, '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests', '-p', 'test_*.py'])
    if any(sha(ROOT / path) != digest for path, digest in source_hashes.items()):
        raise ValueError('publication sources changed during validation')
    result = dict(schema=1, status='pass', sources_sha256=source_hashes, executable_sha256=executable_hashes,
        compiler_invocation=compiler, compiler_binary_sha256=sha(Path(compiler).resolve()), compiler_version=version,
        checks=checks, native_integration=False, headset_operations=False, measured_speedup=False,
        promotion_eligible=False, scope='Host prefix recorder/analyzer contract; terminal tail, live integration and physical presentation remain unqualified.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': 'pass', 'checks': len(checks)}))


if __name__ == '__main__':
    main()
