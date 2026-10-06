#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""WiVRn Pro dashboard, reusing Shora's native Qt6 tracking interface."""
import copy
import json
import os
from pathlib import Path
import sys
import tomllib

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, QSaveFile, QIODevice, QFileDevice, QEvent, Qt
from PySide6.QtGui import QIcon
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusVariant
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QLineEdit, QPlainTextEdit, QSpinBox, QInputDialog,
    QMenu, QSystemTrayIcon, QScrollArea)
from shora_qt import HubWindow, STYLE
from tracking_panel import DEFAULTS

SERVICE = 'io.github.wivrn.Server'
OBJECT = '/io/github/wivrn/Server'
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
PROFILE = CONFIG_HOME / 'wivrn-pro' / 'shora.toml'
SOCKET = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'wivrn-pro' / 'ShoraControl'


def atomic_save(path, text, expected):
    """Avoid silently overwriting another editor's changes; commit complete files."""
    current = path.read_text() if path.exists() else None
    if current != expected:
        raise ValueError('The file changed outside this window. Reload before saving.')
    path.parent.mkdir(parents=True, exist_ok=True)
    file = QSaveFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(file.errorString())
    if not file.setPermissions(QFileDevice.Permission.ReadOwner | QFileDevice.Permission.WriteOwner):
        raise OSError(file.errorString())
    payload = text.encode()
    if file.write(payload) != len(payload) or not file.commit():
        raise OSError(file.errorString())
    path.chmod(0o600)


def toml_value(value):
    if isinstance(value, bool): return str(value).lower()
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)): return repr(value)
    if isinstance(value, list): return '[' + ', '.join(map(toml_value, value)) + ']'
    if isinstance(value, dict): return '{' + ', '.join(json.dumps(k) + ' = ' + toml_value(v) for k, v in value.items() if v is not None) + '}'
    if hasattr(value, 'isoformat'): return value.isoformat()
    raise ValueError(f'Cannot save TOML value: {type(value).__name__}')


def merge_tuning(text, settings):
    profile = tomllib.loads(text)
    for key in (*DEFAULTS, 'stay_aligned'):
        if key in settings:
            profile[key] = copy.deepcopy(settings[key])
    return '# WiVRn Pro tracking profile; current tuning saved from Shora.\n' + ''.join(
        json.dumps(k) + ' = ' + toml_value(v) + '\n' for k, v in profile.items())



class ServerClient(QObject):
    state = Signal(dict)
    failure = Signal(str)
    saved = Signal()
    def __init__(self, parent=None, bus=None):
        super().__init__(parent)
        self.bus = bus if bus is not None else QDBusConnection.sessionBus()
        self.pending = set()
        self.refreshing = False
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.service_process = QProcess(self)
        self.service_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.service_process.finished.connect(self.service_finished)
        self.service_process.errorOccurred.connect(lambda _: self.failure.emit(self.service_process.errorString()))
        self.service_deadline = QTimer(self)
        self.service_deadline.setSingleShot(True)
        self.service_deadline.timeout.connect(self.service_process.kill)
        QTimer.singleShot(0, self.refresh)

    def call(self, interface, method, args, callback, quiet=False):
        msg = QDBusMessage.createMethodCall(SERVICE, OBJECT, interface, method)
        msg.setArguments(args)
        watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg, 3000), self)
        self.pending.add(watcher)
        def done():
            reply = watcher.reply()
            self.pending.discard(watcher)
            if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                if not quiet: self.failure.emit(reply.errorMessage())
                callback(None)
            else: callback(reply.arguments())
            watcher.deleteLater()
        watcher.finished.connect(done)

    def refresh(self):
        if self.refreshing: return
        self.refreshing = True
        names = ['JsonConfiguration', 'SessionRunning', 'SystemName', 'SteamCommand', 'PairingEnabled', 'Pin', 'KnownKeysJson', 'WayVRStatus']
        state = {}
        remaining = set(names)
        def receive(name, args):
            if args:
                value = args[0]
                state[name] = value.variant() if isinstance(value, QDBusVariant) else value
            remaining.discard(name)
            if not remaining:
                self.refreshing = False
                try: state['KnownKeys'] = json.loads(state.get('KnownKeysJson') or '[]')
                except (ValueError, TypeError): state['KnownKeys'] = []
                self.state.emit(state if 'JsonConfiguration' in state else {})
        for name in names:
            self.call('org.freedesktop.DBus.Properties', 'Get', [SERVICE, name],
                      lambda args, name=name: receive(name, args), quiet=True)

    def method(self, name, *args):
        self.call(SERVICE, name, list(args), lambda _: self.refresh())

    def save(self, config, expected):
        # Re-read before writing so a second dashboard isn't silently overwritten.
        def checked(args):
            if args is None: return
            current = args[0]
            if isinstance(current, QDBusVariant): current = current.variant()
            try: current = json.loads(current or '{}')
            except (ValueError, TypeError):
                self.failure.emit('Server returned invalid configuration JSON.'); return
            if current is None: current = {}
            if current != expected:
                self.failure.emit('WiVRn settings changed elsewhere. Reload before saving.')
                return
            def written(reply):
                if reply is not None: self.saved.emit()
                self.refresh()
            self.call('org.freedesktop.DBus.Properties', 'Set',
                      [SERVICE, 'JsonConfiguration', QDBusVariant(json.dumps(config))], written)
        self.call('org.freedesktop.DBus.Properties', 'Get', [SERVICE, 'JsonConfiguration'], checked)

    def service(self, action):
        if action not in ('start', 'stop', 'restart'): raise ValueError(action)
        if self.service_process.state() != QProcess.ProcessState.NotRunning: return
        self.service_process.start('systemctl', ['--user', action, 'wivrn-pro.service'])
        self.service_deadline.start(20000)

    def service_finished(self, code, _status):
        self.service_deadline.stop()
        if code: self.failure.emit(bytes(self.service_process.readAllStandardOutput()).decode(errors='replace'))
        self.refresh()


class StreamingPanel(QWidget):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.original = None
        self.latest = None
        self.dirty = False
        self.loading = False
        layout = QVBoxLayout(self)
        self.status = QLabel('WiVRn service is stopped')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.encoder = QComboBox()
        self.encoder.addItems(['Preserve current layout', 'auto', 'vaapi', 'vulkan', 'qsv'])
        self.encoder.setToolTip('Selecting an encoder replaces the encoder layout. QSV requires a QSV-enabled server build.')
        self.codec = QComboBox()
        self.codec.addItems(['Preserve current codec', 'h264', 'h265'])
        self.depth = QComboBox()
        self.depth.addItems(['Use default', '8', '10'])
        self.tcp = QCheckBox('Use TCP for streaming')
        self.hid = QCheckBox('Forward input devices')
        self.debug = QCheckBox('Enable runtime debug UI')
        self.wayvr = QCheckBox('Run bundled upstream WayVR with each VR session')
        self.wayvr_dashboard = QCheckBox('Open this dashboard inside WayVR')
        self.hostname = QLineEdit()
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(9757)
        for label, control in [('Encoder', self.encoder), ('Codec', self.codec), ('Bit depth', self.depth),
                               ('Hostname', self.hostname), ('Port', self.port), ('', self.tcp), ('', self.hid), ('', self.debug), ('', self.wayvr), ('', self.wayvr_dashboard)]:
            form.addRow(label, control)
        layout.addLayout(form)
        layout.addWidget(QLabel('Full configuration JSON (application, OpenVR, custom encoder layouts and other options)'))
        self.raw = QPlainTextEdit()
        layout.addWidget(self.raw, 1)
        row = QHBoxLayout()
        self.apply = QPushButton('Save WiVRn settings')
        self.apply.clicked.connect(self.save)
        row.addWidget(self.apply)
        reload = QPushButton('Reload settings'); reload.clicked.connect(self.reload); row.addWidget(reload)
        layout.addLayout(row)
        self.notice = QLabel('Saved options apply to the next streaming session.')
        self.notice.setWordWrap(True); layout.addWidget(self.notice)
        self.wayvr_status = QLabel('WayVR: waiting for service'); self.wayvr_status.setWordWrap(True)
        layout.addWidget(self.wayvr_status)
        self.wayvr_restart = QPushButton('Restart WayVR / apply saved overlay settings')
        self.wayvr_restart.clicked.connect(lambda: backend.method('RestartWayVR'))
        layout.addWidget(self.wayvr_restart)
        self.pairing = QLabel(); layout.addWidget(self.pairing)
        pair = QHBoxLayout()
        for text, method, args in [('Pair Quest Pro (2 min)', 'EnablePairing', [120]), ('Disable pairing', 'DisablePairing', []), ('Disconnect headset', 'Disconnect', [])]:
            button = QPushButton(text); button.clicked.connect(lambda _, m=method, a=args: backend.method(m, *a)); pair.addWidget(button)
        layout.addLayout(pair)
        self.keys = QComboBox(); layout.addWidget(self.keys)
        keyrow = QHBoxLayout()
        rename = QPushButton('Rename headset'); rename.clicked.connect(self.rename_key); keyrow.addWidget(rename)
        revoke = QPushButton('Forget selected headset'); revoke.clicked.connect(self.revoke_key); keyrow.addWidget(revoke)
        layout.addLayout(keyrow)
        backend.state.connect(self.update_state)
        backend.failure.connect(self.notice.setText)
        backend.saved.connect(self.saved)
        self.raw.textChanged.connect(self.changed)
        for widget in (self.encoder, self.codec, self.depth): widget.currentIndexChanged.connect(self.changed)
        for widget in (self.tcp, self.hid, self.debug, self.wayvr, self.wayvr_dashboard): widget.toggled.connect(self.changed)
        self.hostname.textEdited.connect(self.changed); self.port.valueChanged.connect(self.changed)

    def changed(self, *_):
        if not self.loading: self.dirty = True

    def saved(self):
        self.dirty = False
        self.notice.setText('Settings saved. They apply to the next streaming session.')

    def update_state(self, state):
        self.apply.setEnabled(bool(state))
        self.wayvr_restart.setEnabled(bool(state.get('SessionRunning')))
        self.wayvr_status.setText('WayVR: ' + state.get('WayVRStatus', 'service unavailable'))
        if not state:
            self.status.setText('WiVRn service is stopped or unavailable')
            self.pairing.setText(''); self.keys.clear()
            return
        self.status.setText(f"{'Streaming' if state.get('SessionRunning') else 'Ready'} · {state.get('SystemName') or 'Quest Pro disconnected'}\nSteam launch: {state.get('SteamCommand', '')}")
        self.pairing.setText(f"Pairing PIN: {state.get('Pin', '')}" if state.get('PairingEnabled') else 'Pairing disabled')
        keys = state.get('KnownKeys', [])
        if isinstance(keys, list):
            selected = self.keys.currentData(); self.keys.clear()
            for key in keys:
                if isinstance(key, (list, tuple)) and len(key) >= 2: self.keys.addItem(key[0], key[1])
            index = self.keys.findData(selected)
            if index >= 0: self.keys.setCurrentIndex(index)
        try:
            self.latest = json.loads(state.get('JsonConfiguration') or '{}')
            if self.latest is None: self.latest = {}
            if not isinstance(self.latest, dict): raise ValueError('Configuration must be an object')
            if not self.dirty: self.reload()
        except (ValueError, TypeError) as error: self.notice.setText(str(error))

    def reload(self):
        if self.latest is None: return
        self.loading = True
        self.original = copy.deepcopy(self.latest)
        self.raw.setPlainText(json.dumps(self.original, indent=2))
        self.encoder.setCurrentIndex(0); self.codec.setCurrentIndex(0)
        self.depth.setCurrentIndex({8: 1, 10: 2}.get(self.original.get('bit-depth'), 0))
        self.hostname.setText(self.original.get('hostname', ''))
        self.port.setValue(self.original.get('port', 9757))
        self.tcp.setChecked(self.original.get('tcp-only', False))
        self.hid.setChecked(self.original.get('hid-forwarding', False))
        self.debug.setChecked(self.original.get('debug-gui', False))
        overlay = self.original.get('wayvr', {})
        if not isinstance(overlay, dict): overlay = {}
        self.wayvr.setChecked(overlay.get('enabled', True))
        self.wayvr_dashboard.setChecked(overlay.get('dashboard', True))
        self.initial_controls = self.controls()
        self.loading = False; self.dirty = False

    def controls(self):
        return {'tcp-only': self.tcp.isChecked(), 'hid-forwarding': self.hid.isChecked(),
                'debug-gui': self.debug.isChecked(), 'hostname': self.hostname.text(),
                'port': self.port.value(), 'bit-depth': [None, 8, 10][self.depth.currentIndex()],
                'wayvr-enabled': self.wayvr.isChecked(), 'wayvr-dashboard': self.wayvr_dashboard.isChecked()}

    def configuration(self):
        value = json.loads(self.raw.toPlainText())
        if not isinstance(value, dict): raise ValueError('Configuration must be a JSON object')
        for key, setting in self.controls().items():
            if setting != self.initial_controls[key]:
                if key.startswith('wayvr-'):
                    overlay = value.setdefault('wayvr', {})
                    if not isinstance(overlay, dict): raise ValueError('wayvr must be an object')
                    overlay[key.removeprefix('wayvr-')] = setting
                elif setting is None: value.pop(key, None)
                else: value[key] = setting
        if self.encoder.currentIndex():
            if self.encoder.currentText() == 'auto': value.pop('encoder', None)
            else: value['encoder'] = self.encoder.currentText()
        if self.codec.currentIndex():
            encoder = value.get('encoder', {})
            def set_codec(item):
                item = {'encoder': item} if isinstance(item, str) else copy.deepcopy(item)
                if not isinstance(item, dict): raise ValueError('Invalid encoder layout')
                item['codec'] = self.codec.currentText()
                return item
            value['encoder'] = [set_codec(e) for e in encoder] if isinstance(encoder, list) else set_codec(encoder)
        return value

    def save(self):
        try:
            if self.original is not None: self.backend.save(self.configuration(), self.original)
        except (ValueError, TypeError) as error: self.notice.setText(str(error))

    def rename_key(self):
        if not self.keys.currentData(): return
        name, accepted = QInputDialog.getText(self, 'Rename headset', 'Name', text=self.keys.currentText())
        if accepted and name: self.backend.method('RenameKey', self.keys.currentData(), name)

    def revoke_key(self):
        if self.keys.currentData(): self.backend.method('RevokeKey', self.keys.currentData())


class Dashboard(HubWindow):
    def __init__(self, backend=None, profile=PROFILE, socket_path=SOCKET, attach=True, surface='desktop'):
        self.backend = backend or ServerClient()
        self.profile_path = Path(profile)
        super().__init__(server='/unused', config=str(profile), socket_path=str(socket_path), attach=False)
        self.setWindowTitle('WiVRn Pro · Streaming, Shora & WayVR')
        self.findChild(QLabel, 'brand').setText('WiVRn PRO')
        self.settings.setTitle('Tracking devices and face/eye OSC')
        form = self.settings.layout()
        for index in range(form.count()):
            row, column, rows, columns = form.getItemPosition(index)
            if row < 3 and form.itemAt(index).widget(): form.itemAt(index).widget().hide()
        self.haritorax.setToolTip('Enable serial tracking in the saved startup profile.')
        self.face.setItemText(3, 'OpenXR (Quest Pro face & eyes)')
        save_devices = QPushButton('Save device / OSC settings')
        save_devices.clicked.connect(self.save_devices)
        form.addWidget(save_devices, 6, 0, 1, 5)
        self.start_button.setText('Start WiVRn'); self.stop_button.setText('Stop WiVRn')
        self.stop_button.setEnabled(True)
        self.attach_button.setText('Reconnect tracking view')
        self.detach_button.setText('Restart WiVRn')
        self.detach_button.clicked.disconnect()
        self.detach_button.clicked.connect(lambda: self.backend.service('restart'))
        self.tabs.insertTab(0, StreamingPanel(self.backend), 'WiVRn streaming')
        self.tabs.setCurrentIndex(0)
        self.logs.setPlaceholderText('Service logs: journalctl --user -u wivrn-pro.service\nTracking diagnostics and calibration messages appear below.')
        self.backend.failure.connect(self.show_error)
        self.profile = QPlainTextEdit()
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel('Tracking startup profile — HaritoraX, face/eye OSC, assignments and tuning'))
        layout.addWidget(self.profile)
        actions = QHBoxLayout()
        for text, callback in [('Reload profile', self.load_profile), ('Save profile', self.save_profile), ('Save active tuning into profile', self.save_tuning)]:
            button = QPushButton(text); button.clicked.connect(callback); actions.addWidget(button)
        layout.addLayout(actions)
        note = QLabel(f'Profile: {self.profile_path}\nTracking tuning and relaxed poses are saved automatically when applied. Manual profile changes apply when WiVRn restarts. Runtime reset offsets are not persistent.')
        note.setWordWrap(True); layout.addWidget(note)
        self.tabs.addTab(page, 'Tracking profile')
        self.profile_original = None
        self.initial_devices = self.device_values()
        self.load_profile()
        self.message.setText('Start WiVRn to connect streaming and tracking services.')
        self.setup_tray(surface)
        if attach: self.attach_server()

    def setup_tray(self, surface):
        self.tray = None
        self.quit_requested = False
        self.restore_maximized = False
        root = Path(__file__).resolve().parents[2]
        icon_path = root / 'icons/hicolor/128x128/apps/wivrn-pro-menu.png'
        if not icon_path.exists():
            icon_path = root / 'images/wivrn-pro-menu/128.png'
        icon = QIcon.fromTheme('wivrn-pro-menu', QIcon(str(icon_path)))
        self.setWindowIcon(icon)
        if surface != 'desktop':
            return
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('WiVRn Pro')
        menu = QMenu(self)
        menu.addAction('Show WiVRn Pro', self.restore_from_tray)
        menu.addSeparator()
        menu.addAction('Quit dashboard', self.quit_dashboard)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            if getattr(self, 'tray', None) and QSystemTrayIcon.isSystemTrayAvailable():
                self.restore_maximized = bool(event.oldState() & Qt.WindowState.WindowMaximized)
                QTimer.singleShot(0, self.hide_to_tray)

    def hide_to_tray(self):
        # Recheck after the window manager finishes minimizing. Without a tray,
        # leave the ordinary taskbar entry available so the window is reachable.
        if self.isMinimized() and self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()

    def restore_from_tray(self):
        if self.restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_from_tray()

    def quit_dashboard(self):
        self.quit_requested = True
        if self.close():
            QApplication.instance().quit()

    def closeEvent(self, event):
        if not self.quit_requested and self.tray and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            if not self.isMinimized():
                self.restore_maximized = self.isMaximized()
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
        if event.isAccepted() and self.tray:
            self.tray.hide()

    def load_profile(self):
        try:
            self.profile_original = self.profile_path.read_text() if self.profile_path.exists() else None
            self.profile.setPlainText(self.profile_original or '')
            self.load_devices()
        except (ValueError, OSError) as error: self.show_error(str(error))

    def update_snapshot(self, snapshot):
        super().update_snapshot(snapshot)
        # The worker also writes this file. Refresh clean editors, but retain
        # drafts and their original contents for the existing conflict check.
        if (self.profile.toPlainText() == (self.profile_original or '') and
                self.device_values() == self.initial_devices):
            try:
                text = self.profile_path.read_text() if self.profile_path.exists() else None
                if text != self.profile_original:
                    self.load_profile()
            except OSError as error:
                self.show_error(str(error))

    def save_profile(self):
        try:
            text = self.profile.toPlainText()
            tomllib.loads(text)
            atomic_save(self.profile_path, text, self.profile_original)
            self.profile_original = text
            self.load_devices()
            self.show_error('Tracking profile saved; restart WiVRn to apply startup settings.')
        except (ValueError, OSError) as error: self.show_error(str(error))

    def device_values(self):
        return {'enabled': self.haritorax.isChecked(), 'ports': [p.strip() for p in self.serial.text().split(';') if p.strip()],
                'model': self.model.currentIndex(), 'face': self.face.currentIndex(),
                'output': self.face_output.currentIndex(), 'sheet': self.face_sheet.text().strip()}

    def load_devices(self):
        profile = tomllib.loads(self.profile.toPlainText())
        serial = profile.get('haritorax', {}); face = profile.get('face', {})
        self.haritorax.setChecked(serial.get('enabled', False))
        self.serial.setText('; '.join(serial.get('ports', [])))
        self.model.setCurrentIndex({'x2': 1, 'wireless': 2}.get(serial.get('model'), 0))
        self.face.setCurrentIndex(1 if not face.get('enabled', False) else (3 if face.get('source') == 'openxr' else 2))
        self.face_output.setCurrentIndex({'auto': 1, 'unift': 2, 'json': 3, 'both': 4}.get(face.get('output'), 0))
        self.face_sheet.setText(face.get('translation_sheet', ''))
        self.initial_devices = self.device_values()

    def save_devices(self):
        try:
            values = self.device_values()
            changed = {key for key in values if values[key] != self.initial_devices[key]}
            if not changed:
                self.show_error('Device settings are unchanged.'); return
            profile = tomllib.loads(self.profile.toPlainText())
            serial = profile.setdefault('haritorax', {}); face = profile.setdefault('face', {})
            for key in ('enabled', 'ports'):
                if key in changed: serial[key] = values[key]
            if 'model' in changed:
                if values['model']: serial['model'] = ['x2', 'wireless'][values['model'] - 1]
                else: serial.pop('model', None)
            if 'face' in changed and values['face']:
                face['enabled'] = values['face'] != 1
                if values['face'] != 1: face['source'] = ['babble', 'openxr'][values['face'] - 2]
            if 'output' in changed:
                if values['output']: face['output'] = ['auto', 'unift', 'json', 'both'][values['output'] - 1]
                else: face.pop('output', None)
            if 'sheet' in changed:
                if values['sheet']:
                    face['translation_sheet'] = str(Path(values['sheet']).expanduser()); face.pop('avatar', None)
                else: face.pop('translation_sheet', None)
            if 'output' in changed and face.get('output') == 'unift':
                face.pop('translation_sheet', None); face.pop('avatar', None)
            if face.get('enabled') and face.get('output') in ('json', 'both') and not face.get('translation_sheet'):
                raise ValueError('JSON face output requires an avatar OSC JSON mapping.')
            text = '# WiVRn Pro tracking profile\n' + ''.join(json.dumps(k) + ' = ' + toml_value(v) + '\n' for k, v in profile.items())
            self.profile.setPlainText(text)
            self.save_profile()
        except (ValueError, TypeError, KeyError) as error: self.show_error(str(error))

    def save_tuning(self):
        if not self.last_snapshot: self.show_error('Connect to tracking before saving tuning.'); return
        try:
            text = merge_tuning(self.profile.toPlainText(), self.last_snapshot['settings'])
            self.profile.setPlainText(text)
            self.save_profile()
        except (ValueError, KeyError) as error: self.show_error(str(error))

    def start_server(self): self.backend.service('start'); self.attach_server()
    def stop_server(self): self.backend.service('stop')
    def connection_changed(self, connected):
        super().connection_changed(connected)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    from dashboard_instance import DashboardInstance
    instance = DashboardInstance()
    try:
        if not instance.acquire(): return 0
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    window = Dashboard(surface=instance.surface); instance.window = window; window.show()
    try:
        return app.exec()
    finally:
        instance.close()

if __name__ == '__main__': sys.exit(main())
