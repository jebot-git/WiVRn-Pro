#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Train a CJK speaker metric on A770: OpenVINO backbone + PyTorch XPU head.

Every unique manifest window is consumed. The backbone stays frozen; the
256x256 residual metric is trained on dataset speaker labels with clean/noisy
views. Reports distinguish source hours, augmentation and speaker-disjoint
validation/test. No identity classifier is shipped to the live application.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import openvino as ov
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'apps/spatial-subtitles/python'))
from local_recognition import select_device
from local_speaker import SpeakerModel


def pairs(rows,split,language,seed=713):
    rng=np.random.default_rng(seed);groups={}
    for i,r in enumerate(rows):
        if r['split']==split and r['language']==language:groups.setdefault(r['speaker'],[]).append(i)
    groups={k:v for k,v in groups.items() if len(v)>=2}
    keys=list(groups)
    if len(keys)<2:raise ValueError('Too few held-out speakers for '+language+'/'+split)
    left=[];right=[];labels=[]
    for _ in range(2000):
        speaker,other=rng.choice(keys,2,replace=False)
        a,b=rng.choice(groups[speaker],2,replace=False)
        # Reject windows from the same source utterance when possible.
        for _ in range(12):
            if rows[a]['source_file']!=rows[b]['source_file']:break
            b=rng.choice(groups[speaker])
        if rows[a]['source_file']==rows[b]['source_file']:continue
        left.extend([a,a]);right.extend([b,rng.choice(groups[other])]);labels.extend([1,0])
    if not labels:raise ValueError('No independent held-out utterance pairs')
    return np.array(left),np.array(right),np.array(labels,dtype=bool)


def metric(vectors,trials):
    a,b,same=trials;scores=(vectors[a]*vectors[b]).sum(axis=1)
    thresholds=np.linspace(-.1,.99,400)
    errors=[(float(np.mean(scores[~same]>=t)),float(np.mean(scores[same]<t))) for t in thresholds]
    best=min(range(len(errors)),key=lambda i:abs(errors[i][0]-errors[i][1]))
    far,frr=errors[best]
    return dict(eer=(far+frr)/2,threshold=float(thresholds[best]),false_match=far,false_reject=frr,
                pairs=len(same),positive_mean=float(scores[same].mean()),negative_mean=float(scores[~same].mean()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--base',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--epochs',type=int,default=30);p.add_argument('--minimum-hours',type=float,default=10)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(4);torch.manual_seed(713);rng=np.random.default_rng(713)
    rows=json.loads(a.data.read_text())['rows'];hours=Counter()
    for row in rows:hours[row['split']]+=row['seconds']/3600
    if hours['train']<a.minimum_hours:raise ValueError('Less than requested unique training audio: '+str(hours))
    if len({r['sha256'] for r in rows})!=len(rows):raise ValueError('Duplicate audio hashes')
    speakers={split:{r['speaker'] for r in rows if r['split']==split} for split in hours}
    if any(speakers[x]&speakers[y] for x in speakers for y in speakers if x!=y):raise ValueError('Speaker leakage')
    device,name=select_device();candidates=[i for i in range(torch.xpu.device_count()) if 'A770' in torch.xpu.get_device_name(i)]
    if len(candidates)!=1:raise RuntimeError('Exactly one A770 required for training')
    xpu=torch.device('xpu',candidates[0]);torch.xpu.set_device(xpu)
    cache=a.output/'embeddings.npz';manifest_hash=hashlib.sha256(a.data.read_bytes()).hexdigest()
    start=time.monotonic()
    if cache.exists():
        archive=np.load(cache)
        if str(archive['manifest_hash'])!=manifest_hash:raise ValueError('Embedding cache is from another corpus')
        clean,augmented=archive['clean'],archive['augmented']
    else:
        model=SpeakerModel(a.base,device);clean=[];augmented=[]
        for i,row in enumerate(rows):
            pcm,sr=sf.read(a.data.parent/row['path'],dtype='int16')
            if sr!=16000 or hashlib.sha256(pcm.astype('<i2').tobytes()).hexdigest()!=row['sha256']:
                raise ValueError('Corpus audio changed: '+row['path'])
            x=pcm.astype(np.float32)/32768
            v=model.embed(x)
            if v is None:raise ValueError('Non-speech/too quiet training window')
            # Noise and mild reverberation approximate playback/room changes.
            noise=rng.normal(size=len(x)).astype(np.float32)
            noise*=np.sqrt(np.mean(x*x))*10**(-rng.uniform(15,30)/20)
            noisy=x+noise;delay=int(rng.uniform(.02,.06)*16000)
            noisy[delay:]+=x[:-delay]*rng.uniform(.1,.3)
            clean.append(v);augmented.append(model.embed(noisy))
            if i%500==0:print('Features',i,'/',len(rows),flush=True)
        clean=np.array(clean,dtype=np.float32);augmented=np.array(augmented,dtype=np.float32)
        np.savez(cache,clean=clean,augmented=augmented,manifest_hash=manifest_hash)
    languages=sorted({r['language'] for r in rows})
    trials={split:{lang:pairs(rows,split,lang) for lang in languages} for split in ('validation','test')}
    baseline={split:{lang:metric(clean,t) for lang,t in sets.items()} for split,sets in trials.items()}
    head=torch.nn.Linear(256,256,bias=False).to(xpu)
    with torch.no_grad():head.weight.copy_(torch.eye(256,device=xpu))
    optimizer=torch.optim.AdamW(head.parameters(),lr=.0003,weight_decay=0)
    train=np.array([i for i,r in enumerate(rows) if r['split']=='train'])
    label_names=sorted(speakers['train']);lookup={s:i for i,s in enumerate(label_names)}
    labels=np.array([lookup.get(r['speaker'],-1) for r in rows])
    by_speaker={s:np.flatnonzero(labels==s) for s in range(len(label_names))}
    X=torch.tensor(clean,device=xpu);A=torch.tensor(augmented,device=xpu)
    best=float('inf');history=[];selected=0
    for epoch in range(a.epochs):
        rng.shuffle(train);losses=[]
        for start_idx in range(0,len(train),128):
            ids=train[start_idx:start_idx+128]
            positives=np.array([rng.choice(by_speaker[labels[i]]) for i in ids])
            batch=torch.tensor(np.concatenate([ids,positives]),device=xpu)
            y=torch.tensor(labels[np.concatenate([ids,positives])],device=xpu)
            z=F.normalize(head(torch.cat([X[batch[:len(ids)]],A[batch[len(ids):]]])),dim=1)
            similarities=z@z.T
            same=y[:,None]==y[None,:]; diagonal=torch.eye(len(y),device=xpu,dtype=torch.bool)
            positive=same&~diagonal;negative=~same
            # Pull within-speaker utterances together and push confusable voices apart.
            loss=F.relu(.8-similarities[positive]).square().mean()+F.relu(similarities[negative]-.25).square().mean()
            loss+=.05*(head.weight-torch.eye(256,device=xpu)).square().sum()/256
            optimizer.zero_grad();loss.backward();optimizer.step();losses.append(float(loss.detach().cpu()))
        with torch.no_grad():adapted=F.normalize(head(X),dim=1).cpu().numpy()
        scores={lang:metric(adapted,t) for lang,t in trials['validation'].items()}
        error=float(np.mean([v['eer'] for v in scores.values()]))
        history.append(dict(epoch=epoch+1,loss=float(np.mean(losses)),validation=scores))
        print('Epoch',epoch+1,'loss',history[-1]['loss'],'validation EER',error,flush=True)
        if error<best:
            best=error;selected=epoch+1
            np.save(a.output/'projection-weight.npy',head.weight.detach().cpu().numpy())
    with torch.no_grad():
        head.weight.copy_(torch.tensor(np.load(a.output/'projection-weight.npy'),device=xpu))
        adapted=F.normalize(head(X),dim=1).cpu().numpy()
    evaluation={split:{lang:metric(adapted,t) for lang,t in sets.items()} for split,sets in trials.items()}
    head=head.cpu().eval()
    ov_model=ov.convert_model(head,example_input=torch.zeros(1,256))
    ov.save_model(ov_model,a.output/'projection.xml',compress_to_fp16=False)
    shutil.copy2(a.base/'speaker.onnx',a.output/'speaker.onnx')
    compiled=ov.Core().compile_model(a.output/'projection.xml',device)
    ov_vectors=np.concatenate([compiled([v[None]])[0].copy() for v in clean])
    ov_vectors/=np.linalg.norm(ov_vectors,axis=1,keepdims=True)
    difference=float(np.max(np.abs(ov_vectors-adapted)))
    counts=Counter((r['source'],r['language'],r['genre'],r['split']) for r in rows)
    report=dict(kind='frozen WeSpeaker ResNet34 + trained 256x256 speaker metric',device=name,
        base_revision='f0c48c298fd835726c27956a5d617bad7115627e',manifest_sha256=manifest_hash,
        hours=dict(hours),speakers={k:len(v) for k,v in speakers.items()},windows=len(rows),
        corpus_counts={'/'.join(k):v for k,v in counts.items()},baseline=baseline,evaluation=evaluation,
        selected_epoch=selected,history=history,openvino_max_abs_difference=difference,
        elapsed_seconds=time.monotonic()-start,
        limitations=['Emilia labels are automatic; this evaluation is not human-labelled diarization error rate.',
            'Source-scoped labels cannot guarantee globally disjoint identities.',
            'No overlap separation or identity recognition.'],
        promoted=False)
    (a.output/'training-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('history','corpus_counts')},indent=2),flush=True)

if __name__=='__main__':main()
