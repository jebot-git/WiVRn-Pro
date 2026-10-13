#!/usr/bin/env python3
"""Build matched local A/B artifacts without installing or uploading them."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
OUTPUT = SOURCE.parent / 'artifacts/audio-ab-20261007'
SDK = Path('/home/blux/Android/Sdk')
BUILD_TOOLS = SDK / 'build-tools/35.0.0'
VERSION_CODE = 2026100705  # Equal versions allow A/B/restore without uninstalling app data.


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    refs = json.loads((SOURCE / 'fork/audio-ab/upstream.json').read_text())
    for name, info in refs['files'].items():
        assert digest(SOURCE / name) == info['sha256'], name
    env = os.environ.copy()
    env['JAVA_HOME'] = '/home/blux/.local/share/entryway-toolchains/jdk-17.0.20.1+1'
    env['ANDROID_HOME'] = str(SDK)
    aux = SOURCE.parent.parent / 'wivrn-upstream/toolchain'
    env['PATH'] = ':'.join(map(str, [Path(env['JAVA_HOME']) / 'bin', SDK / 'ndk/29.0.14206865/shader-tools/linux-x86_64', aux / 'KTX-Software-4.4.2-Linux-x86_64/bin', aux / 'usr/bin'])) + ':' + env['PATH']
    env['LIBRARY_PATH'] = str(aux / 'lib')
    signing = Path('/home/blux/.local/share/wivrn-pro/meta')
    env['WIVRN_SIGNING_STORE'] = str(signing / 'wivrn-pro-release.p12')
    env['WIVRN_SIGNING_PASSWORD'] = (signing / 'signing-password').read_text().strip()

    def run(args, log):
        with (OUTPUT / log).open('w') as stream:
            result = subprocess.run(list(map(str, args)), cwd=SOURCE, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'{log} failed; inspect {OUTPUT / log}')

    artifacts = {}
    try:
        for variant, stock in [('A', 'ON'), ('B', 'OFF')]:
            print(f'Building {variant}: stock audio={stock}', flush=True)
            run(['./gradlew', '--offline', '--no-daemon', '--max-workers=4', 'assembleRelease',
                 '-Psuffix=.spatial', '-Pwivrn_app_name=WiVRn Pro Audio ' + variant,
                 '-Pwivrn_version=26.9-pro1-audio-' + variant.lower(), '-Pwivrn_versionCode=' + str(VERSION_CODE),
                 '-Pwivrn_meta_icon=1', '-Pwivrn_offline=1', '-Pwivrn_audio_ab_stock=' + stock,
                 '-Pfetchcontent_base_dir=' + str(SOURCE / 'build/android-deps')], variant + '-android-build.log')
            apk = OUTPUT / (variant + '.apk')
            shutil.copy2(SOURCE / 'build/android/outputs/apk/release/source-release.apk', apk)
            cert = subprocess.check_output([str(BUILD_TOOLS / 'apksigner'), 'verify', '--print-certs', str(apk)], env=env, text=True)
            assert '123e560aa78b2727ab3d9c36cfb48c9aeb800c728abba0e91423c7bae9b3afd5' in cert
            badging = subprocess.check_output([str(BUILD_TOOLS / 'aapt'), 'dump', 'badging', str(apk)], text=True)
            assert "name='org.meumeu.wivrn.spatial'" in badging
            assert f"versionCode='{VERSION_CODE}'" in badging
            assert f"versionName='26.9-pro1-audio-{variant.lower()}'" in badging
            subprocess.run([str(BUILD_TOOLS / 'zipalign'), '-c', '-P', '16', '4', str(apk)], check=True)
            (OUTPUT / (variant + '-apk-verification.txt')).write_text(cert + '\n' + badging.splitlines()[0] + '\n16 KB alignment passed\n')
            run(['cmake', '-S', '.', '-B', 'build/server', '-DWIVRN_AUDIO_AB_STOCK=' + stock], variant + '-server-configure.log')
            run(['cmake', '--build', 'build/server', '--target', 'wivrn-server', '--parallel', '4'], variant + '-server-build.log')
            server = OUTPUT / (variant + '-server')
            shutil.copy2(SOURCE / 'build/server/server/wivrn-pro-server', server)
            subprocess.run(['strip', '--strip-unneeded', str(server)], check=True)
            ldd = subprocess.check_output(['ldd', str(server)], text=True)
            assert 'not found' not in ldd
            (OUTPUT / (variant + '-dependencies.txt')).write_text(ldd)
            artifacts[variant] = {'apk': apk.name, 'apk_sha256': digest(apk), 'server': server.name, 'server_sha256': digest(server), 'version_code': VERSION_CODE, 'version_name': f'26.9-pro1-audio-{variant.lower()}'}
            print(f'{variant} built, signed and verified', flush=True)
    finally:
        # Leave normal server builds on the default upstream audio implementation.
        run(['cmake', '-S', '.', '-B', 'build/server', '-DWIVRN_AUDIO_AB_STOCK=ON'], 'restore-build-default.log')

    manifest = {'package': 'org.meumeu.wivrn.spatial', 'version_code': VERSION_CODE,
                'scope': 'Pinned upstream audio versus keep-alive in the same Pro base; not a whole-app upstream comparison.',
                'upstream': refs, 'artifacts': artifacts,
                'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip(),
                'source_has_uncommitted_changes': True, 'installed': False, 'uploaded': False}
    (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (OUTPUT / 'source.patch').write_text(subprocess.check_output(['git', 'diff', '--binary'], cwd=SOURCE, text=True))
    # Include untracked source files, including the exact upstream reference and fixes.
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=SOURCE).decode().split('\0')
    for name in filter(None, untracked):
        dest = OUTPUT / 'new-source-files' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, dest)
    print(f'Prepared artifacts: {OUTPUT}', flush=True)


if __name__ == '__main__':
    main()
