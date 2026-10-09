#!/usr/bin/env python3
"""Bounded standalone builds/correctness only: never ADB, sockets, or native app."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def run(build, selected, output):
    here = Path(__file__).resolve().parent
    root = here.parents[3]
    need(build.is_absolute() and not build.exists(), 'fresh absolute build directory required')
    need(not build.is_relative_to(root), 'build must be outside worktree')
    need(type(selected) is int and 0 <= selected < 16, 'device index')
    output.mkdir(parents=True, exist_ok=True)
    native = root/'validation/wivrn-codecs/integration/source-overlay/client/decoder'
    sources = [*sorted(here.glob('*.cpp')), *sorted(here.glob('*.comp')),
               here/'build.sh', Path(__file__).resolve(), here/'README.txt',
               *sorted(native.glob('staged_rowblocks_image.*')),
               here.parent/'direct_image_gpu/gpu_oracle.hpp',
               here.parent/'direct_image_gpu/host_copy.hpp',
               here.parent/'rowblocks_gpu/decode.comp',
               root/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444/codec.hpp']
    before = {str(p.relative_to(root)): sha(p) for p in sources}
    commands = []

    def command(label, argv, timeout):
        started = time.monotonic()
        process = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True,
                                   env=os.environ | {'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
                                                     'UBSAN_OPTIONS': 'halt_on_error=1'})
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        out, err = output/(label+'.stdout'), output/(label+'.stderr')
        out.write_bytes(stdout)
        err.write_bytes(stderr)
        commands.append(dict(label=label, argv=argv, timeout_s=timeout,
                             elapsed_s=time.monotonic()-started, returncode=process.returncode, host_timeout=timed_out,
                             stdout_sha256=sha(out), stderr_sha256=sha(err)))
        need(process.returncode == 0 and not stderr and not timed_out, 'failed or diagnostic command: '+label)
        return stdout

    command('build', ['bash', str(here/'build.sh'), str(build)], 180)
    artifacts = sorted(p for p in build.rglob('*') if p.is_file())
    binaries = {str(p): sha(p) for p in artifacts}
    cpu = json.loads(command('cpu-asan', [str(build/'staged-image-test-asan'), '--cpu-only'], 30))
    need(cpu['pass'] is True and cpu['checks'] == 66 and cpu['gpu_used'] is False
         and cpu['headset_used'] is False and cpu['native_renderer_executed'] is False, 'CPU population/scope')
    shapes = [(1,1), (1,129), (17,33), (129,65), (4095,3), (4096,1), (1,4096), (576,640)]
    observations = []
    for label, name in [('gpu', 'staged-image-test'), ('gpu-asan', 'staged-image-test-asan')]:
        raw = command(label, [str(build/name), str(build/'shaders'), '--device', str(selected), '--full'], 120)
        rows = [json.loads(line) for line in raw.splitlines()]
        need(len(rows) == len(shapes)+1, 'GPU result population')
        need([(r['width'],r['height']) for r in rows[:-1]] == shapes, 'GPU shape population')
        for r in rows[:-1]:
            need(r['device_type'] in (1,2) and r['unorm_storage_srgb_linear_supported'] is True,
                 'hardware capability scope')
        result = rows[-1]
        need(result['pass'] is True and result['checks'] == 826 and result['gpu_used'] is True
             and result['headset_used'] is False and result['performance_claim'] is False
             and result['native_renderer_executed'] is False, 'GPU checks/scope')
        observations.append(dict(label=label, cases=rows[:-1], result=result))
    after = {str(p.relative_to(root)): sha(p) for p in sources}
    need(before == after, 'sources changed during qualification')
    need(binaries == {str(p): sha(p) for p in artifacts}, 'compiled artifacts changed')
    tools = {}
    ndk = Path(os.environ.get('ANDROID_NDK_HOME', '/home/blux/Android/Sdk/ndk/29.0.14206865'))
    for name, path in [('clang++', Path(shutil.which('clang++'))), ('glslc', Path(shutil.which('glslc'))),
                       ('android-clang++', ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++'),
                       ('spirv-val', Path(os.environ.get('SPIRV_VAL', str(ndk/'shader-tools/linux-x86_64/spirv-val'))))]:
        tools[name] = dict(path=str(path), sha256=sha(path), version=subprocess.check_output([str(path),'--version'], text=True).strip())
    receipt = dict(schema=1, status='pass', source_sha256=before, binary_sha256=binaries,
                   commands=commands, tools=tools, sources_unchanged=True, binaries_unchanged=True,
                   observations=observations, cpu=cpu, android_build_only=True,
                   headset_used=False, native_renderer_executed=False, performance_claim=False,
                   full_goal_qualified=False,
                   scope='Private borrowed-device staged image and first-consumer correctness; renderer integration and device runs pending')
    (output/'qualification.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ['status','sources_unchanged','android_build_only','headset_used','full_goal_qualified']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent/'results')
    args = parser.parse_args()
    run(args.build, args.device, args.output)
