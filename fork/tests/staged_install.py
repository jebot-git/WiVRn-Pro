#!/usr/bin/env python3
"""Check staged installations and parse units; never install or activate a service."""
from pathlib import Path
import subprocess, tempfile
root = Path(__file__).resolve().parents[2]
cache = (root/'build/server/CMakeCache.txt').read_text().splitlines()
install_prefix = Path(next(line.split('=',1)[1] for line in cache if line.startswith('CMAKE_INSTALL_PREFIX:PATH=')))
for variant in ('off', 'on'):
    stage = root/f'build/package-integrated-{variant}'
    prefix = stage/install_prefix.relative_to('/')
    binary = prefix/'bin/wivrn-pro-server'
    assert binary.stat().st_mode & 0o111
    assert (prefix/'bin/wivrn-pro-dashboard').stat().st_mode & 0o111
    modules = prefix/'share/wivrn-pro/dashboard'
    assert (modules/'wivrn_dashboard.py').read_bytes() == (root/'dashboard/shora/wivrn_dashboard.py').read_bytes()
    assert not list(prefix.rglob('wivrn-pro-shora.service'))
    for name in ('SlimeTora-MIT.txt', 'SlimeVR-MIT.txt', 'oscavmgr-MIT.txt', 'haritorax-interpreter-MIT.txt', 'THIRD_PARTY.md', 'solarxr/LICENSE-MIT'):
        assert (prefix/'share/licenses/wivrn-pro/shora'/name).exists(), name
    with tempfile.TemporaryDirectory(prefix='wivrn-unit-check-') as tmp:
        unit = Path(tmp)/'wivrn-pro.service'
        text = (prefix/'lib/systemd/user/wivrn-pro.service').read_text()
        text = text.replace('ExecStart='+str(install_prefix/'bin/wivrn-pro-server'), 'ExecStart='+str(binary))
        unit.write_text(text)
        subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True)
    print('PASS staged', variant, 'package: executable, Qt modules, single verified unit, licenses')
