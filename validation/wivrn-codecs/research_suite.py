#!/usr/bin/env python3
"""Run the source-bound offline regression suite; never controls a headset."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def source_manifest(root):
    """Bind tracked and pending source; generated/ignored evidence is excluded."""
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached',
                             '--others', '--exclude-standard'], check=True, capture_output=True)
    names = sorted(set(result.stdout.decode().split('\0')) - {''})
    return {name: digest(root / name) for name in names
            if name.startswith(('validation/wivrn-codecs/', 'prototypes/vr444/'))
            and '__pycache__' not in Path(name).parts and not name.endswith('.pyc')
            and (root / name).is_file()}


def catalog(root, out, compiler):
    """Explicit audited commands: there is no shell or user-provided command."""
    units = [
        ('validators', 'validation/wivrn-codecs/tests'),
        ('codec-python', 'prototypes/vr444'),
        ('source-mapping', 'prototypes/vr444/experiments/compositor_freshness'),
        ('datagram-v2', 'prototypes/vr444/experiments/datagram_v2'),
        ('gaze-control', 'prototypes/vr444/experiments/dynamic_foveation'),
        ('clock-analysis', 'prototypes/vr444/experiments/latency_clock'),
        ('scheduling-model', 'prototypes/vr444/experiments/latency_schedule'),
        ('compression-wrapper', 'prototypes/vr444/experiments/packet_compression'),
    ]
    tasks = [{'id': name, 'kind': 'python-unittest', 'timeout_s': 120,
              'commands': [[sys.executable, '-m', 'unittest', 'discover', '-s', str(root / path),
                            '-p', 'test_*.py', '-v']]} for name, path in units]
    integration = root / 'validation/wivrn-codecs/integration'
    overlay = integration / 'source-overlay'
    codec = overlay / 'common/codec_test/vr444'
    flags = ['-std=c++20', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
             '-Wno-missing-field-initializers', '-fsanitize=address,undefined',
             '-fno-omit-frame-pointer', '-pthread', '-DWIVRN_CODEC_TESTS=1',
             '-I', str(overlay / 'common'), '-I', str(overlay / 'server/driver'),
             '-I', str(codec), '-I', str(integration)]
    cpp = [
        ('wire-bounds', [integration / 'wire_test.cpp'], []),
        ('stereo-admission', [integration / 'stereo_admission_test.cpp'], []),
        ('selection-trace', [integration / 'selection_trace_test.cpp'], []),
        ('selection-policy', [root / 'prototypes/vr444/experiments/selection_policy/test_selection_policy.cpp'], []),
        ('source-recorder', [root / 'prototypes/vr444/experiments/compositor_freshness/source_trace_test.cpp'], []),
        ('trace-ring', [integration / 'trace_sink_test.cpp'], [str(out / 'trace-ring.jsonl')]),
        ('layer-filter', [integration / 'meta_sr_policy_test.cpp'], []),
        ('mapped-destination', [root / 'prototypes/vr444/experiments/cpu_upload_live/destination_test.cpp',
                                codec / 'direct.cpp', codec / 'codec.cpp'], []),
        ('intra-v2-bounds', [root / 'prototypes/vr444/experiments/intra_v2/test.cpp',
                             root / 'prototypes/vr444/experiments/intra_v2/intra_v2.cpp',
                             codec / 'codec.cpp'], []),
    ]
    for name, sources, arguments in cpp:
        binary = out / 'bin' / name
        tasks.append({'id': name, 'kind': 'cpp-sanitized', 'timeout_s': 180,
                      'commands': [[compiler, *flags, *map(str, sources), '-o', str(binary)],
                                   [str(binary), *arguments]]})
    return tasks


def execute(argv, cwd, log_path, timeout_s, env):
    started = time.monotonic()
    result = {'argv': argv, 'status': 'fail', 'exit_code': None}
    with Path(log_path).open('wb') as log:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result['exit_code'] = process.wait(timeout=timeout_s)
                result['status'] = 'pass' if result['exit_code'] == 0 else 'fail'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                result.update(status='timeout', exit_code=process.returncode)
        except OSError as error:
            result.update(status='unavailable', error=str(error))
    result.update(elapsed_s=time.monotonic() - started,
                  log=str(log_path), log_sha256=digest(log_path))
    return result


def run(root, out, requested=None, compiler='clang++'):
    root, out = Path(root).resolve(), Path(out).resolve()
    tasks = catalog(root, out, compiler)
    known = {task['id'] for task in tasks}
    if requested is not None:
        if not requested or set(requested) - known:
            raise ValueError('unknown or empty task selection')
        tasks = [task for task in tasks if task['id'] in requested]
    out.mkdir(parents=True, exist_ok=False)
    (out / 'bin').mkdir()
    original = source_manifest(root)
    save(out / 'source-manifest.json', original)
    env = os.environ.copy()
    env.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1',
               PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1')
    report = {'schema': 1, 'status': 'running', 'scope': 'offline regression only',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'headset_operations': False, 'performance_qualification': False,
              'promotion_eligible': False, 'leak_sanitizer_enabled': False,
              'source_manifest_sha256': digest(out / 'source-manifest.json'),
              'python': sys.version, 'compiler': shutil.which(compiler), 'tasks': []}
    save(out / 'plan.json', tasks)
    for task in tasks:
        item = {key: value for key, value in task.items() if key != 'commands'}
        item['commands'] = []
        for number, command in enumerate(task['commands']):
            log = out / f"{task['id']}-{number}.log"
            check = execute(command, root, log, task['timeout_s'], env)
            item['commands'].append(check)
            if check['status'] != 'pass':
                break
        item['status'] = 'pass' if all(c['status'] == 'pass' for c in item['commands']) else 'fail'
        if item['kind'] == 'python-unittest':
            text = Path(item['commands'][-1]['log']).read_text(errors='replace')
            counts = re.findall(r'^Ran (\d+) tests? in ', text, re.MULTILINE)
            item['test_count'] = int(counts[-1]) if counts else 0
            if item['test_count'] == 0:
                item.update(status='fail', error='no Python tests collected')
        report['tasks'].append(item)
        save(out / 'summary.json', report)
        print(task['id'] + ': ' + item['status'], flush=True)
    current = source_manifest(root)
    report['source_changed'] = sorted(name for name in original.keys() | current.keys()
                                      if original.get(name) != current.get(name))
    report['status'] = ('pass' if not report['source_changed'] and report['tasks']
                        and all(t['status'] == 'pass' for t in report['tasks']) else 'fail')
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['python_tests'] = sum(t.get('test_count', 0) for t in report['tasks'])
    report['cpp_test_programs'] = sum(t['kind'] == 'cpp-sanitized' for t in report['tasks'])
    save(out / 'summary.json', report)
    save(out / 'hashes.json', {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*'))
                              if p.is_file() and p.name != 'hashes.json'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='Print commands without executing them')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--task', action='append', help='Restrict to explicitly named tasks')
    parser.add_argument('--compiler', default='clang++')
    args = parser.parse_args()
    if args.list:
        print(json.dumps(catalog(ROOT, Path('NEW_OUTPUT_DIRECTORY'), args.compiler), indent=2))
        return 0
    if args.out is None:
        parser.error('--out is required for execution')
    try:
        result = run(ROOT, args.out, args.task, args.compiler)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ('status', 'python_tests', 'cpp_test_programs',
                                                   'headset_operations', 'source_changed')}))
    return 0 if result['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
