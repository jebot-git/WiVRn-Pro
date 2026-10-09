#!/usr/bin/env python3
"""Audit exact RRB GPU fixture coverage, stereo block domains and pixel gates."""
import argparse
import hashlib
import json
import math
from pathlib import Path

MODES = {'buffer_copy', 'image_unorm', 'image_uint'}
TIMES = ('validation_lz4_ms', 'input_copy_ms', 'stereo_gpu_ms', 'pipeline_wall_ms', 'oracle_ms')
GOLDENS = {'plain-tail': (1, 17, [1, 0]), 'up-tail-reset': (1, 17, [2, 0]),
           'raw-up-reset': (1, 32, [0, 2]), 'raw-odd': (3, 17, [0, 0])}


def expected_cases(full, maximum, corpus):
    result = {}
    def add(w, h, kind, q, policy, prefix):
        for rows in (16, 32, 64, 128):
            right = 16 if rows == 128 else rows*2
            name = f'{prefix}/{w}x{h}-p{kind}-q{q}-policy{policy}-leftrows{rows}-rightrows{right}'
            result[name] = (w, h, [rows, right])
    shapes = [(1, 1), (1, 17), (17, 1), (17, 19), (31, 33), (65, 47), (256, 2)]
    if full:
        shapes.append((576, 640))
    for w, h in shapes:
        for kind in range(3):
            for q in (1, 8):
                for policy in (1, 2):
                    add(w, h, kind, q, policy, 'generated')
    for h in (7, 8, 9, 31, 32, 33, 127, 128, 129, 255, 256, 257, 4095, 4096):
        for w in (1, 17):
            add(w, h, 2, 1, 2, 'boundary')
    for w, h in ((4096, 1), (4095, 3)):
        add(w, h, 2, 1, 2, 'boundary')
    if maximum:
        for kind in (0, 1):
            add(2048, 2048, kind, 1, 2, 'maximum')
    for name, (w, h, _) in GOLDENS.items():
        result[f'golden/{name}.rrb'] = (w, h, [16, 16])
    for row in corpus:
        name, rows = row.get('rrb'), row.get('block_rows')
        if (type(name) is not str or Path(name).is_absolute() or '..' in Path(name).parts
                or not name.endswith('.rrb') or 'corpus/'+name in result
                or rows not in (16, 32, 64, 128) or row.get('exact') is not True):
            raise ValueError('manifest RRB identity')
        result['corpus/'+name] = (row['width'], row['height'], [rows, rows])
    return result


def source_snapshot(directory, manifest):
    rows = manifest.get('cases')
    if type(rows) is not list or len(rows) != 152:
        raise ValueError('complete 38 by four-block-size manifest required')
    expected_cases(False, False, rows)
    names = {row['rrb'] for row in rows}
    if {str(p.relative_to(directory)) for p in directory.rglob('*.rrb')} != names:
        raise ValueError('complete RRB corpus required')
    sources, files = {}, {}
    for row in rows:
        sources.setdefault(row['source'], set()).add(row['block_rows'])
        for key, length in (('rrb', row['wire_bytes']), ('rgba', row['width']*row['height']*4)):
            name = row[key]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('source path bounds')
            raw = (directory/name).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != length or digest != row[key+'_sha256']:
                raise ValueError('source differs from immutable export')
            value = dict(path=name, bytes=length, sha256=digest)
            if name in files and files[name] != value:
                raise ValueError('shared reference hash mismatch')
            files[name] = value
    if len(sources) != 38 or any(v != {16, 32, 64, 128} for v in sources.values()):
        raise ValueError('source block variant coverage')
    return sorted(files.values(), key=lambda item: item['path'])


def audit(data, manifest):
    full, maximum = data.get('full_fixture_matrix'), data.get('maximum_fixtures')
    if (data.get('schema') != 1 or data.get('experiment') != 'rowblocks-gpu'
            or data.get('headset_used') is not False or data.get('pass') is not True
            or data.get('promotion_blocked') is not True or data.get('rrb_block_modes') != 3
            or data.get('cpu_prefix_restore_in_path') is not False
            or data.get('shared_prefix_barriers') is not False or data.get('workgroup_columns') != 128
            or data.get('cpu_grammar_crc_lz4_in_path') is not True
            or type(full) is not bool or type(maximum) is not bool
            or type(data.get('format_list')) is not bool
            or type(data.get('warmups')) is not int or type(data.get('repetitions')) is not int
            or data.get('warmups') != 1 or data.get('repetitions') != 1):
        raise ValueError('scope/layout gate')
    corpus_count = data.get('corpus_cases')
    if type(corpus_count) is not int or corpus_count not in (0, len(manifest['cases'])):
        raise ValueError('complete corpus coverage')
    expected = expected_cases(full, maximum, manifest['cases'] if corpus_count else [])
    cases = data.get('cases')
    if (type(cases) is not list or data.get('case_count') != len(cases)
            or len(cases) != len(expected) or {c.get('name') for c in cases} != set(expected)):
        raise ValueError('exact complete fixture identities')
    device, largest_error, counts, differing_sizes = None, 0, [0, 0, 0], 0
    for case in cases:
        w, h = case.get('width'), case.get('height')
        block_rows, block_counts = case.get('eye_block_rows'), case.get('eye_block_counts')
        if (type(w) is not int or type(h) is not int or not 1 <= w <= 4096
                or not 1 <= h <= 4096 or w*h > 4194304
                or (w, h, block_rows) != expected[case['name']]
                or type(block_counts) is not list or block_counts != [(h+r-1)//r for r in block_rows]
                or any(type(n) is not int for n in block_counts)
                or type(case.get('dispatch_groups')) is not int
                or case.get('dispatch_groups') != ((w+127)//128)*max(block_counts)
                or case.get('stereo_raw_rgb_bytes') != w*h*6):
            raise ValueError('shape/unequal-stereo-dispatch/upload bounds')
        differing_sizes += block_rows[0] != block_rows[1]
        modes = case.get('eye_block_modes')
        if type(modes) is not list or len(modes) != 2:
            raise ValueError('per-eye block modes required')
        for eye in (0, 1):
            if (type(modes[eye]) is not list or len(modes[eye]) != block_counts[eye]
                    or any(type(v) is not int or v not in (0, 1, 2) for v in modes[eye])):
                raise ValueError('block mode accounting')
            for mode in modes[eye]:
                counts[mode] += 1
        if case['name'].startswith('golden/'):
            expected_modes = GOLDENS[Path(case['name']).stem][2]
            if modes != [expected_modes, expected_modes]:
                raise ValueError('independent golden block modes')
        identity = (case.get('device'), case.get('device_type'), case.get('driver'))
        flags = case.get('input_memory_flags')
        if (not identity[0] or type(identity[1]) is not int or identity[1] not in (1, 2) or type(identity[2]) is not int
                or type(flags) is not int or not 0 < flags <= 0xffffffff or flags & 6 != 6):
            raise ValueError('hardware/coherent-input identity')
        if device is None:
            device = identity
        if device != identity:
            raise ValueError('mixed physical-device baseline')
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != 3 or {r.get('mode') for r in rows} != MODES:
            raise ValueError('complete output arms')
        for row in rows:
            if any(row.get(key) is not True for key in ('bytes_exact', 'one_eotf', 'sampled_baseline_exact',
                   'malformed_pair_rejected_before_write_or_submit', 'stereo_shape_rejected_before_write_or_submit',
                   'upload_after_failed_admission_rejected')):
                raise ValueError('exactness/stereo/lifetime gate')
            for key in TIMES + ('max_linear_error', 'max_srgb_code_error'):
                value = row.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError('nonfinite or missing observation')
            if row['max_srgb_code_error'] >= .5:
                raise ValueError('EOTF inverse must preserve original byte')
            largest_error = max(largest_error, row['max_srgb_code_error'])
    if not all(counts) or data.get('rrb_block_mode_counts') != counts:
        raise ValueError('all three block modes and exact aggregate accounting')
    return dict(pass_=True, fixture_count=len(cases), differing_stereo_block_size_fixtures=differing_sizes,
                measured_stereo_arm_invocations=len(cases)*3, warmup_stereo_arm_invocations=len(cases)*3,
                block_mode_counts=counts, device=device, maximum_srgb_code_error=largest_error,
                exact_optimal_image_bytes=True, sampled_same_device_baseline_exact=True,
                cpu_prefix_restore_in_path=False, cpu_grammar_crc_lz4_in_path=True,
                promotion_blocked=True, headset_used=False, performance_gate_evaluated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    report = audit(json.loads(raw), json.loads(args.manifest.read_text()))
    report['pass'] = report.pop('pass_')
    report['result_sha256'] = hashlib.sha256(raw).hexdigest()
    report['corpus_manifest_sha256'] = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    if args.out:
        with args.out.open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
