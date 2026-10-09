#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import argparse
import json
import math
from pathlib import Path
from statistics import mean


def summarize(data):
    names=['vr444_plain_lz4','rrf_global_up','rrb16_up','rrb32_up','rrb64_up','rrb128_up','rrb32_adaptive']
    metrics=['server_transcode_ms','source_reconstruction_ms','wrapper_encode_ms','client_decode_ms','server_plus_client_ms']
    cases=data['cases']
    if data['pass'] is not True or len({c['name'] for c in cases})!=len(cases):raise ValueError('invalid result')
    for case in cases:
        if not case['exact_decoded_rgba'] or [p['name'] for p in case['profiles']]!=names:raise ValueError('profile/exactness')
        for p in case['profiles']:
            for metric in metrics:
                samples=p[metric]['samples']
                if len(samples)!=data['repetitions'] or any(not math.isfinite(s) or s<0 for s in samples):raise ValueError('samples')
    groups={}
    for name in ('paired-a','paired-b','lossless','synthetic-q1','synthetic-q8','all'):
        rows=[c for c in cases if (c['name'].startswith('synthetic/') and c['q']==int(name[-1]))] if name.startswith('synthetic-') else [c for c in cases if name=='all' or c['name'].startswith(name+'/')]
        if not rows:continue
        profiles=[]
        for i,n in enumerate(names):
            chosen=[c['profiles'][i] for c in rows];wire=sum(p['wire_bytes'] for p in chosen)
            profiles.append(dict(profile=n,wire_bytes=wire,
                saved_percent_vs_vr444=100*(1-wire/sum(c['profiles'][0]['wire_bytes'] for c in rows)),
                saved_percent_vs_global_up=100*(1-wire/sum(c['profiles'][1]['wire_bytes'] for c in rows)),
                mode_counts=[sum(p['mode_counts'][m] for p in chosen) for m in range(3)],
                mean_case_p50_ms={m:mean(p[m]['p50'] for p in chosen) for m in metrics}))
        groups[name]=dict(cases=len(rows),profiles=profiles)
    return dict(schema=1,backend=data['backend'],groups=groups,
                statistic='means of per-eye case p50; no pooled percentile or stereo prediction',
                limitation='single x86 host,24 captured eyes+14 synthetic; no frequency/thermal/affinity control; no headset/transfer/compositor measurement')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(summarize(json.loads(a.input.read_text())),indent=2)+'\n')
