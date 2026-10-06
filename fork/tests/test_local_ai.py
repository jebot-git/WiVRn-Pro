# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'dashboard/shora'))
import subtitle_backend as sub
import local_worker as wire
import local_translation as translation
from local_backend import LocalBackend

class LocalTests(unittest.TestCase):
    def test_settings_reject_cpu_and_invalid_models(self):
        for value in ({'local_device':'CPU'}, {'local_speaker_threshold':float('nan')},
                      {'local_ocr_confidence':float('nan')}, {'local_speech_model':'relative/path'}, {'local_ocr_model':42}):
            with self.assertRaises(ValueError): sub.Settings.parse(value)
        self.assertEqual(sub.Settings.parse({'recognition_backend':'openvino','distinguish_voices':False}).local_device,'A770')

    def test_private_frames_and_size_bound(self):
        a,b=socket.socketpair()
        with a,b:
            wire.send(a,{'text':'日本語 한국어 中文'})
            self.assertEqual(wire.receive(b)['text'],'日本語 한국어 中文')
            a.sendall(struct.pack('!I',wire.MAX_REQUEST+1))
            with self.assertRaises(ValueError): wire.receive(b)

    def test_original_never_invokes_provider(self):
        with patch.object(translation,'agent_translate',side_effect=AssertionError('network')):
            self.assertEqual(translation.translate([{'text':'한국어','speaker':0}],'Original',sub.Settings())[0]['text'],'한국어')

    def test_translation_ids_cache_and_cancel(self):
        translation._cache.clear()
        lines=[{'text':'窓','box':[.1,.1,.2,.1]}]
        with patch.object(translation,'agent_translate',return_value={'translations':[{'id':0,'text':'Window'}]}) as agent:
            for i in range(2):
                result=translation.translate(lines,'English',sub.Settings())
                self.assertEqual(result[0]['box'],lines[0]['box'])
            self.assertEqual(agent.call_count,1)
            cancelled=threading.Event(); cancelled.set()
            self.assertEqual(translation.translate(lines,'English',sub.Settings(),cancelled),[])
        translation._cache.clear()
        with patch.object(translation,'agent_translate',return_value={'translations':[{'id':9,'text':'wrong'}]}):
            with self.assertRaises(sub.APIError): translation.translate(lines,'English',sub.Settings())

    def test_failed_start_closes_worker(self):
        backend=LocalBackend()
        with patch.object(backend,'start',side_effect=sub.APIError('failed')), patch.object(backend,'close') as close:
            with self.assertRaises(sub.APIError): backend.infer('speech',b'12',sub.Settings(),None)
            close.assert_called_once()

    def test_gui_local_voice_options_roundtrip(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from subtitle_panel import SubtitlePanel
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            panel=SubtitlePanel(tmp,Path(tmp)/'status.json')
            panel.recognition.setCurrentIndex(1)
            self.assertTrue(panel.voices.isChecked()); self.assertTrue(panel.voices.isEnabled())
            panel.speaker_model.setText('/tmp/speaker-export');panel.speaker_threshold.setValue(.72)
            panel.local_model.setText("/tmp/completed-whisper-export")
            panel.local_ocr_model.setText("/tmp/completed-ocr-export")
            panel.save()
            settings=sub.Settings.load(Path(tmp)/'subtitles.json')
            self.assertEqual(settings.recognition_backend,'openvino')
            self.assertEqual(settings.local_speech_model,'/tmp/completed-whisper-export')
            self.assertEqual(settings.local_ocr_model,'/tmp/completed-ocr-export')
            self.assertEqual(settings.local_speaker_model,'/tmp/speaker-export')
            self.assertEqual(settings.local_speaker_threshold,.72)
            panel.timer.stop(); panel.close()

if __name__=='__main__': unittest.main()
