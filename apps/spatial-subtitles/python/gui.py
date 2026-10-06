#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import asdict
import json, sys, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QLabel,
    QScrollArea,
    QFileDialog,
    QHBoxLayout,
)
from settings import Settings, CONFIG, RUNTIME, atomic_json


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spatial Subtitles — local OpenVINO")
        self.resize(680, 840)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Independent VR overlay. Speech, scene text, translation and voice colours run on this PC. No external inference or account is used."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.fields = {}
        choices = {
            "language": ["English", "Serbian", "Original"],
            "ocr_language": ["English", "Serbian", "Original"],
            "source_language": [
                "Auto",
                "Japanese",
                "Korean",
                "Chinese",
                "Thai",
                "English",
                "Serbian",
                "Russian",
            ],
            "local_device": ["A770", "GPU.0", "GPU.1"],
            "font": ["Jupiteroid", "Sans"],
        }
        labels = {
            "enabled": "Enable spoken subtitles",
            "split_voices_within_chunk": "Split voices within a speech chunk (slower)",
            "ocr_enabled": "Enable scene OCR",
            "language": "Subtitle output language",
            "ocr_language": "Scene output language",
            "source_language": "Source language (Auto uses script detection)",
            "audio_sink": "Playback sink to monitor",
            "local_translation_model": "Trained translation export",
            "local_speech_model": "Trained speech export",
            "local_ocr_model": "Trained OCR heads directory",
            "local_speaker_model": "Trained speaker model",
            "save_training_audio": "Save audio for reviewed training",
            "log_speech": "Save speech transcripts",
            "log_ocr": "Save scene text transcripts",
        }
        for name, value in asdict(Settings()).items():
            if isinstance(value, bool):
                w = QCheckBox()
            elif name in choices:
                w = QComboBox()
                w.addItems(choices[name])
            elif isinstance(value, str):
                w = QLineEdit()
            elif isinstance(value, int):
                w = QSpinBox()
                w.setRange(1, 8192)
            else:
                w = QDoubleSpinBox()
                w.setDecimals(3)
                w.setRange(0.001, 365)
                w.setSingleStep(0.1)
            self.fields[name] = w
            label = labels.get(name, name.replace("_", " ").capitalize())
            if name.endswith("_model"):
                row = QHBoxLayout()
                row.addWidget(w)
                button = QPushButton("Browse…")
                button.clicked.connect(
                    lambda checked=False, field=w: self.browse(field)
                )
                row.addWidget(button)
                form.addRow(label, row)
            else:
                form.addRow(label, w)
        info = QLabel(
            "Scene capture is opt-in per application through the bundled Vulkan OpenXR layer. Voice direction is estimated from stereo audio. Auto language detection can confuse Japanese text containing only kanji; select Japanese explicitly when needed. Recordings stay local and require review before training."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        button = QPushButton("Apply now")
        button.clicked.connect(self.save)
        layout.addWidget(button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.load()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    def browse(self, w):
        path = QFileDialog.getExistingDirectory(self, "Select local model", w.text())
        if path:
            w.setText(path)

    def load(self):
        try:
            s = Settings.load()
        except Exception as e:
            self.status.setText(str(e))
            s = Settings()
        for name, w in self.fields.items():
            v = getattr(s, name)
            if isinstance(w, QCheckBox):
                w.setChecked(v)
            elif isinstance(w, QComboBox):
                w.setCurrentText(v)
            elif isinstance(w, QLineEdit):
                w.setText(v)
            else:
                w.setValue(v)

    def save(self):
        try:
            d = {
                k: (
                    w.isChecked()
                    if isinstance(w, QCheckBox)
                    else w.currentText()
                    if isinstance(w, QComboBox)
                    else w.text()
                    if isinstance(w, QLineEdit)
                    else w.value()
                )
                for k, w in self.fields.items()
            }
            s = Settings.parse(d)
            atomic_json(CONFIG, asdict(s))
            self.status.setText("Applied locally.")
        except Exception as e:
            self.status.setText(str(e))

    def refresh(self):
        try:
            d = json.loads((RUNTIME / "status.json").read_text())
            self.status.setText(
                d["state"]
                if time.time() - d["updated"] < 5
                else "Overlay backend is stopped"
            )
        except (OSError, ValueError):
            self.status.setText("Start Spatial Subtitles to activate the overlay.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec())
