#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install a system runtime and persistent device access for one WiVRn-Pro user.

Does not select a display manager, replace the active OpenXR runtime, restart
WiVRn, or change the user's WiVRn/WayVR configuration.
"""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile


def install_file(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    # Replacing an ELF atomically preserves already-loaded runtimes.
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with source.open('rb') as data:
                shutil.copyfileobj(data, stream)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), 0, 0)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def install_runtime(prefix, root):
    prefix = prefix.resolve(strict=True)
    source = prefix / 'share/openxr/1/openxr_wivrn_pro.json'
    manifest = json.loads(source.read_text())
    libraries = []
    for key in ('library_path', 'MND_libmonado_path'):
        value = manifest['runtime'].get(key)
        if not value:
            continue
        library = (source.parent / value).resolve(strict=True)
        if not library.is_relative_to(prefix) or not library.is_file():
            raise ValueError(f'Runtime library must be within the installed prefix: {library}')
        name = Path(value).name
        libraries.append((library, root / 'usr/local/lib/wivrn-pro' / name))
        manifest['runtime'][key] = '../../../lib/wivrn-pro/' + name
    if 'library_path' not in manifest['runtime']:
        raise ValueError('Runtime manifest has no library_path')
    for source_library, target in libraries:
        install_file(source_library, target, 0o755)
    with tempfile.TemporaryDirectory() as directory:
        updated = Path(directory) / 'manifest.json'
        updated.write_text(json.dumps(manifest, indent=2) + '\n')
        install_file(updated, root / 'usr/local/share/openxr/1/openxr_wivrn_pro.json')
    return [target for _, target in libraries]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--user', required=True, help='The single trusted streaming/desktop user')
    parser.add_argument('--destdir', type=Path, help='Stage files only; do not change accounts or services')
    args = parser.parse_args()
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0:
        parser.error('Use an unprivileged streaming user')
    if not args.destdir and os.geteuid() != 0:
        parser.error('System installation requires root; run this command with sudo')
    os.umask(0o022)
    root = args.destdir.resolve() if args.destdir else Path('/')
    libraries = install_runtime(args.prefix, root)
    with tempfile.TemporaryDirectory() as directory:
        rule = Path(directory) / 'rule'
        rule.write_text('# WiVRn-Pro pre-login virtual keyboard/mouse access.\n'
                        'SUBSYSTEM=="misc", KERNEL=="uinput", GROUP="wivrn-pro-input", MODE="0660", '
                        'OPTIONS+="static_node=uinput"\n')
        install_file(rule, root / 'etc/udev/rules.d/71-wivrn-pro-input.rules')
        rule.write_text('uinput\n')
        install_file(rule, root / 'etc/modules-load.d/wivrn-pro.conf')
    if args.destdir:
        print(f'Staged runtime, module and input rules at {root}; no host changes')
        return
    try:
        grp.getgrnam('wivrn-pro-input')
    except KeyError:
        subprocess.run(['groupadd', '--system', 'wivrn-pro-input'], check=True)
    groups = ['wivrn-pro-input']
    try:
        grp.getgrnam('render')
        groups.append('render')
    except KeyError:
        pass
    subprocess.run(['usermod', '-a', '-G', ','.join(groups), account.pw_name], check=True)
    subprocess.run(['modprobe', 'uinput'], check=True)
    subprocess.run(['udevadm', 'control', '--reload-rules'], check=True)
    subprocess.run(['udevadm', 'trigger', '--subsystem-match=misc', '--sysname-match=uinput'], check=True)
    subprocess.run(['udevadm', 'settle'], check=True)
    subprocess.run(['loginctl', 'enable-linger', account.pw_name], check=True)
    # Existing user-unit enablement is preserved. Boot setup remains explicit.
    print('Installed system runtime:', *libraries, sep='\n  ')
    print(f'Enabled lingering for {account.pw_name}. Enable wivrn-pro.service in that user manager. '
          'Reboot to give its manager the new device groups; WiVRn was not restarted.')
    print('Re-run this installer after updating the user-prefix runtime to keep both copies matched.')


if __name__ == '__main__':
    main()
