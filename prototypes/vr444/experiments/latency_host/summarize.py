#!/usr/bin/env python3
"""Summarize isolated host replay controls; never turn GPU queue noise into CPU claims."""
import argparse
import json
from pathlib import Path
import statistics

parser=argparse.ArgumentParser()
parser.add_argument('directory',type=Path)
args=parser.parse_args()
rounds=[]
for name in ['benchmark.json','benchmark-reversed.json']:
    data=json.loads((args.directory/name).read_text())
    assert data['status']=='pass' and len(data['cases'])==16
    captures=[case for case in data['cases'] if 'custom' in case['name']]
    assert len(captures)==8
    variants=[]
    for index in range(4):
        observations=[case['variants'][index] for case in captures]
        fields=[key for key,value in observations[0].items() if isinstance(value,dict)]
        variants.append(dict(name=observations[0]['name'],
                             capture_mean_of_case_medians_ms={field:statistics.mean(row[field]['p50'] for row in observations) for field in fields},
                             capture_case_median_total_range_ms=[min(row['total_ms']['p50'] for row in observations),max(row['total_ms']['p50'] for row in observations)]))
    baseline=variants[0]['capture_mean_of_case_medians_ms']
    for variant in variants:
        sample=variant['capture_mean_of_case_medians_ms']
        variant['difference_from_baseline_total_ms']=sample['total_ms']-baseline['total_ms']
        variant['output_copy_and_pack_ms']=sample['expanded_or_pack_ms']+sample['finish_pack_ms']
    rounds.append(dict(file=name,cases=len(data['cases']),exact_checks=data['exact_checks'],variants=variants))
result=dict(status='pass',rounds=rounds,
            metric_definition='Arithmetic mean across 8 capture eye fixtures of each fixture median; not a pooled latency percentile.',
            scope='One-eye cached replay after producer readback. No LZ4, WCT, network, compositor, optical measurements or equal-quality native comparison.',
            outcome='Mapped bulk packing consistently reduces host output-copy/pack wall time. Span input lowers host wall time outside submit further, but does not have the lowest total time in these controls. GPU submit/wait variance remains material.')
(args.directory/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
