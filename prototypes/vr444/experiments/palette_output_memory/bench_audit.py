#!/usr/bin/env python3
"""Whole-prototype host timing with exact corpus coverage and explicit scope."""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

MODES = {'cpu_canonical', 'full_copy', 'mapped_coherent', 'compact_copy'}
COMPONENTS = ('stereo_eye_preflight_ms', 'stereo_upload_ms', 'stereo_device_timestamp_span_ms',
              'stereo_submit_wait_ms', 'stereo_assembly_crc_ms')


def groups(manifest):
    result, real = {}, {}
    rows = manifest.get('cases')
    if type(rows) is not list or not rows:
        raise ValueError('manifest cases required')
    names = set()
    for row in rows:
        name = row.get('rpt')
        if (type(name) is not str or name in names or Path(name).is_absolute()
                or '..' in Path(name).parts or not name.endswith('.rpt') or row.get('exact') is not True):
            raise ValueError('manifest source identity')
        names.add(name)
        if name.startswith('synthetic/'):
            result['corpus/'+name] = dict(eyes=[row, row], synthetic=True)
        else:
            if not (name.endswith('-0.rpt') or name.endswith('-1.rpt')):
                raise ValueError('captured eye suffix')
            pair = real.setdefault(name[:-6], {})
            eye = int(name[-5])
            if eye in pair:
                raise ValueError('duplicate eye')
            pair[eye] = row
    for name, pair in real.items():
        if set(pair) != {0, 1} or any(pair[0][key] != pair[1][key] for key in ('width', 'height', 'q')):
            raise ValueError('incomplete or mismatched stereo source')
        result['corpus/'+name] = dict(eyes=[pair[0], pair[1]], synthetic=False)
    return result


def source_snapshot(corpus, manifest):
    expected = {r['rpt'] for r in manifest['cases']}
    actual = {str(p.relative_to(corpus)) for p in corpus.rglob('*.rpt')}
    if expected != actual:
        raise ValueError('corpus differs from complete manifest')
    files = []
    for row in manifest['cases']:
        for key, size in (('rpt', row['wire_bytes']), ('rgba', row['width']*row['height']*4)):
            name = row[key]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('source path bound')
            path = corpus/name
            raw = path.read_bytes()
            if len(raw) != size:
                raise ValueError('source length differs from manifest')
            files.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    return sorted(files, key=lambda r: r['path'])


def quantile(values, p):
    return sorted(values)[math.ceil(len(values)*p)-1]


def audit(data, manifest):
    expected = groups(manifest)
    synthetic = sum(g['synthetic'] for g in expected.values())
    if (data.get('schema') != 1 or data.get('experiment') != 'palette-host-timing'
            or data.get('pass') is not True or data.get('headset_used') is not False
            or data.get('host_stage_measurement') is not True or data.get('promotion_blocked') is not True
            or data.get('cpu_reference_and_gpu_bytes_exact') is not True
            or data.get('poison_and_clear_in_total_time') is not True or data.get('warmups') != 3
            or data.get('real_capture_pairs') != len(expected)-synthetic
            or data.get('synthetic_duplicated_eye_cases') != synthetic
            or data.get('source_corpus_cases') != len(manifest['cases'])):
        raise ValueError('host benchmark scope/accounting')
    reps, cases = data.get('repetitions'), data.get('cases')
    if type(reps) is not int or not 1 <= reps <= 63:
        raise ValueError('repetitions')
    if (type(cases) is not list or len(cases) != len(expected) or data.get('case_count') != len(cases)
            or {c.get('name') for c in cases} != set(expected)):
        raise ValueError('complete source coverage required')
    report, captured = [], {}
    for case in cases:
        source = expected[case['name']]
        w, h = source['eyes'][0]['width'], source['eyes'][0]['height']
        wire = [e['wire_bytes'] for e in source['eyes']]
        if (case.get('synthetic_duplicated_eyes') is not source['synthetic']
                or (case.get('width'), case.get('height')) != (w, h) or case.get('eye_wire_bytes') != wire
                or case.get('device_type') not in (1, 2) or not case.get('device')):
            raise ValueError('source or device identity')
        tiles = ((w+15)//16)*((h+15)//16)
        copied = dict(cpu_canonical=0, full_copy=2*(772*tiles+8), mapped_coherent=0,
                      compact_copy=sum(4+((v-32+3)//4)*4 for v in wire))
        rows = case.get('iterations')
        if type(rows) is not list or len(rows) != reps*4:
            raise ValueError('sample accounting')
        elapsed = {m: [] for m in MODES}
        for rep in range(reps):
            observed = rows[rep*4:rep*4+4]
            if {r.get('mode') for r in observed} != MODES or [r.get('order') for r in observed] != list(range(4)):
                raise ValueError('arm coverage/order')
            for row in observed:
                mode = row['mode']
                if (type(row.get('rep')) is not int or row['rep'] != rep
                        or row.get('canonical_bytes_exact') is not True or row.get('decoded_pixels_exact') is not True
                        or row.get('stereo_copied_bytes') != copied[mode]
                        or row.get('queue_submissions') != (0 if mode == 'cpu_canonical' else 4 if mode == 'compact_copy' else 2)):
                    raise ValueError('exactness/cost accounting')
                for key in ('stereo_total_ms', 'stereo_pair_preflight_ms'):
                    value = row.get(key)
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError('invalid wall timing')
                if row['stereo_pair_preflight_ms'] > row['stereo_total_ms']:
                    raise ValueError('preflight outside total')
                for key in COMPONENTS:
                    value = row.get(key)
                    if mode == 'cpu_canonical':
                        if value is not None:
                            raise ValueError('uninstrumented CPU component fabricated')
                    elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError('invalid GPU component')
                elapsed[mode].append(row['stereo_total_ms'])
        stats = {mode: dict(samples=len(v), p50_ms=quantile(v,.5), p95_ms=quantile(v,.95), max_ms=max(v)) for mode, v in elapsed.items()}
        report.append(dict(name=case['name'], synthetic_duplicated_eyes=source['synthetic'], base_q=source['eyes'][0]['q'], arms=stats))
        if not source['synthetic']:
            q = str(source['eyes'][0]['q'])
            rows = captured.setdefault(q, {m: [] for m in MODES})
            for mode in MODES:
                rows[mode].append(stats[mode]['p50_ms'])
    summary = {q: {m: dict(capture_pairs=len(v), median_of_pair_medians_ms=statistics.median(v)) for m, v in arms.items()}
               for q, arms in captured.items()}
    return dict(pass_=True, case_count=len(cases), real_capture_pairs=len(expected)-synthetic,
                synthetic_duplicated_eye_cases=synthetic, measured_stereo_arm_invocations=len(cases)*reps*4,
                captured_summary_by_base_q=summary, per_case=report, host_stage_only=True,
                headset_used=False, sub15_gate_evaluated=False, promotion_blocked=True,
                limitations=['Existing desktop processes and GPU workloads are not controlled.',
                             'CPU RGBA input is already available; earlier VR444 decode/quantization, LZ4, network and client expansion are excluded.',
                             'Every GPU run includes intermediate poison, full output clear and host readback poison.',
                             'Repeated static-input host stage timings cannot establish moving-scene headset or optical latency.'])


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
        with args.out.open('x') as f: json.dump(report, f, indent=2); f.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'per_case'}, indent=2))


if __name__ == '__main__': main()
