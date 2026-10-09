#!/usr/bin/env python3
"""Conditional sampling of exact known frame supply; not a pacing simulator."""
import argparse
from bisect import bisect_right
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('supply_envelope', HERE / 'target_envelope.py')
envelope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envelope)
oracle, cf = envelope.oracle, envelope.cf


def known_pairs(traces):
    """Exact ever-observed complete metadata, including startup observations."""
    known = {}
    for sequence, row in enumerate(traces, 1):
        for pair in oracle.complete_pairs(row):
            frame = pair[0]['frame']
            if frame in known:
                if not cf.replay.snapshot_equal(pair, known[frame]['pair']):
                    raise ValueError('known pair identity has conflicting metadata')
                known[frame]['observed_sequences'].add(sequence)
                continue
            known[frame] = dict(pair=pair, first_observed_sequence=sequence, observed_sequences={sequence},
                frame=frame, target=pair[0]['view_display_time_xr_ns'],
                receipt=min(f['receive_xr_ns'] for f in pair[:2]))
    return known


def available_nodes(traces, presented, deadlines, known, shift_ns, publication_advance_ns, threshold_ns):
    """Only under-threshold nodes are needed to maximize their possible count.

    Removing over-threshold choices from any legal path preserves all order
    constraints, so the maximum good-only chain bounds good count on any path.
    """
    ordered = sorted(known.values(), key=lambda p: (p['receipt'], p['frame']))
    receipts = [p['receipt'] for p in ordered]
    nodes = []
    for deadline in deadlines:
        sequence = deadline['sequence']
        row, present = traces[sequence - 1], presented[sequence - 1]
        select, submit = row['selection_end_xr_ns'] + shift_ns, present['submit_xr_ns'] + shift_ns
        if select <= 0 or submit < select or submit >= 2**63:
            raise ValueError('shifted sampling time outside valid local timestamp domain')
        admission = observed_admission(row, present, select, submit)
        start, stop = bisect_right(receipts, submit - threshold_ns), bisect_right(receipts, submit)
        candidates = []
        for item in ordered[start:stop]:
            ready = max(max(f['receive_xr_ns'], f['ready_xr_ns'] - publication_advance_ns)
                        for f in item['pair'] if f['present'])
            if ready > select:
                continue
            candidates.append(dict(sequence=sequence, frame=item['frame'], target=item['target'],
                age_ns=submit - item['receipt'], threshold_ns=threshold_ns,
                runtime_target=row['predicted_display_xr_ns'],
                before_recorded_admission=admission['before_recorded_admission'],
                absent_from_original_snapshot=sequence not in item['observed_sequences'],
                before_first_recorded_observation=sequence < item['first_observed_sequence']))
        nodes.extend(sorted(candidates, key=lambda n: n['frame']))
    return nodes


def observed_admission(row, present, select, submit):
    before_wait = select < row['wait_frame_end_xr_ns']
    before_render = select < row['render_entry_xr_ns']
    before_fence = row['selection_phase'] == 'after-fence' and select < present['render_fence_end_xr_ns']
    return dict(before_recorded_wait_return=before_wait, before_recorded_render_entry=before_render,
        before_required_recorded_fence=before_fence,
        before_recorded_admission=before_wait or before_render or before_fence,
        submit_at_or_after_original_runtime_prediction=submit >= row['predicted_display_xr_ns'])


def summarize(nodes, generations, warm_count):
    solution = oracle.optimal_chain(nodes, 'under_threshold_first')
    chosen = [nodes[i] for i in solution['path']]
    if solution['unique_count'] != solution['under_threshold_count']:
        raise ValueError('good-only population contains an over-threshold choice')
    good = solution['under_threshold_count']
    return dict(under_threshold_available_nodes=len(nodes),
        deadlines_with_any_under_threshold_pair=len({n['sequence'] for n in nodes}),
        maximum_advancing_under_threshold_count=good,
        necessary_unique_count_ceiling_for_p95_under_threshold=min(warm_count, good * 100 // 95),
        unique_latched_app_sets=len({generations[n['frame']] for n in chosen}),
        chosen_absent_from_original_snapshot=sum(n.get('absent_from_original_snapshot', False) for n in chosen),
        chosen_before_first_recorded_observation=sum(n.get('before_first_recorded_observation', False) for n in chosen),
        chosen_before_recorded_admission=sum(n.get('before_recorded_admission', False) for n in chosen),
        source_target_minus_original_runtime_target_ms=cf.distribution(
            [(n['target'] - n['runtime_target']) / 1e6 for n in chosen]),
        certificate=[[n['sequence'], n['frame']] for n in chosen])


def evaluate(traces, presented, source_rows,
             shifts_ns=(-11_111_111, -5_555_555, 0, 5_555_555, 11_111_111),
             publication_advances_ns=(0, 1_000_000, 2_000_000, 4_000_000, 8_000_000),
             warmup_ns=2_000_000_000, threshold_ns=15_000_000):
    if (not isinstance(shifts_ns, (list, tuple)) or not shifts_ns
            or any(type(v) is not int or abs(v) > 50_000_000 for v in shifts_ns)
            or len(set(shifts_ns)) != len(shifts_ns)):
        raise ValueError('distinct exact sampling shifts within +/-50ms required')
    if (not isinstance(publication_advances_ns, (list, tuple)) or not publication_advances_ns
            or any(type(v) is not int or not 0 <= v <= 50_000_000 for v in publication_advances_ns)
            or len(set(publication_advances_ns)) != len(publication_advances_ns)):
        raise ValueError('distinct exact publication advances in [0,50ms] required')
    original_nodes, deadlines, generations, verified, joined = envelope.population(
        traces, presented, source_rows, warmup_ns, threshold_ns)
    known = known_pairs(traces)
    if set(known) - generations.keys():
        raise ValueError('every known pair must have a source join')
    # Source snapshot constraints are retained for the original baseline only.
    observed = summarize([n for n in original_nodes if n['age_ns'] < threshold_ns], generations, len(deadlines))
    alternatives = []
    for advance in publication_advances_ns:
        for shift in shifts_ns:
            nodes = available_nodes(traces, presented, deadlines, known, shift, advance, threshold_ns)
            constraints = [observed_admission(traces[d['sequence'] - 1], presented[d['sequence'] - 1],
                traces[d['sequence'] - 1]['selection_end_xr_ns'] + shift,
                presented[d['sequence'] - 1]['submit_xr_ns'] + shift) for d in deadlines]
            alternatives.append(dict(sampling_shift_ns=shift, hypothetical_publication_advance_ns=advance,
                shifted_deadline_constraint_counts={key: sum(c[key] for c in constraints) for key in constraints[0]},
                with_recorded_admission_floor=summarize([n for n in nodes if not n['before_recorded_admission']], generations, len(deadlines)),
                **summarize(nodes, generations, len(deadlines))))
    return dict(schema=1, status='pass', analysis_kind='known_supply_sampling_sensitivity',
        warm_observed_deadlines=len(deadlines), known_complete_pairs=len(known), warmup_ns=warmup_ns,
        strict_age_threshold_ns=threshold_ns, observed_snapshot_no_target_ceiling=observed, alternatives=alternatives,
        observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
        measured_speedup=False, latency_gate_evaluated=False, runnable_policy=False, native_behavior_changed=False,
        promotion_eligible=False, release_ready=False, assumptions=[
            'Only exact ever-observed complete-pair metadata is used; unseen or never-complete frames are not invented.',
            'Known supply allows unlimited retention and earlier use of later-observed metadata; actual handle ownership is not simulated.',
            'All hypothetical choices require every participating eye to be ready by the hypothetical selection instant.',
            'Publication advance subtracts a declared amount independently from each ready timestamp, clipped at that eye receipt.',
            'Publication advance is a hypothetical timestamp transformation, not a measured removable decode/queue cost.',
            'Sampling shift moves selection and submit together, preserving each observed selection-to-submit gap.',
            'Original warm-deadline membership and runtime prediction targets stay fixed; OpenXR rescheduling/feedback is not simulated.',
            'Earlier sampling may precede recorded wait/render/fence admission; counts are explicit and a separate bound preserves those floors.',
            'Respecting recorded admission is necessary for that fixed schedule, not proof that shifted GPU/runtime submission is executable.',
            'Future-target limits are removed in all populations; projection pose quality is unqualified.',
            'Only strictly under-threshold nodes are needed to bound their maximum count; deleting bad nodes preserves path order.',
            'A p95 count ceiling is necessary, not an attainable joint cadence/age result.',
            'The known universe can omit real frames, so this is not a complete upper bound over unobserved supply.',
            'Host times never enter local timestamp subtraction; application IDs are not simulation/input epochs.'])


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
        raise ValueError('inputs must be manifest-bound')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    result = evaluate(*rows)
    if any(cf.frozen.sha(run / name) != manifest[name] for name in names):
        raise ValueError('evidence changed during analysis')
    if cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('manifest changed during analysis')
    dependencies = [Path(__file__), Path(envelope.__file__), Path(oracle.__file__), Path(cf.__file__),
                    Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'], original_result_overwritten=False,
        sources_sha256={str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies})
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps([dict(shift_ns=a['sampling_shift_ns'], advance_ns=a['hypothetical_publication_advance_ns'],
        good=a['maximum_advancing_under_threshold_count'],
        count_ceiling=a['necessary_unique_count_ceiling_for_p95_under_threshold'],
        absent=a['chosen_absent_from_original_snapshot']) for a in result['alternatives']]))


if __name__ == '__main__':
    main()
