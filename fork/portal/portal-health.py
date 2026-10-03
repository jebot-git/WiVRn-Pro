#!/usr/bin/env python3
"""Recover KDE ScreenCast portals without restarting the desktop or VR runtime."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

BACKEND = 'plasma-xdg-desktop-portal-kde.service'
FRONTEND = 'xdg-desktop-portal.service'


def command(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, '', 'command timed out')


def owner(name):
    result = command('busctl', '--user', '--timeout=3', 'call',
                     'org.freedesktop.DBus', '/org/freedesktop/DBus',
                     'org.freedesktop.DBus', 'NameHasOwner', 's', name)
    return result.returncode == 0 and result.stdout.strip() == 'b true'


def healthy(name, interface):
    # Do not activate a service just by probing it.
    if not owner(name):
        return False
    result = command('busctl', '--user', '--timeout=3', '--auto-start=no',
                     'introspect', name, '/org/freedesktop/portal/desktop', interface)
    return result.returncode == 0


def decide(state, now, backend_ok, frontend_ok):
    """Two failing probes, cooldown and a rolling restart budget."""
    state['restarts'] = [t for t in state.get('restarts', []) if 0 <= now - t < 600]
    if backend_ok and frontend_ok:
        state['failures'] = 0
        return []
    state['failures'] = state.get('failures', 0) + 1
    if state['failures'] < 2:
        return []
    if state['restarts'] and now - state['restarts'][-1] < 120:
        return []
    if len(state['restarts']) >= 3:
        return []
    state['restarts'].append(now)
    state['failures'] = 0
    # Restart frontend after the backend so it exports the recovered interfaces.
    return [BACKEND, FRONTEND] if not backend_ok else [FRONTEND]


def main():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        return
    directory = Path(runtime) / 'wivrn-portal-health'
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / 'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # A VR runtime can outlive graphical login. Never start portals at logout.
        if command('systemctl', '--user', 'is-active', '--quiet', 'graphical-session.target').returncode:
            return
        if not owner('org.kde.KWin'):
            return
        state_path = directory / 'state.json'
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        backend_ok = healthy('org.freedesktop.impl.portal.desktop.kde',
                             'org.freedesktop.impl.portal.ScreenCast')
        frontend_ok = healthy('org.freedesktop.portal.Desktop',
                              'org.freedesktop.portal.ScreenCast')
        actions = decide(state, time.monotonic(), backend_ok, frontend_ok)
        state_path.write_text(json.dumps(state))
        for unit in actions:
            result = command('systemctl', '--user', 'restart', unit)
            print(f'Portal recovery: {unit}: {result.returncode} {result.stderr.strip()}', flush=True)
            if result.returncode:
                break
        if actions:
            restored = healthy('org.freedesktop.portal.Desktop', 'org.freedesktop.portal.ScreenCast')
            print(f'Portal recovery: ScreenCast available={restored}', flush=True)


if __name__ == '__main__':
    main()
