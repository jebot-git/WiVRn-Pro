#!/usr/bin/env python3
"""Bind the dedicated build, full native source population and rendered shaders."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[4]
INTEGRATION = ROOT/'validation/wivrn-codecs/integration'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--host-result', type=Path, required=True)
    p.add_argument('--android-result', type=Path, required=True)
    p.add_argument('--oracle-result', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    source = args.source.resolve()
    if args.out.exists():
        raise ValueError('receipt already exists')
    results = {}
    for kind, path in (('host', args.host_result), ('android', args.android_result), ('oracle', args.oracle_result)):
        value = json.loads(path.read_text())
        if value['returncode'] != 0:
            raise ValueError('unsuccessful build: '+kind)
        results[kind] = {'path': str(path.resolve()), 'sha256': sha(path), 'result': value}
    manifest = json.loads((INTEGRATION/'overlay-manifest.json').read_text())
    hashes = {}
    for name, row in manifest.items():
        hashes[name] = sha(source/name)
        if hashes[name] != row['after_sha256'] or hashes[name] != sha(INTEGRATION/'source-overlay'/name):
            raise ValueError('overlay/native source mismatch: '+name)
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=source).split(b'\0')
    absent, symlinks = [], {}
    for raw in files:
        if not raw:
            continue
        name = raw.decode(); path = source/name
        if path.is_file():
            hashes[name] = sha(path)
            if path.is_symlink(): symlinks[name] = str(path.readlink())
        elif not path.exists():
            absent.append(name)
    artifacts = ('build/codec-server/server/wivrn-pro-server', 'build/codec-server/openxr_wivrn-dev.json',
                 'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk', 'build/codec-server/rrb-reference')
    for name in artifacts:
        hashes[name] = sha(source/name)
    runtime = json.loads((source/'build/codec-server/openxr_wivrn-dev.json').read_text())['runtime']
    for key in ('library_path', 'MND_libmonado_path'):
        path = Path(runtime[key])
        if not path.resolve().is_relative_to(source):
            raise ValueError('runtime library escapes isolated build')
        hashes[str(path.relative_to(source))] = sha(path)
    if hashes[artifacts[-1]] != results['oracle']['result']['binary_sha256']:
        raise ValueError('reference executable changed')
    for name, expected in results['oracle']['result']['source_sha256'].items():
        if sha(Path(name)) != expected:
            raise ValueError('reference build input changed: '+name)
    caches = list((source/'.cxx/Debug').glob('*/arm64-v8a/CMakeCache.txt'))
    if len(caches) != 1:
        raise ValueError('one exact Android CMake output required')
    cache = caches[0]; cache_text = cache.read_text()
    if 'WIVRN_CODEC_TESTS:BOOL=ON\n' not in cache_text:
        raise ValueError('dedicated test build required')
    optimization = re.findall(r'^WIVRN_OPTIMIZE_SHADERS:BOOL=(ON|OFF)$', cache_text, re.M)
    if len(optimization) != 1:
        raise ValueError('shader optimization selection missing')
    shaders = {}
    for name in ('reprojection.vert', 'reprojection.frag'):
        literal = cache.parent/'client'/(name+'.spv')
        selected = literal.with_name(literal.name+('-opt' if optimization[0] == 'ON' else '-nopt'))
        text = literal.read_text()
        if not re.fullmatch(r'(?:\s*0x[0-9A-Fa-f]{1,8}\s*,)+\s*', text):
            raise ValueError('invalid embedded shader literal')
        decoded = b''.join(struct.pack('<I', int(word, 16)) for word in re.findall(r'0x[0-9A-Fa-f]+', text))
        if decoded != selected.read_bytes() or decoded[:4] != b'\x03\x02\x23\x07':
            raise ValueError('embedded shader differs from selected SPIR-V')
        for path in (literal, selected):
            hashes[str(path.relative_to(source))] = sha(path)
        shaders[name] = dict(path=str(selected.relative_to(source)), sha256=sha(selected),
            embedded_path=str(literal.relative_to(source)), embedded_sha256=sha(literal), literal_equivalent=True)
    for path in (cache, cache.parent/'client/wivrn_shaders.cpp', source/'cmake/CompileGLSL.cmake'):
        hashes[str(path.relative_to(source))] = sha(path)
    for name, expected in hashes.items():
        if sha(source/name) != expected:
            raise ValueError('file changed while sealing build: '+name)
    receipt = dict(schema=1, built_utc=datetime.now(timezone.utc).isoformat(), source=str(source),
        integration_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        native_base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(),
        sha256=dict(sorted(hashes.items())), absent_tracked_files=sorted(absent), symlinks=symlinks,
        overlay_files_verified=len(manifest), overlay_manifest_sha256=sha(INTEGRATION/'overlay-manifest.json'),
        renderer_shaders=shaders, build_results=results, production_package='org.meumeu.wivrn',
        external_artifacts_sha256={'/usr/bin/hello_xr': sha(Path('/usr/bin/hello_xr'))},
        dedicated_package='org.meumeu.wivrn.codectest', installed=False,
        scope='Built artifact and source identity; requires separate installed APK verification and live evidence.')
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2); stream.write('\n')
    print(json.dumps(dict(receipt=str(args.out), files=len(hashes), renderer_shaders=list(shaders))))


if __name__ == '__main__': main()
