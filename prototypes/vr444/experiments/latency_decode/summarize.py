#!/usr/bin/env python3
"""Summarize standalone timing files; never infer live or optical latency."""
import hashlib
import json
from pathlib import Path
import statistics
import sys

def summarize(path):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    cases = data['cases']
    assert data['pass'] and all(c['exact'] for c in cases)
    assert len(cases) == 122 and data['warmups'] == 3
    captured = [c for c in cases if not c['name'].startswith('edge/')]
    assert len(captured) == 38
    keys = ['reference_cpu_total_ms', 'direct_scalar_ms', 'direct_simd_ms', 'preflight_only_ms']
    if data['gpu_enabled']:
        keys += ['gpu_total_with_unpack_ms', 'gpu_dispatch_ms']
    for case in cases:
        for key in keys:
            values = case[key]['samples']
            assert len(values) == data['repetitions'] and all(v >= 0 for v in values)
            assert sorted(values) == values
            assert values[(len(values)-1)//2] == case[key]['p50']
    return {
        'source': str(path), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'status': 'standalone_exact_pass', 'vector_backend': data['vector_backend'],
        'cases': len(cases), 'captured_cases': len(captured),
        'warmups': data['warmups'], 'repetitions': data['repetitions'],
        'full_rgba_checks': len(cases)*(data['warmups']+data['repetitions'])*(4 if data['gpu_enabled'] else 3),
        'captured_constant_tile_fraction': sum(c['constant_tiles'] for c in captured)/sum(c['tiles'] for c in captured),
        'captured_mean_case_quantiles_ms': {
            key: {q: statistics.mean(c[key][q] for c in captured) for q in ['p50', 'p95']}
            for key in keys
        },
        'generated_576x640': [{k:c[k] for k in ['name', 'tiles', 'constant_tiles', 'mixed_tiles']} | {
            'p50_ms': {k:c[k]['p50'] for k in keys}}
            for c in cases if c['name'].startswith('edge/576x640')],
        'limitations': [
            'mean of case medians or p95 values, not a pooled latency percentile',
            'CPU output is reusable normal memory; RGBA staging copy and upload excluded',
            'GPU total includes CPU readback, unlike live resident path',
            'CPU variants rotate order; GPU executes afterward each iteration',
            'captured content is sparse; generated fixtures do not establish real-scene generality',
            'standalone, no compositor/network/LZ4/optical timing'],
    }

if __name__ == '__main__':
    print(json.dumps(summarize(sys.argv[1]), indent=2))
