#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Review captured audio labels and export only explicitly approved training data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import wave
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'apps/spatial-subtitles/python'))
from subtitle_recording import recording_root
from settings import atomic_json
from text_utils import clean_text


def export_rows(root):
    rows=[]
    for path in sorted((root/'captures').glob('*.json')):
        if path.is_symlink():continue
        data=json.loads(path.read_text())
        if data.get('reviewed') is not True:continue
        audio=path.with_suffix('.wav')
        if audio.is_symlink() or not audio.is_file():continue
        digest=hashlib.sha256(audio.read_bytes()).hexdigest()
        if digest!=data['audio_sha256']:raise ValueError('Changed audio: '+path.stem)
        with wave.open(str(audio),'rb') as f:
            if (f.getnchannels(),f.getsampwidth(),f.getframerate())!=(1,2,16000):raise ValueError('Invalid training WAV')
            seconds=f.getnframes()/16000
        if not 0<seconds<=30:raise ValueError('Invalid clip duration')
        rows.append(dict(path=str(audio.resolve()),id=path.stem,sha256=digest,seconds=seconds,
            text=data['corrected_text'],language=data['source_language'],split='train',reviewed=True,
            source='user-reviewed WiVRn speaker capture'))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=recording_root())
    commands=p.add_subparsers(dest='command',required=True)
    commands.add_parser('list')
    approve=commands.add_parser('approve');approve.add_argument('id');approve.add_argument('--text',required=True)
    approve.add_argument('--language',required=True,help='Spoken language code, e.g. ko, zh, ja, en, sr; not translation language')
    export=commands.add_parser('export');export.add_argument('output',type=Path)
    a=p.parse_args()
    if a.command=='list':
        for path in sorted((a.root/'captures').glob('*.json')):
            if path.is_symlink():continue
            data=json.loads(path.read_text());print(json.dumps(dict(id=path.stem,reviewed=data.get('reviewed',False),hypothesis=data.get('hypothesis','')),ensure_ascii=False))
    elif a.command=='approve':
        if not re.fullmatch('[a-f0-9]{32}',a.id):raise ValueError('Invalid capture ID')
        if not re.fullmatch('[a-z]{2,3}',a.language):raise ValueError('Use a Whisper language code')
        text=clean_text(a.text,1500)
        if not text:raise ValueError('A corrected original-language transcript is required')
        path=a.root/'captures'/(a.id+'.json')
        if path.is_symlink():raise ValueError('Unsafe capture metadata path')
        data=json.loads(path.read_text());data.update(reviewed=True,corrected_text=text,source_language=a.language)
        atomic_json(path,data);print('Approved',a.id)
    else:
        rows=export_rows(a.root)
        if not rows:raise ValueError('No reviewed clips to export')
        # Copy reviewed WAVs into an independent dataset so recording retention
        # cannot remove inputs in the middle of a training run.
        audio_dir=a.output.with_suffix('').with_name(a.output.stem+'-audio')
        audio_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        for row in rows:
            target=audio_dir/(row['id']+'.wav')
            if target.is_symlink():raise ValueError('Unsafe export path')
            import os
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:
                os.fchmod(f.fileno(),0o600);f.write(Path(row['path']).read_bytes())
            row['path']=str(target.resolve())
        atomic_json(a.output,dict(dataset='reviewed-wivrn-captures',rows=rows))
        print('Exported',len(rows),'reviewed training clips to',a.output)
if __name__=='__main__':main()
