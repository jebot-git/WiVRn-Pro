#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import argparse
import json
import math
from pathlib import Path
from statistics import mean


def summarize(data):
    names=['vr444_serial','rrf_up_serial','rrb32_serial','rrb32_workers2','rrb32_workers4','rrb128_serial','rrb128_workers2','rrb128_workers4']
    metrics=['server_transcode_ms','client_decode_wall_ms','client_process_cpu_ms','server_plus_client_wall_ms']
    cases=data['cases']
    if data['pass'] is not True or len({c['name'] for c in cases})!=len(cases):raise ValueError('invalid result')
    for c in cases:
        if not c['exact_stereo'] or not c['serial_worker_wire_equal'] or [p['name'] for p in c['profiles']]!=names:raise ValueError('profile/exactness')
        if c['synthetic_duplicate_eye']!=(c['left_source']==c['right_source']):raise ValueError('stereo provenance')
        for first in (2,5):
            if len({p['stereo_wire_bytes'] for p in c['profiles'][first:first+3]})!=1:raise ValueError('worker wire differs')
        for p in c['profiles']:
            for metric in metrics:
                samples=p[metric]['samples']
                if len(samples)!=data['repetitions'] or any(not math.isfinite(s) or s<0 for s in samples):raise ValueError('samples')
    groups={}
    for name in ('paired-a','paired-b','lossless','synthetic-q1','synthetic-q8','all'):
        rows=[c for c in cases if c['synthetic_duplicate_eye'] and c['q']==int(name[-1])] if name.startswith('synthetic-') else [c for c in cases if name=='all' or c['name'].startswith(name+'/')]
        if not rows:continue
        profiles=[]
        for i,n in enumerate(names):
            chosen=[c['profiles'][i] for c in rows]
            profiles.append(dict(profile=n,stereo_wire_bytes=sum(p['stereo_wire_bytes'] for p in chosen),mean_pair_p50_ms={m:mean(p[m]['p50'] for p in chosen) for m in metrics}))
        groups[name]=dict(cases=len(rows),profiles=profiles)
    return dict(schema=1,backend=data['backend'],groups=groups,
        actual_captured_pairs=sum(not c['synthetic_duplicate_eye'] for c in cases),synthetic_duplicated_eye_profiles=sum(c['synthetic_duplicate_eye'] for c in cases),
        statistic='means of per-stereo-profile medians; no pooled percentile or headset prediction',
        limitation='single x86 host without frequency/affinity/thermal control; worker startup separate; process CPU sums all worker use; no GPU/upload/network/headset measurement')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(summarize(json.loads(a.input.read_text())),indent=2)+'\n')
