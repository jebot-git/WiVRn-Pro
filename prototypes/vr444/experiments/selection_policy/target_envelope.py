#!/usr/bin/env python3
"""Separate target-lead sensitivity from fixed-deadline frame availability.

Larger-than-one-period envelopes are diagnostic relaxations only. No live
selector, projection pose, frame deadline or safety/qualification gate changes.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('envelope_oracle', HERE / 'availability_oracle.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
cf = oracle.cf
DEFAULT_ENVELOPES = ((0, 1), (1, 2), (1, 1), (5, 4), (3, 2), (7, 4), (2, 1), (3, 1), None)


def validate_envelopes(envelopes):
    if not isinstance(envelopes, (list, tuple)) or not envelopes:
        raise ValueError('nonempty explicit envelope declarations required')
    result = []
    for value in envelopes:
        if value is None:
            result.append(None)
            continue
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or any(type(x) is not int for x in value)
                or not 1 <= value[1] <= 1024 or not 0 <= value[0] <= 4 * value[1]):
            raise ValueError('envelopes must be exact ratios from zero to four periods, or None for no target ceiling')
        # Reject semantically duplicated declarations too, e.g. 1/1 and 2/2.
        from math import gcd
        divisor = gcd(*value)
        result.append((value[0] // divisor, value[1] // divisor))
    if len(set(result)) != len(result):
        raise ValueError('duplicate target envelope')
    return tuple(result)


def population(traces, presented, source_rows, warmup_ns, threshold_ns):
    if (type(warmup_ns) is not int or warmup_ns < 0
            or type(threshold_ns) is not int or threshold_ns <= 0):
        raise ValueError('invalid warmup or strict age threshold')
    verified = cf.trace.evaluate(traces, presented)
    if verified['status'] != 'pass' or len(traces) != len(presented):
        raise ValueError('complete valid observed candidate trace required')
    cutoff = presented[0]['submit_xr_ns'] + warmup_ns
    snapshots, nodes, needed, deadlines = {}, [], set(), []
    for sequence, (record, present) in enumerate(zip(traces, presented), 1):
        needed.add(present['left'])
        pairs = oracle.complete_pairs(record)
        for pair in pairs:
            frame = pair[0]['frame']
            if frame in snapshots and not cf.replay.snapshot_equal(pair, snapshots[frame]):
                raise ValueError('one wire identity has conflicting metadata across acquisition')
            snapshots[frame] = pair
            needed.add(frame)
        if present['submit_xr_ns'] < cutoff:
            continue
        full = (min(record['predicted_display_period_xr_ns'],
                    record['predicted_display_xr_ns'] - record['previous_predicted_display_xr_ns'])
                if record['predicted_display_period_xr_ns'] and record['previous_predicted_display_xr_ns'] else 0)
        deadlines.append(dict(sequence=sequence, submit_xr_ns=present['submit_xr_ns']))
        for pair in sorted(pairs, key=lambda p: p[0]['frame']):
            if any(f['ready_xr_ns'] > record['selection_end_xr_ns'] for f in pair if f['present']):
                raise ValueError('recorded pair not ready at observed selection')
            receipt = min(f['receive_xr_ns'] for f in pair[:2])
            publication = max(f['ready_xr_ns'] for f in pair[:2])
            age = present['submit_xr_ns'] - receipt
            if age < 0 or publication > present['submit_xr_ns']:
                raise ValueError('pair receipt/publication follows observed submission')
            nodes.append(dict(sequence=sequence, frame=pair[0]['frame'], target=pair[0]['view_display_time_xr_ns'],
                runtime_target=record['predicted_display_xr_ns'], runtime_interval_bound_ns=full,
                age_ns=age, threshold_ns=threshold_ns, submit_xr_ns=present['submit_xr_ns'],
                publication_to_submit_ns=present['submit_xr_ns'] - publication))
    if not deadlines:
        raise ValueError('no warm observed deadlines')
    joined = cf.source.evaluate(source_rows, needed)
    if joined['status'] != 'pass' or joined.get('ambiguous_projection_matches'):
        raise ValueError('unambiguous source joins required for every complete observed pair')
    generations = {r['system_frame_id']: tuple((a['pacer_id'], a['app_frame_id']) for a in r['app_frames'])
                   for r in joined['system_to_app_frames']}
    if any(not ids or len(dict(ids)) != len(ids) for ids in generations.values()):
        raise ValueError('source joins have missing or ambiguous application generations')
    return nodes, deadlines, generations, verified, joined


def within(node, envelope):
    return (envelope is None or node['target'] <= node['runtime_target']
            + node['runtime_interval_bound_ns'] * envelope[0] // envelope[1])


def path_summary(nodes, solution, generations):
    selected = [nodes[i] for i in solution['path']]
    return dict(unique_count=solution['unique_count'], under_threshold_count=solution['under_threshold_count'],
        unique_latched_app_sets=len({generations[n['frame']] for n in selected}),
        conditional_receive_age_ms=cf.distribution([n['age_ns'] / 1e6 for n in selected]),
        source_target_minus_runtime_target_ms=cf.distribution([(n['target'] - n['runtime_target']) / 1e6 for n in selected]),
        publication_to_original_submit_ms=cf.distribution([n['publication_to_submit_ns'] / 1e6 for n in selected]),
        certificate=[[n['sequence'], n['frame']] for n in selected])


def evaluate(traces, presented, source_rows, envelopes=DEFAULT_ENVELOPES,
             warmup_ns=2_000_000_000, threshold_ns=15_000_000):
    envelopes = validate_envelopes(envelopes)
    nodes, deadlines, generations, verified, joined = population(traces, presented, source_rows, warmup_ns, threshold_ns)
    alternatives = []
    for envelope in envelopes:
        available = [n for n in nodes if within(n, envelope)]
        paths = [dict(objective=objective, **path_summary(available, oracle.optimal_chain(available, objective), generations))
                 for objective in ('unique_first', 'under_threshold_first')]
        quantile = oracle.best_quantile_at_max_count(available)
        quantile_path = [available[i] for i in quantile.pop('path')]
        quantile['certificate'] = [[n['sequence'], n['frame']] for n in quantile_path]
        quantile['source_target_minus_runtime_target_ms'] = cf.distribution(
            [(n['target'] - n['runtime_target']) / 1e6 for n in quantile_path])
        alternatives.append(dict(future_limit_ratio=list(envelope) if envelope is not None else None,
            target_ceiling_removed=envelope is None, within_existing_one_period_limit=envelope is not None and envelope[0] <= envelope[1],
            available_nodes=len(available), distinct_available_wire_frames=len({n['frame'] for n in available}),
            deadlines_with_any_pair=len({n['sequence'] for n in available}),
            deadlines_with_any_pair_under_threshold=len({n['sequence'] for n in available if n['age_ns'] < threshold_ns}),
            oracle_paths=paths, best_p95_at_maximum_unique_count=quantile))
    return dict(schema=1, status='pass', analysis_kind='frozen_target_envelope_sensitivity',
        warm_observed_deadlines=len(deadlines), observed_deadline_span_ns=deadlines[-1]['submit_xr_ns'] - deadlines[0]['submit_xr_ns'],
        warmup_ns=warmup_ns, strict_age_threshold_ns=threshold_ns, complete_pairs_source_joined=len(generations),
        observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
        alternatives=alternatives, measured_speedup=False, latency_gate_evaluated=False, runnable_policy=False,
        native_behavior_changed=False, promotion_eligible=False, release_ready=False, assumptions=[
            'Only exact complete recorded histories or actually retained pairs at observed selections are available.',
            'Deadlines, publication/receive timestamps, source identity, skipped attempts and feedback are frozen.',
            'At most one advancing wire ID per observed deadline; frame IDs strictly advance and source targets do not regress.',
            'Warm-start ownership, closest-target guards and successor reservation are relaxed; waiting and repeats cost nothing.',
            'A larger target envelope is a diagnostic relaxation, not permission to submit an unqualified future-predicted pose.',
            'Removing the target ceiling does not introduce frames outside the exact recorded snapshots.',
            'The maximum-count and maximum-under-threshold objectives can select different paths.',
            'A lower optimistic age bound is not an attainable selector or live cadence result.',
            'Source IDs identify latched application generations, not distinct simulation epochs or optical freshness.',
            'Host source timestamps are never subtracted from client timestamps.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    sealed = cf.frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('sealed diagnostic reanalysis failed')
    manifest_path = run / 'hashes.json'
    manifest = json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('manifest changed after verification')
    names = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    if not set(names).issubset(manifest):
        raise ValueError('all inputs must be manifest-bound')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    result = evaluate(*rows)
    for name in names:
        if cf.frozen.sha(run / name) != manifest[name]:
            raise ValueError('evidence changed during analysis')
    if cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('manifest changed during analysis')
    dependencies = [Path(__file__), Path(oracle.__file__), Path(cf.__file__), Path(cf.frozen.__file__),
                    Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'], original_result_overwritten=False,
        sources_sha256={str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies})
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps([dict(envelope=a['future_limit_ratio'], under_threshold=a['deadlines_with_any_pair_under_threshold'],
        max_count=a['oracle_paths'][0]['unique_count'], max_good=a['oracle_paths'][1]['under_threshold_count'],
        min_p95_ns=a['best_p95_at_maximum_unique_count']['minimum_age_ns']) for a in result['alternatives']]))


if __name__ == '__main__':
    main()
