#!/usr/bin/env python3
"""Run explicit offline GPU research checks. Never controls a headset.

The default audit level requires no Vulkan device. CPU and host levels rebuild
the final mapped-input matrix, including Android compile-only and SPIR-V checks.
Host timings emitted by correctness probes are not performance qualification.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from history import verify_catalog

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EXPERIMENTS = HERE.parent
AUDITS = ('direct_image_gpu', 'vulkan_extensions', 'palette_gpu',
          'palette_gpu_encode', 'palette_output_memory', 'rowfilter_gpu',
          'rowfilter_image_timing', 'host_image_copy', 'host_image_timing',
          'rowblocks_gpu', 'rowblocks_image_timing', 'mapped_input_decode',
          'gpu_qualification_suite')


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def source_snapshot(root):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached',
                             '--others', '--exclude-standard'], check=True, capture_output=True)
    prefixes = ('prototypes/vr444/experiments/',
                'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444/')
    return {name: digest(root / name)
            for name in sorted(set(result.stdout.decode().split('\0')) - {''})
            if name.startswith(prefixes) and (root / name).is_file()
            and '__pycache__' not in Path(name).parts}


def execute(argv, out, label, timeout=600):
    """Capture both streams; timeout only terminates this owned process group."""
    stdout, stderr = out / f'{label}.stdout', out / f'{label}.stderr'
    report = dict(argv=list(map(str, argv)), status='fail', exit_code=None)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',
               UBSAN_OPTIONS='halt_on_error=1', PYTHONDONTWRITEBYTECODE='1',
               PYTHONHASHSEED='0')
    start = time.monotonic()
    with stdout.open('xb') as output, stderr.open('xb') as error:
        try:
            process = subprocess.Popen(report['argv'], cwd=ROOT, env=env,
                                       stdout=output, stderr=error, start_new_session=True)
            try:
                report['exit_code'] = process.wait(timeout=timeout)
                report['status'] = 'pass' if process.returncode == 0 else 'fail'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                report.update(status='timeout', exit_code=process.returncode)
        except OSError as failure:
            report.update(status='unavailable', error=str(failure))
    report.update(elapsed_seconds=time.monotonic() - start,
                  stdout=str(stdout), stdout_sha256=digest(stdout),
                  stderr=str(stderr), stderr_sha256=digest(stderr))
    return report


def require_python_tests(result):
    text = Path(result['stderr']).read_text(errors='replace')
    counts = re.findall(r'^Ran (\d+) tests? in ', text, re.MULTILINE)
    if (result['status'] != 'pass' or len(counts) != 1 or int(counts[0]) == 0
            or re.search(r'^OK\s*$', text, re.MULTILINE) is None
            or not text.rstrip().endswith('\nOK')
            or re.search(r'\bskipped\b', text)):
        raise ValueError('Python check did not execute a nonempty passing suite without skips')
    result['test_count'] = int(counts[0])


def require_cpu_matrix(result):
    value = json.loads(Path(result['stdout']).read_text())
    if not isinstance(value, dict):
        raise ValueError('CPU matrix report must be an object')
    booleans = ('pass', 'late_partial_decode_withheld', 'vulkan_initialized', 'headset_used')
    integers = ('cases', 'reuse_rounds', 'rejections')
    if (any(type(value.get(key)) is not bool for key in booleans)
            or any(type(value.get(key)) is not int for key in integers)
            or result['status'] != 'pass' or value != {
            'pass': True, 'cases': 838, 'reuse_rounds': 2, 'rejections': 8382,
            'late_partial_decode_withheld': True, 'vulkan_initialized': False,
            'headset_used': False}):
        raise ValueError('incomplete or changed mapped CPU matrix')
    result['matrix'] = value


def required_stages(level, devices):
    if level not in ('audits', 'cpu', 'host'):
        raise ValueError('unknown suite level')
    if level != 'host' and devices:
        raise ValueError('host device selection requires the host level')
    stages = ['history'] + [f'audit-{name}' for name in AUDITS]
    if level in ('cpu', 'host'):
        stages += ['build', 'mapped-cpu']
    if level == 'host':
        if not devices or len(set(devices)) != len(devices) or any(n < 0 for n in devices):
            raise ValueError('unique explicit nonnegative host device indices required')
        for device in devices:
            stages += [f'host-device-{device}', f'host-audit-{device}']
    return stages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--level', choices=('audits', 'cpu', 'host'), default='audits')
    parser.add_argument('--rrf-corpus', type=Path)
    parser.add_argument('--rrb-corpus', type=Path)
    parser.add_argument('--host-device', type=int, action='append', default=[])
    parser.add_argument('--icd', type=Path)
    args = parser.parse_args()
    expected = required_stages(args.level, args.host_device)
    if args.level in ('cpu', 'host') and (not args.rrf_corpus or not args.rrb_corpus):
        parser.error('CPU and host levels require both explicit corpus directories')
    if args.level == 'host' and not args.icd:
        parser.error('host level requires the explicit local Vulkan ICD')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = source_snapshot(ROOT)
    save(out / 'sources-before.json', before)
    report = dict(schema=1, start_utc=utc(), level=args.level, expected_stages=expected,
                  headset_used=False, performance_qualification=False,
                  promotion_eligible=False, fresh_checks=[], requested_suite_pass=False,
                  declared_host_checks_pass=False)
    completed = []
    corpus_before = None
    corpus_snapshot = None
    try:
        if args.level in ('cpu', 'host'):
            spec = importlib.util.spec_from_file_location('mapped_suite_audit', EXPERIMENTS / 'mapped_input_decode/audit.py')
            matrix_audit = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(matrix_audit)
            manifests = [json.loads((EXPERIMENTS / name / 'results/corpus-hashes.json').read_text())
                         for name in ('rgb_rowfilter', 'rgb_rowblocks')]
            matrix_audit.matched_variants(*manifests)
            corpus_snapshot = lambda: [matrix_audit.source_snapshot(args.rrf_corpus, manifests[0]),
                                       matrix_audit.source_snapshot(args.rrb_corpus, manifests[1], True)]
            corpus_before = corpus_snapshot()
            save(out / 'corpora-before.json', corpus_before)
        archive = verify_catalog(ROOT, json.loads((HERE / 'catalog.json').read_text()))
        save(out / 'history.json', archive)
        completed.append('history')
        for name in AUDITS:
            label = f'audit-{name}'
            result = execute([sys.executable, '-m', 'unittest', 'discover', '-s',
                              EXPERIMENTS / name, '-p', 'test_*.py', '-v'], out, label, 120)
            report['fresh_checks'].append(dict(id=label, **result))
            require_python_tests(report['fresh_checks'][-1])
            completed.append(label)
        if args.level in ('cpu', 'host'):
            mapped = EXPERIMENTS / 'mapped_input_decode'
            build = out / 'build'
            result = execute(['bash', mapped / 'build.sh', build], out, 'build', 900)
            report['fresh_checks'].append(dict(id='build', **result))
            if result['status'] != 'pass':
                raise ValueError('native/Android/SPIR-V build failed or unavailable')
            completed.append('build')
            rrf_gold = EXPERIMENTS / 'rgb_rowfilter/fixtures'
            rrb_gold = EXPERIMENTS / 'rgb_rowblocks/fixtures'
            result = execute([build / 'pair-test-asan', rrf_gold, args.rrf_corpus,
                              rrb_gold, args.rrb_corpus], out, 'mapped-cpu')
            report['fresh_checks'].append(dict(id='mapped-cpu', **result))
            require_cpu_matrix(report['fresh_checks'][-1])
            completed.append('mapped-cpu')
            if args.level == 'host':
                for device in args.host_device:
                    label = f'host-device-{device}'
                    # env is a fixed launcher, not an arbitrary command string.
                    result = execute(['env', f'VK_ICD_FILENAMES={args.icd}',
                                      build / 'mapped-input-host', build, args.rrf_corpus,
                                      args.rrb_corpus, '--qualify', rrf_gold, '--device', device], out, label)
                    report['fresh_checks'].append(dict(id=label, **result))
                    if result['status'] != 'pass':
                        raise ValueError('host GPU correctness matrix failed or unsupported')
                    completed.append(label)
                    label = f'host-audit-{device}'
                    audit = execute([sys.executable, mapped / 'audit.py', result['stdout'],
                                     EXPERIMENTS / 'rgb_rowfilter/results/corpus-hashes.json',
                                     EXPERIMENTS / 'rgb_rowblocks/results/corpus-hashes.json',
                                     '--out', out / f'{label}.json'], out, label, 120)
                    report['fresh_checks'].append(dict(id=label, **audit))
                    if audit['status'] != 'pass':
                        raise ValueError('host GPU matrix audit failed')
                    audited = json.loads((out / f'{label}.json').read_text())
                    if audited.get('pass') is not True or audited.get('case_count') != 170 or audited.get('qualification_only') is not True:
                        raise ValueError('unexpected host qualification scope')
                    completed.append(label)
            report['built_artifact_sha256'] = {str(p.relative_to(out)): digest(p)
                                               for p in sorted(build.rglob('*')) if p.is_file()}
        report['requested_suite_pass'] = completed == expected
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as failure:
        report['error'] = str(failure)
    after = source_snapshot(ROOT)
    save(out / 'sources-after.json', after)
    report.update(finish_utc=utc(), completed_stages=completed, sources_unchanged=before == after,
                  source_manifest_sha256=digest(out / 'sources-before.json'))
    if corpus_before is not None:
        try:
            corpus_after = corpus_snapshot()
            save(out / 'corpora-after.json', corpus_after)
            report['corpora_unchanged'] = corpus_after == corpus_before
        except (ValueError, OSError) as failure:
            report.update(corpora_unchanged=False, corpus_error=str(failure))
        report['requested_suite_pass'] &= report['corpora_unchanged']
    report['requested_suite_pass'] &= report['sources_unchanged']
    report['declared_host_checks_pass'] = report['requested_suite_pass'] and args.level == 'host'
    save(out / 'report.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'fresh_checks'}, indent=2))
    return 0 if report['requested_suite_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
