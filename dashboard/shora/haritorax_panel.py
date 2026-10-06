"""Explicit, readback-verified HaritoraX sensor settings over the dongle."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QFormLayout, QComboBox, QCheckBox, QLabel, QPushButton


class HaritoraXPanel(QGroupBox):
    command_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__("HaritoraX sensors · dongle", parent)
        self.reports = {}
        self.loading = False
        self.dirty = False
        self.pending = False
        layout = QFormLayout(self)
        self.tracker = QComboBox()
        self.mode = QComboBox()
        self.mode.addItem("Mode 1 · magnetometer on", 1)
        self.mode.addItem("Mode 2 · magnetometer off", 2)
        self.fps = QComboBox()
        for rate in (50, 100):
            self.fps.addItem(str(rate), rate)
        self.accelerometer = QCheckBox("Accelerometer correction")
        self.gyroscope = QCheckBox("Gyroscope correction")
        self.magnetometer = QCheckBox("Magnetometer correction")
        layout.addRow("Physical tracker", self.tracker)
        layout.addRow("Sensor mode", self.mode)
        layout.addRow("Tracker transmission FPS", self.fps)
        for field in (self.accelerometer, self.gyroscope, self.magnetometer):
            layout.addRow(field)
            field.toggled.connect(self.edited)
        self.mode.currentIndexChanged.connect(self.edited)
        self.fps.currentIndexChanged.connect(self.edited)
        self.apply = QPushButton("Apply to selected tracker")
        self.reload = QPushButton("Reload reported settings")
        layout.addRow(self.apply)
        layout.addRow(self.reload)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addRow(self.notice)
        help_text = QLabel("Settings affect the physical sensor and its extension. Magnetometer heading disables server yaw correction for these sensors. After a change, repeat standing and mounting calibration. FPS changes sensor transmission, not the skeleton update rate.")
        help_text.setWordWrap(True)
        layout.addRow(help_text)
        self.tracker.currentIndexChanged.connect(self.load_report)
        self.reload.clicked.connect(self.load_report)
        self.apply.clicked.connect(self.submit)
        self.update_snapshot(None)

    def report(self):
        return self.reports.get(tuple(self.tracker.currentData() or ()))

    def fresh(self):
        report = self.report()
        return bool(report and report.get("settings") and report.get("settings_age_ms") is not None and report["settings_age_ms"] < 5000)

    def enable_controls(self):
        fresh = self.fresh() and not self.pending
        for field in (self.mode, self.fps, self.accelerometer, self.gyroscope, self.magnetometer, self.reload):
            field.setEnabled(fresh)
        self.tracker.setEnabled(not self.pending)
        self.apply.setEnabled(fresh and self.dirty)

    def update_snapshot(self, snapshot):
        reports = {}
        for port in (snapshot or {}).get("ports", []):
            if port.get("connected"):
                for report in port.get("trackers", []):
                    reports[tuple(report["mac"])] = {**report, "port": port["path"]}
        selected = tuple(self.tracker.currentData() or ())
        changed = list(reports) != list(self.reports)
        self.reports = reports
        if changed:
            self.tracker.blockSignals(True)
            self.tracker.clear()
            for mac, report in reports.items():
                self.tracker.addItem(f"{report['name']} · {report['port']} / {report['channel']}", list(mac))
            if selected in reports:
                self.tracker.setCurrentIndex(list(reports).index(selected))
            self.tracker.blockSignals(False)
            if selected != tuple(self.tracker.currentData() or ()):
                self.dirty = False
        if not self.pending and not self.dirty:
            self.load_report()
        elif not self.fresh() and not self.pending:
            self.notice.setText("Waiting for fresh settings from the selected tracker. Unapplied edits retained.")
        self.enable_controls()

    def load_report(self, *_):
        if self.pending:
            return
        self.dirty = False
        if self.fresh():
            settings = self.report()["settings"]
            self.loading = True
            self.mode.setCurrentIndex(self.mode.findData(settings["sensor_mode"]))
            self.fps.setCurrentIndex(self.fps.findData(settings["fps"]))
            for key in ("accelerometer", "gyroscope", "magnetometer"):
                getattr(self, key).setChecked(settings[key])
            self.loading = False
            self.notice.setText("Reported by tracker. Changes require an explicit Apply.")
        else:
            self.notice.setText("Waiting for a connected tracker with fresh settings readback.")
        self.enable_controls()

    def edited(self, *_):
        if self.loading:
            return
        self.dirty = True
        self.notice.setText("Unapplied sensor settings.")
        self.enable_controls()

    def submit(self):
        if not self.fresh() or self.pending:
            return
        self.pending = True
        self.enable_controls()
        self.notice.setText("Waiting for tracker confirmation…")
        self.command_requested.emit({"command": "set_haritora_settings", "tracker_mac": list(self.tracker.currentData()),
            "hardware": {"sensor_mode": self.mode.currentData(), "fps": self.fps.currentData(),
                **{key: getattr(self, key).isChecked() for key in ("accelerometer", "gyroscope", "magnetometer")}}})

    def command_finished(self, command, ok, error):
        if command != "set_haritora_settings":
            return
        self.pending = False
        self.dirty = not ok
        self.enable_controls()
        self.notice.setText("Tracker confirmed settings. Repeat standing and mounting calibration." if ok else error)
