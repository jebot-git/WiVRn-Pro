#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import argparse
import json
import math
from pathlib import Path
from statistics import mean


def summarize(data):
    names=['vr444_plain_lz4','rgb_plain','rgb_up','planes_plain','planes_up','green_plain','green_up','green_planes_plain','green_planes_up']
    metrics=['server_transcode_ms','source_reconstruction_ms','wrapper_encode_ms','client_decode_ms','server_plus_client_ms']
    cases=data['cases']
    if data['pass'] is not True or len({c['name'] for c in cases})!=len(cases):raise ValueError('invalid result')
    for c in cases:
        if not c['exact_decoded_rgba'] or [p['name'] for p in c['profiles']]!=names:raise ValueError('profile/exactness')
        for p in c['profiles']:
            for metric in metrics:
                samples=p[metric]['samples']
                if len(samples)!=data['repetitions'] or any(not math.isfinite(s) or s<0 for s in samples):raise ValueError('samples')
    groups={}
    for name in ('paired-a','paired-b','lossless','synthetic-q1','synthetic-q8','all'):
        rows=[c for c in cases if c['name'].startswith('synthetic/') and c['q']==int(name[-1])] if name.startswith('synthetic-') else [c for c in cases if name=='all' or c['name'].startswith(name+'/')]
        if not rows:continue
        baseline=sum(c['profiles'][0]['wire_bytes'] for c in rows);profiles=[]
        for i,n in enumerate(names):
            chosen=[c['profiles'][i] for c in rows];wire=sum(p['wire_bytes'] for p in chosen)
            profiles.append(dict(profile=n,wire_bytes=wire,saved_percent_vs_vr444=100*(1-wire/baseline),
                raw_fallback_cases=sum(p['selected_mode']==0 for p in chosen),mean_case_p50_ms={m:mean(p[m]['p50'] for p in chosen) for m in metrics}))
        groups[name]=dict(cases=len(rows),profiles=profiles)
    return dict(schema=1,backend=data['backend'],groups=groups,statistic='means of per-eye case medians, not pooled percentiles or stereo predictions',
                limitation='single x86 host;24 captured eyes+14synthetics; no clock/thermal/affinity control, transfer, GPU upload, compositor or headset measurement')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(summarize(json.loads(a.input.read_text())),indent=2)+'\n')
