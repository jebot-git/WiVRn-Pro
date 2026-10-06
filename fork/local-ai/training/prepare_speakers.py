#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a deduplicated speaker corpus; audio and manifests stay outside Git.

Emilia access must already be authorised by the account owner. Speaker labels
are dataset labels (automatically produced for Emilia), not verified identities.
CN-Celeb's human-corrected labels supply an independent multi-genre source.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

EMILIA_REVISION = 'd7f2f7340a6385696f3766c8049fa920a4707c07'


def language_matches(text,lang):
    letters=[c for c in str(text) if c.isalpha()]
    if len(letters)<3:return False
    hangul=sum('\uac00'<=c<='\ud7a3' for c in letters)
    kana=sum('\u3040'<=c<='\u30ff' for c in letters)
    han=sum('\u4e00'<=c<='\u9fff' for c in letters)
    return {'ko':hangul/len(letters)>.5,'ja':kana>=2 and (kana+han)/len(letters)>.5,
            'zh':han/len(letters)>.5}[lang]


class Corpus:
    def __init__(self, root):
        self.root=root; (root/'audio').mkdir(parents=True,exist_ok=True)
        self.path=root/'manifest.json'
        self.rows=json.loads(self.path.read_text())['rows'] if self.path.exists() else []
        self.hashes={r['sha256'] for r in self.rows}
        self.seconds=Counter(); self.speakers=Counter()
        for r in self.rows:
            self.seconds[(r['source'],r['language'],r['split'])]+=r['seconds']
            self.speakers[r['speaker']]+=r['seconds']

    def add(self, raw, speaker, language, source, source_file, licence, genre, targets, split=None, speaker_limit=360):
        split=split or ('validation' if int(hashlib.sha256(speaker.encode()).hexdigest()[:8],16)%10==0
               else 'test' if int(hashlib.sha256(speaker.encode()).hexdigest()[:8],16)%10==1 else 'train')
        if self.seconds[(source,language,split)]>=targets[split] or self.speakers[speaker]>=speaker_limit: return
        try: samples,rate=sf.read(io.BytesIO(raw),dtype='float32',always_2d=True)
        except sf.LibsndfileError: return
        samples=samples.mean(axis=1)
        if rate!=16000:
            divisor=math.gcd(rate,16000);samples=resample_poly(samples,16000//divisor,rate//divisor)
        # Keep complete, non-overlapping windows; duration counts only saved audio.
        for start in range(0,len(samples),64000):
            x=samples[start:start+64000]
            if len(x)<16000 or np.sqrt(np.mean(x*x))<.005: continue
            pcm=np.clip(x*32768,-32768,32767).astype('<i2')
            digest=hashlib.sha256(pcm.tobytes()).hexdigest()
            if digest in self.hashes: continue
            duration=len(pcm)/16000
            if self.seconds[(source,language,split)]>=targets[split] or self.speakers[speaker]>=speaker_limit: break
            path=Path('audio')/(digest+'.wav');sf.write(self.root/path,pcm,16000,subtype='PCM_16')
            self.rows.append(dict(path=str(path),sha256=digest,seconds=duration,speaker=speaker,
                language=language,source=source,source_file=source_file,source_offset=start/16000,
                licence=licence,genre=genre,split=split))
            self.hashes.add(digest);self.seconds[(source,language,split)]+=duration;self.speakers[speaker]+=duration

    def complete(self,source,lang,targets):
        return all(self.seconds[(source,lang,split)]>=target for split,target in targets.items())

    def save(self):
        totals={'/'.join(k):round(v/3600,4) for k,v in self.seconds.items()}
        data=dict(schema=1,rows=self.rows,hours=totals,unique_pcm_windows=len(self.rows),
                  speaker_label_note='Emilia: automatic, source-scoped labels; CN-Celeb: corrected labels. Cross-source identities unknown.')
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False));temp.replace(self.path)
        print(json.dumps(totals),flush=True)


def emilia(corpus,lang,token,downloads,targets):
    from huggingface_hub import HfApi,hf_hub_download
    source='Emilia';prefix=f'Emilia/{lang.upper()}'
    files=HfApi(token=token).list_repo_tree('amphion/Emilia-Dataset',path_in_repo=prefix,
        revision=EMILIA_REVISION,repo_type='dataset')
    for item in files:
        if corpus.complete(source,lang,targets): break
        if not item.path.endswith('.tar'): continue
        archive=hf_hub_download('amphion/Emilia-Dataset',item.path,repo_type='dataset',
            revision=EMILIA_REVISION,token=token,local_dir=downloads)
        with tarfile.open(archive) as tar:
            # Metadata precedes or follows audio depending on the shard.
            members={m.name:m for m in tar if m.isfile()}
            for name,member in members.items():
                if not name.endswith('.json'): continue
                meta=json.load(tar.extractfile(member)); audio=name[:-5]+'.mp3'
                if audio not in members: continue
                if str(meta.get('language','')).lower()!=lang: continue
                if not language_matches(meta.get('text',''),lang) or float(meta.get('dnsmos',0))<3:continue
                speaker='Emilia/'+lang+'/'+str(meta['speaker'])
                corpus.add(tar.extractfile(members[audio]).read(),speaker,lang,source,
                    item.path+'#'+audio,'CC-BY-NC-4.0','unclassified online speech',targets)
                if corpus.complete(source,lang,targets):break
        corpus.save()


def cnceleb(corpus,archive,targets):
    allowed={'interview','entertainment','audiobook','live_broadcast','speech','vlog'}
    with tarfile.open(archive,'r|gz') as tar:
        for member in tar:
            # The data tree contains train + eval speakers; split ourselves by
            # speaker ID. Avoid duplicate eval/test symlinks/copies.
            if not member.isfile() or '/data/' not in member.name or not member.name.endswith('.flac'):continue
            parts=Path(member.name).stem.split('-');speaker=Path(member.name).parent.name
            genre=parts[0] if not parts[0].startswith('id') else parts[1]
            if genre not in allowed:continue
            corpus.add(tar.extractfile(member).read(),'CN-Celeb/'+speaker,'zh','CN-Celeb',member.name,
                'CC-BY-SA-4.0',genre,targets)
            if corpus.complete('CN-Celeb','zh',targets):break
    corpus.save()


def zeroth(corpus,archive,targets):
    with tarfile.open(archive,'r|gz') as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith('.flac'):continue
            speaker=Path(member.name).parent.name
            corpus.add(tar.extractfile(member).read(),'Zeroth/'+speaker,'ko','Zeroth',member.name,
                'CC-BY-4.0','read news',targets)
            if corpus.complete('Zeroth','ko',targets):break
    corpus.save()


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--emilia',choices=['ja','ko','zh']);p.add_argument('--token-file',type=Path)
    p.add_argument('--downloads',type=Path)
    p.add_argument('--cn-celeb',type=Path);p.add_argument('--train-hours',type=float,default=4)
    p.add_argument('--zeroth',type=Path)
    p.add_argument('--eval-hours',type=float,default=.5);a=p.parse_args()
    corpus=Corpus(a.output);targets=dict(train=a.train_hours*3600,validation=a.eval_hours*3600,test=a.eval_hours*3600)
    if a.emilia:emilia(corpus,a.emilia,a.token_file.read_text().strip() if a.token_file else None,a.downloads or a.output/'downloads',targets)
    if a.cn_celeb:cnceleb(corpus,a.cn_celeb,targets)
    if a.zeroth:zeroth(corpus,a.zeroth,targets)
    corpus.save()
    source,lang=('Emilia',a.emilia) if a.emilia else ('Zeroth','ko') if a.zeroth else ('CN-Celeb','zh')
    if not corpus.complete(source,lang,targets):raise SystemExit('Insufficient data: requested duration has not been reached')

if __name__=='__main__':main()
