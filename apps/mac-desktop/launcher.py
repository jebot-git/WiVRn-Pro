#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent Linux client; WayVR is an optional window destination."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import sys
from PySide6.QtCore import QProcess, QSettings, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton
from protocol import ssh_arguments
from receiver import Relay


def migrate_settings(settings, legacy):
    """One-way, non-destructive import of the old dashboard's connection."""
    if settings.contains('migration_complete'):
        return
    for key in ('host', 'user'):
        if not settings.contains(key) and legacy.contains(key):
            settings.setValue(key, legacy.value(key))
    settings.setValue('migration_complete', True)
    settings.sync()


def find_wayvr():
    return shutil.which('wayvrctl') or shutil.which('wivrn-pro-wayvrctl')


def wayvr_arguments(executable, icon, host, user):
    ssh_arguments(host, user)
    return ['process-launch', '--name', 'Mac desktop', executable, '1920x1080',
            'floating', icon, '--', shlex.join(['--connect', '--host', host, '--user', user])]


class ConnectionWindow(QWidget):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mac Desktop Relay')
        self.resize(640, 280)
        self.settings = settings if settings is not None else QSettings('MacDesktopRelay', 'Client')
        migrate_settings(self.settings, QSettings('WiVRn-Pro', 'MacDesktop'))
        self.receivers = []
        layout = QVBoxLayout(self)
        description = QLabel('Connect to your Mac’s virtual desktop. Start its background companion and authorize this PC’s SSH key before connecting.')
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.host = QLineEdit(str(self.settings.value('host', '')))
        self.host.setPlaceholderText('Mac hostname or IP address')
        self.user = QLineEdit(str(self.settings.value('user', '')))
        self.user.setPlaceholderText('macOS account short name')
        form.addRow('Mac host', self.host)
        form.addRow('Mac user', self.user)
        layout.addLayout(form)
        self.desktop = QPushButton('Open desktop')
        self.desktop.clicked.connect(self.open_desktop)
        layout.addWidget(self.desktop)
        self.vr = QPushButton('Open in WayVR')
        self.vr.setEnabled(bool(find_wayvr()))
        self.vr.setToolTip('Optional: requires a running WayVR compositor.')
        self.vr.clicked.connect(self.open_wayvr)
        layout.addWidget(self.vr)
        self.status = QLabel('1080p · up to 15 fps · Ctrl+Alt+Esc releases remote input')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.process.kill)

    def target(self):
        host, user = self.host.text().strip(), self.user.text().strip()
        ssh_arguments(host, user)
        self.settings.setValue('host', host)
        self.settings.setValue('user', user)
        self.settings.sync()
        return host, user

    def open_desktop(self):
        try:
            host, user = self.target()
        except ValueError as error:
            self.status.setText(str(error))
            return
        # One companion currently accepts one receiver. Reuse this app's window.
        self.receivers = [w for w in self.receivers if w.isVisible()]
        if self.receivers:
            self.receivers[0].raise_()
            self.receivers[0].activateWindow()
            return
        window = Relay(host, user)
        self.receivers.append(window)
        window.show()
        self.hide()

    def open_wayvr(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            host, user = self.target()
        except ValueError as error:
            self.status.setText(str(error))
            return
        ctl = find_wayvr()
        executable = os.environ.get('MAC_DESKTOP_RELAY_EXECUTABLE') or shutil.which('mac-desktop-relay')
        icon = os.environ.get('MAC_DESKTOP_RELAY_ICON', '')
        if not ctl or not executable:
            self.status.setText('Install Mac Desktop Relay and WayVR to use this destination.')
            return
        self.process.setProgram(ctl)
        self.process.setArguments(wayvr_arguments(executable, icon, host, user))
        self.vr.setEnabled(False)
        self.status.setText('Opening in WayVR…')
        self.process.start()
        self.deadline.start(10000)

    def finished(self, code, status):
        self.deadline.stop()
        self.vr.setEnabled(bool(find_wayvr()))
        output = bytes(self.process.readAllStandardOutput()).decode(errors='replace').strip()
        self.status.setText('Launch request sent; connection status is inside the Mac window.'
                            if code == 0 and status == QProcess.ExitStatus.NormalExit
                            else 'WayVR launch failed: ' + (output[-500:] or 'Check that WayVR is running.'))

    def failed(self, error):
        self.deadline.stop()
        self.vr.setEnabled(bool(find_wayvr()))
        self.status.setText(self.process.errorString())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host')
    parser.add_argument('--user')
    parser.add_argument('--connect', action='store_true', help='Connect immediately using saved or supplied settings')
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName('Mac Desktop Relay')
    app.setDesktopFileName('mac-desktop-relay')
    icon = os.environ.get('MAC_DESKTOP_RELAY_ICON')
    if icon:
        app.setWindowIcon(QIcon(icon))
    settings = QSettings('MacDesktopRelay', 'Client')
    migrate_settings(settings, QSettings('WiVRn-Pro', 'MacDesktop'))
    if args.connect or (args.host and args.user):
        host = args.host or str(settings.value('host', ''))
        user = args.user or str(settings.value('user', ''))
        try:
            ssh_arguments(host, user)
        except ValueError as error:
            parser.error(str(error))
        window = Relay(host, user)
    else:
        window = ConnectionWindow(settings)
        if args.host: window.host.setText(args.host)
        if args.user: window.user.setText(args.user)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
