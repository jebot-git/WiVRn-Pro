#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package a native /usr Release build; run inside the target distribution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def elf(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open('rb') as f:
        return f.read(4) == b'\x7fELF'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('format', choices=['rpm', 'deb', 'arch'])
    parser.add_argument('build', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--version', default='26.9.1')
    parser.add_argument('--release', default='1')
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    build, output = args.build.resolve(), args.output.resolve()
    cache = (build / 'CMakeCache.txt').read_text()
    assert 'CMAKE_INSTALL_PREFIX:PATH=/usr\n' in cache, 'Configure with install prefix /usr'
    assert 'CMAKE_BUILD_TYPE:STRING=Release\n' in cache, 'Use a Release build'
    for flag in ['WIVRN_USE_NVENC', 'WIVRN_USE_VAAPI', 'WIVRN_USE_QSV',
                 'WIVRN_FEATURE_STEAMVR_LIGHTHOUSE', 'WIVRN_BUILD_DASHBOARD']:
        assert f'{flag}:BOOL=ON\n' in cache, f'{flag} must be enabled'
    output.mkdir(parents=True, exist_ok=True)
    work = build.parent / f'packaging-{args.format}'
    work.mkdir(exist_ok=True)
    stage = work / 'root'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    run('cmake', '--install', str(build), '--strip', env=dict(os.environ, DESTDIR=str(stage)))
    licenses = stage / 'usr/share/licenses/wivrn-pro'
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'COPYING', licenses / 'COPYING')
    shutil.copy2(source / 'LICENSE-OFL-1.1', licenses / 'LICENSE-OFL-1.1')
    # Monado is linked into our binaries, not supplied by a distro package.
    monado_line = next((line for line in cache.splitlines()
                        if line.startswith('FETCHCONTENT_SOURCE_DIR_MONADO:PATH=') and line.split('=', 1)[1]), None)
    monado = Path(monado_line.split('=', 1)[1]) if monado_line else build / '_deps/monado-src'
    shutil.copytree(monado / 'LICENSES', licenses / 'monado', dirs_exist_ok=True)
    shutil.copy2(monado / 'LICENSE', licenses / 'monado/LICENSE')
    docs = stage / 'usr/share/doc/wivrn-pro'
    docs.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'COPYING', docs / 'copyright')
    for name in ['fork/PORTABILITY.md', 'fork/STEAM-FRAME.md', 'docs/configuration.md', 'docs/building.md', 'fork/packaging/README.md']:
        shutil.copy2(source / name, docs / Path(name).name)
    (docs / 'build-info.json').write_text(json.dumps({
        'commit': args.commit, 'version': args.version, 'release': args.release,
        'os_release': Path('/etc/os-release').read_text(),
        'encoders': ['nvenc', 'vaapi', 'qsv', 'vulkan'] +
                    (['pyrowave'] if 'WIVRN_USE_PYROWAVE:BOOL=ON\n' in cache else []),
        'tracking_modes': ['slimevr', 'motoc'],
    }, indent=2) + '\n')
    # CMake's PROGRAMS installation does not strip the Rust executables.
    for path in stage.rglob('*'):
        if elf(path):
            run('strip', '--strip-unneeded', str(path))
            linked = run('ldd', str(path), capture_output=True).stdout
            assert 'not found' not in linked, f'Unresolved dependency in {path}:\n{linked}'
    manifest = stage / 'usr/share/openxr/1/openxr_wivrn_pro.json'
    runtime = json.loads(manifest.read_text())['runtime']['library_path']
    assert (manifest.parent / runtime).is_file(), f'Broken runtime manifest: {runtime}'
    for relative in ['usr/bin/wivrn-pro-server', 'usr/bin/wivrn-pro-dashboard',
                     'usr/bin/wivrn-pro-wayvrctl', 'usr/libexec/wivrn-pro/wayvr',
                     'usr/libexec/wivrn-pro/motoc', 'usr/lib/systemd/user/wivrn-pro.service']:
        assert (stage / relative).is_file(), f'Missing {relative}'
    assert len(list((stage / 'usr/share/wivrn-pro/encoder-presets/profiles').glob('*.json'))) == 8
    assert not (stage / 'usr/local').exists()
    if args.format == 'arch':
        # makepkg records .PKGINFO, .BUILDINFO and .MTREE using native tools.
        pkgbuild = f'''pkgname=wivrn-pro
pkgver={args.version}
pkgrel={args.release}
pkgdesc='WiVRn Pro standalone server, spatial anchors, tracking bridge, Motoc and WayVR'
arch=('x86_64')
url='https://github.com/jebot-git/WiVRn-Pro'
license=('GPL-3.0-or-later' 'MIT' 'Apache-2.0' 'BSL-1.0' 'OFL-1.1')
depends=('boost-libs' 'openssl' 'vulkan-icd-loader' 'ffmpeg' 'libbsd' 'avahi'
         'librsvg' 'libdrm' 'pipewire' 'libnotify' 'libarchive' 'systemd-libs'
         'wayland' 'libxrandr' 'libinput' 'alsa-lib' 'fontconfig' 'libxkbcommon'
         'libxkbcommon-x11' 'libxcb' 'libx11' 'libxext' 'python' 'pyside6'
         'openxr' 'openvr' 'hicolor-icon-theme')
optdepends=('steam: SteamVR Lighthouse driver for Motoc tracking'
            'vulkan-driver: GPU Vulkan implementation'
            'libva-mesa-driver: AMD VAAPI encoding'
            'intel-media-driver: Intel VAAPI encoding'
            'libvpl: Intel QSV dispatcher')
conflicts=('wivrn' 'wivrn-server' 'wivrn-dashboard')
options=('!debug' '!strip')
package() {{
    cp -a "$startdir/root/." "$pkgdir/"
}}
'''
        (work / 'PKGBUILD').write_text(pkgbuild)
        run('makepkg', '--force', '--nodeps', cwd=work,
            env=dict(os.environ, PKGDEST=str(output), PACKAGER='WiVRn Pro local build'))
    else:
        config = {
            'CPACK_GENERATOR': args.format.upper(),
            'CPACK_PACKAGE_NAME': 'wivrn-pro',
            'CPACK_PACKAGE_VERSION': args.version,
            'CPACK_PACKAGE_FILE_NAME': f'wivrn-pro-{args.version}-{args.release}-x86_64',
            'CPACK_SYSTEM_NAME': 'Linux',
            'CPACK_PACKAGE_CONTACT': 'WiVRn Pro contributors <noreply@localhost>',
            'CPACK_PACKAGE_HOMEPAGE_URL': 'https://github.com/jebot-git/WiVRn-Pro',
            'CPACK_PACKAGE_DESCRIPTION_SUMMARY': 'WiVRn Pro standalone VR server with tracking bridge, Motoc and WayVR',
            'CPACK_PACKAGE_DESCRIPTION': 'Quest Pro streaming with NVENC, VAAPI, QSV and Vulkan encoding; spatial anchors, exclusive SlimeVR or Motoc tracking, and a unified Qt dashboard.',
            'CPACK_PACKAGE_DIRECTORY': str(output),
            'CPACK_INSTALLED_DIRECTORIES': f'{stage};/',
            'CPACK_PACKAGING_INSTALL_PREFIX': '/',
        }
        if args.format == 'deb':
            config.update({
                'CPACK_DEBIAN_FILE_NAME': 'DEB-DEFAULT',
                'CPACK_DEBIAN_PACKAGE_RELEASE': args.release,
                'CPACK_DEBIAN_PACKAGE_SHLIBDEPS': 'ON',
                'CPACK_DEBIAN_PACKAGE_SECTION': 'games',
                'CPACK_DEBIAN_PACKAGE_DEPENDS': 'python3 (>= 3.11), python3-pyside6.qtwidgets, python3-pyside6.qtnetwork, python3-pyside6.qtdbus, libopenxr-loader1, libinput10, libxkbcommon-x11-0, pipewire, avahi-daemon, dbus-user-session',
                'CPACK_DEBIAN_PACKAGE_RECOMMENDS': 'libvpl2',
                'CPACK_DEBIAN_PACKAGE_CONFLICTS': 'wivrn, wivrn-server, wivrn-dashboard',
            })
        else:
            config.update({
                'CPACK_RPM_FILE_NAME': 'RPM-DEFAULT',
                'CPACK_RPM_PACKAGE_RELEASE': args.release,
                'CPACK_RPM_PACKAGE_LICENSE': 'GPL-3.0-or-later AND MIT AND Apache-2.0 AND BSL-1.0 AND OFL-1.1',
                'CPACK_RPM_PACKAGE_REQUIRES': 'python3 >= 3.11, python3-pyside6, openxr-libs, libinput, libxkbcommon-x11, pipewire, avahi',
                'CPACK_RPM_PACKAGE_CONFLICTS': 'wivrn, wivrn-server, wivrn-dashboard',
                'CPACK_RPM_EXCLUDE_FROM_AUTO_FILELIST_ADDITION': '/usr/libexec;/usr/share/licenses;/usr/lib/firewalld;/usr/lib/firewalld/services;/usr/share/openxr;/usr/share/openxr/1',
                'CPACK_RPM_SPEC_MORE_DEFINE': '%define _build_id_links none',
            })
        script = work / 'CPackConfig.cmake'
        script.write_text(''.join(f'set({key} [==[{value}]==])\n' for key, value in config.items()))
        run('cpack', '--config', str(script), cwd=work)
    for path in sorted(output.iterdir()):
        if path.is_file() and (path.name.endswith(('.deb', '.rpm', '.pkg.tar.zst'))):
            print(hashlib.sha256(path.read_bytes()).hexdigest(), path.name)


if __name__ == '__main__':
    main()
