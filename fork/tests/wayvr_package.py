#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Staged package/CLI checks only; never initialize OpenXR or a compositor."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
cache = (root/'build/server/CMakeCache.txt').read_text().splitlines()
install = Path(next(line.split('=',1)[1] for line in cache if line.startswith('CMAKE_INSTALL_PREFIX:PATH=')))
prefix = root/'build/package-wayvr'/install.relative_to('/')
worker = prefix/'libexec/wivrn-pro/wayvr'
assert worker.is_file() and worker.stat().st_mode & 0o111
help_ = subprocess.check_output([str(worker), '--help'], text=True)
assert '--openxr' in help_ and '--openvr' not in help_
libs = subprocess.check_output(['ldd', str(worker)], text=True)
assert 'not found' not in libs and 'openvr_api' not in libs
assert (prefix/'share/licenses/wivrn-pro/wayvr/LICENSE').is_file()
assert (prefix/'share/wivrn-pro/dashboard/wivrn_dashboard.py').read_bytes() == (root/'dashboard/shora/wivrn_dashboard.py').read_bytes()
if 'WIVRN_FEATURE_STEAMVR_LIGHTHOUSE:BOOL=ON' in cache:
    motoc = prefix/'libexec/wivrn-pro/motoc'
    assert motoc.is_file() and motoc.stat().st_mode & 0o111
    assert 'anchor' in subprocess.check_output([str(motoc), '--help'], text=True)
    assert (prefix/'share/licenses/wivrn-pro/motoc/LICENSE').is_file()
assert (prefix/'share/wivrn-pro/dashboard/motoc_panel.py').is_file()
assert len(list((prefix/'share/wivrn-pro/encoder-presets/profiles').glob('*.json'))) >= 8
unit = (prefix/'lib/systemd/user/wivrn-pro.service').read_text()
assert 'KillMode=control-group' in unit and 'wayvr.service' in unit
with tempfile.TemporaryDirectory(prefix='wivrn-wayvr-unit-') as directory:
    path = Path(directory)/'wivrn-pro.service'
    path.write_text(unit.replace(str(install/'bin/wivrn-pro-server'),str(prefix/'bin/wivrn-pro-server')))
    subprocess.run(['systemd-analyze','--user','verify',str(path)], check=True)
print('PASS staged upstream WayVR binary, OpenXR-only CLI, linked libraries, Qt modules, licenses and systemd unit')
