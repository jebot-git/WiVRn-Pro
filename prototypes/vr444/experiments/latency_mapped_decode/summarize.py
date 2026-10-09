#!/usr/bin/env python3
"""Strict audit of alternating cached-copy versus mapped-direct image uploads."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
ARMS = ['cached_copy','mapped_direct']
KEYS = ['cpu_decode_ms','staging_copy_ms','upload_submit_fence_ms','decode_through_upload_ms',
        'readback_submit_fence_ms','readback_record_submit_fence_ms']
def dist(v):
    v=sorted(v)
    assert v and all(math.isfinite(x) for x in v)
    return dict(count=len(v),p50=v[(len(v)-1)//2],p95=v[math.ceil(.95*len(v))-1],
                p99=v[math.ceil(.99*len(v))-1],minimum=v[0],maximum=v[-1])
def summarize(path):
    raw=Path(path).read_bytes();d=json.loads(raw)
    assert d['pass'] and d['format']=='VK_FORMAT_R8G8B8A8_SRGB' and d['optimal_tiling']
    assert d['validation_extra_transfer_src_usage'] and d['warmups']==3
    cases=d['cases'];assert len(cases)==122
    captured=[c for c in cases if not c['name'].startswith('edge/')];assert len(captured)==38
    first_counts={a:0 for a in ARMS}
    for ci,c in enumerate(cases):
        assert c['device_type']!=4 and c['staging_memory_flags'] & 6 == 6
        assert c['memory_types'][c['staging_memory_type']]['flags']==c['staging_memory_flags']
        assert c['rgba_bytes']==c['width']*c['height']*4
        assert len(c['paired_iterations'])==d['repetitions']
        for i,it in enumerate(c['paired_iterations']):
            assert it['measured_rep']==i and it['first_arm']==ARMS[(i+3+ci)%2]
            first_counts[it['first_arm']]+=1
        for a in ARMS:
            assert c['arms'][a]['cpu_exact'] and c['arms'][a]['image_readback_exact']
            for k in KEYS:
                v=c['arms'][a][k]['samples']; chronological=[it[a][k] for it in c['paired_iterations']]
                assert len(v)==d['repetitions'] and v==sorted(chronological)
                assert all(math.isfinite(x) and x>=0 for x in v)
                assert c['arms'][a][k]['p50']==v[(len(v)-1)//2]
                assert c['arms'][a][k]['p95']==v[math.ceil(.95*len(v))-1]
                assert c['arms'][a][k]['max']==v[-1]
            if a=='mapped_direct':assert all(x==0 for x in c['arms'][a]['staging_copy_ms']['samples'])
    assert first_counts['cached_copy']==first_counts['mapped_direct']
    deltas=[it['mapped_direct']['decode_through_upload_ms']-it['cached_copy']['decode_through_upload_ms']
            for c in captured for it in c['paired_iterations']]
    return dict(status='alternating_two_arm_image_exact_pass',source=str(path),source_sha256=hashlib.sha256(raw).hexdigest(),
        cases=len(cases),captured_cases=len(captured),warmups=3,repetitions=d['repetitions'],
        cpu_rgba_checks=len(cases)*(3+d['repetitions'])*2,image_rgba_checks=len(cases)*(3+d['repetitions'])*2,
        measured_first_arm_counts=first_counts,vector_backend=d['vector_backend'],
        devices=sorted(set(c['device'] for c in cases)),staging_flags=sorted(set(c['staging_memory_flags'] for c in cases)),
        captured_mean_case_quantiles_ms={a:{k:{q:statistics.mean(c['arms'][a][k][q] for c in captured) for q in ['p50','p95']} for k in KEYS} for a in ARMS},
        captured_paired_pipeline_delta_mapped_minus_cached_ms=dist(deltas),
        captured_case_median_wins_for_mapped=sum(c['arms']['mapped_direct']['decode_through_upload_ms']['p50']<c['arms']['cached_copy']['decode_through_upload_ms']['p50'] for c in captured),
        generated_576x640=[dict(name=c['name'],constant_tiles=c['constant_tiles'],mixed_tiles=c['mixed_tiles'],
            pipeline_p50_ms={a:c['arms'][a]['decode_through_upload_ms']['p50'] for a in ARMS},
            paired_pipeline_delta_ms=dist([it['mapped_direct']['decode_through_upload_ms']-it['cached_copy']['decode_through_upload_ms'] for it in c['paired_iterations']])) for c in cases if c['name'].startswith('edge/576x640')],
        limits=['standalone private graphics queue, no XR contention/network/LZ4/stereo publication',
                'same selected memory type for both arms; flags recorded, not an inferred Adreno cache policy',
                'initial Shape and output allocation reused; direct decoder internally rechecks framing',
                'image poison and readback validation outside measured pipeline, with cache/queue perturbation',
                'extra image TRANSFER_SRC usage; first-compatible allocation can differ from live VMA',
                'exact bytes in SRGB image, not sampled/displayed pixels or optical latency',
                'no change to quantization or compression; same reference reconstruction'])
if __name__=='__main__':print(json.dumps(summarize(sys.argv[1]),indent=2))
