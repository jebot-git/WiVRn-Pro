#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install a systemd user unit pointing at this checkout (no root required)."""
import argparse
import os
from pathlib import Path
import sys

import catalog


def unit_argument(value):
    # ExecStart is parsed by systemd, not a shell. Escape both expansion syntaxes.
    value = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Control characters are not supported in service paths')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-dir', type=Path, default=Path(
        os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user',
        help='Destination directory; override to stage the unit for review')
    parser.add_argument('--catalog', type=Path, default=catalog.default_path())
    parser.add_argument('--runtime', type=Path, default=Path('/usr/share/openxr/1/openxr_wivrn.json'))
    parser.add_argument('--no-track', action='store_true', help='Serve saved/Quest data without the PC OpenXR helper')
    args = parser.parse_args()
    command = [Path(sys.executable).absolute(), '-u', root / 'bridge.py',
               '--catalog', args.catalog.expanduser().resolve()]
    if not args.no_track:
        helper = root / 'build/tracking-source'
        runtime = args.runtime.expanduser().resolve()
        if not helper.is_file() or not os.access(helper, os.X_OK) or not runtime.is_file():
            parser.error('Run ./build.sh and select an installed WiVRn runtime, or use --no-track')
        command += ['--track', '--helper', helper, '--runtime', runtime]
    template = (root / 'systemd/spatial-bridge.service.in').read_text()
    try:
        content = template.replace('@EXEC_START@', ' '.join(map(unit_argument, command)))
    except ValueError as error:
        parser.error(str(error))
    destination = args.unit_dir.expanduser().resolve() / 'spatial-bridge.service'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    print(f'Installed {destination}')
    print('Run: systemctl --user daemon-reload')
    print('Then: systemctl --user enable --now spatial-bridge.service')
    print('After updating a running service: systemctl --user restart spatial-bridge.service')


if __name__ == '__main__':
    main()
