#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import argparse
import json
from pathlib import Path
import statistics
p=argparse.ArgumentParser();p.add_argument('results',type=Path);a=p.parse_args()
manifest=json.loads((a.results/'corpus-manifest.json').read_text())
family={x['name']:x['family'] for x in manifest}
profiles={name:json.loads((a.results/file).read_text()) for name,file in [('host','host-benchmark.json'),('quest_table','quest-benchmark.json'),('quest_crc','quest-crc-benchmark.json'),('quest_table_repeat','quest-table-repeat.json')]}
out=dict(scope='standalone stateless WPC1 CPU viability, no network/codec GPU/display timing',aggregates=[])
for device,data in profiles.items():
    for fam in sorted(set(family.values())):
        for block in [16777216,16384,1100]:
            rows=[r for r in data['rows'] if family[r['sample']]==fam and r['block']==block]
            if not rows:continue
            out['aggregates'].append(dict(device=device,family=fam,block=block,samples=len(rows),
                ratio=sum(r['wrapped_bytes']for r in rows)/sum(r['original_bytes']for r in rows),
                raw_escapes=sum(r['raw_escape']for r in rows),
                **{f'{stage}_mean_of_sample_medians_ms':statistics.mean(r[stage+'_ms']['p50']for r in rows) for stage in ['encode','decode','crc','copy']},
                maximum_sample_decode_p95_ms=max(r['decode_ms']['p95']for r in rows)))
host={(r['sample'],r['block']):r for r in profiles['host']['rows']}
break_even=[]
for r in profiles['quest_crc']['rows']:
    h=host[r['sample'],r['block']]
    saved=r['original_bytes']-r['wrapped_bytes'];cost=h['encode_ms']['p50']+r['decode_ms']['p50']
    break_even.append(dict(sample=r['sample'],block=r['block'],bytes_saved=saved,serial_cpu_ms=cost,
                           ideal_payload_break_even_mbps=max(0,8*saved/(cost*1000))))
out['serial_model']=break_even
out['model_limits']='Host encode + Quest decode medians assumed serial. Pure payload serialization only; excludes transport/AEAD/packet overhead, queueing and GPU/display. Crossover is not measured network performance.'
print(json.dumps(out,indent=2))
