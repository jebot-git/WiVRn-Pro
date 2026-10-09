#!/usr/bin/env python3
"""Audit mapped-input memory/copy comparisons with exact source and stage accounting."""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

import importlib.util
_spec = importlib.util.spec_from_file_location("rrf_image_audit", Path(__file__).resolve().parents[1]/"rowfilter_image_timing/audit.py")
rrf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rrf)
ARMS = ['rrf_cpu_reference','rrf_staged_default','rrf_direct_default','rrf_staged_cached','rrf_direct_cached','rrb32_staged_default','rrb32_direct_default','rrb32_staged_cached','rrb32_direct_cached']
TIMES = rrf.TIMES

def matched_variants(manifest, blocks):
    rrf.groups(manifest)
    sources = {r['rrf']: r for r in manifest['cases']}
    result = {name: {} for name in sources}
    for row in blocks.get('cases', []):
        name, b = row.get('rrb'), row.get('block_rows')
        if type(name) is not str or type(b) is not int or b not in (16,32,64,128) or not name.endswith(f'.r{b}.rrb'):
            raise ValueError('RRB variant identity')
        source = name[:-len(f'.r{b}.rrb')]+'.rrf'
        if source not in sources or b in result[source]:
            raise ValueError('unmatched/duplicate RRB variant')
        ref = sources[source]
        for key in ('width','height','q','rgba','rgba_sha256','exact'):
            if row.get(key) != ref.get(key):
                raise ValueError('RRF/RRB source mismatch')
        if (row.get('source_kind') != ('synthetic' if source.startswith('synthetic/') else 'captured')
                or row.get('blocks') != math.ceil(row['height']/b)):
            raise ValueError('RRB source kind/partition')
        result[source][b] = row
    if any(set(v) != {16,32,64,128} for v in result.values()):
        raise ValueError('incomplete RRB variants')
    return result


def source_snapshot(corpus, manifest, blocks=False):
    if not blocks:
        return rrf.source_snapshot(corpus, manifest)
    expected = {r['rrb'] for r in manifest['cases']}
    if {str(p.relative_to(corpus)) for p in corpus.rglob('*.rrb')} != expected:
        raise ValueError('complete RRB corpus manifest required')
    result = {}
    for row in manifest['cases']:
        for key, length in (('rrb',row['wire_bytes']),('rgba',row['width']*row['height']*4)):
            name = row[key]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('source path bounds')
            raw = (corpus/name).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != length or digest != row[key+'_sha256']:
                raise ValueError('source differs from immutable export')
            result[name] = dict(path=name, bytes=length, sha256=digest)
    return sorted(result.values(), key=lambda r:r['path'])


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


def audit(data, manifest, block_manifest):
    qualify = data.get('qualification_only')
    if (data.get('schema') != 1 or data.get('experiment') != 'mapped-input-decode'
            or type(qualify) is not bool or data.get('headset_used') is not False
            or data.get('pass') is not True or data.get('promotion_blocked') is not True
            or data.get('warmups') != (1 if qualify else 3) or data.get('memory_gate_rejections') != 5
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
    expected = rrf.groups(manifest)
    variants = matched_variants(manifest, block_manifest)
    if qualify:
        # 96 generated + 30 boundary + 2 maximum + 4 independent goldens.
        qualified_shapes = qualification_shapes(manifest)
        if {c['name'] for c in cases} != set(qualified_shapes):
            raise ValueError('qualification fixture scope')
    else:
        if {c['name'] for c in cases} != set(expected):
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
                    or (w, h) != (source['eyes'][0]['width'], source['eyes'][0]['height'])):
                raise ValueError('source shape/origin/wire mismatch')
        descriptions = case.get('variants')
        if type(descriptions) is not list or len(descriptions) != 2:
            raise ValueError('complete format variants')
        for v, description in zip((0,2),descriptions):
            left = 8 << v if v else 0
            right = (16 if left == 128 else left*2) if qualify and v else left
            counts = [math.ceil(h/b) if b else 1 for b in (left,right)]
            if (type(description.get('compressed_present')) is not bool or type(description.get('variant')) is not int or description.get('variant') != v or description.get('eye_block_rows') != [left,right]
                    or description.get('eye_block_counts') != counts):
                raise ValueError('exact per-eye block partition')
            sizes = description.get('eye_wire_bytes')
            if type(sizes) is not list or len(sizes) != 2 or any(type(n) is not int or not 32 <= n <= 16777216 for n in sizes):
                raise ValueError('bounded stereo wire lengths')
            if not qualify:
                wanted = [eye['wire_bytes'] if not v else variants[eye['rrf']][left]['wire_bytes'] for eye in source['eyes']]
                if sizes != wanted:
                    raise ValueError('exact immutable wire lengths')
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != reps*9:
            raise ValueError('complete arm samples')
        values = {arm: [] for arm in ARMS}
        remainder = {arm: [] for arm in ARMS}
        memories = {}
        for rep in range(reps):
            batch = rows[rep*9:rep*9+9]
            if {r.get('mode') for r in batch} != set(ARMS):
                raise ValueError('missing/duplicate arm')
            for order, row in enumerate(batch):
                mode = ARMS[(case_index+rep+data['warmups']+order)%9]
                if (type(row.get('rep')) is not int or type(row.get('order')) is not int or type(row.get('variant')) is not int
                        or row.get('rep') != rep or row.get('order') != order or row.get('mode') != mode):
                    raise ValueError('rotated interleaving identity')
                if (row.get('variant') != (2 if ARMS.index(mode)>=5 else 0)
                        or row.get('stereo_uploaded_pixel_bytes') != w*h*(8 if mode==ARMS[0] else 6)
                        or row.get('timed_queue_submissions') != 2
                        or any(row.get(k) is not True for k in ('image_bytes_exact',
                               'sampled_cpu_copy_baseline_exact', 'malformed_pair_rejected_before_write_or_submit', 'wrong_shape_rejected_before_write_or_submit'))):
                    raise ValueError('pixel cost or exactness gate')
                arm = ARMS.index(mode)
                direct, cached = arm in (2,4,6,8), arm in (3,4,7,8)
                pixels = w*h*(8 if arm==0 else 6)
                desc = descriptions[1 if arm>=5 else 0]
                metadata = sum((1+4*n)*4+4 for n in desc['eye_block_counts']) if arm>=5 else (16 if arm else 0)
                if (row.get('split_decode_measured') is not direct or row.get('cached_requested') is not cached
                        or row.get('intermediate_pixel_copy_bytes') != (0 if direct else pixels)
                        or row.get('mapped_output_bytes') != pixels or row.get('metadata_bytes') != metadata
                        or row.get('late_decoder_gate_exercised') is not (direct and desc['compressed_present'])):
                    raise ValueError('mapped copy/decode/failure accounting')
                flags, types = row.get('input_memory_flags'), row.get('input_memory_type')
                if (type(flags) is not list or len(flags)!=2 or any(type(f) is not int or f<0 or f&6!=6 or (cached and f&14!=14) for f in flags)
                        or type(types) is not list or len(types)!=2 or any(type(t) is not int or not 0<=t<32 for t in types)):
                    raise ValueError('actual compatible memory properties')
                identity_memory = (tuple(types),tuple(flags))
                if mode in memories and memories[mode]!=identity_memory:
                    raise ValueError('memory changed during reused case')
                memories[mode]=identity_memory
                for key in TIMES + ('max_srgb_code_error','direct_preflight_ms','direct_decode_ms'):
                    v = row.get(key)
                    if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                        raise ValueError('nonfinite observation')
                split = row['direct_preflight_ms']+row['direct_decode_ms']
                combined = row['stereo_preflight_decode_ms']
                if (direct and abs(split-combined)>max(1e-5,combined*1e-5)) or (not direct and split!=0):
                    raise ValueError('direct split intervals')
                total, poison = row['stereo_total_ms'], row['stereo_poison_ms']
                if row['max_srgb_code_error'] >= .5 or poison > total:
                    raise ValueError('color/poison interval bounds')
                nonoverlap = sum(row[k] for k in ('stereo_poison_ms', 'stereo_preflight_decode_ms',
                                                  'stereo_upload_ms', 'stereo_pipeline_wall_ms'))
                if nonoverlap > total+max(1e-5, total*1e-5):
                    raise ValueError('outer wall understates measured stages')
                values[mode].append(total)
                remainder[mode].append(total-poison)
        if len({memories[ARMS[a]] for a in (0,1,2,5,6)}) != 1 or len({memories[ARMS[a]] for a in (3,4,7,8)}) != 1:
            raise ValueError('staged/direct memory selection is confounded')
        arms = {arm: dict(outer_wall=stats(values[arm]), without_measured_poison_interval=stats(remainder[arm])) for arm in ARMS}
        for arm in ARMS:
            arms[arm]['stages'] = {key: stats([r[key] for r in rows if r['mode'] == arm]) for key in TIMES[1:]}
        reports.append(dict(name=case['name'], arms=arms, variants=descriptions, input_memory={a:dict(types=list(m[0]),flags=list(m[1])) for a,m in memories.items()}))
        if not qualify and not expected[case['name']]['synthetic']:
            q = str(expected[case['name']]['eyes'][0]['q'])
            group = captured.setdefault(q, {a: [] for a in ARMS})
            for arm in ARMS:
                group[arm].append(arms[arm]['outer_wall']['p50_ms'])
    return dict(pass_=True, qualification_only=qualify, case_count=len(cases), device=identity,
                measured_stereo_arm_invocations=len(cases)*reps*9, per_case=reports,
                captured_summary_by_base_q={q: {a: dict(pairs=len(v), median_of_pair_medians_ms=statistics.median(v)) for a, v in arms.items()} for q, arms in captured.items()},
                headset_used=False, promotion_blocked=True, sub15_gate_evaluated=False,
                limitations=['Private host GPU and uncontrolled desktop workload.',
                             'Whole outer clock includes poison; interval subtraction does not remove its cache effects.',
                             'Matched RRF/RRB32 private client image stage; server, network and compositor excluded.',
                             'Repeated static inputs and reused workspace capacities after warmup.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('block_manifest', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    report = audit(json.loads(raw), json.loads(args.manifest.read_text()), json.loads(args.block_manifest.read_text()))
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
