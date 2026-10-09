#!/usr/bin/env python3
import argparse,json,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('results',type=Path);args=p.parse_args()
summary={}
for file in sorted(args.results.glob('*.json')):
    try:data=json.loads(file.read_text())
    except (ValueError,UnicodeError):continue
    if not data.get('pass') or 'cases' not in data:continue
    groups={}
    for label,predicate in {
        'captured_q8':lambda c:c['name'].startswith('paired'),
        'captured_q1':lambda c:c['name'].startswith('lossless'),
        'synthetic':lambda c:c['name'].startswith('synthetic/'),
        'edges':lambda c:c['name'].startswith('edge/'),
    }.items():
        cases=[c for c in data['cases'] if predicate(c)]
        if not cases:continue
        group={'cases':len(cases),'mean_constant_tile_fraction':statistics.mean(c['constant_tiles']/c['tiles'] for c in cases)}
        for key in ['base_gpu_ms','fast_gpu_ms','base_wall_ms','fast_wall_ms']:
            group[key+'_mean_case_p50']=statistics.mean(c[key]['p50'] for c in cases)
        group['gpu_change_percent']=100*(group['fast_gpu_ms_mean_case_p50']/group['base_gpu_ms_mean_case_p50']-1)
        group['wall_change_percent']=100*(group['fast_wall_ms_mean_case_p50']/group['base_wall_ms_mean_case_p50']-1)
        groups[label]=group
    summary[file.name]={'cases':len(data['cases']),'repetitions':data['repetitions'],'warmups':data['warmups'],'exact_calls':len(data['cases'])*(data['repetitions']+data['warmups'])*2,'groups':groups}
print(json.dumps(summary,indent=2))
