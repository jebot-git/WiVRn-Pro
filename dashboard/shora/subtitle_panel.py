# SPDX-License-Identifier: GPL-3.0-or-later
"""Hot subtitle controls; secrets never travel through WiVRn's network protocol."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QCheckBox,
    QLineEdit, QComboBox, QDoubleSpinBox, QPushButton, QHBoxLayout, QFileDialog, QSpinBox)
from subtitle_backend import Settings, atomic_json, atomic_write, config_dir, status_path


class SubtitlePanel(QWidget):
    def __init__(self, directory=None, status=None):
        super().__init__()
        self.directory = Path(directory) if directory else config_dir()
        self.path = self.directory / 'subtitles.json'
        self.status_file = Path(status) if status else status_path()
        self.expected = None
        layout = QVBoxLayout(self)
        title = QLabel('Speech and scene translation'); title.setObjectName('heading'); layout.addWidget(title)
        note = QLabel('Cloud recognition sends WiVRn speaker audio and enabled scene captures to OpenAI. '
            'Local OpenVINO recognition keeps audio and images on this PC; only recognized text is sent for translation. '
            'Original output stays fully local. Your headset microphone is not used.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout(); layout.addLayout(form)
        self.recognition = QComboBox()
        self.recognition.addItem('OpenAI API', 'openai')
        self.recognition.addItem('Local OpenVINO (experimental)', 'openvino')
        form.addRow('Speech and OCR recognition', self.recognition)
        self.local_translation = QComboBox()
        self.local_translation.addItem('ChatGPT sign-in (Codex)', 'chatgpt')
        self.local_translation.addItem('OpenAI API', 'openai')
        form.addRow('Translate locally recognized text with', self.local_translation)
        self.local_device = QComboBox(); self.local_device.addItems(['A770','GPU.0','GPU.1','GPU.2','GPU.3'])
        form.addRow('OpenVINO GPU (A770 selects by name)',self.local_device)
        self.local_model = QLineEdit(); self.local_model.setPlaceholderText('Bundled Whisper large-v3-turbo (leave blank)')
        model_row = QHBoxLayout(); model_row.addWidget(self.local_model)
        self.browse_model = QPushButton('Choose export…'); model_row.addWidget(self.browse_model)
        self.browse_model.clicked.connect(self.choose_model)
        form.addRow('Trained speech model (OpenVINO directory)', model_row)
        self.local_ocr_model = QLineEdit(); self.local_ocr_model.setPlaceholderText('Bundled OCR models (leave blank)')
        ocr_model_row = QHBoxLayout(); ocr_model_row.addWidget(self.local_ocr_model)
        self.browse_ocr_model = QPushButton('Choose export…'); ocr_model_row.addWidget(self.browse_ocr_model)
        self.browse_ocr_model.clicked.connect(self.choose_ocr_model)
        form.addRow('Trained OCR heads (model directory)',ocr_model_row)
        self.local_confidence = QDoubleSpinBox(); self.local_confidence.setRange(.5,1); self.local_confidence.setSingleStep(.05)
        form.addRow('Minimum local OCR confidence',self.local_confidence)
        self.enabled = QCheckBox('Enable recognition and translation')
        form.addRow(self.enabled)
        self.language = QComboBox(); self.language.setEditable(True)
        self.language.addItems(['English', 'Serbian', 'Original'])
        form.addRow('Output language', self.language)
        self.font = QComboBox(); self.font.addItem('Jupiteroid (CC0)', 'jupiteroid'); self.font.addItem('System sans serif', 'system')
        form.addRow('Subtitle font', self.font)
        self.voices = QCheckBox('Distinguish voices with labels and colours')
        form.addRow(self.voices)
        self.recognition.currentIndexChanged.connect(self.backend_changed)
        self.spatial = QCheckBox('Place subtitles using estimated stereo direction')
        form.addRow(self.spatial)
        self.model = QLineEdit(); form.addRow('Translation model', self.model)
        self.transcription = QComboBox(); self.transcription.setEditable(True)
        self.transcription.addItems(['gpt-4o-transcribe-diarize', 'gpt-4o-mini-transcribe', 'gpt-transcribe'])
        form.addRow('Speech model', self.transcription)
        self.voices.toggled.connect(lambda value: self.transcription.setCurrentText('gpt-4o-transcribe-diarize') if value else None)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Leave blank to keep the saved key or OPENAI_API_KEY')
        form.addRow('OpenAI API key', self.key)
        self.key_state = QLabel(); form.addRow(self.key_state)
        self.numbers = {}
        for name, label, lo, hi, step in [('duration', 'Display duration (seconds)', 2,10,.5),
                ('scale', 'Subtitle size', .6,1.5,.1), ('segment_seconds', 'Maximum audio chunk (seconds)',1,5,.5),
                ('silence_seconds', 'Pause to finish a phrase (seconds)',.2,1.5,.1),
                ('threshold', 'Audio activity threshold',.001,.2,.001)]:
            box = QDoubleSpinBox(); box.setDecimals(3 if name == 'threshold' else 1)
            box.setRange(lo, hi); box.setSingleStep(step); self.numbers[name] = box; form.addRow(label, box)
        ocr_heading = QLabel('Scene text translation (experimental)'); ocr_heading.setObjectName('heading'); layout.addWidget(ocr_heading)
        ocr_note = QLabel('When enabled, occasional images of the streamed OpenXR scene are read by the selected recognition backend. '
            'Images stay in memory locally. Only the streamed scene is captured; headset passthrough and system UI are excluded. '
            'Translations cover text with approximately matching text and background colours. Placement uses an approximate plane; '
            'moving text may become misaligned. API usage is billed to your account.')
        ocr_note.setWordWrap(True); layout.addWidget(ocr_note)
        ocr_form = QFormLayout(); layout.addLayout(ocr_form)
        self.ocr_enabled = QCheckBox('Enable scene text translation'); ocr_form.addRow(self.ocr_enabled)
        self.ocr_language = QComboBox(); self.ocr_language.setEditable(True)
        self.ocr_language.addItems(['English','Serbian','Original']); ocr_form.addRow('Scene output language',self.ocr_language)
        for name,label,lo,hi,step in [('ocr_interval','Seconds between scene scans',2,30,1),
                ('ocr_duration','Scene overlay duration (seconds)',2,10,.5),
                ('ocr_distance','Approximate overlay plane (metres)',.5,10,.5)]:
            box = QDoubleSpinBox(); box.setRange(lo,hi); box.setSingleStep(step); box.setDecimals(1)
            self.numbers[name] = box; ocr_form.addRow(label,box)
        recording_title = QLabel('Local recordings and training'); recording_title.setObjectName('heading'); layout.addWidget(recording_title)
        recording_note = QLabel('Optional: save recognized speaker-audio clips for later training and/or save transcript logs. '
            'Files stay on this PC. Review and correct training labels before using them; saving clips does not retrain or replace the model. '
            'Logs include original recognized text and translations. Scene images and headset microphone audio are never saved. '
            'Older files are deleted when the age or storage limit is reached while recording is active.')
        recording_note.setWordWrap(True); layout.addWidget(recording_note)
        recording_form = QFormLayout(); layout.addLayout(recording_form)
        self.save_training_audio = QCheckBox('Save recognized audio clips for training (review required)')
        self.log_speech = QCheckBox('Save speech transcripts and translations')
        self.log_ocr = QCheckBox('Save scene-text transcripts and translations')
        for box in (self.save_training_audio,self.log_speech,self.log_ocr):recording_form.addRow(box)
        self.recording_days=QSpinBox();self.recording_days.setRange(1,365)
        self.recording_mb=QSpinBox();self.recording_mb.setRange(32,8192)
        recording_form.addRow('Keep recordings for days',self.recording_days)
        recording_form.addRow('Maximum recordings size (MiB)',self.recording_mb)
        from subtitle_recording import recording_root
        self.recording_folder=recording_root()
        location=QLabel(str(self.recording_folder));location.setWordWrap(True)
        recording_form.addRow('Local folder',location)
        open_recordings=QPushButton('Open recordings folder');open_recordings.clicked.connect(self.open_recordings)
        recording_form.addRow(open_recordings)
        limitations = QLabel('Scene text uses the same translation backend, model and font selected above. '
            'Up to four clear text regions are shown. Head movement hides old results; moving scene objects are not tracked. '
            'Direction is a left/right estimate from the speaker mix, not a game-object location. '
            'Uncertain directions stay centered. Overlapping voices and background music can reduce accuracy. '
            'Voice labels are anonymous and provisional until a clear reference is available; up to four voices are remembered for this session. '
            'Local recognition requires the optional OpenVINO setup, has no voice identification yet, and skips low-confidence text. '
            'ChatGPT translation requires codex login and consumes ChatGPT plan usage.')
        limitations.setWordWrap(True); layout.addWidget(limitations)
        actions = QHBoxLayout(); layout.addLayout(actions)
        self.apply = QPushButton('Apply during session'); self.apply.clicked.connect(self.save); actions.addWidget(self.apply)
        reload = QPushButton('Reload settings'); reload.clicked.connect(self.load); actions.addWidget(reload)
        remove = QPushButton('Remove saved key'); remove.clicked.connect(self.remove_key); actions.addWidget(remove)
        self.notice = QLabel(); self.notice.setWordWrap(True); layout.addWidget(self.notice)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        layout.addStretch()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_status); self.timer.start(1000)
        self.load(); self.refresh_status()

    def open_recordings(self):
        if not self.recording_folder.is_dir():
            self.notice.setText('No recordings yet. Enable an option and apply it during a session.');return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.recording_folder)))

    def read(self): return self.path.read_text() if self.path.exists() else None

    def backend_changed(self):
        local = self.recognition.currentData() == 'openvino'
        self.local_translation.setEnabled(local); self.local_device.setEnabled(local); self.local_confidence.setEnabled(local)
        self.voices.setEnabled(not local)
        self.local_model.setEnabled(local); self.browse_model.setEnabled(local)
        self.local_ocr_model.setEnabled(local); self.browse_ocr_model.setEnabled(local)
        if local: self.voices.setChecked(False)

    def choose_model(self):
        directory = QFileDialog.getExistingDirectory(self,'Select completed OpenVINO Whisper export',self.local_model.text())
        if directory: self.local_model.setText(directory)

    def choose_ocr_model(self):
        directory = QFileDialog.getExistingDirectory(self,'Select completed OCR head export',self.local_ocr_model.text())
        if directory: self.local_ocr_model.setText(directory)

    def load(self):
        try:
            self.expected = self.read()
            settings = Settings.parse(json.loads(self.expected)) if self.expected is not None else Settings()
            self.recognition.setCurrentIndex(self.recognition.findData(settings.recognition_backend))
            self.local_translation.setCurrentIndex(self.local_translation.findData(settings.local_translation))
            self.local_device.setCurrentText(settings.local_device); self.local_confidence.setValue(settings.local_ocr_confidence)
            for name in ('save_training_audio','log_speech','log_ocr'):getattr(self,name).setChecked(getattr(settings,name))
            self.recording_days.setValue(settings.recording_retention_days);self.recording_mb.setValue(settings.recording_max_mb)
            self.local_model.setText(settings.local_speech_model)
            self.local_ocr_model.setText(settings.local_ocr_model)
            self.ocr_enabled.setChecked(settings.ocr_enabled); self.ocr_language.setCurrentText(settings.ocr_language)
            self.enabled.setChecked(settings.enabled); self.language.setCurrentText(settings.language)
            self.font.setCurrentIndex(self.font.findData(settings.font))
            self.voices.setChecked(settings.distinguish_voices); self.spatial.setChecked(settings.spatial)
            self.model.setText(settings.model); self.transcription.setCurrentText(settings.transcription_model)
            for name, box in self.numbers.items(): box.setValue(getattr(settings, name))
            self.key.clear(); self.notice.setText('Changes apply within a second; no VR restart is needed.')
            self.backend_changed()
        except (OSError, ValueError, TypeError): self.notice.setText('Cannot read subtitle settings. Fix the configuration file and reload.')
        self.refresh_key()

    def refresh_key(self):
        self.key_state.setText('A private API key is saved.' if (self.directory / 'subtitles-openai.key').exists()
            else 'Uses OPENAI_API_KEY from the server environment, if set.')

    def save(self):
        try:
            settings = Settings.parse(dict(ocr_enabled=self.ocr_enabled.isChecked(),ocr_language=self.ocr_language.currentText().strip(),enabled=self.enabled.isChecked(), language=self.language.currentText().strip(),
                recognition_backend=self.recognition.currentData(),local_translation=self.local_translation.currentData(),
                local_device=self.local_device.currentText(),local_ocr_confidence=self.local_confidence.value(),
                save_training_audio=self.save_training_audio.isChecked(),log_speech=self.log_speech.isChecked(),log_ocr=self.log_ocr.isChecked(),
                recording_retention_days=self.recording_days.value(),recording_max_mb=self.recording_mb.value(),
                local_speech_model=self.local_model.text().strip(),
                local_ocr_model=self.local_ocr_model.text().strip(),
                font=self.font.currentData(), distinguish_voices=self.voices.isChecked(), spatial=self.spatial.isChecked(), model=self.model.text().strip(),
                transcription_model=self.transcription.currentText().strip(), **{name:box.value() for name,box in self.numbers.items()}))
            if self.read() != self.expected: raise ValueError('Settings changed elsewhere. Reload before applying.')
            key = self.key.text().strip()
            if key:
                if len(key) > 512 or any(c.isspace() for c in key): raise ValueError('Invalid API key')
                atomic_write(self.directory / 'subtitles-openai.key', key + '\n')
            atomic_json(self.path, asdict(settings)); self.expected = self.read()
            self.key.clear(); self.refresh_key()
            self.notice.setText('Applied. Waiting for an active VR session.' if settings.enabled or settings.ocr_enabled else 'Speech and scene translation disabled.')
        except (OSError, ValueError, TypeError) as exc: self.notice.setText(str(exc))

    def remove_key(self):
        try:
            (self.directory / 'subtitles-openai.key').unlink(missing_ok=True)
            self.key.clear(); self.refresh_key()
            self.notice.setText('Saved key removed. OPENAI_API_KEY may still be supplied by the server environment.')
        except OSError: self.notice.setText('Could not remove the saved key.')

    def refresh_status(self):
        try:
            status = json.loads(self.status_file.read_text())
            if time.time() - status['updated'] > 4: raise ValueError()
            self.status.setText(f"{status['state']} · skipped stale/busy chunks: {status.get('dropped', 0)}\n{status.get('ocr_state', 'Scene translation disabled')}\n{status.get('recording_state','Recording off')}")
        except (OSError, ValueError, KeyError, TypeError): self.status.setText('No active subtitle worker. Connect the matching headset client to WiVRn.')
