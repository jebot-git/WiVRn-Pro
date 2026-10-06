#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fit a conservative language-balanced speaker centring model on training only.

This is statistical domain adaptation, not backbone fine-tuning. The earlier
gradient-trained metric remains separately available for comparison. A single
centering strength is selected on validation; test is evaluated afterwards.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np
import openvino as ov
import torch
from train_speakers import pairs,metric,select_device


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--embeddings',type=Path,required=True);p.add_argument('--base',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    manifest=json.loads(a.data.read_text());rows=manifest['rows'];cache=np.load(a.embeddings)
    digest=hashlib.sha256(a.data.read_bytes()).hexdigest()
    if str(cache['manifest_hash'])!=digest:raise ValueError('Embedding manifest mismatch')
    if sum(r['seconds'] for r in rows if r['split']=='train')<36000:raise ValueError('At least 10 hours required')
    vectors=cache['clean'];langs=sorted({r['language'] for r in rows})
    device,name=select_device();devices=[i for i in range(torch.xpu.device_count()) if 'A770' in torch.xpu.get_device_name(i)]
    if len(devices)!=1:raise RuntimeError('Exactly one A770 required')
    xpu=torch.device('xpu',devices[0]);torch.xpu.set_device(xpu)
    X=torch.tensor(vectors,device=xpu)
    centres=[X[[i for i,r in enumerate(rows) if r['split']=='train' and r['language']==lang]].mean(0) for lang in langs]
    centre=torch.stack(centres).mean(0).cpu().numpy()
    trials={split:{lang:pairs(rows,split,lang) for lang in langs} for split in ('validation','test')}
    candidates=[]
    for alpha in (0,.25,.5,.75,1):
        adjusted=vectors-alpha*centre;adjusted/=np.linalg.norm(adjusted,axis=1,keepdims=True)
        scores={lang:metric(adjusted,t) for lang,t in trials['validation'].items()}
        candidates.append(dict(alpha=alpha,validation=scores,mean_eer=float(np.mean([v['eer'] for v in scores.values()]))))
    selected=min(candidates,key=lambda x:x['mean_eer']);alpha=selected['alpha']
    adjusted=vectors-alpha*centre;adjusted/=np.linalg.norm(adjusted,axis=1,keepdims=True)
    baseline={split:{lang:metric(vectors,t) for lang,t in sets.items()} for split,sets in trials.items()}
    evaluation={split:{lang:metric(adjusted,t) for lang,t in sets.items()} for split,sets in trials.items()}
    # Conservative live operating point: <= 1% false matches on each language's
    # validation pairs. EER thresholds themselves are too permissive for colours.
    thresholds=[]
    for left,right,same in trials['validation'].values():
        negatives=(adjusted[left[~same]]*adjusted[right[~same]]).sum(1)
        thresholds.append(float(np.quantile(negatives,.99)))
    # Live phrases can average several window embeddings; keep a conservative
    # floor above the isolated-window pair threshold to avoid merging voices.
    threshold=round(float(np.clip(max(thresholds),.4,.95)),3)
    head=torch.nn.Linear(256,256)
    with torch.no_grad():head.weight.copy_(torch.eye(256));head.bias.copy_(torch.tensor(-alpha*centre))
    head.eval();a.output.mkdir(parents=True,exist_ok=False)
    ov.save_model(ov.convert_model(head,example_input=torch.zeros(1,256)),a.output/'projection.xml',compress_to_fp16=False)
    shutil.copy2(a.base/'speaker.onnx',a.output/'speaker.onnx')
    if (a.base/'README.md').is_file():shutil.copy2(a.base/'README.md',a.output/'BASE-MODEL.md')
    compiled=ov.Core().compile_model(a.output/'projection.xml',device)
    if compiled.get_property('EXECUTION_DEVICES')!=[device]:raise RuntimeError('Wrong export evaluation GPU')
    exported=np.concatenate([compiled([v[None]])[0].copy() for v in vectors])
    exported/=np.linalg.norm(exported,axis=1,keepdims=True)
    export_error=float(np.max(np.abs(exported-adjusted)))
    export_evaluation={lang:metric(exported,t) for lang,t in trials['test'].items()}
    eligible=alpha>0 and export_error<.001 and all(export_evaluation[lang]['eer']<=baseline['test'][lang]['eer'] for lang in langs)
    report=dict(kind='language-balanced speaker centring fitted on training embeddings; frozen backbone',
        device=name,hours=manifest['hours'],manifest_sha256=digest,validation_candidates=candidates,
        selected_alpha=alpha,baseline=baseline,evaluation=evaluation,openvino_test=export_evaluation,
        openvino_max_abs_difference=export_error,recommended_threshold=threshold,eligible_for_promotion=eligible,
        limitations=['Source speaker labels are partly automatic, not guaranteed global identities.',
            'EER measures same/different speaker pairs, not diarization or transcription accuracy.',
            'No simultaneous voice separation; short or ambiguous phrases stay neutral.'])
    (a.output/'calibration-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
