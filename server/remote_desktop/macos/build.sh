#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")"
app="${1:-build/WiVRn-Pro Mac Desktop.app}"
mkdir -p "$app/Contents/MacOS"
xcrun clang -fobjc-arc -fblocks -O2 -Wall -Wextra -Wno-unused-parameter \
  -mmacosx-version-min=14.0 main.m -o "$app/Contents/MacOS/MacDesktop" \
  -framework Cocoa -framework ScreenCaptureKit -framework CoreImage \
  -framework CoreMedia -framework CoreVideo -framework ApplicationServices \
  -framework Security -framework ImageIO
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>io.github.wivrn.pro.macdesktop</string>
<key>CFBundleName</key><string>WiVRn-Pro Mac Desktop</string>
<key>CFBundleExecutable</key><string>MacDesktop</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSScreenCaptureUsageDescription</key><string>Show your dedicated virtual desktop in WiVRn-Pro on your headset.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - --identifier io.github.wivrn.pro.macdesktop "$app"
printf 'Built %s\n' "$app"
