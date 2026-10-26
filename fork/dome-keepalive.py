#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run DomeD in its own service, preserving explicit stops and the active login."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def command(environment, context):
    program = environment.get('WIVRN_DOME_EXECUTABLE') or shutil.which('dome-compositor')
    if not program:
        return None
    backend = context.get('backend')
    if backend not in ('drm', 'nested'):
        backend = 'nested'
    # Never launch a graphical compositor before a user's desktop exists.
    if subprocess.run(['systemctl', '--user', '--quiet', 'is-active', 'graphical-session.target']).returncode:
        return None
    if backend == 'nested' and not (environment.get('WAYLAND_DISPLAY') or environment.get('DISPLAY')):
        return None
    environment['DOME_START_XR'] = '1'
    environment['DOME_LOGIN_SESSION'] = '0'
    environment['DOME_BACKEND'] = backend
    if backend == 'drm':
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET'):
            environment.pop(key, None)
    return [program, '--backend', backend]


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
    if stop.exists():
        return 0
    # A manual launch or the native login wrapper may already be recovering.
    reply = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.NameHasOwner', 'org.domed.Compositor'], capture_output=True, text=True)
    if reply.returncode == 0 and 'true' in reply.stdout:
        return 0
    try:
        context = json.loads((root / 'wivrn-context.json').read_text())
    except (OSError, ValueError):
        context = {}
    env = dict(os.environ)
    args = command(env, context)
    if args is None:
        return 0
    # The XR client must use this WiVRn runtime; never change the desktop's
    # selected global OpenXR runtime or an already running compositor's setup.
    manifest = Path(__file__).resolve().parents[1] / 'share/openxr/1/openxr_wivrn_pro.json'
    if manifest.is_file():
        env['XR_RUNTIME_JSON'] = str(manifest)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])
    os.execvpe(args[0], args, env)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('DomeD supervision:', error, file=sys.stderr)
        sys.exit(1)
