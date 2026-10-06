# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'dashboard/shora'))
import subtitle_backend as sub
import ocr_backend as ocr


def response(regions):
    return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'regions':regions})}]}]}


def region():
    return dict(text='Излаз',box=[.2,.3,.4,.1],confidence=.95,foreground=[1,.8,.2],background=[.1,.2,.3])


class OCRTests(unittest.TestCase):
    def test_vision_request_and_colour_preservation(self):
        calls=[]
        def opened(request,timeout):
            calls.append(json.loads(request.data)); return io.BytesIO(json.dumps(response([region()])).encode())
        result=ocr.translate_scene(bytes(1024*1024*4),replace(sub.Settings(),ocr_language='Serbian'),threading.Event(),sub.OpenAIBackend('fake',opened))
        self.assertEqual(result[0]['foreground'],[1,.8,.2]); self.assertEqual(result[0]['background'],[.1,.2,.3])
        self.assertEqual(result[0]['text'],'Излаз')
        body=calls[0]; self.assertFalse(body['store']); self.assertEqual(body['model'],'gpt-6-luna')
        content=body['input'][0]['content']; self.assertIn('Serbian',content[0]['text'])
        self.assertTrue(content[1]['image_url'].startswith('data:image/png;base64,'))
        cancelled=threading.Event(); cancelled.set()
        self.assertEqual(ocr.translate_scene(b'',sub.Settings(),cancelled),[])
        self.assertEqual(len(calls),1)

    def test_reject_malformed_boxes_colours_and_low_confidence(self):
        def run(regions):
            class Fake:
                def request(self,*args): return response(regions)
            return ocr.translate_scene(bytes(1024*1024*4),sub.Settings(),threading.Event(),Fake())
        self.assertEqual(run([dict(region(),confidence=.3)]),[])
        for bad in [dict(region(),box=[.9,0,.2,.1]),dict(region(),foreground=[float('nan'),0,0]),
                    dict(region(),background=[2,0,0]),dict(region(),box=[0,0,.1]),dict(region(),confidence=True)]:
            with self.assertRaises(sub.APIError): run([bad])
        with self.assertRaises(sub.APIError): run([region()]*5)
        png=ocr.png_rgba(bytes(16),2,2)
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n'); self.assertEqual(struct.unpack('!II',png[16:24]),(2,2))

    def test_memory_frame_roundtrip_and_disable_drops_inflight_result(self):
        events=[]; controller=ocr.SceneOCR(lambda **event:events.append(event))
        settings=replace(sub.Settings(),ocr_enabled=True)
        start=threading.Event(); finish=threading.Event()
        def translate(*args): start.set(); finish.wait(2); return [dict(region(),speaker=0,bearing=0,spatial=True)]
        fd=os.memfd_create('test-ocr',os.MFD_CLOEXEC); os.write(fd,bytes(1024*1024*4))
        try:
            with patch('ocr_backend.api_key',return_value='fake'),patch('ocr_backend.translate_scene',side_effect=translate):
                now=time.monotonic(); controller.tick(now,settings)
                self.assertEqual(events[-1]['ocr_request'],controller.request)
                metadata=dict(ocr_frame=controller.request,captured=now,capture_pose=[0,0,0,0,0,0,1],capture_fov=[-.8,.8,.8,-.8])
                controller.receive(json.dumps(metadata).encode(),[fd],settings,time.monotonic()); fd=-1
                self.assertTrue(start.wait(1)); self.assertIsNotNone(controller.future)
                controller.reset(False,True); events.clear(); finish.set()
                controller.future[0].result(timeout=2); controller.tick(time.monotonic(),sub.Settings())
                self.assertFalse(events); self.assertIn('disabled',controller.state)
        finally:
            finish.set(); controller.close(); controller.executor.shutdown(wait=True)
            if fd>=0: os.close(fd)

    def test_disabled_and_unconfigured_never_request_scene(self):
        events=[]; controller=ocr.SceneOCR(lambda **event:events.append(event))
        try:
            controller.tick(1,sub.Settings()); self.assertFalse(events)
            with patch('ocr_backend.api_key',side_effect=ValueError()):
                controller.tick(2,replace(sub.Settings(),ocr_enabled=True))
            self.assertFalse(events)
            for values in [{'ocr_enabled':1},{'ocr_interval':0},{'ocr_distance':float('nan')},{'ocr_language':''}]:
                with self.assertRaises(ValueError): sub.Settings.parse(values)
        finally: controller.close()

    def test_gui_separate_language_enable_and_hot_disable(self):
        from test_subtitles import SubtitlePanelTests
        from subtitle_panel import SubtitlePanel
        SubtitlePanelTests.setUpClass()
        with tempfile.TemporaryDirectory() as tmp:
            panel=SubtitlePanel(tmp,Path(tmp)/'status')
            try:
                self.assertFalse(panel.ocr_enabled.isChecked())
                panel.ocr_enabled.setChecked(True); panel.ocr_language.setCurrentText('Serbian'); panel.save()
                settings=sub.Settings.load(panel.path)
                self.assertTrue(settings.ocr_enabled); self.assertFalse(settings.enabled)
                self.assertEqual(settings.ocr_language,'Serbian'); self.assertEqual(settings.language,'English')
                panel.ocr_enabled.setChecked(False); panel.enabled.setChecked(True); panel.save()
                settings=sub.Settings.load(panel.path)
                self.assertFalse(settings.ocr_enabled); self.assertTrue(settings.enabled)
            finally: panel.close()

if __name__=='__main__': unittest.main()
