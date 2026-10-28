#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Route WiVRn user services to a system installation without restarting them."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path('/usr/local'))
    parser.add_argument('--destdir', type=Path, help='Stage overrides only, without changing host services')
    args = parser.parse_args()
    if not args.prefix.is_absolute() or any(c in str(args.prefix) for c in '\n\r\0'):
        parser.error('Installation prefix must be an absolute path without control characters')
    if not args.destdir and os.geteuid() != 0:
        parser.error('Use --destdir to stage as a regular user, or sudo to install system files')
    root = args.destdir.resolve() if args.destdir else Path('/')
    def path(relative):
        return json.dumps(str(args.prefix / relative).replace('%', '%%'), ensure_ascii=False)
    commands = {
        'wivrn-pro': ['ExecStartPre=', 'ExecStartPre=-'+path('bin/wivrn-pro-check')+' --startup --manifest '+path('share/openxr/1/openxr_wivrn_pro.json'), 'ExecStart=', 'ExecStart='+path('bin/wivrn-pro-server')],
        'wivrn-pro-dome': ['ExecStart=', 'ExecStart='+path('bin/wivrn-pro-dome')],
        'wivrn-pro-dashboard': ['ExecCondition=', 'ExecCondition='+path('bin/wivrn-pro-server')+' --desktop-autostart-allowed', 'ExecStart=', 'ExecStart='+path('bin/wivrn-pro-dashboard')],
    }
    for name, lines in commands.items():
        target = root / 'etc/systemd/user' / (name+'.service.d') / '90-wivrn-pro-system.conf'
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as stream:
            temp = Path(stream.name)
            try:
                stream.write('# Use the root-owned system installation, including over legacy user units.\n[Service]\n'+'\n'.join(lines)+'\n')
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        print(target)
    print('Service enablement and running processes unchanged; run systemctl --user daemon-reload after installation.')


if __name__ == '__main__':
    main()
