#!/usr/bin/env python3
"""Exact good-count optimum when every frozen deadline must get a new frame."""
import argparse
from array import array
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('full_count_supply', HERE / 'supply_phase.py')
supply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supply)
cf, oracle = supply.cf, supply.oracle


def solve(frames, deadlines, threshold_ns=15_000_000):
    """Layered DP with a necessary rank band, not a greedy insertion heuristic.

    For D required choices from N ordered identities, choice k (zero based)
    has global frame rank in [k, N-D+k]. Strict ID progression proves this
    bound even when readiness and source-target order are adversarial.
    """
    if type(threshold_ns) is not int or not 0 < threshold_ns < 2**63:
        raise ValueError('positive exact threshold required')
    if not isinstance(frames, list) or not isinstance(deadlines, list) or not deadlines:
        raise ValueError('frame list and nonempty deadline list required')
    if any(type(f) is not dict or any(type(f.get(k)) is not int or not 0 <= f[k] < 2**63
            for k in ('frame', 'target', 'receipt', 'ready')) or not 0 < f['receipt'] <= f['ready'] or not f['target'] for f in frames):
        raise ValueError('invalid frame timing/identity')
    if any(type(d) is not dict or any(type(d.get(k)) is not int or not 0 < d[k] < 2**63
            for k in ('sequence', 'selection', 'submit')) or d['selection'] > d['submit'] for d in deadlines):
        raise ValueError('invalid deadline timing/identity')
    if any(a['sequence'] >= b['sequence'] or a['selection'] >= b['selection'] or a['submit'] >= b['submit']
           for a, b in zip(deadlines, deadlines[1:])):
        raise ValueError('deadlines must strictly advance in sequence and local time')
    frames = sorted(frames, key=lambda f: f['frame'])
    if len({f['frame'] for f in frames}) != len(frames):
        raise ValueError('duplicate frame identity')
    count, available = len(deadlines), len(frames)
    if available < count:
        return dict(full_count_feasible=False, required_count=count, known_frames=available,
            maximum_under_threshold_count=None, certificate=[], reason='fewer known identities than required deadlines')
    width = available - count + 1
    if count * width > 5_000_000:
        raise ValueError('full-count state budget exceeds five million rank-band candidates')
    parents, ranks, prior_scores, states = [], [], [], 0
    previous = []
    for stage, deadline in enumerate(deadlines):
        tree = oracle.PrefixBest([frames[i] for i in previous]) if stage else None
        for index, rank in enumerate(previous):
            frame = frames[rank]
            tree.update(frame['frame'], frame['target'], ((prior_scores[index],), index))
        current, links, scores = array('I'), array('i'), []
        for rank in range(stage, available - count + stage + 1):
            frame = frames[rank]
            if frame['ready'] > deadline['selection']:
                continue
            predecessor = tree.query(frame['frame'], frame['target']) if tree else None
            if stage and predecessor is None:
                continue
            good = int(deadline['submit'] - frame['receipt'] < threshold_ns)
            score, parent = (predecessor[0][0] + good, predecessor[1]) if stage else (good, -1)
            current.append(rank)
            links.append(parent)
            scores.append(score)
        if not current:
            return dict(full_count_feasible=False, required_count=count, known_frames=available,
                maximum_under_threshold_count=None, certificate=[], states_retained=states,
                first_unreachable_deadline_sequence=deadline['sequence'], reason='no complete advancing path through every deadline')
        parents.append(links)
        ranks.append(current)
        states += len(current)
        previous, prior_scores = current, scores
    cursor = max(range(len(prior_scores)), key=lambda i: (prior_scores[i], -i))
    best = prior_scores[cursor]
    chosen = []
    for stage in range(count - 1, -1, -1):
        chosen.append(frames[ranks[stage][cursor]])
        cursor = parents[stage][cursor]
    chosen.reverse()
    if any(a['frame'] >= b['frame'] or a['target'] > b['target'] for a, b in zip(chosen, chosen[1:])):
        raise ValueError('full-count certificate violates identity/target order')
    ages = [d['submit'] - f['receipt'] for d, f in zip(deadlines, chosen)]
    if any(f['ready'] > d['selection'] for d, f in zip(deadlines, chosen)) or sum(a < threshold_ns for a in ages) != best:
        raise ValueError('full-count certificate violates readiness/score')
    required_good = (count * 95 + 99) // 100
    return dict(full_count_feasible=True, required_count=count, known_frames=available,
        maximum_under_threshold_count=best, required_good_count_for_p95=required_good,
        p95_under_threshold_attainable_in_model=best >= required_good,
        conditional_certificate_receive_age_ms=cf.distribution([a / 1e6 for a in ages]),
        states_retained=states, maximum_rank_band_width=width,
        certificate=[[d['sequence'], f['frame']] for d, f in zip(deadlines, chosen)])


def evaluate(traces, presented, source_rows,
             scenarios=((0, 0), (0, 8_000_000), (5_555_555, 0), (5_555_555, 2_000_000), (5_555_555, 4_000_000), (5_555_555, 8_000_000)),
             warmup_ns=2_000_000_000, threshold_ns=15_000_000):
    if (not isinstance(scenarios, (list, tuple)) or not scenarios
            or any(not isinstance(v, (list, tuple)) or len(v) != 2 or any(type(x) is not int for x in v)
                   or not 0 <= v[0] <= 50_000_000 or not 0 <= v[1] <= 50_000_000 for v in scenarios)
            or len({tuple(v) for v in scenarios}) != len(scenarios)):
        raise ValueError('distinct nonnegative sampling-shift/publication-advance scenarios within 50ms required')
    _, warm, generations, verified, joined = supply.envelope.population(traces, presented, source_rows, warmup_ns, threshold_ns)
    known = supply.known_pairs(traces)
    if set(known) - generations.keys():
        raise ValueError('known complete pair lacks source join')
    alternatives = []
    for shift, advance in scenarios:
        frames = [dict(frame=item['frame'], target=item['target'], receipt=item['receipt'],
            ready=max(max(f['receive_xr_ns'], f['ready_xr_ns'] - advance) for f in item['pair'] if f['present']))
            for item in known.values()]
        deadlines = []
        for row in warm:
            sequence = row['sequence']
            trace, present = traces[sequence - 1], presented[sequence - 1]
            select, submit = trace['selection_end_xr_ns'] + shift, present['submit_xr_ns'] + shift
            admission = supply.observed_admission(trace, present, select, submit)
            if admission['before_recorded_admission']:
                raise ValueError('scenario precedes recorded runtime admission')
            deadlines.append(dict(sequence=sequence, selection=select, submit=submit))
        result = solve(frames, deadlines, threshold_ns)
        certificate = result['certificate']
        result.update(sampling_shift_ns=shift, hypothetical_publication_advance_ns=advance,
            chosen_absent_from_original_snapshot=sum(seq not in known[frame]['observed_sequences'] for seq, frame in certificate),
            chosen_before_first_recorded_observation=sum(seq < known[frame]['first_observed_sequence'] for seq, frame in certificate),
            unique_latched_app_sets=len({generations[frame] for _, frame in certificate}),
            source_target_minus_original_runtime_target_ms=cf.distribution([
                (known[frame]['target'] - traces[seq - 1]['predicted_display_xr_ns']) / 1e6 for seq, frame in certificate]))
        alternatives.append(result)
    return dict(schema=1, status='pass', analysis_kind='known_supply_exact_full_deadline_count',
        warm_observed_deadlines=len(warm), warmup_ns=warmup_ns, strict_age_threshold_ns=threshold_ns,
        observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
        alternatives=alternatives, measured_speedup=False, latency_gate_evaluated=False, runnable_policy=False,
        native_behavior_changed=False, promotion_eligible=False, release_ready=False, assumptions=[
            'Every original warm deadline must choose a distinct advancing known wire frame; no waiting/skipped deadline is permitted in this full-count model.',
            'Complete ever-observed metadata and source joins are exact, but unlimited retention and earlier use of later-observed pairs remain optimistic.',
            'Per-eye publication advance is clipped at receipt and is hypothetical, not a measured removable service cost.',
            'Sampling shift preserves each observed selection-to-submit gap and respects recorded wait/render/required-fence admission.',
            'Future-target ceilings are removed; projection quality, ownership, GPU schedule and OpenXR feedback remain unqualified.',
            'A maximum-good certificate establishes only conditional attainability in this known-supply model, not a live performance result.',
            'Certificate age percentiles are descriptive of the maximum-good path; the solver does not claim to minimize every percentile.',
            'Unseen frames and unobserved process tails remain outside the known universe; application IDs are not simulation/input epochs.'])


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
    if any(cf.frozen.sha(run / name) != manifest[name] for name in names) or cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('evidence or manifest changed during analysis')
    dependencies = [Path(__file__), Path(supply.__file__), Path(supply.envelope.__file__), Path(oracle.__file__), Path(cf.__file__),
                    Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'], original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False, sources_sha256={str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies})
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps([dict(shift_ns=a['sampling_shift_ns'], advance_ns=a['hypothetical_publication_advance_ns'],
        feasible=a['full_count_feasible'], good=a['maximum_under_threshold_count'],
        conditional_p95=a.get('conditional_certificate_receive_age_ms', {}).get('p95')) for a in result['alternatives']]))


if __name__ == '__main__':
    main()
