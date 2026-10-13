# SPDX-License-Identifier: GPL-3.0-or-later
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QCheckBox, QLineEdit, QComboBox, QLabel, QPushButton, QHBoxLayout

class MotocPanel(QWidget):
    def __init__(self, client, streaming=None):
        super().__init__()
        self.client = client
        self.dirty = False
        self.loading = False
        self.anchors_available = True
        self.saved_mode = "once"
        layout = QVBoxLayout(self)
        if streaming is not None:
            layout.addWidget(streaming.lighthouse_controllers)
            streaming.lighthouse_controllers.show()
            self.save_controllers = QPushButton('Save WiVRn settings')
            self.save_controllers.setToolTip('Save the controller toggle and any pending edits in WiVRn streaming settings.')
            self.save_controllers.clicked.connect(streaming.save)
            self.save_controllers.setEnabled(streaming.original is not None)
            streaming.backend.state.connect(lambda state: self.save_controllers.setEnabled(bool(state)))
            layout.addWidget(self.save_controllers)
            self.controller_notice = QLabel('Applies in Motoc bridge mode. Power on and pair controllers before starting WiVRn. '
                'Save WiVRn settings and restart WiVRn after changing this option. '
                'Lighthouse controllers use the same Motoc playspace alignment as trackers.')
            self.controller_notice.setWordWrap(True)
            streaming.save_message.connect(self.controller_notice.setText)
            layout.addWidget(self.controller_notice)
        self.enabled = QCheckBox('Use Motoc calibration for Lighthouse devices')
        self.keep_alive = QCheckBox('Keep calibration running and resume after process/reconnect interruptions')
        self.source = QLineEdit('WiVRn HMD')
        self.target = QLineEdit(); self.target.setPlaceholderText('LHR-… tracker serial')
        self.mode = QComboBox()
        for text, mode in [('One-time alignment', 'once'), ('Continuous — rigid headset tracker', 'rigid'), ('Continuous — room anchors (experimental)', 'anchors')]:
            self.mode.addItem(text, mode)
        form = QFormLayout()
        for name, widget in [('', self.enabled), ('Headset device serial', self.source), ('Lighthouse device serial', self.target), ('Calibration mode', self.mode), ('', self.keep_alive)]: form.addRow(name, widget)
        layout.addLayout(form)
        note = QLabel('Select Motoc / Lighthouse bridge mode in WiVRn streaming settings and restart WiVRn first. '
            'For initial calibration, hold the selected tracker firmly against the headset and move them together slowly. '
            'Rigid mode requires that attachment throughout use.')
        note.setWordWrap(True); layout.addWidget(note)
        self.anchor_note = QLabel('Anchor mode needs at least three saved, distributed room markers; '
            'wait for “Room anchor reference saved” in the service log before detaching the tracker. '
            'Anchors preserve an initial alignment; they cannot detect moved base stations. Recalibrate after moving them.')
        self.anchor_note.setWordWrap(True); layout.addWidget(self.anchor_note)
        self.status = QLabel('Waiting for tracking worker'); self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout()
        self.save = QPushButton('Save Motoc options'); self.save.clicked.connect(self.save_settings); row.addWidget(self.save)
        self.buttons = []
        for title, command in [('Calibrate', 'motoc_calibrate'), ('Resume saved calibration', 'motoc_continue'), ('Stop calibration', 'motoc_stop')]:
            button = QPushButton(title); button.clicked.connect(lambda _, c=command: client.send(c)); row.addWidget(button); self.buttons.append(button)
        layout.addLayout(row); layout.addStretch()
        client.snapshot.connect(self.update_snapshot)
        client.connection_changed.connect(self.connection_changed)
        client.command_finished.connect(self.finished)
        for widget in (self.enabled, self.keep_alive): widget.toggled.connect(self.changed)
        for widget in (self.source, self.target): widget.textEdited.connect(self.changed)
        self.mode.currentIndexChanged.connect(self.changed)
        self.connection_changed(False)
        if streaming is not None:
            streaming.backend.state.connect(self.headset_changed)
    def headset_changed(self, state):
        self.anchors_available = state.get('ClientModel') != 'steam-frame'
        self.anchor_note.setVisible(self.anchors_available)
        # Hide the choice without changing the user's saved Quest calibration.
        index = self.mode.findData('anchors')
        if index >= 0:
            self.mode.model().item(index).setEnabled(self.anchors_available)
            self.mode.view().setRowHidden(index, not self.anchors_available)
        if not self.anchors_available and self.mode.currentData() == 'anchors':
            self.loading = True
            self.mode.setCurrentIndex(self.mode.findData('once'))
            self.loading = False
            self.status.setText('Select and save one-time or rigid tracker alignment for Steam Frame. The previous calibration remains saved until you apply changes.')
        self.update_buttons()

    def update_buttons(self):
        allowed = getattr(self, 'calibration_enabled', False) and (self.anchors_available or self.saved_mode != 'anchors')
        for button in self.buttons: button.setEnabled(allowed)
        # Stopping calibration must always remain possible.
        if self.buttons: self.buttons[-1].setEnabled(getattr(self, 'calibration_enabled', False))

    def changed(self, *_):
        if not self.loading: self.dirty = True
        self.update_buttons()
    def values(self):
        return dict(enabled=self.enabled.isChecked(), source=self.source.text().strip(), target=self.target.text().strip(), mode=self.mode.currentData(), keep_alive=self.keep_alive.isChecked())
    def save_settings(self):
        if not self.anchors_available and self.mode.currentData() == 'anchors':
            self.status.setText('Select one-time or rigid tracker alignment for Steam Frame.')
            return
        self.client.send({'command': 'set_motoc_settings', 'motoc': self.values()})
    def finished(self, command, ok, error):
        if not command.startswith('motoc_') and command != 'set_motoc_settings': return
        if command == 'set_motoc_settings' and ok: self.dirty = False
        if not ok: self.status.setText(error)
    def connection_changed(self, connected):
        self.save.setEnabled(connected)
        for button in self.buttons: button.setEnabled(False)
        if not connected:
            self.calibration_enabled = False
            self.status.setText('Tracking worker unavailable')
    def update_snapshot(self, snapshot):
        state = snapshot.get('motoc', {}); settings = state.get('settings', {})
        self.saved_mode = settings.get('mode', 'once')
        self.status.setText(('Running · ' if state.get('running') else '') + state.get('message', 'Motoc not available'))
        if not self.dirty:
            self.loading = True
            self.enabled.setChecked(settings.get('enabled', False)); self.keep_alive.setChecked(settings.get('keep_alive', True))
            self.source.setText(settings.get('source', 'WiVRn HMD')); self.target.setText(settings.get('target', ''))
            self.mode.setCurrentIndex(max(0, self.mode.findData((settings.get('mode', 'once') if self.anchors_available or self.saved_mode != 'anchors' else 'once'))))
            self.loading = False
        self.calibration_enabled = bool(settings.get('enabled')) and snapshot.get('tracking_mode') == 'motoc'
        self.update_buttons()
