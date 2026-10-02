# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch the optional Mac desktop receiver in WayVR's compositor."""
import os
from pathlib import Path
import re
import shlex
import shutil
from PySide6.QtCore import QProcess, QSettings, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton


class MacDesktopPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings('WiVRn-Pro', 'MacDesktop')
        layout = QVBoxLayout(self)
        description = QLabel('Open one remote 1920×1080 Mac desktop in VR. Start the Mac companion first and authorize this PC’s SSH key. Click the desktop to control it; Ctrl+Alt+Esc releases input.')
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
        self.launch = QPushButton('Open Mac desktop in VR')
        self.launch.clicked.connect(self.open_desktop)
        layout.addWidget(self.launch)
        self.status = QLabel('Prototype: JPEG at up to 15 fps; video and mouse/keyboard only.')
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

    def open_desktop(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        host, user = self.host.text().strip(), self.user.text().strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,252}', host) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9._-]{0,63}', user):
            self.status.setText('Enter a valid host and macOS account short name.')
            return
        bindir = os.environ.get('WIVRN_PRO_BINDIR')
        def executable(name):
            return str(Path(bindir) / name) if bindir else shutil.which(name)
        ctl, receiver = executable('wivrn-pro-wayvrctl'), executable('wivrn-pro-mac-desktop')
        if not ctl or not receiver or not all(os.access(p, os.X_OK) for p in (ctl, receiver)):
            self.status.setText('The Mac desktop receiver and WayVR control tool must be installed first.')
            return
        self.settings.setValue('host', host)
        self.settings.setValue('user', user)
        self.settings.sync()
        icon = str(Path(receiver).resolve().parent.parent / 'share/icons/hicolor/128x128/apps/wivrn-pro-menu.png')
        args = shlex.join(['--host', host, '--user', user])
        self.process.setProgram(ctl)
        self.process.setArguments(['process-launch', '--name', 'Mac desktop', receiver, '1920x1080', 'floating', icon, '--', args])
        self.launch.setEnabled(False)
        self.status.setText('Opening in WayVR…')
        self.process.start()
        self.deadline.start(10000)

    def finished(self, code, status):
        self.deadline.stop()
        self.launch.setEnabled(True)
        output = bytes(self.process.readAllStandardOutput()).decode(errors='replace').strip()
        if code == 0 and status == QProcess.ExitStatus.NormalExit:
            self.status.setText('Launch request sent. Connection status appears inside the Mac desktop window.')
        else:
            self.status.setText('WayVR launch failed: ' + (output[-500:] or 'Check that WayVR is running.'))

    def failed(self, error):
        self.deadline.stop()
        self.launch.setEnabled(True)
        self.status.setText(self.process.errorString())
