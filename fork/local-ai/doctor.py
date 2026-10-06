#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with local-ai/venv/bin/python. Inputs and results stay on this PC."""
import argparse
import json
from pathlib import Path
import sys
import time
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'dashboard/shora'))
from local_recognition import LocalRecognizer
p=argparse.ArgumentParser()
p.add_argument('--device',default='A770')
p.add_argument('--wav',type=Path,action='append',default=[])
p.add_argument('--image',type=Path)
p.add_argument('--repeat',type=int,default=2)
p.add_argument('--voices',action='store_true')
p.add_argument('--speaker-model',default='')
a=p.parse_args()
r=LocalRecognizer(a.device,speaker_model=a.speaker_model)
start=time.monotonic(); r.load_speech(); r.load_ocr()
print(json.dumps(dict(device=r.device,name=r.device_name,load_seconds=time.monotonic()-start,ocr_devices=r.compiled_devices)))
for path in a.wav:
    with wave.open(str(path),'rb') as w:
        if (w.getnchannels(),w.getframerate(),w.getsampwidth())!=(1,16000,2):
            raise ValueError('Expected mono 16 kHz PCM16 WAV')
        pcm=w.readframes(w.getnframes())
    result=r.speech(pcm,distinguish=a.voices)
    # Report cluster IDs, never dump the speaker embedding vectors.
    if a.voices:
        from local_speaker import SpeakerTracker
        if 'tracker' not in globals():tracker=SpeakerTracker()
        for segment in result.get('segments',[]):
            segment['speaker']=tracker.assign(segment.pop('embedding',None))
    print(json.dumps(dict(file=path.name,**result),ensure_ascii=False))
if a.image:
    from PIL import Image
    import numpy as np
    pixels=np.asarray(Image.open(a.image).convert('RGB'))
    for i in range(max(1,min(a.repeat,10))):
        print(json.dumps(dict(iteration=i,**r.image(pixels)),ensure_ascii=False))
