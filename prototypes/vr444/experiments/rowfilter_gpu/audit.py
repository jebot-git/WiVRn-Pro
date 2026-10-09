#!/usr/bin/env python3
"""Fail-closed gate for row-filter GPU exactness, not a performance gate."""
import argparse
import hashlib
import json
import math
from pathlib import Path

MODES = {'buffer_copy', 'image_unorm', 'image_uint'}
TIMES = ('validation_lz4_ms', 'input_copy_ms', 'stereo_gpu_ms', 'pipeline_wall_ms', 'oracle_ms')


def audit(data):
    columns = data.get('columns')
    if (data.get('schema') != 1 or data.get('experiment') != 'rowfilter-gpu'
            or data.get('headset_used') is not False or data.get('pass') is not True
            or data.get('promotion_blocked') is not True or data.get('rrf_modes') != 3
            or data.get('cpu_prefix_restore_in_path') is not False
            or data.get('cpu_grammar_crc_lz4_in_path') is not True
            or type(columns) is not int or columns not in (1, 4, 16)
            or type(data.get('format_list')) is not bool
            or data.get('warmups') != 1 or data.get('repetitions') != 1):
        raise ValueError('scope/layout gate')
    cases = data.get('cases')
    if (type(cases) is not list or not cases or data.get('case_count') != len(cases)
            or len({c.get('name') for c in cases}) != len(cases)):
        raise ValueError('unique complete cases')
    counts = data.get('rrf_eye_mode_counts')
    if (type(counts) is not list or len(counts) != 3
            or any(type(v) is not int or v <= 0 for v in counts) or sum(counts) != len(cases)*2):
        raise ValueError('all three RRF modes/accounting')
    device = None
    largest_error = 0
    for case in cases:
        w, h = case.get('width'), case.get('height')
        if (type(w) is not int or type(h) is not int or not 1 <= w <= 4096
                or not 1 <= h <= 4096 or w*h > 4194304
                or case.get('dispatch_groups') != (w+columns-1)//columns
                or case.get('stereo_raw_rgb_bytes') != w*h*6):
            raise ValueError('shape/dispatch/upload bounds')
        identity = (case.get('device'), case.get('device_type'), case.get('driver'))
        flags = case.get('input_memory_flags')
        if (not identity[0] or identity[1] not in (1, 2) or type(identity[2]) is not int
                or type(flags) is not int or flags & 6 != 6):
            raise ValueError('hardware/coherent-input identity')
        if device is None:
            device = identity
        if device != identity:
            raise ValueError('mixed device baseline')
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != 3 or {r.get('mode') for r in rows} != MODES:
            raise ValueError('complete output arms')
        for row in rows:
            if any(row.get(key) is not True for key in (
                    'bytes_exact', 'one_eotf', 'sampled_baseline_exact',
                    'malformed_pair_rejected_before_write_or_submit')):
                raise ValueError('exactness/stereo/lifetime gate')
            for key in TIMES + ('max_linear_error', 'max_srgb_code_error'):
                value = row.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError('nonfinite or missing observation')
            if row['max_srgb_code_error'] >= .5:
                raise ValueError('EOTF inverse must preserve original byte')
            largest_error = max(largest_error, row['max_srgb_code_error'])
    return dict(pass_=True, columns=columns, fixture_count=len(cases),
                measured_stereo_arm_invocations=len(cases)*3, warmup_stereo_arm_invocations=len(cases)*3,
                rrf_eye_mode_counts=counts, device=device, maximum_srgb_code_error=largest_error,
                exact_optimal_image_bytes=True, sampled_same_device_baseline_exact=True,
                cpu_prefix_restore_in_path=False, cpu_grammar_crc_lz4_in_path=True,
                promotion_blocked=True, headset_used=False, performance_gate_evaluated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    report = audit(json.loads(raw))
    report['pass'] = report.pop('pass_')
    report['result_sha256'] = hashlib.sha256(raw).hexdigest()
    if args.out:
        with args.out.open('x') as f:
            json.dump(report, f, indent=2)
            f.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
