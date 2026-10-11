#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only package smoke checks; no compositor, device or service startup."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path('/'))
args = parser.parse_args()
root = args.root.resolve()
for relative, marker in [('usr/bin/wivrn-pro-server', '--help'),
                         ('usr/libexec/wivrn-pro/wayvr', '--openxr'),
                         ('usr/libexec/wivrn-pro/motoc', 'anchor')]:
    program = root / relative
    result = subprocess.run([str(program), '--help'], check=True, text=True, capture_output=True)
    assert marker in result.stdout + result.stderr, relative
    linked = subprocess.check_output(['ldd', str(program)], text=True)
    assert 'not found' not in linked, linked
    dynamic = subprocess.check_output(['readelf', '-d', str(program)], text=True)
    assert '/build/' not in dynamic and '/home/' not in dynamic, 'Build-tree runtime search path'
manifest = root / 'usr/share/openxr/1/openxr_wivrn_pro.json'
library = manifest.parent / json.loads(manifest.read_text())['runtime']['library_path']
assert library.is_file()
ctypes.CDLL(str(library))
for name in ['wivrn-pro.service', 'wivrn-pro-dashboard.service']:
    assert (root / 'usr/lib/systemd/user' / name).is_file()
assert len(list((root / 'usr/share/wivrn-pro/encoder-presets/profiles').glob('*.json'))) == 8
assert (root / 'usr/share/licenses/wivrn-pro/motoc/LICENSE').is_file()
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(root / 'usr/share/wivrn-pro/dashboard'))
from PySide6 import QtCore, QtWidgets, QtNetwork, QtDBus
import wivrn_dashboard
import motoc_panel
import tracking_panel
app = QtWidgets.QApplication([])
print('PASS installed CLI, linked libraries, runtime manifest, runtime dlopen, Qt imports, services, profiles and licenses')
