#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapt frozen PP-OCR features + CTC heads on A770 using public CJK text.

This is synthetic sign adaptation, not training on photographs or handwriting.
OpenVINO extracts frozen features on the Arc; PyTorch XPU trains the final heads.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import unicodedata
import cv2
import numpy as np
import onnx
from onnx import numpy_helper
import openvino as ov
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'dashboard/shora'))
from local_recognition import select_device


def distance(a,b):
 row=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  nxt=[i]
  for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
  row=nxt
 return row[-1]


def main():
 p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,required=True)
 p.add_argument('--models',type=Path,required=True); p.add_argument('--font',type=Path,required=True)
 p.add_argument('--output',type=Path,required=True); p.add_argument('--epochs',type=int,default=3)
 a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=False)
 torch.set_num_threads(4); torch.manual_seed(173); rng=random.Random(173)
 gpu,name=select_device('A770'); core=ov.Core()
 devices=[i for i in range(torch.xpu.device_count()) if 'A770' in torch.xpu.get_device_name(i)]
 if len(devices)!=1: raise RuntimeError('Exactly one A770 required')
 device=torch.device('xpu',devices[0]); torch.xpu.set_device(device)
 manifest=json.loads(a.data.read_text()); report=dict(dataset=manifest['dataset'],revision=manifest['revision'],
  licence=manifest['licence'],font_sha256=hashlib.sha256(a.font.read_bytes()).hexdigest(),
  font_source='https://github.com/notofonts/noto-cjk',font_licence='SIL Open Font License 1.1',
  device=name,epochs=a.epochs,kind='synthetic sign CTC-head adaptation; frozen detector and feature extractor',models={})
 for head,filename,languages in [('cjk','PP-OCRv6_rec_small.onnx',('zh','ja')),('korean','korean_PP-OCRv5_rec_mobile.onnx',('ko',))]:
  source=a.models/filename; graph=onnx.load(source)
  values={x.name:numpy_helper.to_array(x).copy() for x in graph.graph.initializer}
  chars=next(x.value for x in graph.metadata_props if x.key=='character').splitlines()
  chars=['blank']+chars+[' ']; lookup={c:i for i,c in enumerate(chars) if i}
  wname,bname='linear_8.w_0','linear_8.b_0'
  if len(chars)!=values[bname].shape[0]: raise RuntimeError('Dictionary mismatch')
  matmul=next(n for n in graph.graph.node if wname in n.input)
  model=core.read_model(source); feature=model.add_outputs(matmul.input[0])[0]
  model=ov.Model([feature],model.get_parameters())
  compiled=core.compile_model(model,gpu,{'PERFORMANCE_HINT':'LATENCY'})
  if compiled.get_property('EXECUTION_DEVICES')!=[gpu]: raise RuntimeError('Feature extraction used wrong GPU')
  prepared=[]
  for index,row in enumerate(manifest['rows']):
   if row['language'] not in languages:continue
   # Independent FLEURS validation phrases, never the VR room phrases.
   text=unicodedata.normalize('NFKC',row['text']).strip()[:rng.randint(8,18)]
   if not text or any(c not in lookup for c in text):continue
   font=ImageFont.truetype(str(a.font),rng.randint(22,40),index={'ja':0,'ko':1,'zh':2}[row['language']])
   bounds=font.getbbox(text); width,height=bounds[2]-bounds[0]+12,bounds[3]-bounds[1]+12
   light=rng.choice([True,False]); fg=tuple(rng.randint(0,65) if light else rng.randint(190,255) for _ in range(3))
   bg=tuple(rng.randint(190,255) if light else rng.randint(0,65) for _ in range(3))
   image=Image.new('RGB',(width,height),bg); ImageDraw.Draw(image).text((6-bounds[0],6-bounds[1]),text,font=font,fill=fg)
   image=image.filter(ImageFilter.GaussianBlur(rng.uniform(0,.45)))
   path=a.output/'examples'/row['split']/f'{index}.png';path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
   bgr=np.asarray(image)[:,:,::-1]; h,w=bgr.shape[:2]; target_w=max(320,math.ceil(48*w/h))
   scaled=cv2.resize(bgr,(math.ceil(48*w/h),48)).transpose(2,0,1).astype(np.float32)/127.5-1
   normalized=np.zeros((1,3,48,target_w),np.float32);normalized[0,:,:,:scaled.shape[2]]=scaled
   features=compiled([normalized])[0].copy()
   prepared.append(dict(text=text,language=row['language'],split=row['split'],features=torch.from_numpy(features).to(device),
                        labels=torch.tensor([lookup[c] for c in text],dtype=torch.long,device=device)))
  weight=torch.nn.Parameter(torch.from_numpy(values[wname]).to(device));bias=torch.nn.Parameter(torch.from_numpy(values[bname]).to(device))
  def evaluate():
   totals={}; results=[]
   with torch.no_grad():
    for row in prepared:
     if row['split']!='validation':continue
     ids=(row['features']@weight+bias).argmax(-1)[0].cpu().tolist(); last=None; text=''
     for value in ids:
      if value and value!=last:text+=chars[value]
      last=value
     ref=''.join(row['text'].split());hyp=''.join(text.split());err=distance(ref,hyp)
     total=totals.setdefault(row['language'],[0,0]);total[0]+=err;total[1]+=len(ref)
     results.append(dict(language=row['language'],reference=row['text'],hypothesis=text))
   return dict(cer={k:v[0]/max(1,v[1]) for k,v in totals.items()},rows=results)
  baseline=evaluate(); train=[r for r in prepared if r['split']=='train']
  if not train or len(baseline['cer'])!=len(languages):raise RuntimeError('Missing eligible train/validation language')
  optim=torch.optim.AdamW([weight,bias],lr=1e-5,weight_decay=0)
  loss_fn=torch.nn.CTCLoss(blank=0,zero_infinity=False)
  for epoch in range(a.epochs):
   rng.shuffle(train); losses=[]
   for row in train:
    optim.zero_grad(set_to_none=True); log_probs=(row['features']@weight+bias).log_softmax(-1).transpose(0,1)
    loss=loss_fn(log_probs,row['labels'],torch.tensor([log_probs.shape[0]]),torch.tensor([len(row['labels'])]))
    if not torch.isfinite(loss):raise RuntimeError('Non-finite CTC loss')
    loss.backward();torch.nn.utils.clip_grad_norm_([weight,bias],1);optim.step();losses.append(loss.item())
   print(head,'epoch',epoch+1,'loss',sum(losses)/len(losses),flush=True)
  after=evaluate()
  replacements={wname:weight.detach().cpu().numpy(),bname:bias.detach().cpu().numpy()}
  for initializer in graph.graph.initializer:
   if initializer.name in replacements: initializer.CopyFrom(numpy_helper.from_array(replacements[initializer.name],initializer.name))
  onnx.checker.check_model(graph);onnx.save(graph,a.output/(head+'.onnx'))
  report['models'][head]=dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),train_samples=len(train),
     validation_samples=len(after['rows']),baseline=baseline['cer'],candidate=after['cer'],
     passes_pilot_gate=all(after['cer'][k]<=baseline['cer'][k] for k in baseline['cer']))
  (a.output/(head+'-evaluation.json')).write_text(json.dumps(dict(before=baseline,after=after),ensure_ascii=False,indent=2))
  print(head,report['models'][head],flush=True)
 (a.output/'training-report.json').write_text(json.dumps(report,indent=2))
 print('Candidate models saved; do not promote without real-scene validation',flush=True)
if __name__=='__main__': main()
