#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional OCR-package settings; contains no speech/subtitle controls."""
from dataclasses import asdict
import sys
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QFormLayout,QLabel,QComboBox,QLineEdit,QSpinBox,QPushButton,QMessageBox
from settings import Settings,PROVIDERS,BACKENDS,LANGUAGES
import deepl_translation,google_translation
class Window(QWidget):
    def __init__(self):
        super().__init__();self.setWindowTitle('Hands Frame OCR and translation');self.resize(650,500)
        self.settings=Settings.load();self.fields={};layout=QVBoxLayout(self)
        note=QLabel('OCR runs on the selected local GPU. Google or DeepL receives recognized text only; images stay on this PC. Original output skips translation.');note.setWordWrap(True);layout.addWidget(note)
        form=QFormLayout();layout.addLayout(form)
        options={'translation_provider':PROVIDERS,'ocr_backend':BACKENDS,'local_device':('A770','GPU.0','GPU.1','GPU.2','GPU.3'),'source_language':LANGUAGES}
        labels={'translation_provider':'Translation provider','ocr_backend':'OCR GPU backend','local_device':'OpenVINO GPU','gpu_device_id':'CUDA / ROCm GPU index','local_ocr_model':'OCR recognition model directory','source_language':'Source language','ocr_max_regions':'Maximum text regions','ocr_detector_size':'OCR detector size'}
        for name,value in asdict(self.settings).items():
            if name in options:
                field=QComboBox();field.addItems(options[name]);field.setCurrentText(value)
            elif type(value) is int:
                field=QSpinBox();field.setRange(0,960);field.setValue(value)
            else:field=QLineEdit(value)
            self.fields[name]=field;form.addRow(labels[name],field)
        def update_gpu_fields():
            openvino=self.fields['ocr_backend'].currentText()=='OpenVINO'
            self.fields['local_device'].setEnabled(openvino);self.fields['gpu_device_id'].setEnabled(not openvino)
        self.fields['ocr_backend'].currentTextChanged.connect(update_gpu_fields);update_gpu_fields()
        self.keys={}
        for name,api in [('DeepL API Free',deepl_translation),('Google Cloud Translation',google_translation)]:
            field=QLineEdit();field.setEchoMode(QLineEdit.Password)
            field.setPlaceholderText('Leave blank to keep the saved key')
            self.keys[api]=field;form.addRow(name+' key',field)
        save=QPushButton('Save');save.clicked.connect(self.save);layout.addWidget(save)
    def save(self):
        try:
            values={name:(field.currentText() if isinstance(field,QComboBox) else field.value() if isinstance(field,QSpinBox) else field.text()) for name,field in self.fields.items()}
            validated=Settings.parse(values)
            for api,field in self.keys.items():
                if field.text().strip():api.save_key(field.text());field.clear()
            validated.save();self.settings=validated
            QMessageBox.information(self,'Hands Frame','Settings saved. They apply to subsequent text captures.')
        except (OSError,ValueError) as error:QMessageBox.warning(self,'Hands Frame',str(error))
if __name__=='__main__':
    app=QApplication(sys.argv);window=Window();window.show();sys.exit(app.exec())
