#!/usr/bin/env python3
"""Summarize audited matched reports; paired differences are not group differences."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path


def comparisons(report):
    if len(report['per_case']) != 26 or len({row['name'] for row in report['per_case']}) != 26:
        raise ValueError('complete distinct captured and synthetic matrix required')
    rows = [row for row in report['per_case'] if '/synthetic/' not in row['name']]
    if len(rows) != 12 or len({row['name'] for row in rows}) != 12:
        raise ValueError('all twelve distinct captured stereo pairs are required')
    pairs = [(f'{kind}_staged_{memory}', f'{kind}_direct_{memory}')
             for kind in ('rrf', 'rrb32') for memory in ('default', 'cached')]
    pairs += [('rrf_cpu_reference', f'{kind}_direct_cached') for kind in ('rrf', 'rrb32')]
    pairs += [(f'{kind}_direct_default', f'{kind}_direct_cached') for kind in ('rrf', 'rrb32')]
    result = []
    for baseline, candidate in pairs:
        item = dict(baseline=baseline, candidate=candidate, captured_pairs=12, paired_differences={})
        for percentile in ('p50_ms', 'p95_ms'):
            delta = {row['name']: row['arms'][candidate]['outer_wall'][percentile]
                     - row['arms'][baseline]['outer_wall'][percentile] for row in rows}
            values = list(delta.values())
            item['paired_differences'][percentile] = dict(
                improved_pairs=sum(value < 0 for value in values),
                regressed_pairs=sum(value > 0 for value in values),
                equal_pairs=sum(value == 0 for value in values),
                median_candidate_minus_baseline_ms=statistics.median(values),
                per_pair_candidate_minus_baseline_ms=delta)
        result.append(item)
    return result


def summarize(reports):
    if not reports or len({tuple(row['device']) for row in reports}) != len(reports):
        raise ValueError('distinct physical device reports required')
    bound = None
    for report in reports:
        if (report.get('pass') is not True or report.get('qualification_only') is not False
                or report.get('case_count') != 26 or report.get('measured_stereo_arm_invocations') != 7254
                or report.get('headset_used') is not False or report.get('promotion_blocked') is not True):
            raise ValueError('complete audited host comparison required')
        conditions = report['conditions']
        for key in ('research_teams_confirmed_idle', 'manifests_unchanged', 'corpora_unchanged',
                    'binary_unchanged', 'sources_unchanged', 'shaders_unchanged'):
            if conditions.get(key) is not True:
                raise ValueError('measurement conditions failed')
        if conditions.get('repetitions') != 31 or conditions.get('exit_code') != 0:
            raise ValueError('measurement invocation incomplete')
        identity = {key: conditions[key] for key in ('manifest_sha256', 'binary_sha256',
                                                     'source_sha256', 'shader_sha256')}
        if bound is not None and identity != bound:
            raise ValueError('device batches must use identical measured code and inputs')
        bound = identity
    return dict(schema=1, experiment='mapped-input-decode',
                qualified_source_commit='370411c7764a5451a388e2ef9a9dd9f83557aab0',
                headset_used=False, promotion_blocked=True, sub15_gate_evaluated=False,
                measurements=reports,
                captured_paired_comparisons=[dict(device=report['device'], comparisons=comparisons(report))
                                             for report in reports],
                interpretation=[
                    'All direct/staged comparisons preserve the actual input memory choice and decoded pixels.',
                    'Direct output removes an intermediate copy and workspace clear/resize zero-fill; it is not a memcpy-only experiment.',
                    'All six stereo RGB bytes per pixel still become GPU-visible.',
                    'Arc cached/default labels select the same actual memory type; they are duplicate controls.',
                    'The literal-message oracle cleanup is common to these new arms; older branch numbers are not matched controls.',
                    'Per-case p95 values use 31 samples and retain every regression; they are not a headset tail bound.',
                    'Private readiness includes poison and two submissions, excluding server/network/compositor and first consumer.',
                    'A median of per-pair differences differs from subtracting two quality-group medians.',
                    'RRF/RRB wire inflation and transport size limits are unchanged; no global promotion follows.'
                ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = [path.read_bytes() for path in args.reports]
    result = summarize([json.loads(data) for data in raw])
    result['audited_report_sha256'] = [hashlib.sha256(data).hexdigest() for data in raw]
    here = Path(__file__).resolve().parent
    root = here.parents[3]
    result['postprocessing_source_sha256'] = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (here / 'summarize.py', here / 'test_summary.py')}
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
