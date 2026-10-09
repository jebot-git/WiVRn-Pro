#!/usr/bin/env python3
"""Retain all mapped-input checks and audit the first actual stereo consumer."""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import statistics
from pathlib import Path

_spec = importlib.util.spec_from_file_location('mapped_readiness_audit', Path(__file__).resolve().parents[1] / 'mapped_input_decode/audit.py')
mapped = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapped)
ARMS, TIMES = mapped.ARMS, mapped.TIMES
matched_variants, source_snapshot = mapped.matched_variants, mapped.source_snapshot


def audit(data, manifest, block_manifest):
    if data.get('experiment') != 'mapped-input-consumer':
        raise ValueError('first-consumer experiment required')
    readiness = copy.deepcopy(data)
    readiness['experiment'] = 'mapped-input-decode'
    report = mapped.audit(readiness, manifest, block_manifest)
    captured, groups = {}, mapped.rrf.groups(manifest)
    for case, result in zip(data['cases'], report['per_case'], strict=True):
        if case['name'] != result['name']:
            raise ValueError('readiness/consumer case join')
        for row in case['iterations']:
            for key in ('first_consumer_saved_before_oracle', 'first_consumer_samples_exact',
                        'right_consumer_omission_rejected'):
                if row.get(key) is not True:
                    raise ValueError('first consumer evidence missing')
            if (type(row.get('first_consumer_submissions')) is not int or row['first_consumer_submissions'] != 1
                    or type(row.get('joint_queue_submissions')) is not int or row['joint_queue_submissions'] != 3):
                raise ValueError('first consumer/joint submission count')
            for key in ('first_consumer_wall_ms', 'first_consumer_device_ms', 'joint_outer_wall_ms'):
                value = row.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10000:
                    raise ValueError('invalid consumer clock')
            # The probe emits six significant decimal digits. Allow only their
            # rounding error when checking nested intervals, not omitted work.
            tolerance = max(1., row['joint_outer_wall_ms']) * 1e-5
            if (row['joint_outer_wall_ms'] + tolerance < row['stereo_total_ms'] + row['first_consumer_wall_ms']
                    or row['first_consumer_wall_ms'] + tolerance < row['first_consumer_device_ms']):
                raise ValueError('consumer/joint clock omits an enclosed interval')
        for arm in ARMS:
            rows = [row for row in case['iterations'] if row['mode'] == arm]
            for source, target in (('first_consumer_wall_ms', 'first_consumer_wall'),
                                   ('first_consumer_device_ms', 'first_consumer_device'),
                                   ('joint_outer_wall_ms', 'joint_outer_wall')):
                result['arms'][arm][target] = mapped.stats([row[source] for row in rows])
        if not data['qualification_only'] and not groups[case['name']]['synthetic']:
            q = str(groups[case['name']]['eyes'][0]['q'])
            group = captured.setdefault(q, {arm: [] for arm in ARMS})
            for arm in ARMS:
                group[arm].append(result['arms'][arm]['joint_outer_wall']['p50_ms'])
    report.update(experiment='mapped-input-consumer', first_consumer_audited=True,
                  right_eye_omission_controls=len(data['cases']) * 9,
                  captured_joint_summary_by_base_q={q: {arm: dict(pairs=len(values), median_of_pair_medians_ms=statistics.median(values))
                                                        for arm, values in arms.items()} for q, arms in captured.items()})
    report['limitations'] += [
        'First consumer samples actual SRGB images, but is a private compute probe, not the native compositor.',
        'Joint outer clock includes readiness and first-consumer submit/fence/query, with three submissions.',
        'Consumer snapshots and independent byte/color oracle occur after the joint outer clock.']
    return report


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
    if args.out:
        with args.out.open('x') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'per_case'}, indent=2))


if __name__ == '__main__':
    main()
