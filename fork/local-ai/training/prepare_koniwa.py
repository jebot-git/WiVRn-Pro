#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add documented Japanese audiobooks and annotated radio speech to training.

Koniwa metadata: https://github.com/koniwa/koniwa . Archives are from the public
Drive link in that repository. Radio windows use conservative, automatic voice
clusters; these are explicitly not ground-truth speaker annotations.
"""
import argparse
import io
import json
import math
from pathlib import Path
import sys
import zipfile
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from prepare_speakers import Corpus
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'apps/spatial-subtitles/python'))
from local_recognition import select_device
from local_speaker import SpeakerModel


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    corpus=Corpus(a.output);model=SpeakerModel(a.root/'base',select_device()[0])
    targets=dict(train=1800,validation=0,test=0)
    for series in ('librivox','amagasaki','free_culture_2012'):
        centres=[]
        with zipfile.ZipFile(a.root/(series+'.zip')) as z:
            for filename in sorted(z.namelist()):
                if not filename.endswith('.mp3'):continue
                metadata=a.root/'koniwa/data'/series/(Path(filename).stem+'.json')
                if not metadata.exists():continue
                item=json.loads(metadata.read_text());meta=item['meta']
                if series=='librivox':
                    if not meta.get('speaker') or corpus.speakers['Koniwa/book/'+meta['speaker']]>=360:continue
                    corpus.add(z.read(filename),'Koniwa/book/'+meta['speaker'],'ja','Koniwa audiobook',
                        filename,meta['license_sound'],'audiobook',targets,split='train')
                    if corpus.complete('Koniwa audiobook','ja',targets):break
                    continue
                if not item['annotation']:continue
                audio,sr=sf.read(io.BytesIO(z.read(filename)),dtype='float32',always_2d=True)
                audio=audio.mean(axis=1)
                if sr!=16000:
                    g=math.gcd(sr,16000);audio=resample_poly(audio,16000//g,sr//g)
                spans=[]
                for annotation in item['annotation']:
                    start,end=annotation['start'],annotation['end']
                    if spans and start<=spans[-1][1]+.08:spans[-1][1]=end
                    else:spans.append([start,end])
                for start,end in spans:
                    for offset in np.arange(start,end-2,4):
                        x=audio[int(offset*16000):int(min(end,offset+4)*16000)]
                        mid=len(x)//2;left=model.embed(x[:mid]);right=model.embed(x[mid:])
                        if left is None or right is None or np.dot(left,right)<.55:continue
                        v=np.array(model.embed(x));scores=[float(v@c) for c in centres]
                        if scores and max(scores)>.60:index=int(np.argmax(scores))
                        elif not scores or max(scores)<.40:
                            index=len(centres);centres.append(v)
                        else:continue
                        data=io.BytesIO();sf.write(data,x,16000,format='WAV',subtype='PCM_16')
                        corpus.add(data.getvalue(),f'Koniwa/{series}/automatic-{index}','ja',
                            'Koniwa radio',filename+f'@{offset:.3f}',
                            'CC-BY-SA-3.0' if series=='free_culture_2012' else 'CC-BY-4.0',
                            'radio talk show / on-demand episode' if series=='free_culture_2012' else 'radio broadcast',
                            targets,split='train')
        corpus.save()
    provenance=dict(repository='https://github.com/koniwa/koniwa',
        archive_url='https://drive.google.com/drive/folders/1edUnYJpT8y0ZmAQSE_fJPQ6VnNBS6qWA',
        radio_labels='automatic WeSpeaker clusters, only annotated speech and stable two-half windows',
        audiobook_labels='reader names supplied by Koniwa; training IDs only, never sent to headset',
        note='J-WAVE archive README states BY-SA 3.0; preserve this stronger licence despite metadata saying BY 3.0')
    (a.output/'provenance.json').write_text(json.dumps(provenance,indent=2))

if __name__=='__main__':main()
