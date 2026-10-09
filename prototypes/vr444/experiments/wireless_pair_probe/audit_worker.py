#!/usr/bin/env python3
"""Audit source-bound actual Android CPU observations, never a live VR gate."""
import argparse
import hashlib
import json
import math
from pathlib import Path

NAMES = ['vr444_serial', 'rrf_up_serial', 'rrb32_serial', 'rrb32_workers2', 'rrb32_workers4', 'rrb128_serial', 'rrb128_workers2', 'rrb128_workers4']
METRICS = ['server_transcode_ms', 'client_decode_wall_ms', 'client_process_cpu_ms', 'server_plus_client_wall_ms']
def require(ok, message):
    if not ok: raise ValueError(message)
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def audit(plan_path, directory, original_path):
    plan = json.loads(plan_path.read_text()); receipt_path = directory/'receipt.json'; receipt = json.loads(receipt_path.read_text())
    require(receipt['plan_sha256'] == digest(plan_path), 'plan changed')
    require(receipt['headset_used'] is True and receipt['openxr_used'] is False, 'observation scope')
    require(plan['source_head'] == '260a1a2244b2381270398b65a1d3d2d282867508', 'unexpected codec source')
    labels = set()
    for command in receipt['commands']:
        label = command['label']; require(label not in labels and '/' not in label and '..' not in label, 'label'); labels.add(label)
        require(command['returncode'] == 0, 'command failed: '+label)
        for stream in ['stdout','stderr']:
            require(digest(directory/(label+'.'+stream)) == command[stream+'_sha256'], 'command output changed')
    for i, item in enumerate(plan['files']):
        require((directory/f'hash-{i}.stdout').read_text().split()[0] == item['sha256'], 'device input hash')
    require((directory/'serial.stdout').read_text().strip() == plan['expected_serial'], 'device identity')
    for name in ['workers-test', 'workers-bench-31']:
        require(name in labels and (directory/(name+'.stderr')).read_bytes() == b'', 'missing/failed measured command')
    test = json.loads((directory/'workers-test.stdout').read_text())
    require(test['pass'] is True and test['stereo_roundtrips'] == 217 and test['rejections'] == 4273 and test['corruption_cases'] == 4000, 'worker correctness population')
    raw = json.loads((directory/'workers-bench-31.stdout').read_text())
    require(raw['pass'] is True and raw['backend'] == 'aarch64-neon' and raw['repetitions'] == 31 and raw['warmups'] == 3, 'benchmark header')
    original = {c['name']: c for c in json.loads(original_path.read_text())['cases']}
    expected = {str(Path(k).parent/Path(k).stem[:-2]) for k in plan['corpus_selection']['files']}
    require(len(raw['cases']) == len(expected) == 3 and {c['name'] for c in raw['cases']} == expected, 'case population')
    rows = []
    for case in raw['cases']:
        ref = original[case['name']]
        for key in ['left_source','right_source','width','height','q','synthetic_duplicate_eye']:
            require(case[key] == ref[key], 'source or shape changed')
        require(case['exact_stereo'] is True and case['serial_worker_wire_equal'] is True, 'exactness')
        require([p['name'] for p in case['profiles']] == NAMES, 'arm population')
        base = case['profiles'][0]
        for i,p in enumerate(case['profiles']):
            require(p['stereo_wire_bytes'] == ref['profiles'][i]['stereo_wire_bytes'], 'canonical size changed')
            for key in METRICS:
                values = p[key]['samples']; require(len(values) == 31 and all(type(v) in (float,int) and math.isfinite(v) and v >= 0 for v in values), 'samples')
                order = sorted(values)
                for q,v in [('p50',order[15]),('p95',order[29]),('max',order[-1])]: require(p[key][q] == v, 'quantile mismatch')
            for s,c,total in zip(p[METRICS[0]]['samples'],p[METRICS[1]]['samples'],p[METRICS[3]]['samples']): require(math.isclose(s+c,total,rel_tol=1e-7,abs_tol=1e-7), 'unmatched clocks')
            rows.append(dict(case=case['name'],profile=p['name'],pair_wire_bytes=p['stereo_wire_bytes'],within_256k_pair_cap=p['stereo_wire_bytes']<=262144,
                wire_percent_vs_vr444=100*(p['stereo_wire_bytes']/base['stereo_wire_bytes']-1),
                wall_p50_ms=p[METRICS[1]]['p50'],wall_p95_ms=p[METRICS[1]]['p95'],
                wall_p50_delta_vs_vr444_ms=p[METRICS[1]]['p50']-base[METRICS[1]]['p50'],wall_p95_delta_vs_vr444_ms=p[METRICS[1]]['p95']-base[METRICS[1]]['p95'],
                process_cpu_p50_ms=p[METRICS[2]]['p50'],process_cpu_p95_ms=p[METRICS[2]]['p95'],
                nominal_90hz_one_core_percent_from_cpu_p50=9*p[METRICS[2]]['p50']))
    return dict(schema=1,pass_=True,scope='Actual Quest private CPU decoder stage; three predetermined captured stereo pairs; no OpenXR, queue, network, GPU or sustained-rate qualification.',
        source_commit=plan['source_head'],source_hashes={str(p):digest(p) for p in [plan_path,receipt_path,original_path,directory/'workers-bench-31.stdout',Path(__file__)]},
        measured_stereo_invocations=3*8*31,rows=rows,
        cpu_budget_note='90Hz percentage is a simple process CPU demand extrapolation, not measured utilization, thermal headroom, affinity or sustainable cadence.',
        promotion_eligible=False)
if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('plan',type=Path);p.add_argument('directory',type=Path);p.add_argument('original',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result=audit(a.plan,a.directory,a.original);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'pass','measured_stereo_invocations':result['measured_stereo_invocations']}))
