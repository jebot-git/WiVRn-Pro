#!/usr/bin/env python3
"""Run actual pacer constructor error paths using a host-only worker-start fault."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
OVERLAY = ROOT / 'validation/wivrn-codecs/integration/source-overlay'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--compile-commands', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    native = args.native_source.resolve()
    paths = ('server/compositor/pacer.cpp', 'server/compositor/pacer.h', 'server/compositor/codec_pacer_trace.h')
    database = json.loads(args.compile_commands.read_text())
    entry = next(e for e in database if Path(e['file']) == native / paths[0])
    compiler = shutil.which('clang++')
    if not compiler:
        raise RuntimeError('clang++ required')
    checks = []
    cached_support = args.compile_commands.parent / 'common/CMakeFiles/wivrn-common.dir/smp.cpp.o'
    if not cached_support.is_file():
        raise RuntimeError('cached common support object required for packet-header static initializers')
    def run(name, command, env=None, timeout=120, expected=0):
        result = subprocess.run(command, cwd=entry['directory'], env=env, text=True,
                                capture_output=True, timeout=timeout)
        checks.append(dict(name=name, command=command, returncode=result.returncode, expected_returncode=expected,
                           stdout=result.stdout, stderr=result.stderr))
        if result.returncode != expected:
            raise RuntimeError(name + ': ' + result.stderr + result.stdout)
        return result
    with tempfile.TemporaryDirectory(prefix='pacer-constructor-regression-') as folder:
        temporary = Path(folder)
        mapping = temporary / 'overlay.json'
        mapping.write_text(json.dumps(dict(version=0, roots=[dict(type='file', name=str(native/path),
            **{'external-contents': str(OVERLAY/path)}) for path in paths])))
        tokens = entry.get('arguments') or shlex.split(entry['command'])
        flags, skip = [], False
        for token in tokens[1:]:
            if skip:
                skip = False
                continue
            if token in ('-o', '-MF', '-MT', '-MQ'):
                skip = True
                continue
            if token in ('-c', '-MD', '-MMD', str(native/paths[0])):
                continue
            flags.append(token)
        flags += ['-ivfsoverlay', str(mapping), '-fno-color-diagnostics', '-Wno-unknown-warning-option',
                  '-ffunction-sections', '-fdata-sections', '-UNDEBUG']
        objects = []
        for name, source in (('pacer', native/paths[0]), ('constructor-test', HERE/'constructor_test.cpp')):
            target = temporary/(name+'.o')
            run('compile ' + name, [compiler, *flags, '-c', str(source), '-o', str(target)])
            objects.append(str(target))
        executable = temporary/'constructor-test'
        run('link isolated native constructor test', [compiler, *objects, str(cached_support), '-lcrypto', '-pthread', '-Wl,--gc-sections',
            '-Wl,--export-dynamic-symbol=pthread_create', '-o', str(executable)])
        existing = temporary/'existing.jsonl'
        existing.write_text('preserve this file\n')
        for name, value, expected in (
            ('relative path rejects before worker', 'relative.jsonl', 'trace-error'),
            ('existing path rejects before worker', str(existing), 'trace-error'),
            ('missing parent rejects before worker', str(temporary/'absent'/'trace.jsonl'), 'trace-error'),
            ('disabled diagnostics reaches worker-start fault', None, 'thread-error'),
            ('valid output initializes before worker-start fault', str(temporary/'valid.jsonl'), 'thread-error')):
            env = dict(os.environ)
            env.pop('WIVRN_CODEC_PACER_TRACE', None)
            if value is not None:
                env['WIVRN_CODEC_PACER_TRACE'] = value
            run(name, [str(executable), expected], env=env, timeout=10)
        if existing.read_text() != 'preserve this file\n':
            raise RuntimeError('exclusive diagnostic creation overwrote an existing file')
        serialized = [json.loads(line) for line in (temporary/'valid.jsonl').read_text().splitlines()]
        if serialized[-1] != dict(type='footer', records=0, dropped=0, complete=True):
            raise RuntimeError('initialized recorder did not cleanly close after worker-start fault')

        # Negative control reconstructs the original late initialization only
        # in a temporary VFS file. The injected worker error must occur first.
        fixed = (OVERLAY/paths[0]).read_text()
        early = """#ifdef WIVRN_CODEC_TESTS
        // Allocate/open diagnostics before the worker member starts. A path
        // error must not unwind a jthread blocked on the compute condition.
        codec_test_pacer_id(codec_test::pacer_trace().enabled() ? codec_test::next_pacer_trace_id() : 0),
#endif
"""
        if early not in fixed:
            raise RuntimeError('constructor negative-control marker changed')
        late = fixed.replace(early, '').replace('\tu_var_add_root(this, "Pacer", false);',
            '#ifdef WIVRN_CODEC_TESTS\n'
            '\tif (codec_test::pacer_trace().enabled()) codec_test_pacer_id = codec_test::next_pacer_trace_id();\n'
            '#endif\n\tu_var_add_root(this, "Pacer", false);')
        mutant = temporary/'late-initialization.cpp'
        mutant.write_text(late)
        vfs = json.loads(mapping.read_text())
        vfs['roots'][0]['external-contents'] = str(mutant)
        mapping.write_text(json.dumps(vfs))
        mutant_object = temporary/'late-initialization.o'
        run('compile temporary negative-control constructor',
            [compiler, *flags, '-c', str(native/paths[0]), '-o', str(mutant_object)])
        mutant_executable = temporary/'late-constructor-test'
        run('link temporary negative control', [compiler, str(mutant_object), objects[1], str(cached_support),
            '-lcrypto', '-pthread', '-Wl,--gc-sections', '-Wl,--export-dynamic-symbol=pthread_create',
            '-o', str(mutant_executable)])
        env = dict(os.environ, WIVRN_CODEC_PACER_TRACE='relative.jsonl')
        run('negative control detects worker startup preceding trace error',
            [str(mutant_executable), 'trace-error'], env=env, timeout=10, expected=4)
    files = [Path(__file__), HERE/'constructor_test.cpp'] + [OVERLAY/p for p in paths]
    result = dict(schema=1, status='pass', checks=checks,
        sources_sha256={str(p.relative_to(ROOT)): sha(p) for p in files},
        compile_commands_sha256=sha(args.compile_commands), cached_support_sha256=sha(cached_support),
        cached_support_path=str(cached_support),
        underlying_native_sha256={p: sha(native/p) for p in paths if (native/p).exists()},
        native_constructor_executed=True, worker_threads_started=False, native_predictor_executed=False,
        native_sources_modified=False, linked_server_build=False, headset_operations=False,
        scope='Actual host constructor initialization/error ordering, using deterministic pthread_create EAGAIN injection and u_var/debug stubs.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(status='pass', checks=len(checks))))


if __name__ == '__main__':
    main()
