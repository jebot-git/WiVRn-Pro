#!/usr/bin/env python3
"""Audit byte-exact standalone image uploads; no live/display inference."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

KEYS = ['cpu_decode_ms', 'staging_copy_ms', 'upload_submit_fence_ms',
        'decode_through_upload_ms', 'readback_submit_fence_ms',
        'readback_record_submit_fence_ms']

def summarize(path):
    raw = Path(path).read_bytes()
    d = json.loads(raw)
    assert d['pass'] and d['format'] == 'VK_FORMAT_R8G8B8A8_SRGB'
    assert d['optimal_tiling'] and d['validation_extra_transfer_src_usage']
    cases = d['cases']
    assert len(cases) == 122
    captures = [c for c in cases if not c['name'].startswith('edge/')]
    assert len(captures) == 38
    for c in cases:
        assert c['cpu_exact'] and c['image_readback_exact']
        assert c['device_type'] != 4  # VK_PHYSICAL_DEVICE_TYPE_CPU
        assert c['rgba_bytes'] == c['width']*c['height']*4
        assert c['staging_memory_flags'] & 6 == 6  # HOST_VISIBLE|HOST_COHERENT
        assert c['readback_memory_flags'] & 6 == 6
        for k in KEYS:
            v = c[k]['samples']
            assert len(v) == d['repetitions'] and v == sorted(v)
            assert all(math.isfinite(t) and t >= 0 for t in v)
            assert c[k]['p50'] == v[(len(v)-1)//2]
            assert c[k]['p95'] == v[(len(v)*95+99)//100-1]
            assert c[k]['max'] == v[-1]
    checks = len(cases)*(d['warmups']+d['repetitions'])
    return {
        'status': 'standalone_image_byte_exact_pass',
        'source': str(path), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'cases': len(cases), 'captured_cases': len(captures),
        'warmups': d['warmups'], 'repetitions': d['repetitions'],
        'cpu_full_rgba_checks': checks, 'image_readback_full_rgba_checks': checks,
        'vector_backend': d['vector_backend'],
        'devices': sorted(set(c['device'] for c in cases)),
        'captured_mean_case_quantiles_ms': {
            k: {q: statistics.mean(c[k][q] for c in captures) for q in ['p50','p95']}
            for k in KEYS},
        'generated_576x640': [{k:c[k] for k in ['name','constant_tiles','mixed_tiles']} | {
            'p50_ms':{k:c[k]['p50'] for k in KEYS}}
            for c in cases if c['name'].startswith('edge/576x640')],
        'limits': ['standalone private graphics queue; no XR, compositor load, network or LZ4',
                  'preflight Shape and CPU allocation reused; live adds preflight and resize',
                  'CPU decode timing includes its internal preflight',
                  'image poison and separate validation readback outside decode-through-upload timing',
                  'image adds TRANSFER_SRC usage; allocation can differ from live VMA allocation',
                  'wall-clock submit/fence timing, not pure GPU transfer timestamp',
                  'exact transferred image bytes, not sampled sRGB conversion or displayed pixels',
                  'mean case quantiles, not a pooled percentile'],
    }

if __name__ == '__main__':
    print(json.dumps(summarize(sys.argv[1]),indent=2))
