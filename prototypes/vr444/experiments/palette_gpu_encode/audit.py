#!/usr/bin/env python3
"""Fail-closed offline RPT encoder result audit; never promotes timing."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def audit(data):
    if (data.get('schema') != 1 or data.get('experiment') != 'palette-gpu-encode'
            or data.get('pass') is not True or data.get('headset_used') is not False
            or data.get('performance_claim') is not False or data.get('promotion_blocked') is not True
            or data.get('canonical_modes') != 6 or data.get('warmups') != 1):
        raise ValueError('encoder qualification scope')
    reps = data.get('repetitions')
    if type(reps) is not int or not 1 <= reps <= 30:
        raise ValueError('repetitions')
    cases = data.get('cases')
    if type(cases) is not list or not cases or data.get('case_count') != len(cases):
        raise ValueError('fixture accounting')
    names = set()
    devices = set()
    maximum_blocks = 0
    for case in cases:
        name = case.get('name')
        if type(name) is not str or not name or name in names:
            raise ValueError('fixture identity')
        names.add(name)
        if case.get('device_type') not in (1, 2, 3) or type(case.get('device')) is not str or not case['device']:
            raise ValueError('hardware identity')
        devices.add(case['device'])
        w, h = case.get('width'), case.get('height')
        if any(type(v) is not int or not 1 <= v <= 4096 for v in (w, h)) or w*h > 4194304:
            raise ValueError('image bounds')
        tiles = ((w+15)//16)*((h+15)//16)
        blocks = (tiles+255)//256
        maximum_blocks = max(maximum_blocks, blocks)
        if case.get('tiles') != tiles or case.get('scan_blocks') != blocks:
            raise ValueError('scan geometry')
        if (case.get('stereo_uploaded_bytes') != w*h*8
                or case.get('stereo_readback_capacity_bytes') != 2*(tiles*772+8)):
            raise ValueError('upload/readback accounting')
        wire = case.get('stereo_wire_bytes')
        if type(wire) is not int or not 2*(32+tiles*7) <= wire <= 2*(32+4*tiles+3*w*h):
            raise ValueError('canonical wire bounds')
        for key, required in (('input_memory_flags', 6), ('readback_memory_flags', 6), ('output_memory_flags', 1)):
            flags = case.get(key)
            if type(flags) is not int or flags & required != required:
                raise ValueError('memory qualification')
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != reps:
            raise ValueError('iteration accounting')
        for rep, row in enumerate(rows):
            if type(row.get('rep')) is not int or row['rep'] != rep:
                raise ValueError('iteration order')
            if any(row.get(k) is not True for k in ('canonical_bytes_exact', 'decoded_pixels_exact', 'malformed_pair_rejected_before_submit')):
                raise ValueError('correctness gate')
            for key in ('stereo_preflight_ms', 'stereo_upload_ms', 'stereo_gpu_and_full_readback_ms',
                        'stereo_submit_wait_ms', 'stereo_assembly_crc_ms'):
                value = row.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError('nonfinite/negative timing evidence')
    return dict(correctness_pass=True, case_count=len(cases), measured_stereo_iterations=len(cases)*reps,
                devices=sorted(devices), software=False, maximum_scan_blocks=maximum_blocks,
                performance_claim=False, promotion_blocked=True, headset_used=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    report = audit(json.loads(raw)) | dict(result_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
