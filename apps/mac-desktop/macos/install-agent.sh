#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")"
source_app="${1:-build/Mac Desktop Relay.app}"
# Preserve the existing app path and signing identifier for macOS privacy grants.
app="$HOME/Applications/Mac Desktop Relay.app"
if [[ -d "$HOME/Applications/WiVRn-Pro Mac Desktop.app" ]]; then
    app="$HOME/Applications/WiVRn-Pro Mac Desktop.app"
fi
label=local.mac-desktop-relay
plist="$HOME/Library/LaunchAgents/$label.plist"
uid_value="$(id -u)"
mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/Mac Desktop Relay"
/bin/launchctl bootout "gui/$uid_value/$label" 2>/dev/null || true
# Stop only the installed companion, including the former foreground instance.
for pid in $(/usr/bin/pgrep -x MacDesktop || true); do
    if [[ "$(/bin/ps -p "$pid" -o comm=)" == "$app/Contents/MacOS/MacDesktop" ]]; then
        kill -TERM "$pid"
        for attempt in {1..30}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
        if kill -0 "$pid" 2>/dev/null; then
            printf 'Companion did not exit; installation stopped.\n' >&2
            exit 1
        fi
    fi
done
/usr/bin/ditto "$source_app" "$app"
umask 077
/usr/bin/plutil -create xml1 "$plist"
/usr/bin/plutil -insert Label -string "$label" "$plist"
/usr/bin/plutil -insert ProgramArguments -json '[]' "$plist"
/usr/bin/plutil -insert ProgramArguments.0 -string "$app/Contents/MacOS/MacDesktop" "$plist"
/usr/bin/plutil -insert ProgramArguments.1 -string '--background' "$plist"
/usr/bin/plutil -insert RunAtLoad -bool true "$plist"
/usr/bin/plutil -insert KeepAlive -json '{"SuccessfulExit":false}' "$plist"
/usr/bin/plutil -insert LimitLoadToSessionType -string Aqua "$plist"
/usr/bin/plutil -insert ThrottleInterval -integer 10 "$plist"
/usr/bin/plutil -insert StandardOutPath -string "$HOME/Library/Logs/Mac Desktop Relay/agent.log" "$plist"
/usr/bin/plutil -insert StandardErrorPath -string "$HOME/Library/Logs/Mac Desktop Relay/agent.log" "$plist"
chmod 600 "$plist"
/bin/launchctl enable "gui/$uid_value/$label"
/bin/launchctl bootstrap "gui/$uid_value" "$plist"
printf 'Installed login agent: %s\nCompanion: %s\n' "$label" "$app"
