#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check WiVRn-Pro host access as the streaming user, without creating XR/input devices."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


def command(argv, timeout=8, env=None):
    if not shutil.which(argv[0]):
        raise RuntimeError(f"Install {argv[0]} to check this capability")
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(f"{' '.join(argv)} failed: {(result.stderr or result.stdout).strip()[-600:]}")
    return result.stdout


def open_device(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode):
            raise RuntimeError(f"{path} is not a character device")
    finally:
        os.close(fd)
    return info


def persistent_access(info):
    groups = set(os.getgroups()) | {os.getgid()}
    return bool(info.st_mode & stat.S_IWOTH or
                info.st_gid in groups and info.st_mode & stat.S_IWGRP or
                info.st_uid == os.getuid() and info.st_mode & stat.S_IWUSR)


def runtime_check(manifest, system=False, matching=None):
    manifest = manifest.resolve(strict=True)
    if system and (manifest.stat().st_uid != 0 or manifest.stat().st_mode & 0o022):
        raise RuntimeError(f"LightXR needs a root-owned, non-writable manifest: {manifest}")
    runtime = json.loads(manifest.read_text())['runtime']
    for key in ('library_path', 'MND_libmonado_path'):
        if key not in runtime:
            continue
        library = (manifest.parent / runtime[key]).resolve(strict=True)
        if system and (library.stat().st_uid != 0 or library.stat().st_mode & 0o022):
            raise RuntimeError(f"System runtime library is user-writable: {library}")
        ctypes.CDLL(str(library))
        if matching:
            reference = json.loads(matching.read_text())['runtime'][key]
            reference = (matching.parent / reference).resolve(strict=True)
            if hashlib.sha256(library.read_bytes()).digest() != hashlib.sha256(reference.read_bytes()).digest():
                raise RuntimeError('System runtime differs from the user installation; re-run wivrn-pro-install-system')
    return str(manifest)


def gpu_check():
    nodes = sorted(Path('/dev/dri').glob('renderD*'))
    accessible = []
    for node in nodes:
        try:
            if persistent_access(open_device(node)):
                accessible.append(str(node))
        except OSError:
            pass
    if not accessible:
        raise RuntimeError('No DRM render node with persistent access; check GPU driver and render-group access')
    env = dict(os.environ)
    # Prove Vulkan works without a logged-in desktop or window-system connection.
    for key in ('DISPLAY', 'WAYLAND_DISPLAY'):
        env.pop(key, None)
    output = command(['vulkaninfo', '--summary'], env=env)
    types = re.findall(r'deviceType\s*=\s*(\S+)', output)
    if not any(kind in ('PHYSICAL_DEVICE_TYPE_DISCRETE_GPU', 'PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU',
                        'VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU', 'VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU')
               for kind in types):
        raise RuntimeError('Vulkan did not enumerate a hardware GPU (software rendering is insufficient)')
    return ', '.join(accessible) + '; hardware Vulkan available (encoder selection is checked on headset connection)'


def pipewire_check():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    info = runtime.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError(f'Invalid user runtime directory: {runtime}')
    command(['pw-cli', 'info', '0'])  # Connects to the real server; socket activation is supported.
    command(['systemctl', '--user', 'is-active', 'wireplumber.service'])
    return 'PipeWire responds and WirePlumber is active'


def input_check():
    info = open_device('/dev/uinput')
    if not persistent_access(info):
        raise RuntimeError('/dev/uinput opens through an ACL only; pre-login access is not assured. '
                           'Run wivrn-pro-install-system --user USER and reboot for group membership')
    return '/dev/uinput opens with persistent owner/group access'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] /
                        'share/openxr/1/openxr_wivrn_pro.json')
    parser.add_argument('--system', action='store_true', help='Also verify the LightXR system runtime')
    parser.add_argument('--startup', action='store_true', help='Also check a system runtime if installed')
    args = parser.parse_args()
    checks = [('runtime', lambda: runtime_check(args.manifest)),
              ('GPU', gpu_check), ('PipeWire', pipewire_check), ('input', input_check)]
    system_manifest = Path('/usr/local/share/openxr/1/openxr_wivrn_pro.json')
    if args.system or args.startup and system_manifest.exists():
        checks.append(('system runtime', lambda: runtime_check(
            system_manifest, True, args.manifest)))
    failed = 0
    for name, check in checks:
        try:
            print(f'WiVRn-Pro {name}: OK: {check()}', flush=True)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            failed += 1
            print(f'WiVRn-Pro {name}: UNAVAILABLE: {error}', flush=True)
    return int(failed != 0)


if __name__ == '__main__':
    raise SystemExit(main())
