#!/usr/bin/env python3
"""Validate all output-memory arms and their exact copy/submission costs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
MODES = {'full_copy', 'mapped_coherent', 'compact_copy'}


def audit(data):
    if (data.get('schema') != 1 or data.get('experiment') != 'palette-output-memory'
            or data.get('pass') is not True or data.get('headset_used') is not False
            or data.get('performance_claim') is not False or data.get('promotion_blocked') is not True
            or data.get('canonical_modes') != 6 or data.get('canonical_tie_cases') != 4 or data.get('warmups') != 1):
        raise ValueError('output-memory qualification scope')
    reps = data.get('repetitions')
    cases = data.get('cases')
    if type(reps) is not int or not 1 <= reps <= 30:
        raise ValueError('repetitions')
    if type(cases) is not list or not cases or data.get('case_count') != len(cases):
        raise ValueError('fixture accounting')
    names, devices = set(), set()
    maximum_blocks = 0
    for case in cases:
        name = case.get('name')
        if type(name) is not str or not name or name in names:
            raise ValueError('fixture identity')
        names.add(name)
        if case.get('device_type') not in (1, 2) or type(case.get('device')) is not str or not case['device']:
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
        capacity = 2*(tiles*772+8)
        if case.get('stereo_uploaded_bytes') != w*h*8 or case.get('stereo_readback_capacity_bytes') != capacity:
            raise ValueError('allocation/input accounting')
        wire = case.get('eye_wire_bytes')
        if (type(wire) is not list or len(wire) != 2
                or any(type(v) is not int or not 32+tiles*7 <= v <= 32+4*tiles+3*w*h for v in wire)):
            raise ValueError('canonical wire bounds')
        copied = dict(full_copy=capacity, mapped_coherent=0, compact_copy=sum(4+((v-32+3)//4)*4 for v in wire))
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != reps*3:
            raise ValueError('arm coverage')
        for rep in range(reps):
            observed = rows[rep*3:rep*3+3]
            if {r.get('mode') for r in observed} != MODES or [r.get('order') for r in observed] != list(range(3)):
                raise ValueError('arm rotation/identity')
            for row in observed:
                mode = row['mode']
                if type(row.get('rep')) is not int or row['rep'] != rep:
                    raise ValueError('iteration order')
                if row.get('stereo_copied_bytes') != copied[mode] or row.get('queue_submissions') != (4 if mode == 'compact_copy' else 2):
                    raise ValueError('copy/submission cost omitted')
                required_output = 6 if mode == 'mapped_coherent' else 1
                for key, required in (('input_memory_flags', 6), ('readback_memory_flags', 6), ('output_memory_flags', required_output)):
                    flags = row.get(key)
                    if type(flags) is not int or flags & required != required:
                        raise ValueError('memory qualification')
                if any(row.get(k) is not True for k in ('canonical_bytes_exact', 'decoded_pixels_exact', 'malformed_pair_rejected_before_submit')):
                    raise ValueError('correctness gate')
                for key in ('stereo_preflight_ms', 'stereo_upload_ms', 'stereo_device_timestamp_span_ms', 'stereo_submit_wait_ms', 'stereo_assembly_crc_ms'):
                    value = row.get(key)
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError('nonfinite/negative timing evidence')
    if not {f'canonical-tie-{i}' for i in range(4)} <= names:
        raise ValueError('canonical tie vectors missing')
    return dict(correctness_pass=True, case_count=len(cases), measured_stereo_arm_iterations=len(cases)*reps*3,
                devices=sorted(devices), software=False, maximum_scan_blocks=maximum_blocks,
                output_modes=sorted(MODES), performance_claim=False, promotion_blocked=True, headset_used=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    print(json.dumps(audit(json.loads(raw)) | dict(result_sha256=hashlib.sha256(raw).hexdigest()), indent=2))


if __name__ == '__main__': main()
