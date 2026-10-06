# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from dataclasses import asdict, replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'dashboard/shora'))
import subtitle_backend as sub


def frame(left=0, right=0):
    return b''.join(struct.pack('<hh', int(left*math.sin(i*.15)), int(right*math.sin(i*.15))) for i in range(320))


class SubtitleTests(unittest.TestCase):
    def test_defaults_validation_and_private_key(self):
        self.assertFalse(sub.Settings().enabled)
        self.assertEqual(sub.Settings().language, 'English')
        self.assertEqual(sub.Settings().model, 'gpt-6-luna')
        for bad in ({'duration':float('nan')}, {'enabled':1}, {'threshold':0}, {'model':'x\r\ny'}, {'bogus':1}, {'font':'unknown'},
                    {'transcription_model':'gpt-4o-mini-transcribe'}):
            with self.assertRaises(ValueError): sub.Settings.parse(bad)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_CONFIG_HOME':tmp}):
            path = sub.config_dir() / 'subtitles-openai.key'
            sub.atomic_write(path, 'test-key')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sub.api_key(), 'test-key')
            path.chmod(0o644)
            with self.assertRaises(ValueError): sub.api_key()

    def test_silence_is_not_uploaded_and_direction_is_conservative(self):
        settings = replace(sub.Settings(), segment_seconds=1)
        gate = sub.Segmenter(settings)
        for i in range(100): self.assertIsNone(gate.feed(frame(), i*.02))
        samples = [gate.feed(frame(12000,2000), 2+i*.02) for i in range(80)]
        segment = next(x for x in samples if x)
        angle, spatial = sub.direction(segment.pans, True)
        self.assertTrue(spatial); self.assertLess(angle, 0)
        self.assertLessEqual(len(segment.pcm), 32000)
        self.assertEqual(sub.direction([0]*50, True), (0,False))
        self.assertFalse(sub.direction([-.8,.8]*25, True)[1])
        self.assertFalse(sub.direction([.8]*50, False)[1])
        self.assertEqual(sub.direction([], True), (0,False))

    def test_diarization_translation_and_session_voice_references(self):
        requests = []
        turns = [dict(text='Ahoj', speaker='A', start=0, end=2.1), dict(text='Guten Tag', speaker='B', start=2.1, end=3)]
        def opened(request, timeout):
            requests.append(request)
            if request.full_url.endswith('transcriptions'): result = {'segments': turns}
            else:
                body = json.loads(request.data)
                self.assertEqual(body['model'], 'gpt-6-luna')
                self.assertFalse(body['store']); self.assertEqual(body['reasoning'], {'effort':'none'})
                self.assertEqual(json.loads(body['input'])['output_language'], 'English')
                result = {'status':'completed','output':[{'type':'message','content':[{'type':'output_text',
                    'text':json.dumps({'translations':[{'id':0,'text':'Hello'},{'id':1,'text':'Good day'}]})}]}]}
            return io.BytesIO(json.dumps(result).encode())
        segment = sub.Segment(bytes(32000*3), 10, [-.5]*105+[.5]*45)
        voices = sub.Voices()
        result = sub.OpenAIBackend('fake-key', opened).subtitle(segment, sub.Settings(), voices)
        self.assertEqual([x['text'] for x in result], ['Hello','Good day'])
        self.assertNotEqual(result[0]['speaker'], result[1]['speaker'])
        self.assertLess(result[0]['bearing'], 0); self.assertGreater(result[1]['bearing'], 0)
        self.assertIn('Voice 1', voices.references)
        self.assertIn(b'diarized_json', requests[0].data)
        self.assertNotIn(b'name="prompt"', requests[0].data)
        turns[0]['speaker'] = 'Voice 1'
        result = sub.OpenAIBackend('fake-key', opened).subtitle(segment, sub.Settings(), voices)
        self.assertEqual(result[0]['speaker'], 1)
        self.assertIn(b'known_speaker_references[]', requests[2].data)
        self.assertNotEqual(result[1]['speaker'], 2) # B is chunk-local, not a stable identity.
        self.assertEqual(voices.assign('A', {}), 4)

    def test_overlap_does_not_claim_spatial_location_or_register_voice(self):
        def opened(*args, **kwargs):
            return io.BytesIO(json.dumps({'segments':[
                dict(text='one', speaker='A', start=0,end=2.5),dict(text='two',speaker='B',start=1,end=3)]}).encode())
        voices = sub.Voices()
        lines = sub.OpenAIBackend('fake',opened).subtitle(sub.Segment(bytes(96000),0,[.8]*150),
            replace(sub.Settings(),language='Original'),voices)
        self.assertTrue(all(not x['spatial'] for x in lines)); self.assertFalse(voices.references)

    def test_inflight_results_cannot_escape_disable_or_language_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_CONFIG_HOME':tmp,'XDG_RUNTIME_DIR':tmp}):
            a,b = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
            worker = sub.Worker(a)
            try:
                path = sub.config_dir() / 'subtitles.json'
                sub.atomic_json(path, asdict(replace(sub.Settings(),enabled=True)))
                worker.reload(10); b.recv(4096)
                old = worker.generation
                future = Future()
                worker.future = (future,old,sub.Segment(b'',9,[]),worker.settings)
                sub.atomic_json(path, asdict(sub.Settings()))
                worker.reload(10.1); self.assertEqual(json.loads(b.recv(4096)),{'text':''})
                future.set_result([{'text':'Must not appear','speaker':1}])
                worker.tick(10.2)
                b.setblocking(False)
                with self.assertRaises(BlockingIOError): b.recv(4096)
                self.assertFalse(worker.settings.enabled); self.assertIsNone(worker.capture)
            finally: worker.ocr.close(); worker.stop_capture(); worker.executor.shutdown(); a.close(); b.close()

    def test_worker_default_off_and_parent_close_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
            env = dict(os.environ, XDG_CONFIG_HOME=tmp, XDG_RUNTIME_DIR=tmp)
            env.pop('OPENAI_API_KEY',None)
            process = subprocess.Popen([sys.executable, str(ROOT/'dashboard/shora/subtitle_backend.py')],stdin=b,env=env,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            b.close()
            try:
                a.settimeout(3)
                self.assertEqual(json.loads(a.recv(4096)),{'text':''})
                a.close(); out,err = process.communicate(timeout=3)
                self.assertEqual(process.returncode,0,err.decode())
                self.assertNotIn(b'key',out)
            finally:
                if process.poll() is None: process.kill(); process.communicate()
                a.close()


os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication, QLineEdit
from subtitle_panel import SubtitlePanel

class SubtitlePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def test_hot_controls_persistence_disable_and_no_secret_in_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = SubtitlePanel(tmp, Path(tmp)/'status')
            try:
                self.assertFalse(panel.enabled.isChecked())
                self.assertEqual(panel.key.echoMode(), QLineEdit.EchoMode.Password)
                panel.key.setText('fake-key'); panel.enabled.setChecked(True)
                self.assertEqual([panel.language.itemText(i) for i in range(2)], ['English','Serbian'])
                panel.language.setCurrentText('Serbian'); panel.font.setCurrentIndex(1); panel.save()
                config = json.loads(panel.path.read_text())
                self.assertTrue(config['enabled']); self.assertEqual(config['language'],'Serbian'); self.assertEqual(config['font'],'system')
                self.assertTrue(config['distinguish_voices']); self.assertNotIn('fake-key',panel.path.read_text())
                self.assertEqual(panel.key.text(),'')
                panel.enabled.setChecked(False); panel.save()
                self.assertFalse(json.loads(panel.path.read_text())['enabled'])
                sub.atomic_json(panel.path, dict(config,language='French'))
                panel.save(); self.assertIn('changed elsewhere',panel.notice.text())
                self.assertEqual(json.loads(panel.path.read_text())['language'],'French')
            finally: panel.close()

if __name__ == '__main__': unittest.main()
