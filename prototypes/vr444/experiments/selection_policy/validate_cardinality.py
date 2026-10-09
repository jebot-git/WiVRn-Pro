#!/usr/bin/env python3
"""Build, sanitize and bind the exact small-skip solver and Python auditor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=False)
    paths = [Path(__file__), HERE / 'cardinality_oracle.h', HERE / 'cardinality_test.cpp', HERE / 'cardinality_probe.cpp',
             HERE / 'cardinality.py', ROOT / 'validation/wivrn-codecs/tests/test_cardinality.py']
    def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    def hashes(): return {str(p.relative_to(ROOT)): sha(p) for p in paths}
    before = hashes()
    # Preserve the clang++ invocation name: resolving its symlink to clang can
    # switch driver link mode and omit the C++ standard library.
    compiler = Path(shutil.which('clang++')).absolute()
    checks = []
    def run(command, env=None, expected=0):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
        checks.append(dict(command=[str(x) for x in command], returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
        if result.returncode != expected:
            raise RuntimeError('validation command failed: ' + result.stderr[-3000:])
        return result
    version = run([str(compiler), '--version']).stdout
    flags = ['-std=c++20', '-Wall', '-Wextra', '-Werror', '-pedantic']
    test, solver = build / 'cardinality-sanitized', build / 'cardinality-probe'
    run([str(compiler), *flags, '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(HERE / 'cardinality_test.cpp'), '-o', str(test)])
    run([str(test)], env=os.environ | {'ASAN_OPTIONS': 'detect_leaks=0'})
    run([str(compiler), *flags, '-O2', str(HERE / 'cardinality_probe.cpp'), '-o', str(solver)])
    sample = build / 'sample.txt'
    valid = '2 2 1 15\n1 100 1 2\n2 110 12 13\n1 10 11\n2 20 21\n'
    sample.write_text(valid)
    parsed = json.loads(run([str(solver), str(sample)]).stdout)
    if [e['maximum_good'] for e in parsed['frontier']] != [2, 1]:
        raise RuntimeError('probe serialization score mismatch')
    malformed = ['', valid + 'trailing\n', valid.replace('2 2 1 15', '+2 2 1 15', 1),
        valid.replace('2 2 1 15', 'true 2 1 15', 1), valid.replace('2 2 1 15', '10001 2 1 15', 1),
        valid.replace('2 2 1 15', '9223372036854775808 2 1 15', 1),
        valid.replace('2 110 12 13', '2 99 12 13'), valid.replace('2 110 12 13', '2 110 14 13')]
    for i, text in enumerate(malformed):
        sample.write_text(text)
        run([str(solver), str(sample)], expected=2)
    suite = run(['python3', '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests'])
    if hashes() != before:
        raise RuntimeError('source changed during validation')
    result = dict(schema=1, status='pass', compiler=str(compiler), compiler_binary=str(compiler.resolve()), compiler_sha256=sha(compiler), compiler_version=version,
        checks=checks, sources_sha256=before, solver_path=str(solver), solver_sha256=sha(solver),
        sanitizer='address,undefined; leak detection disabled for this environment', exhaustive_small_populations=1024,
        malformed_cli_cases=len(malformed), headset_operations=False, native_behavior_changed=False,
        measured_speedup=False, latency_gate_evaluated=False, scope='Host-only exact cardinality solver correctness and independent certificate audit.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(suite.stderr.strip().splitlines()[-3:])
    print(str(solver))


if __name__ == '__main__':
    main()
