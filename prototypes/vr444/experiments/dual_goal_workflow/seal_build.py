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
    apk_metadata = source/'build/android/outputs/apk/debug/output-metadata.json'
    apk_output = json.loads(apk_metadata.read_text())
    if (apk_output.get('applicationId') != 'org.meumeu.wivrn.codectest'
            or apk_output.get('artifactType', {}).get('type') != 'APK'
            or len(apk_output.get('elements', [])) != 1):
        raise ValueError('dedicated single APK output metadata required')
    apk_name = apk_output['elements'][0]['outputFile']
    if type(apk_name) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+\.apk', apk_name):
        raise ValueError('APK metadata output filename')
    gradle_apk = apk_metadata.parent/apk_name
    if sha(gradle_apk) != hashes[artifacts[2]]:
        raise ValueError('canonical acquisition APK differs from Gradle output')
    for path in (apk_metadata, gradle_apk): hashes[str(path.relative_to(source))] = sha(path)
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
    # The source-to-foveation auditor consumes the host shader actually embedded
    # in this build, independently of the Android submitted-image shader pair.
    host_cache = source/'build/codec-server/CMakeCache.txt'
    host_cache_text = host_cache.read_text()
    host_opt = re.findall(r'^WIVRN_OPTIMIZE_SHADERS:BOOL=(ON|OFF)$', host_cache_text, re.M)
    if len(host_opt) != 1:
        raise ValueError('host shader optimization selection missing')
    host_literal = source/'build/codec-server/server/foveation_rgb.spv'
    host_selected = host_literal.with_name(host_literal.name+('-opt' if host_opt[0] == 'ON' else '-nopt'))
    literal_text = host_literal.read_text()
    if not re.fullmatch(r'(?:\s*0x[0-9A-Fa-f]{1,8}\s*,)+\s*', literal_text):
        raise ValueError('invalid embedded host shader literal')
    host_decoded = b''.join(struct.pack('<I', int(word, 16)) for word in re.findall(r'0x[0-9A-Fa-f]+', literal_text))
    if host_decoded != host_selected.read_bytes() or host_decoded[:4] != b'\x03\x02\x23\x07':
        raise ValueError('embedded host shader differs from selected SPIR-V')
    for path in (host_cache, host_literal, host_selected, host_literal.parent/'wivrn-server_shaders.cpp'):
        hashes[str(path.relative_to(source))] = sha(path)
    host_shader = dict(path=str(host_selected.relative_to(source)), sha256=sha(host_selected),
        embedded_path=str(host_literal.relative_to(source)), embedded_sha256=sha(host_literal), literal_equivalent=True)
    monado_roots = re.findall(r'^FETCHCONTENT_SOURCE_DIR_MONADO:PATH=(.+)$', host_cache_text, re.M)
    if len(monado_roots) != 1 or not Path(monado_roots[0]).is_absolute():
        raise ValueError('explicit external Monado source path required')
    monado_root = Path(monado_roots[0]).resolve()
    monado_view_name = 'src/xrt/compositor/util/comp_swapchain.c'
    monado_view_path = monado_root/monado_view_name
    monado_view_hash = sha(monado_view_path)
    for name, expected in hashes.items():
        if sha(source/name) != expected:
            raise ValueError('file changed while sealing build: '+name)
    receipt = dict(schema=1, built_utc=datetime.now(timezone.utc).isoformat(), source=str(source),
        integration_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        native_base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(),
        sha256=dict(sorted(hashes.items())), absent_tracked_files=sorted(absent), symlinks=symlinks,
        overlay_files_verified=len(manifest), overlay_manifest_sha256=sha(INTEGRATION/'overlay-manifest.json'),
        renderer_shaders=shaders, build_results=results, production_package='org.meumeu.wivrn',
        foveation_shaders={'foveation_rgb.comp': host_shader},
        monado_source_view_contract=dict(source=str(monado_root), files={monado_view_name: monado_view_hash},
            scope='Pinned external Monado source used by isolated host build; native libraries separately bound'),
        external_artifacts_sha256={'/usr/bin/hello_xr': sha(Path('/usr/bin/hello_xr')),
                                  str(monado_view_path): monado_view_hash},
        dedicated_package='org.meumeu.wivrn.codectest', installed=False,
        apk_output=dict(gradle_path=str(gradle_apk.relative_to(source)), canonical_path=artifacts[2],
                        sha256=sha(gradle_apk), metadata_path=str(apk_metadata.relative_to(source))),
        scope='Built artifact and source identity; requires separate installed APK verification and live evidence.')
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2); stream.write('\n')
    print(json.dumps(dict(receipt=str(args.out), files=len(hashes), renderer_shaders=list(shaders))))


if __name__ == '__main__': main()
