#!/usr/bin/env python3
"""Audit whole RRF-to-image comparisons with exact source and stage accounting."""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

ARMS = ['cpu_simd_rgba_copy', 'cpu_simd_host_image_copy']
TIMES = ('stereo_total_ms', 'stereo_poison_ms', 'stereo_preflight_decode_ms',
         'stereo_upload_ms', 'stereo_pipeline_wall_ms', 'stereo_device_span_ms')


def groups(manifest):
    rows = manifest.get('cases')
    if type(rows) is not list or not rows:
        raise ValueError('manifest cases required')
    result, captured, names = {}, {}, set()
    for row in rows:
        name = row.get('rrf')
        if (type(name) is not str or name in names or Path(name).is_absolute()
                or '..' in Path(name).parts or not name.endswith('.rrf') or row.get('exact') is not True):
            raise ValueError('manifest source identity')
        names.add(name)
        synthetic = name.startswith('synthetic/')
        if row.get('source_kind') != ('synthetic' if synthetic else 'captured_eye'):
            raise ValueError('source origin mismatch')
        if synthetic:
            result['corpus/'+name] = dict(eyes=[row, row], synthetic=True)
        else:
            if not (name.endswith('-0.rrf') or name.endswith('-1.rrf')):
                raise ValueError('captured eye suffix')
            pair = captured.setdefault(name[:-6], {})
            eye = int(name[-5])
            if eye in pair:
                raise ValueError('duplicate captured eye')
            pair[eye] = row
    for name, pair in captured.items():
        if set(pair) != {0, 1} or any(pair[0][k] != pair[1][k] for k in ('width', 'height', 'q')):
            raise ValueError('incomplete or mismatched stereo pair')
        result['corpus/'+name] = dict(eyes=[pair[0], pair[1]], synthetic=False)
    return result


def source_snapshot(corpus, manifest):
    expected = {row['rrf'] for row in manifest['cases']}
    if {str(p.relative_to(corpus)) for p in corpus.rglob('*.rrf')} != expected:
        raise ValueError('complete corpus manifest required')
    files = []
    for row in manifest['cases']:
        for key, length in (('rrf', row['wire_bytes']), ('rgba', row['width']*row['height']*4)):
            name = row[key]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('source path bounds')
            raw = (corpus/name).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != length or digest != row[key+'_sha256']:
                raise ValueError('source differs from immutable export')
            files.append(dict(path=name, bytes=length, sha256=digest))
    return sorted(files, key=lambda r: r['path'])


def stats(values):
    values = sorted(values)
    return dict(samples=len(values), p50_ms=values[math.ceil(len(values)*.5)-1],
                p95_ms=values[math.ceil(len(values)*.95)-1], max_ms=values[-1])


def qualification_shapes(manifest):
    shapes = {}
    for w, h in ((1, 1), (1, 17), (17, 1), (17, 19), (31, 33), (65, 47), (256, 2), (576, 640)):
        for kind in range(3):
            for q in (1, 8):
                for policy in (1, 2):
                    shapes[f'generated/{w}x{h}-p{kind}-q{q}-policy{policy}'] = (w, h)
    for h in (7, 8, 9, 31, 32, 33, 127, 128, 129, 255, 256, 257, 4095, 4096):
        for w in (1, 17):
            shapes[f'boundary/{w}x{h}-p2-q1-policy2'] = (w, h)
    for w, h in ((4096, 1), (4095, 3)):
        shapes[f'boundary/{w}x{h}-p2-q1-policy2'] = (w, h)
    for kind in (0, 1):
        shapes[f'maximum/2048x2048-p{kind}-q1-policy2'] = (2048, 2048)
    for name, shape in {'raw-one': (1, 1), 'raw-odd': (3, 1), 'plain-four': (4, 4), 'up-four': (4, 4)}.items():
        shapes[f'golden/{name}.rrf'] = shape
    for row in manifest['cases']:
        shapes['corpus/'+row['rrf']] = (row['width'], row['height'])
    return shapes


def audit(data, manifest):
    qualify = data.get('qualification_only')
    if (data.get('schema') != 1 or data.get('experiment') != 'host-image-copy-timing'
            or type(qualify) is not bool or data.get('headset_used') is not False
            or data.get('pass') is not True or data.get('promotion_blocked') is not True
            or data.get('warmups') != (1 if qualify else 3)
            or any(data.get(k) is not True for k in ('checked_crc_grammar_lz4_in_every_arm',
                   'poison_in_total_time', 'oracle_outside_total_time', 'reused_workspaces'))
            or data.get('cpu_backend') not in ('x86-ssse3-pack-expand+sse2-row',
                   'x86-sse2-row+scalar-pack-expand', 'aarch64-neon', 'scalar-fallback')):
        raise ValueError('benchmark scope and preprocessing')
    reps, cases = data.get('repetitions'), data.get('cases')
    if type(reps) is not int or not 1 <= reps <= 63 or (qualify and reps != 1):
        raise ValueError('repetitions')
    if (type(cases) is not list or not cases or data.get('case_count') != len(cases)
            or len({c.get('name') for c in cases}) != len(cases)):
        raise ValueError('complete unique case accounting')
    expected = groups(manifest)
    if qualify:
        # 96 generated + 30 boundary + 2 maximum + 4 independent goldens.
        qualified_shapes = qualification_shapes(manifest)
        if ({c['name'] for c in cases} != set(qualified_shapes) or data.get('source_corpus_cases') != 0
                or data.get('real_capture_pairs') != 0 or data.get('synthetic_duplicated_eye_cases') != 0):
            raise ValueError('qualification fixture scope')
    else:
        synthetic = sum(g['synthetic'] for g in expected.values())
        if ({c['name'] for c in cases} != set(expected)
                or data.get('source_corpus_cases') != len(manifest['cases'])
                or data.get('real_capture_pairs') != len(expected)-synthetic
                or data.get('synthetic_duplicated_eye_cases') != synthetic):
            raise ValueError('complete paired corpus accounting')
    reports, captured = [], {}
    identity = None
    for case_index, case in enumerate(cases):
        w, h = case.get('width'), case.get('height')
        if type(w) is not int or type(h) is not int or not 1 <= w <= 4096 or not 1 <= h <= 4096 or w*h > 4194304:
            raise ValueError('shape bounds')
        if qualify and (w, h) != qualified_shapes[case['name']]:
            raise ValueError('qualification boundary shape')
        observed = (case.get('device'), case.get('device_type'), case.get('driver'))
        if not observed[0] or observed[1] not in (1, 2) or type(observed[2]) is not int:
            raise ValueError('hardware identity')
        if identity is None:
            identity = observed
        if identity != observed:
            raise ValueError('mixed physical devices')
        if not qualify:
            source = expected[case['name']]
            if (case.get('synthetic_duplicated_eyes') is not source['synthetic']
                    or (w, h) != (source['eyes'][0]['width'], source['eyes'][0]['height'])
                    or case.get('eye_wire_bytes') != [eye['wire_bytes'] for eye in source['eyes']]):
                raise ValueError('source shape/origin/wire mismatch')
        cap = case.get('host_copy_capability', {})
        if (any(cap.get(key) is not True for key in ('queried', 'dependencies_enabled',
                'feature_enabled', 'format_supported', 'combined_usage_supported'))
                or cap.get('usage') != 0x00400007
                or case.get('host_availability_and_shape_rejections') is not True):
            raise ValueError('queried/enabled host copy capability and lifetime gates')
        for name, required in (('source_layouts', {1, 7}), ('destination_layouts', {1, 5})):
            values = cap.get(name)
            if type(values) is not list or any(type(v) is not int for v in values) or not required <= set(values):
                raise ValueError('host copy layout support')
        for key in ('optimal_device_access', 'identical_memory_layout', 'identical_memory_type_requirements'):
            if type(cap.get(key)) is not bool:
                raise ValueError('host copy performance query missing')
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != reps*2:
            raise ValueError('complete arm samples')
        values = {arm: [] for arm in ARMS}
        remainder = {arm: [] for arm in ARMS}
        for rep in range(reps):
            batch = rows[rep*2:rep*2+2]
            if {r.get('mode') for r in batch} != set(ARMS):
                raise ValueError('missing/duplicate arm')
            for order, row in enumerate(batch):
                mode = ARMS[(case_index+rep+data['warmups']+order)%2]
                if row.get('rep') != rep or row.get('order') != order or row.get('mode') != mode:
                    raise ValueError('rotated interleaving identity')
                if (row.get('stereo_uploaded_pixel_bytes') != w*h*8
                        or row.get('timed_queue_submissions') != 2
                        or any(row.get(k) is not True for k in ('image_bytes_exact',
                               'sampled_cpu_copy_baseline_exact', 'malformed_pair_rejected_before_write_or_submit'))):
                    raise ValueError('pixel cost or exactness gate')
                for key in TIMES + ('max_srgb_code_error',):
                    v = row.get(key)
                    if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                        raise ValueError('nonfinite observation')
                total, poison = row['stereo_total_ms'], row['stereo_poison_ms']
                if row['max_srgb_code_error'] >= .5 or poison > total:
                    raise ValueError('color/poison interval bounds')
                nonoverlap = sum(row[k] for k in ('stereo_poison_ms', 'stereo_preflight_decode_ms',
                                                  'stereo_upload_ms', 'stereo_pipeline_wall_ms'))
                if nonoverlap > total+max(1e-5, total*1e-5):
                    raise ValueError('outer wall understates measured stages')
                values[mode].append(total)
                remainder[mode].append(total-poison)
        arms = {arm: dict(outer_wall=stats(values[arm]), without_measured_poison_interval=stats(remainder[arm])) for arm in ARMS}
        reports.append(dict(name=case['name'], arms=arms))
        if not qualify and not expected[case['name']]['synthetic']:
            q = str(expected[case['name']]['eyes'][0]['q'])
            group = captured.setdefault(q, {a: [] for a in ARMS})
            for arm in ARMS:
                group[arm].append(arms[arm]['outer_wall']['p50_ms'])
    return dict(pass_=True, qualification_only=qualify, case_count=len(cases), device=identity,
                measured_stereo_arm_invocations=len(cases)*reps*2, per_case=reports,
                captured_summary_by_base_q={q: {a: dict(pairs=len(v), median_of_pair_medians_ms=statistics.median(v)) for a, v in arms.items()} for q, arms in captured.items()},
                headset_used=False, promotion_blocked=True, sub15_gate_evaluated=False,
                limitations=['Private host GPU and uncontrolled desktop workload.',
                             'Whole outer clock includes poison; interval subtraction does not remove its cache effects.',
                             'Same-format RRF client image stage; server transcode, network and compositor excluded.',
                             'Repeated static inputs and reused workspace capacities after warmup.',
                             'Host-copy performance hints are queried, not measured consumer access costs.'])


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
        with args.out.open('x') as f:
            json.dump(report, f, indent=2)
            f.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'per_case'}, indent=2))


if __name__ == '__main__':
    main()
