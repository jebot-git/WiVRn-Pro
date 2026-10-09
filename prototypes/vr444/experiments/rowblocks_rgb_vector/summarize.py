#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import argparse
import json
import math
from pathlib import Path
from statistics import mean

NAMES=('vr444_serial','rrf_up_serial','rrb32_rgb_scalar_store_workers4','rrb32_fused_rgba_workers4','rrb32_rgb_vector_serial','rrb32_rgb_vector_workers2','rrb32_rgb_vector_workers4','rrb128_rgb_scalar_store_workers4','rrb128_fused_rgba_workers4','rrb128_rgb_vector_serial','rrb128_rgb_vector_workers2','rrb128_rgb_vector_workers4')
METRICS=('server_transcode_wall_ms','server_process_cpu_ms','client_decode_wall_ms','client_process_cpu_ms','server_plus_client_wall_ms')


def validate(data):
    if data.get('pass') is not True or data.get('schema')!=1 or type(data.get('repetitions')) is not int or not 5<=data['repetitions']<=100:raise ValueError('invalid benchmark')
    if type(data.get('cases')) is not list or not 1<=len(data['cases'])<=128:raise ValueError('case count')
    seen=set()
    for c in data['cases']:
        if not isinstance(c.get('name'),str) or c['name'] in seen or c.get('exact_stereo') is not True or c.get('canonical_reference_wire_equal') is not True:raise ValueError('case identity/exactness')
        seen.add(c['name'])
        if type(c.get('synthetic_duplicate_eye')) is not bool or c['synthetic_duplicate_eye']!=(c['left_source']==c['right_source']):raise ValueError('stereo source identity')
        if [p.get('name') for p in c['profiles']]!=list(NAMES):raise ValueError('profile identity/order')
        for p in c['profiles']:
            if type(p.get('stereo_wire_bytes')) is not int or p['stereo_wire_bytes']<2:raise ValueError('wire length')
            for key in METRICS:
                metric=p[key];values=metric['samples'];n=len(values)
                if n!=data['repetitions'] or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values):raise ValueError('duration sample population')
                order=sorted(values)
                if any(metric[k]!=v for k,v in {'p50':order[(n-1)//2],'p95':order[(95*n+99)//100-1],'max':order[-1]}.items()):raise ValueError('duration summary differs')
            for a,b,total in zip(p[METRICS[0]]['samples'],p[METRICS[2]]['samples'],p[METRICS[4]]['samples']):
                if not math.isclose(a+b,total,rel_tol=1e-7,abs_tol=1e-7):raise ValueError('component samples unmatched')
        for start in (2,7):
            if len({p['stereo_wire_bytes'] for p in c['profiles'][start:start+5]})!=1:raise ValueError('parallel encoding changed canonical wire size')


def summarize(data):
    validate(data);groups=[]
    for name in ('lossless','paired-a','paired-b','synthetic'):
        cases=[c for c in data['cases'] if c['name'].startswith(name+'/')]
        if not cases:continue
        rows=[]
        for i,profile in enumerate(NAMES):
            row=dict(profile=profile,cases=len(cases),total_stereo_wire_bytes=sum(c['profiles'][i]['stereo_wire_bytes'] for c in cases))
            for key in METRICS:row['mean_pair_p50_'+key]=mean(c['profiles'][i][key]['p50'] for c in cases)
            rows.append(row)
        groups.append(dict(group=name,profiles=rows))
    return dict(schema=1,status='pass',cases=len(data['cases']),actual_captured_pairs=sum(not c['synthetic_duplicate_eye'] for c in data['cases']),synthetic_duplicated_eye_profiles=sum(c['synthetic_duplicate_eye'] for c in data['cases']),repetitions=data['repetitions'],groups=groups,
        summary_scope='Arithmetic means of fixed case p50s, not a pooled latency percentile or live distribution.',network_measured=False,headset_accessed=False,promotion_eligible=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('benchmark',type=Path);p.add_argument('output',type=Path);a=p.parse_args();result=summarize(json.loads(a.benchmark.read_text()));a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status='pass',cases=result['cases'])))


if __name__=='__main__':main()
