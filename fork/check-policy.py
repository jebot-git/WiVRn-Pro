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
require('std::unique_ptr<wivrn::spatial_source> spatial' in read('client/application.h'), 'Spatial source lost application lifetime')
require('spatial->event(e.header)' in read('client/application.cpp'), 'Central spatial event dispatch removed')
require('spatial->event(' not in read('client/scenes/stream.cpp'), 'Duplicate spatial event dispatch returned')
require('case tab::spatial:' in read('client/scenes/lobby_gui.cpp'), 'Offline lobby capture entry removed')
require('wivrn-shora' in read('server/CMakeLists.txt'), 'Embedded Shora link removed')
require('shora_bridge shora;' in read('server/driver/wivrn_session.h'), 'Shora lifecycle owner removed')
require('wivrn-pro/' in read('patches/monado/9999-private-shora-bridge.patch'), 'Private Shora socket patch removed')
require('"questpro"' in read('build.gradle'), 'Quest Pro manifest target missing')
for preset in json.loads(read('CMakePresets.json'))['configurePresets']:
    for key, value in preset.get('cacheVariables', {}).items():
        require(not (key in ('WIVRN_USE_NVENC','WIVRN_USE_X264') and value == 'ON'), 'Forbidden preset: ' + key)
text = read('server/dist/wivrn-pro.service.in')
require('RuntimeDirectoryPreserve=yes' in text, 'Private socket directory must survive restarts')
require('tracking_worker' in read('server/main.cpp'), 'Integrated tracking worker owner removed')
require('wivrn-tracking' in read('server/shora/Cargo.toml'), 'Native tracking backend removed')
require(not (root / 'server/dist/wivrn-pro-shora.service.in').exists(), 'Standalone Shora service returned')
require('add_subdirectory(wayvr)' in read('server/CMakeLists.txt'), 'Bundled WayVR build removed')
require('openxr,wayland,x11' in read('server/wayvr/CMakeLists.txt') and '--no-default-features' in read('server/wayvr/CMakeLists.txt'), 'WayVR OpenXR-only build policy removed')
require('is_wayvr_application(c.application)' in read('server/main.cpp'), 'Duplicate WayVR autostart guard removed')
require('XR_RUNTIME_JSON' in read('server/wayvr_worker.cpp'), 'WayVR lost matching runtime binding')
require('WIVRN_MANAGED_WAYVR' in read('server/wayvr/upstream/wayvr/src/main.rs'), 'WayVR restart escaped worker supervisor')
require('KillMode=control-group' in text, 'Worker descendants must stop with the service')
if errors:
    sys.exit('\n'.join(errors))
print('Fork policy checks passed')
