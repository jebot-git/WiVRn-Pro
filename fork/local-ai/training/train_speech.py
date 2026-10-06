#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded multilingual decoder LoRA pilot on Intel XPU, with held-out CER."""
import argparse
import json
import math
from pathlib import Path
import random
import time
import unicodedata
import numpy as np
import soundfile as sf
import torch
from transformers import WhisperForConditionalGeneration, WhisperProcessor
from peft import LoraConfig, get_peft_model


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]


def main():
    p=argparse.ArgumentParser(); p.add_argument('--base',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--steps',type=int,default=60); p.add_argument('--learning-rate',type=float,default=5e-5)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
    random.seed(173); np.random.seed(173); torch.manual_seed(173); torch.set_num_threads(4)
    devices=[i for i in range(torch.xpu.device_count()) if 'A770' in torch.xpu.get_device_name(i)]
    if len(devices)!=1: raise RuntimeError('Exactly one A770 required; no CPU training fallback')
    device=torch.device('xpu',devices[0]); torch.xpu.set_device(device)
    processor=WhisperProcessor.from_pretrained(a.base)
    model=WhisperForConditionalGeneration.from_pretrained(a.base,attn_implementation='sdpa',torch_dtype=torch.float32).to(device)
    model.generation_config.forced_decoder_ids=None
    model.generation_config.max_new_tokens=180
    model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,
        target_modules=r'model.decoder.*\.(q_proj|v_proj)',bias='none'))
    manifest=json.loads(a.data.read_text()); rows=manifest['rows']
    # Official train and validation are separate; do not tune on test-room audio.
    if {r['sha256'] for r in rows if r['split']=='train'} & {r['sha256'] for r in rows if r['split']=='validation'}:
        raise RuntimeError('Train/evaluation audio overlap')
    prepared=[]
    for row in rows:
        audio,sr=sf.read(row['path'],dtype='float32')
        features=processor.feature_extractor(audio,sampling_rate=sr,return_tensors='pt').input_features
        processor.tokenizer.set_prefix_tokens(language=row['language'],task='transcribe',predict_timestamps=False)
        labels=processor.tokenizer(row['text'],return_tensors='pt').input_ids[:,1:]
        if labels.shape[1]>400: continue
        prepared.append((row,features,labels))
    def evaluate():
        model.eval(); results=[]; totals={}
        with torch.inference_mode():
            for row,features,labels in prepared:
                if row['split']!='validation': continue
                generated=model.generate(input_features=features.to(device),language=row['language'],task='transcribe',max_new_tokens=180,do_sample=False)
                text=processor.batch_decode(generated,skip_special_tokens=True)[0]
                ref,hyp=normalize(row['text']),normalize(text); error=distance(ref,hyp)
                total=totals.setdefault(row['language'],[0,0]); total[0]+=error; total[1]+=len(ref)
                results.append(dict(id=row['id'],language=row['language'],reference=row['text'],hypothesis=text,edits=error,characters=len(ref)))
        return dict(cer={k:v[0]/max(1,v[1]) for k,v in totals.items()},rows=results)
    print('GPU',torch.xpu.get_device_name(device),'trainable',sum(p.numel() for p in model.parameters() if p.requires_grad),flush=True)
    baseline=evaluate(); (a.output/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2))
    print('Baseline CER',baseline['cer'],flush=True)
    groups={language:[x for x in prepared if x[0]['split']=='train' and x[0]['language']==language] for language in ('ko','zh','ja')}
    for group in groups.values(): random.shuffle(group)
    if not all(groups.values()): raise RuntimeError('Missing language training samples')
    optim=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=a.learning_rate)
    model.train(); model.config.use_cache=False; started=time.monotonic()
    losses=[]
    for step in range(a.steps):
        optim.zero_grad(set_to_none=True); summed=0
        # One example per language per update: balanced despite length differences.
        for language,group in groups.items():
            row,features,labels=group[step%len(group)]
            loss=model(input_features=features.to(device),labels=labels.to(device)).loss/3
            if not torch.isfinite(loss): raise RuntimeError('Non-finite training loss')
            loss.backward(); summed+=loss.item()
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],1)
        optim.step(); losses.append(summed)
        if (step+1)%5==0 or step==0: print('Step',step+1,'loss',round(summed,4),'seconds',round(time.monotonic()-started,1),flush=True)
    model.config.use_cache=True
    model.save_pretrained(a.output/'adapter'); processor.save_pretrained(a.output/'adapter')
    after=evaluate()
    report=dict(base=str(a.base),dataset=manifest['dataset'],revision=manifest['revision'],licence=manifest['licence'],
        device=torch.xpu.get_device_name(device),steps=a.steps,train_samples=sum(map(len,groups.values())),
        validation_samples=len(after['rows']),losses=losses,baseline=baseline['cer'],candidate=after['cer'],
        passes_pilot_gate=all(after['cer'][k]<=baseline['cer'][k] for k in baseline['cer']),
        warning='Small adaptation pilot; validation is not an independent final test and does not establish general improvement.')
    (a.output/'evaluation.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
    (a.output/'report.json').write_text(json.dumps(report,indent=2))
    merged=model.merge_and_unload(); merged.to('cpu'); merged.save_pretrained(a.output/'merged'); processor.save_pretrained(a.output/'merged')
    print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__': main()
