#!/usr/bin/env python3
"""Install user-level KDE safeguards; --root stages files without activation."""
import argparse
from pathlib import Path
import shutil
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, help='Stage under a mock home without activating services')
args = parser.parse_args()
home = args.root or Path.home()
source = Path(__file__).resolve().parent
unitdir = home / '.config/systemd/user'
files = {
    home / '.local/libexec/wivrn-portal-health.py': source / 'portal-health.py',
    unitdir / 'wivrn-portal-health.service': source / 'wivrn-portal-health.service',
    unitdir / 'wivrn-portal-health.timer': source / 'wivrn-portal-health.timer',
}
for service in ['plasma-xdg-desktop-portal-kde.service', 'xdg-desktop-portal.service']:
    files[unitdir / (service + '.d') / '90-wivrn-recovery.conf'] = source / 'restart.conf'
for dest, src in files.items():
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != src.read_bytes():
        shutil.copy2(dest, dest.with_name(dest.name + '.backup-' + str(time.time_ns())))
    shutil.copy2(src, dest)
if not args.root:
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'wivrn-portal-health.timer'], check=True)
print('Staged portal safeguards' if args.root else 'Installed portal safeguards; health timer enabled')
