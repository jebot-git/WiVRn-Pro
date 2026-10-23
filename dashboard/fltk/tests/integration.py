#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private D-Bus, simulated Shora and real FLTK window integration; no live services."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

signal.pthread_sigmask(signal.SIG_SETMASK, [])

if '--private-bus' not in sys.argv:
    raise SystemExit(subprocess.call(['dbus-run-session', '--', sys.executable, __file__, '--private-bus', *sys.argv[1:]]))
sys.argv.remove('--private-bus')
BUILD = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else Path('build-dashboard-fltk').resolve()
from gi.repository import Gio, GLib

CONFIG = {'encoder': [{'encoder': 'vaapi', 'codec': 'h265'}], 'unknown-future': {'preserve': [1, 2, 3]}}
BONES = [{'body_part': 2, 'head': [0, 1.6, 0], 'tail': [0, 1, 0], 'tracked': True},
         {'body_part': 3, 'head': [0, 1, 0], 'tail': [-0.2, 0.05, 0], 'tracked': False}]

class Services:
    def __init__(self, directory):
        self.directory = directory
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.config = CONFIG.copy()
        self.calls = []
        self.registered = []
        self.items = []
        xml = '''<node><interface name="io.github.wivrn.Server">
        <property name="JsonConfiguration" type="s" access="readwrite"/>
        <property name="HeadsetConnected" type="b" access="read"/>
        <property name="ClientModel" type="s" access="read"/>
        <property name="KnownKeysJson" type="s" access="read"/>
        <property name="WayVRStatus" type="s" access="read"/>
        <method name="EnablePairing"><arg type="i" direction="in"/></method>
        <method name="Disconnect"/><method name="RestartWayVR"/>
        </interface></node>'''
        self.add('/io/github/wivrn/Server', xml)
        xml = '''<node><interface name="org.kde.StatusNotifierWatcher">
        <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
        <method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
        <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
        <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
        <property name="ProtocolVersion" type="i" access="read"/>
        <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
        </interface></node>'''
        self.add('/StatusNotifierWatcher', xml)
        for name in ('io.github.wivrn.Server', 'org.kde.StatusNotifierWatcher'):
            self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (name, 4)), None, Gio.DBusCallFlags.NONE, 3000, None)
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()
        self.socket_path = str(directory / 'ShoraControl')
        server = socket.socket(socket.AF_UNIX)
        server.bind(self.socket_path)
        server.listen()
        self.commands = []
        self.server = server
        threading.Thread(target=self.accept, daemon=True).start()

    def add(self, path, xml):
        info = Gio.DBusNodeInfo.new_for_xml(xml)
        self.registered.append(self.connection.register_object(path, info.interfaces[0], self.method, self.get, self.set))

    def method(self, connection, sender, path, interface, name, args, reply):
        self.calls.append((name, args.unpack()))
        if name == 'RegisterStatusNotifierItem':
            requested = args.unpack()[0]
            item = sender + requested if requested.startswith('/') else requested + '/StatusNotifierItem'
            self.items.append(item)
            connection.emit_signal(None, '/StatusNotifierWatcher', 'org.kde.StatusNotifierWatcher', 'StatusNotifierItemRegistered', GLib.Variant('(s)', (item,)))
        reply.return_value(None)

    def get(self, connection, sender, path, interface, name):
        values = {'JsonConfiguration': ('s', json.dumps(self.config)), 'HeadsetConnected': ('b', True),
                  'ClientModel': ('s', 'quest-pro'), 'KnownKeysJson': ('s', '[]'), 'WayVRStatus': ('s', 'running'),
                  'RegisteredStatusNotifierItems': ('as', self.items), 'IsStatusNotifierHostRegistered': ('b', True), 'ProtocolVersion': ('i', 0)}
        return GLib.Variant(*values[name])

    def set(self, connection, sender, path, interface, name, value):
        self.config = json.loads(value.unpack())
        return True

    def accept(self):
        while True:
            try: client, _ = self.server.accept()
            except OSError: return
            threading.Thread(target=self.session, args=(client,), daemon=True).start()

    def session(self, client):
        settings = {'height_m': 1.8, 'smoothing': .3, 'prediction': 0, 'drift_correction': False, 'drift_amount': .5}
        try:
            with client, client.makefile('rb') as stream:
                for line in stream:
                    command = json.loads(line)
                    self.commands.append(command)
                    name = command['command']
                    if name == 'timeout':
                        time.sleep(6)
                        return
                    if name == 'malformed':
                        client.sendall(b'{bad json}\n')
                        return
                    if name == 'oversized':
                        client.sendall(b'x' * (1024 * 1024 + 1))
                        return
                    if name == 'set_settings': settings.update(command['settings'])
                    status = {'bones': BONES, 'settings': settings, 'trackers': [{'active': True}], 'hmd_pose_received': True}
                    payload = json.dumps({'ok': True, 'status': status, 'error': None}).encode() + b'\n'
                    # Deliberately fragment the stream to exercise incremental framing.
                    client.sendall(payload[:17])
                    client.sendall(payload[17:])
        except (BrokenPipeError, ConnectionResetError): pass

    def call(self, item, path, interface, method, signature=None, args=()):
        return self.connection.call_sync(item, path, interface, method, GLib.Variant(signature, args) if signature else None, None, Gio.DBusCallFlags.NONE, 3000, None)

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wivrn-fltk-test-')
        cls.directory = Path(cls.temp.name)
        cls.services = Services(cls.directory)
        binary = cls.directory / 'systemctl'
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WIVRN_TEST_ACTIONS"\nsleep 0.1\nif test "$2" = stop; then echo "intentional service failure"; exit 1; fi\n')
        binary.chmod(0o755)
        cls.environment = dict(os.environ, PATH=str(cls.directory) + ':' + os.environ['PATH'], WIVRN_TEST_ACTIONS=str(cls.directory / 'actions'), XDG_CONFIG_HOME=str(cls.directory / 'config'), XDG_CACHE_HOME=str(cls.directory / 'cache'), XDG_CURRENT_DESKTOP='DomeD', FLTK_BACKEND='x11', WAYLAND_DISPLAY='', WAYLAND_SOCKET='', WAYVR_DISPLAY_AUTH='')

        for key in ('WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'WAYVR_DISPLAY_AUTH', 'WIVRN_DASHBOARD_SURFACE'):
            cls.environment.pop(key, None)

    def test_01_backend(self):
        subprocess.run([BUILD / 'fltk/wivrn-dashboard-backend-test', self.services.socket_path], env=self.environment, check=True, timeout=25)
        self.assertEqual(self.services.config['unknown-future'], CONFIG['unknown-future'])
        self.assertIn(('EnablePairing', (120,)), self.services.calls)
        self.assertEqual((self.directory / 'actions').read_text().splitlines(), ['--user restart wivrn-pro.service', '--user stop wivrn-pro.service'])

    def test_02_profile(self):
        spec = importlib.util.spec_from_file_location('profile_helper', Path(__file__).parents[1] / 'profile.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        path = self.directory / 'profile.toml'
        profile = {'haritorax': {'enabled': True, 'ports': ['/dev/serial/test']}, 'unknown': [1, 'value']}
        result = helper.run({'path': str(path), 'action': 'save', 'expected': None, 'profile': profile})
        self.assertEqual(result['profile'], profile)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, 'changed elsewhere'):
            helper.run({'path': str(path), 'action': 'save', 'expected': None, 'profile': {}})
        before = path.read_text()
        with self.assertRaises(ValueError):
            helper.run({'path': str(path), 'action': 'save', 'expected': before, 'text': '[invalid'})
        self.assertEqual(path.read_text(), before)
        dated = self.directory / 'dates.toml'
        dated.write_text('stamp = 2026-10-23T10:00:00Z\n')
        loaded = helper.run({'path': str(dated), 'action': 'load'})
        transported = json.loads(json.dumps(loaded['profile'], default=lambda v: v.isoformat()))
        saved = helper.run({'path': str(dated), 'action': 'save', 'expected': loaded['text'], 'profile': transported})
        self.assertEqual(saved['profile']['stamp'], loaded['profile']['stamp'])

    def test_03_tray_and_real_window(self):
        display_file = self.directory / 'display'
        with display_file.open('w') as output:
            x = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1600x1200x24', '+extension', 'GLX'], stdout=output, stderr=subprocess.DEVNULL)
        process = None
        try:
            self.wait(lambda: display_file.stat().st_size > 0)
            env = dict(self.environment, DISPLAY=':' + display_file.read_text().strip())
            previous = len(self.services.items)
            stderr = (self.directory / 'ui.log').open('w')
            process = subprocess.Popen([BUILD / 'fltk/wivrn-pro-dashboard', '--page', 'skeleton', '--socket', self.services.socket_path, '--profile', self.directory / 'ui-profile.toml'], env=env, stdout=stderr, stderr=stderr)
            self.wait(lambda: len(self.services.items) > previous or process.poll() is not None, timeout=90)
            self.assertIsNone(process.poll(), (self.directory / 'ui.log').read_text())
            item = self.services.items[-1].split('/')[0]
            # Hidden startup must export a live tray item, with no mapped main window.
            tree = subprocess.check_output(['xwininfo', '-root', '-tree'], env=env, text=True)
            self.assertNotIn('wivrn-pro', tree)
            reply = self.services.call(item, '/StatusNotifierItem', 'org.freedesktop.DBus.Properties', 'Get', '(ss)', ('org.kde.StatusNotifierItem', 'Id'))
            self.assertEqual(reply.unpack()[0], 'wivrn.pro')
            layout = self.services.call(item, '/StatusNotifierItem/Menu', 'com.canonical.dbusmenu', 'GetLayout', '(iias)', (0, -1, [])).unpack()
            self.assertEqual(len(layout[1][2]), 5)
            # A second launch activates the incumbent; it must not register another item.
            subprocess.run([BUILD / 'fltk/wivrn-pro-dashboard', '--show'], env=env, check=True, timeout=10)
            self.wait(lambda: 'wivrn-pro' in subprocess.check_output(['xwininfo', '-root', '-tree'], env=env, text=True))
            self.assertEqual(len(self.services.items), previous + 1)
            # Exercise DomeD's actual tray manager service layer, when supplied.
            driver = os.environ.get('DOME_TRAY_DRIVER')
            if driver:
                def manager(request):
                    result = subprocess.run([driver], input=json.dumps(request) + '\n', text=True, capture_output=True, env=env, check=True, timeout=10)
                    response = json.loads(result.stdout)
                    self.assertTrue(response['ok'], response)
                    return response['value']
                row = next(row for row in manager({'op': 'tray'}) if row['Id'] == 'wivrn.pro')
                self.assertTrue(row['managed'])
                self.assertEqual(len(manager({'op': 'menu', 'item': row, 'parent': 0})), 5)
                manager({'op': 'placement', 'item': row, 'managed': False})
                self.assertFalse(next(row for row in manager({'op': 'tray'}) if row['Id'] == 'wivrn.pro')['managed'])
                manager({'op': 'placement', 'item': row, 'managed': True})
            # Drive service actions through the actual exported tray menu.
            self.services.call(item, '/StatusNotifierItem/Menu', 'com.canonical.dbusmenu', 'Event', '(isvu)', (4, 'clicked', GLib.Variant('i', 0), 0))
            self.wait(lambda: len((self.directory / 'actions').read_text().splitlines()) == 3)
            # Pixel capture proves the native window drew, including the live skeleton tab.
            from PIL import ImageGrab
            image = ImageGrab.grab(xdisplay=env['DISPLAY'])
            self.assertGreater(len(image.getcolors(image.width * image.height)), 20)
            image.save(BUILD / 'fltk-window.png')
            self.services.call(item, '/StatusNotifierItem/Menu', 'com.canonical.dbusmenu', 'Event', '(isvu)', (5, 'clicked', GLib.Variant('i', 0), 0))
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            stderr.close()
            x.terminate()
            try: x.wait(timeout=3)
            except subprocess.TimeoutExpired: x.kill(); x.wait()

    @unittest.skipUnless(os.environ.get('DOME_COMPOSITOR'), 'Set DOME_COMPOSITOR for native Wayland validation')
    def test_04_native_dome_wayland(self):
        import re
        import urllib.request
        from PIL import ImageGrab
        runtime = self.directory / 'wayland-runtime'
        runtime.mkdir(mode=0o700)
        rd, wr = os.pipe()
        x = subprocess.Popen(['Xvfb', '-displayfd', str(wr), '-screen', '0', '1600x1000x24', '-nolisten', 'tcp', '-noreset'], pass_fds=(wr,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(wr)
        with os.fdopen(rd) as stream: display = ':' + stream.readline().strip()
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        env = dict(self.environment, DISPLAY=display, XDG_RUNTIME_DIR=str(runtime), DOME_START_XR='0', DOME_LOGIN_SESSION='0', DOME_RENDERER='gles', LIBGL_ALWAYS_SOFTWARE='1', DOME_PREVIEW_PORT=str(port), XDG_SESSION_TYPE='x11')
        compositor = ui = None
        logpath = self.directory / 'dome.log'
        log = logpath.open('w')
        uilog = (self.directory / 'wayland-ui.log').open('w')
        try:
            compositor = subprocess.Popen([os.environ['DOME_COMPOSITOR'], '--backend', 'nested'], env=env, stdout=log, stderr=log)
            self.wait(lambda: 'Dome Wayland socket:' in logpath.read_text() or compositor.poll() is not None, timeout=30)
            self.assertIsNone(compositor.poll(), logpath.read_text())
            name = re.search(r'Dome Wayland socket: (\S+)', logpath.read_text())[1]
            client_env = dict(env, FLTK_BACKEND='wayland', WAYLAND_DISPLAY=name, XDG_SESSION_TYPE='wayland')
            client_env.pop('DISPLAY', None)  # A Wayland-only client; cannot fall back to X11.
            ui = subprocess.Popen([BUILD / 'fltk/wivrn-pro-dashboard', '--show', '--page', 'skeleton', '--socket', self.services.socket_path, '--profile', self.directory / 'wayland-profile.toml'], env=client_env, stdout=uilog, stderr=uilog)
            def mapped():
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/state', timeout=1) as reply: state = json.load(reply)['scene']
                    return any('WiVRn' in w.get('title', '') for w in state['windows'])
                except (OSError, KeyError): return False
            self.wait(mapped, timeout=60)
            time.sleep(1)
            image = ImageGrab.grab(xdisplay=display)
            image.save(BUILD / 'fltk-dome-wayland.png')
            self.assertGreater(len(image.getcolors(image.width * image.height)), 20)
            self.assertIsNone(ui.poll(), (self.directory / 'wayland-ui.log').read_text())
            ui.terminate()
            self.assertEqual(ui.wait(timeout=10), 0)
        finally:
            for child in (ui, compositor, x):
                if child and child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
            log.close()
            uilog.close()

    @staticmethod
    def wait(condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline: time.sleep(.02)
        if not condition(): raise AssertionError('Timed out waiting for native integration')

if __name__ == '__main__': unittest.main(verbosity=2)
