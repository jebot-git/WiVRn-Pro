#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package a staged ARM64 /usr install with its matching native Steam runtime."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile


RUNTIME_VERSION = '4.0.20260805.254769'
RUNTIME_SHA256 = 'caa4b3bc3aad1cac43d94dbd802a963c13ed63ea1c414f04669ae402af505adf'
RUNTIME_URL = f'https://repo.steampowered.com/steamrt4/images/{RUNTIME_VERSION}/'


def require_arm64(path):
    with path.open('rb') as stream:
        header = stream.read(20)
    if header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\xb7\x00':
        raise ValueError(f'Expected a little-endian ARM64 ELF: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--runtime-archive', type=Path, required=True)
    parser.add_argument('--libktx', type=Path, help='Required when KTX is dynamically linked')
    parser.add_argument('--ktx-license', type=Path, help='KTX distribution license notices')
    parser.add_argument('--openxr-loader', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    with args.runtime_archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != RUNTIME_SHA256:
            raise ValueError('Steam Runtime archive does not match the pinned Valve SHA256')
    for binary in (args.stage / 'usr/bin/wivrn', args.openxr_loader):
        require_arm64(binary)
    if args.libktx:
        require_arm64(args.libktx)
        if not args.ktx_license or not args.ktx_license.is_file():
            parser.error('--libktx requires --ktx-license')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='frame-package-', dir=args.output.parent) as temp:
        bundle = Path(temp) / 'wivrn-pro-frame'
        shutil.copytree(args.stage, bundle, symlinks=True)
        lib = bundle / 'usr/lib'
        lib.mkdir(parents=True, exist_ok=True)
        for original, name in ((args.libktx, 'libktx.so.4'),
                               (args.openxr_loader, 'libopenxr_loader.so.1')):
            if original is None:
                continue
            target = lib / name
            if target.is_symlink():
                target.unlink()
            shutil.copy2(original, target)
        shutil.copy2(source / 'fork/packaging/launch-wivrn-pro', bundle / 'launch-wivrn-pro')
        shutil.copy2(source / 'fork/STEAM-FRAME.md', bundle / 'README.md')
        shutil.copy2(source / 'COPYING', bundle / 'COPYING')
        if args.ktx_license:
            shutil.copy2(args.ktx_license, bundle / ('KTX-LICENSE' + args.ktx_license.suffix))
        with tarfile.open(args.runtime_archive) as runtime:
            runtime.extractall(bundle, filter='data')
        runtime = bundle / 'SteamLinuxRuntime_4-arm64'
        require_arm64(runtime / 'pressure-vessel/bin/pressure-vessel-wrap')
        if 'export PRESSURE_VESSEL_ARCHITECTURES=aarch64-linux-gnu\n' not in (runtime / 'run').read_text():
            raise ValueError('Runtime must select an ARM64 userspace')
        (bundle / 'STEAM-RUNTIME-SOURCE.txt').write_text(
            f'Unmodified Valve Steam Runtime 4 ARM64 {RUNTIME_VERSION}\n'
            f'Archive: {RUNTIME_URL}SteamLinuxRuntime_4-arm64.tar.xz\n'
            f'SHA256: {RUNTIME_SHA256}\n'
            f'Corresponding source packages and manifests: {RUNTIME_URL}\n'
            f'Source package directory: {RUNTIME_URL}sources/\n'
            'Runtime copyright and license notices are retained in its usr/share/doc tree.\n')
        with tarfile.open(args.output, 'w:xz', preset=3) as archive:
            archive.add(bundle, arcname='wivrn-pro-frame')
    print(args.output)


if __name__ == '__main__':
    main()
