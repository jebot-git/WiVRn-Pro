#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover an observed native DomeD login without opening a desktop window."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys


def native_owner_active(root):
    """The native seat lock remains authoritative when its D-Bus API is absent."""
    try:
        fd = os.open(root / 'native-owner.lock', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # An unreadable/unsafe ownership record never permits takeover.
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def command(environment, context):
    # A background watchdog cannot create a nested, focusable desktop window.
    # Old/unknown context also cannot authorize claiming a native DRM seat.
    if not isinstance(context, dict) or context.get('version') != 1 or context.get('backend') != 'drm':
        return None
    if environment.get('XDG_SESSION_DESKTOP', '').lower() != 'dome':
        return None
    program = environment.get('WIVRN_DOME_EXECUTABLE') or shutil.which('dome-compositor')
    if not program:
        return None
    if subprocess.run(['systemctl', '--user', '--quiet', 'is-active', 'graphical-session.target'], timeout=3).returncode:
        return None
    # Desktop mode, a hidden XR desktop and old context all default to no XR
    # launch. Never inherit a forced XR setting from the user-manager environment.
    environment['DOME_START_XR'] = '1' if context.get('xr_active') is True and context.get('shown') is True else '0'
    environment['DOME_LOGIN_SESSION'] = '0'
    environment['DOME_BACKEND'] = 'drm'
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET'):
        environment.pop(key, None)
    return [program, '--backend', 'drm']


def main():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        return 0
    root = Path(runtime) / 'dome'
    stop = root / 'explicit-stop'
    if sys.argv[1:] == ['--resume']:
        stop.unlink(missing_ok=True)
        return subprocess.call(['systemctl', '--user', 'start', 'wivrn-pro-dome.service'])
    if sys.argv[1:]:
        raise ValueError('Usage: wivrn-pro-dome [--resume]')
    if stop.exists() or native_owner_active(root):
        return 0
    # A bus outage or incomplete reply is not evidence that the desktop exited.
    try:
        reply = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.NameHasOwner', 'org.domed.Compositor'], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if reply.returncode != 0 or reply.stdout.strip() != '(false,)':
        return 0
    try:
        context = json.loads((root / 'wivrn-context.json').read_text())
    except (OSError, ValueError):
        return 0
    env = dict(os.environ)
    args = command(env, context)
    if args is None or stop.exists() or native_owner_active(root):
        return 0
    # This is only a fallback for an observed DomeD native login. Its own login
    # wrapper retains first chance to recover the original seat/process group.
    manifest = Path(__file__).resolve().parents[1] / 'share/openxr/1/openxr_wivrn_pro.json'
    if manifest.is_file():
        env['XR_RUNTIME_JSON'] = str(manifest)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])
    os.execvpe(args[0], args, env)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('DomeD supervision:', error, file=sys.stderr)
        sys.exit(1)
