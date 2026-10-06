# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'dashboard/shora'))
sys.path.insert(0,str(ROOT/'fork/local-ai/training'))
from subtitle_backend import Settings
from subtitle_recording import Recorder
from review_captures import export_rows

LINES=[dict(text='Hello',source_text='こんにちは',speaker=0,offset=0)]
class RecordingTests(unittest.TestCase):
    def test_off_creates_nothing_and_validates_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Recorder(Path(tmp)/'recordings');r.configure(Settings())
            try:self.assertIsNone(r.record('speech',LINES,time.monotonic(),bytes(32000)));self.assertFalse(r.root.exists())
            finally:r.close()
        for item in [{'save_training_audio':1},{'log_ocr':'true'},{'recording_max_mb':0},{'recording_retention_days':float('nan')}]:
            with self.assertRaises(ValueError):Settings.parse(item)

    def test_independent_audio_logging_and_review_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Recorder(Path(tmp)/'recordings');s=replace(Settings(),enabled=True,save_training_audio=True)
            r.configure(s)
            try:
                r.record('speech',LINES,time.monotonic(),bytes(32000)).result(2)
                files=list((r.root/'captures').glob('*.json'));self.assertEqual(len(files),1)
                data=json.loads(files[0].read_text());self.assertFalse(data['reviewed']);self.assertEqual(data['hypothesis'],'こんにちは')
                self.assertFalse((r.root/'transcripts').exists());self.assertEqual(export_rows(r.root),[])
                data.update(reviewed=True,corrected_text='こんにちは',source_language='ja');files[0].write_text(json.dumps(data))
                rows=export_rows(r.root);self.assertEqual(rows[0]['text'],'こんにちは');self.assertEqual(rows[0]['language'],'ja')
                self.assertEqual(files[0].stat().st_mode&0o777,0o600)
                self.assertEqual(files[0].with_suffix('.wav').stat().st_mode&0o777,0o600)
                self.assertEqual(r.root.stat().st_mode&0o777,0o700)
                r.configure(replace(s,save_training_audio=False,log_speech=True,log_ocr=True,ocr_enabled=True))
                r.record('speech',LINES,time.monotonic(),bytes(32000)).result(2)
                r.record('ocr',LINES,time.monotonic()).result(2)
                logs=[json.loads(x) for x in next((r.root/'transcripts').glob('*.jsonl')).read_text().splitlines()]
                self.assertEqual([x['kind'] for x in logs],['speech','ocr'])
                self.assertEqual(logs[0]['lines'][0]['source_text'],'こんにちは')
                self.assertEqual(len(list((r.root/'captures').glob('*.wav'))),1)
                self.assertIsNone(r.record('ocr',LINES,time.monotonic()))
            finally:r.close()

    def test_disable_cancels_pending_save_and_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Recorder(Path(tmp)/'recordings');s=replace(Settings(),enabled=True,log_speech=True);r.configure(s)
            try:
                with r.lock:
                    future=r.record('speech',LINES,time.monotonic())
                    r.configure(Settings())
                future.result(2);self.assertFalse(r.root.exists())
                elsewhere=Path(tmp)/'elsewhere';elsewhere.mkdir();r.root.symlink_to(elsewhere,target_is_directory=True)
                r.configure(s);r.record('speech',LINES,time.monotonic()).result(2)
                self.assertFalse(list(elsewhere.iterdir()));self.assertIn('Could not save',r.state)
            finally:r.close()

    def test_retention_and_failed_storage_do_not_stop_recognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Recorder(Path(tmp)/'recordings');s=replace(Settings(),enabled=True,save_training_audio=True,recording_retention_days=1)
            r.configure(s)
            try:
                r.record('speech',LINES,time.monotonic(),bytes(32000)).result(2)
                old=list((r.root/'captures').iterdir())
                for p in old:os.utime(p,(time.time()-172800,)*2)
                r.record('speech',LINES,time.monotonic(),bytes(32000)).result(2)
                self.assertTrue(all(not p.exists() for p in old))
                with patch('subtitle_recording.write_private',side_effect=OSError('full')):
                    r.record('speech',LINES,time.monotonic(),bytes(32000)).result(2)
                self.assertIn('Could not save',r.state)
            finally:r.close()

    def test_gui_round_trip(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        from subtitle_panel import SubtitlePanel
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            panel=SubtitlePanel(tmp,Path(tmp)/'status')
            try:
                for name in ('save_training_audio','log_speech','log_ocr'):
                    self.assertFalse(getattr(panel,name).isChecked());getattr(panel,name).setChecked(True)
                panel.recording_days.setValue(3);panel.recording_mb.setValue(64);panel.save();panel.load()
                s=Settings.load(panel.path)
                self.assertTrue(s.save_training_audio and s.log_speech and s.log_ocr)
                self.assertEqual(s.recording_retention_days,3);self.assertEqual(s.recording_max_mb,64)
            finally:panel.timer.stop();panel.close()

    def test_storage_cap_removes_old_records_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'recordings';(root/'transcripts').mkdir(parents=True)
            old=root/'transcripts/old.jsonl'
            with old.open('wb') as f:f.truncate(32*1024*1024)
            r=Recorder(root);r.configure(replace(Settings(),enabled=True,log_speech=True,recording_max_mb=32))
            try:
                r.record('speech',LINES,time.monotonic()).result(2)
                self.assertFalse(old.exists())
                self.assertLess(sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),32*1024*1024)
            finally:r.close()

if __name__=='__main__':unittest.main()
