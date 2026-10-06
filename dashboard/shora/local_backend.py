# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional GPU recognition subprocess. No audio/images are sent to a provider."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from local_worker import send, receive
from subtitle_backend import APIError, clean_text, direction


def runtime_root():
    return Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'wivrn-pro/local-ai'


def check_runtime():
    if not (runtime_root()/'venv/bin/python').is_file():
        raise ValueError('Install local recognition with fork/local-ai/setup.sh first')


class LocalBackend:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = self.channel = None
        self.device = None

    def close(self):
        if self.channel: self.channel.close(); self.channel=None
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=.15)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            self.process=None

    def start(self, device, speech_model='', ocr_model=''):
        if self.process and self.process.poll() is None and self.device == (device,speech_model,ocr_model): return
        self.close(); check_runtime()
        parent,child = socket.socketpair()
        self.channel=parent; self.device=(device,speech_model,ocr_model)
        log=runtime_root()/'worker.log'
        with log.open('ab') as err:
            try:
                self.process=subprocess.Popen([str(runtime_root()/'venv/bin/python'),
                    str(Path(__file__).with_name('local_worker.py')),'--fd',str(child.fileno()),'--device',device,
                    '--speech-model',speech_model,'--ocr-model',ocr_model],
                    pass_fds=(child.fileno(),),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err)
            finally: child.close()
        parent.settimeout(45)
        result=receive(parent)
        if not result.get('ready'): raise APIError(result.get('error','Local recognition could not start'))
        parent.settimeout(12)

    def infer(self, kind, raw, settings, cancelled, **dimensions):
        if cancelled and cancelled.is_set(): return None
        with self.lock:
            if cancelled and cancelled.is_set(): return None
            try:
                self.start(settings.local_device,settings.local_speech_model,settings.local_ocr_model)
                if cancelled and cancelled.is_set(): return None
                send(self.channel,dict(kind=kind,data=base64.b64encode(raw).decode(),**dimensions))
                result=receive(self.channel)
                if 'error' in result: raise APIError(result['error'])
                return None if cancelled and cancelled.is_set() else result
            except APIError:
                self.close()
                raise
            except (OSError,EOFError,ValueError):
                self.close()
                raise APIError('Local GPU worker failed or timed out; run local-ai doctor') from None

    def subtitle(self, segment, settings, voices, cancelled=None):
        result=self.infer('speech',segment.pcm,settings,cancelled)
        if not result or not clean_text(result.get('text')): return []
        bearing,spatial=direction(segment.pans,settings.spatial)
        lines=[dict(id=0,text=clean_text(result['text'],1500),source_text=clean_text(result['text'],1500),speaker=0,offset=0,bearing=bearing,spatial=spatial)]
        from local_translation import translate
        return translate(lines,settings.language,settings,cancelled)

    def scene(self, rgba, settings, cancelled):
        # Existing capture is tightly packed 1024x1024 RGBA. Strip alpha locally.
        raw=bytearray(len(rgba)//4*3)
        raw[0::3]=rgba[0::4]; raw[1::3]=rgba[1::4]; raw[2::3]=rgba[2::4]
        result=self.infer('image',raw,settings,cancelled,width=1024,height=1024)
        if not result: return []
        regions=[r for r in result['regions'] if r['confidence']>=settings.local_ocr_confidence]
        # Foreign scripts first, then largest remaining regions. Never synthesize
        # text for unreadable signs; preserve detector geometry through translation.
        regions.sort(key=lambda r:(any(ord(c)>127 for c in r['text']),r['box'][2]*r['box'][3]),reverse=True)
        lines=[dict(r,source_text=r['text'],id=i,speaker=0,bearing=0,spatial=True) for i,r in enumerate(regions[:4])]
        from local_translation import translate
        return translate(lines,settings.ocr_language,settings,cancelled)


_backend=None
_guard=threading.Lock()


def backend():
    global _backend
    with _guard:
        if _backend is None: _backend=LocalBackend()
        return _backend


def close():
    if _backend: _backend.close()
