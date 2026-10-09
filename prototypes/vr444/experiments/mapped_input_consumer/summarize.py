#!/usr/bin/env python3
"""Keep readiness and joint first-consumer paired comparisons distinct."""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location('mapped_readiness_summary', Path(__file__).resolve().parents[1] / 'mapped_input_decode/summarize.py')
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
comparisons = base.comparisons


def joint_comparisons(report):
    projected = copy.deepcopy(report)
    for row in projected['per_case']:
        if len(row['arms']) != 9:
            raise ValueError('all nine consumer arms required')
        for arm in row['arms'].values():
            joint = arm['joint_outer_wall']
            if type(joint.get('samples')) is not int or joint['samples'] != 31:
                raise ValueError('complete joint-clock samples required')
            values = [joint[key] for key in ('p50_ms', 'p95_ms', 'max_ms')]
            if (any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10000 for value in values)
                    or values != sorted(values)):
                raise ValueError('invalid joint-clock quantiles')
            arm['outer_wall'] = joint
    return comparisons(projected)


def summarize(reports):
    for report in reports:
        if (report.get('experiment') != 'mapped-input-consumer'
                or report.get('first_consumer_audited') is not True
                or type(report.get('right_eye_omission_controls')) is not int
                or report['right_eye_omission_controls'] != 234):
            raise ValueError('complete first-consumer audit required')
    result = base.summarize(reports)
    result.update(experiment='mapped-input-consumer', qualified_source_commit='b37842d',
                  captured_joint_paired_comparisons=[dict(device=report['device'], comparisons=joint_comparisons(report))
                                                     for report in reports])
    result['captured_readiness_paired_comparisons'] = result.pop('captured_paired_comparisons')
    result['interpretation'] = [
        'Joint outer wall encloses unchanged readiness and first actual sampled-image consumer, including three submissions.',
        'Saved stereo consumer output is compared before the independent image oracle can overwrite it.',
        'Readiness and joint paired differences are separate measured quantiles; consumer p95 is not added to readiness p95.',
        'Direct output removes intermediate copies and workspace zero-fill but still populates all six stereo RGB bytes per pixel.',
        'Arc default/cached labels select the same memory type and are duplicate controls, not different cache policies.',
        'All captured and synthetic profiles are retained, including each paired tail regression; 31 samples do not bound native tails.',
        'This first consumer is a private nearest-SRGB compute probe into host-visible float buffers, not native compositor rendering.',
        'Poison and oracle/snapshot gaps remain; compare only arms inside this batch, not earlier numerical clocks.',
        'RRF/RRB server work, wire growth, network, compositor scheduling and optical response are excluded; no sub-15/headset promotion.'
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = [path.read_bytes() for path in args.reports]
    result = summarize([json.loads(data) for data in raw])
    result['audited_report_sha256'] = [hashlib.sha256(data).hexdigest() for data in raw]
    here = Path(__file__).resolve().parent
    result['postprocessing_source_sha256'] = {
        str(path.relative_to(here.parents[3])): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (here / 'summarize.py', here / 'test_summary.py', here.parent / 'mapped_input_decode/summarize.py')}
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
