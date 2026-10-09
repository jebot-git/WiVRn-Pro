#!/usr/bin/env python3
"""Reproduce sanitized horizon policy checks and independent fixture replay."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--approach', choices=('horizon', 'ready-successor'), default='horizon')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('horizon_checks', HERE / 'counterfactual.py')
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)
    successor = None
    if args.approach == 'ready-successor':
        successor = cf.load('successor_checks', HERE / 'ready_successor.py')
    compiler = shutil.which('clang++')
    if compiler is None:
        raise RuntimeError('clang++ is required')
    checks = []
    with tempfile.TemporaryDirectory(prefix='sub15-horizon-checks-') as folder:
        binary = Path(folder) / (args.approach + '-test')
        test_source = HERE / ('ready_successor_test.cpp' if successor else 'horizon_policy_test.cpp')
        compile_command = [compiler, '-std=c++20', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pedantic',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(ROOT / 'validation/wivrn-codecs/integration/source-overlay/common'),
            str(test_source), '-o', str(binary)]
        env = os.environ | {'ASAN_OPTIONS': 'detect_leaks=0'}
        for name, command in (('ASan/UBSan compile', compile_command), ('ASan/UBSan semantics', [str(binary)]),
                              ('C++ fixture serialization', [str(binary), '--fixtures'])):
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
            if result.returncode:
                raise RuntimeError(name + ' failed: ' + result.stderr[-4000:])
            check = dict(name=name, command=command, returncode=result.returncode, stderr=result.stderr)
            if name == 'C++ fixture serialization':
                fixtures = [json.loads(line) for line in result.stdout.splitlines()]
                for row in fixtures:
                    observed = (successor.choose(row['input']) if successor else
                                cf.choose(row['input'], row['numerator'], row['denominator']))
                    expected = row['expected']
                    for key in ('max_ahead_xr_ns', 'chosen_slots', 'eligible_left_slots'):
                        if observed[key] != expected[key]:
                            raise ValueError('independent fixture disagreement: ' + key)
                    if not cf.replay.snapshot_equal(observed['selected'], expected['selected']):
                        raise ValueError('independent fixture selection disagrees')
                    if successor and observed['successor_left_slots'] != row['successor_left_slots']:
                        raise ValueError('independent successor witness disagrees')
                check['independent_python_replays'] = len(fixtures)
            else:
                check['stdout'] = result.stdout
            checks.append(check)
    sources = [Path(__file__), HERE / 'counterfactual.py', HERE / 'horizon_policy.h', HERE / 'horizon_policy_test.cpp',
               ROOT / 'validation/wivrn-codecs/integration/selection_replay.py',
               ROOT / 'validation/wivrn-codecs/integration/source-overlay/common/codec_test/selection_policy.h',
               ROOT / 'validation/wivrn-codecs/integration/source-overlay/common/codec_test/selection_trace.h',
               ROOT / 'validation/wivrn-codecs/tests/test_selection_counterfactual.py']
    if successor:
        sources += [HERE / 'ready_successor.py', HERE / 'ready_successor_policy.h', test_source,
                    ROOT / 'validation/wivrn-codecs/tests/test_ready_successor.py']
    receipt = dict(schema=1, status='pass', approach=args.approach, checks=checks, synthetic_fixtures_only=True,
                   source_sha256={str(p.relative_to(ROOT)): cf.frozen.sha(p) for p in sources},
                   native_or_headset_changes=False, measured_speedup=False,
                   limitations=['No actual selector integration or timing measurement.',
                                'LeakSanitizer disabled under ptrace; ASan/UBSan remain active.'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'cross_language_fixtures': len(fixtures)}))


if __name__ == '__main__':
    main()
