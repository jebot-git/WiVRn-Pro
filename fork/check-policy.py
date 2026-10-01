#!/usr/bin/env python3
"""Merge guard for the intentionally narrow WiVRn Pro product scope."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import json
import re
import sys
root = Path(__file__).resolve().parent.parent
errors = []
def require(condition, message):
    if not condition:
        errors.append(message)
def read(path):
    return (root / path).read_text()
for stem in ('video_encoder_nvenc', 'video_encoder_x264', 'video_encoder_raw'):
    require(not list((root / 'server/encoder').glob(stem + '*')), 'Removed encoder returned: ' + stem)
for path in ('client/android/usb.cpp', 'client/android/usb.h', 'client/java/NetworkInfoCallback.java',
             'dashboard/adb.cpp', 'dashboard/apk_installer.cpp', 'dashboard/qml/ConnectUsbDialog.qml'):
    require(not (root / path).exists(), 'Removed USB implementation returned: ' + path)
for directory in ('client', 'dashboard'):
    for path in (root / directory).rglob('*'):
        if path.suffix in ('.cpp', '.h', '.java', '.qml'):
            require(not re.search(r'set_usb_networking|startUsbConnection|NetworkInfoCallback|Settings\.(Nvenc|X264)|ApkInstaller|\bAdb\.', path.read_text()), 'Removed API reference: ' + str(path.relative_to(root)))
require('wivrn::video_codec::av1,' not in read('client/decoder/android/android_decoder.cpp'), 'AV1 advertised by Quest Pro')
require('res.push_back(wivrn::video_codec::raw)' not in read('client/decoder/decoder.cpp'), 'Raw codec advertised')
require('fork_policy::intel_arc' in read('server/utils/wivrn_vk_bundle.cpp'), 'GPU selection lost Arc gate')
require('require_wifi(control.get_fd())' in read('client/wivrn_client.cpp'), 'Wi-Fi route check removed')
require('fork_policy::quest_pro' in read('client/hmd_traits.cpp'), 'Client lost Quest Pro gate')
require('info_packet.client_model != "seacliff"' in read('server/driver/wivrn_connection.cpp'), 'Server lost Quest Pro handshake gate')
require('WIVRN_SPATIAL_BRIDGE' not in read('server/driver/spatial_bridge.cpp'), 'Spatial API became optional')
require('wivrn-shora' in read('server/CMakeLists.txt'), 'Embedded Shora link removed')
require('shora_bridge shora;' in read('server/driver/wivrn_session.h'), 'Shora lifecycle owner removed')
require('wivrn-pro/' in read('patches/monado/9999-private-shora-bridge.patch'), 'Private Shora socket patch removed')
require('"questpro"' in read('build.gradle'), 'Quest Pro manifest target missing')
for preset in json.loads(read('CMakePresets.json'))['configurePresets']:
    for key, value in preset.get('cacheVariables', {}).items():
        require(not (key in ('WIVRN_USE_NVENC','WIVRN_USE_X264') and value == 'ON'), 'Forbidden preset: ' + key)
for name in ('wivrn-pro', 'wivrn-pro-shora'):
    text = read('server/dist/' + name + '.service.in')
    require('RuntimeDirectoryPreserve=yes' in text, 'Shared runtime directory must survive independent restarts')
    require('Requires=' not in text and 'BindsTo=' not in text and 'PartOf=' not in text, 'Backend restart must not stop WiVRn')
if errors:
    sys.exit('\n'.join(errors))
print('Fork policy checks passed')
