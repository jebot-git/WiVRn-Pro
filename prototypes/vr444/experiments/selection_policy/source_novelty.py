#!/usr/bin/env python3
"""Exact count/age frontier with at most one choice per source generation set."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('novelty_target', HERE / 'target_admission.py')
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
phase, cardinality, cf, supply = target.phase, target.cardinality, target.cf, target.supply
validate_periods, target_ceilings, eligibility_frames = target.validate_periods, target.target_ceilings, target.eligibility_frames
WARM_MODELS = ('observed_submission_prefix',)


def generation_key(value):
    if (type(value) not in (tuple, list) or not value or any(type(pair) not in (tuple, list) or len(pair) != 2
            or any(type(n) is not int or not 0 < n < 2**63 for n in pair) for pair in value)):
        raise ValueError('nonempty positive exact source identities required')
    key = tuple(sorted(tuple(pair) for pair in value))
    if len({pacer for pacer, _ in key}) != len(key):
        raise ValueError('ambiguous source pacer identity')
    return key


def generation_groups(frames, generations):
    """Keep encoded alternatives but assign one ordinal to each contiguous set."""
    output, seen, previous, group = [], set(), None, -1
    for i, frame in enumerate(frames):
        if type(frame.get('frame')) is not int or frame['frame'] not in generations or i and frames[i-1]['frame'] >= frame['frame']:
            raise ValueError('ordered source-joined encoded frames required')
        key = generation_key(generations[frame['frame']])
        if key != previous:
            if key in seen:
                raise ValueError('source generation recurs noncontiguously; ordered-group solver inapplicable')
            seen.add(key)
            group += 1
            previous = key
        output.append(dict(frame, generation=group))
    return output, dict(known_encoded_frames=len(frames), distinct_generation_sets=len(seen),
        repeated_encoded_identities=len(frames)-len(seen), generation_groups_contiguous=True)


def novel_warm_population(known, traces, presented, cutoff, model, generations):
    if model != 'observed_submission_prefix':
        raise ValueError('source novelty requires actual observed warm prefix')
    # Check the entire known order before warm filtering can hide recurrence.
    grouped, group_contract = generation_groups(sorted(known.values(), key=lambda f:f['frame']), generations)
    admitted, contract = cardinality.warm_population({f['frame']:f for f in grouped}, traces, presented, cutoff, model)
    before = [p for p in presented if p['submit_xr_ns'] < cutoff]
    seen = {generation_key(generations[p['left']]) for p in before}
    output = [f for f in admitted if generation_key(generations[f['frame']]) not in seen]
    contract.update(source_generation_contract=group_contract,
        pre_warm_generation_sets=[list(map(list, key)) for key in sorted(seen)],
        pre_warm_generation_sets_excluded=len(seen), additional_encoded_frames_excluded=len(admitted)-len(output),
        admitted_known_frames=len(output), admitted_generation_sets=len({f['generation'] for f in output}))
    return output, contract


def audit_novelty_frontier(result, original, transformed, deadlines, ceilings, maximum_skips, threshold_ns, targets, generations, contract):
    result = target.audit_target_frontier(result, original, transformed, deadlines, ceilings, maximum_skips, threshold_ns, targets)
    warm = {generation_key(key) for key in contract['pre_warm_generation_sets']}
    mapping = {f['frame']:f for f in original}
    for entry in result['frontier']:
        seen, previous = set(), -1
        for _, frame in entry['certificate']:
            key = generation_key(generations[frame])
            ordinal = mapping[frame]['generation']
            if key in warm or key in seen or type(ordinal) is not int or ordinal <= previous:
                raise ValueError('certificate repeats source generation or actual warm history')
            seen.add(key)
            previous = ordinal
        if entry['feasible']:
            entry.update(distinct_source_generations=len(seen), direct_generation_identity_checked=True,
                conditional_distinct_generation_cadence_hz=entry['conditional_unique_frame_cadence_hz'])
    return result


def evaluate(traces, presented, source_rows, solver, scenarios=((0, 0), (0, 8_000_000), (5_555_555, 0), (5_555_555, 2_000_000), (5_555_555, 4_000_000)),
             warm_models=('observed_submission_prefix',), target_periods=1, warmup_ns=2_000_000_000, threshold_ns=15_000_000, cadence_floor_hz=88):
    validate_periods(target_periods)
    if (type(cadence_floor_hz) is not int or not 0 < cadence_floor_hz <= 1000
            or not isinstance(warm_models, (list, tuple)) or not warm_models or len(set(warm_models)) != len(warm_models)
            or any(m not in WARM_MODELS for m in warm_models)):
        raise ValueError('invalid cadence floor or warm models')
    if (not isinstance(scenarios, (list, tuple)) or not scenarios
            or any(not isinstance(v, (list, tuple)) or len(v) != 2 or any(type(x) is not int or not 0 <= x <= 50_000_000 for x in v) for v in scenarios)
            or len({tuple(v) for v in scenarios}) != len(scenarios)):
        raise ValueError('invalid sampling/publication scenarios')
    _, warm, generations, verified, joined = supply.envelope.population(traces, presented, source_rows, warmup_ns, threshold_ns)
    known = supply.known_pairs(traces)
    if set(known) - generations.keys():
        raise ValueError('known pair lacks source join')
    span = warm[-1]['submit_xr_ns'] - warm[0]['submit_xr_ns']
    minimum_count = (cadence_floor_hz * span + 999_999_999) // 1_000_000_000 + 1
    maximum_skips = len(warm) - minimum_count
    if not 0 <= maximum_skips <= 64:
        raise ValueError('reference window requires invalid or more than 64 skip budgets')
    targets = {d['sequence']: traces[d['sequence'] - 1]['predicted_display_xr_ns'] for d in warm}
    cutoff = presented[0]['submit_xr_ns'] + warmup_ns
    alternatives = []
    with tempfile.TemporaryDirectory(prefix='sub15-cardinality-input-') as temp:
        input_path = Path(temp) / 'population.txt'
        for model in warm_models:
            admitted, warm_contract = novel_warm_population(known, traces, presented, cutoff, model, generations)
            for shift, advance in scenarios:
                frames = [dict(frame=f['frame'], target=f['target'], receipt=f['receipt'], generation=f['generation'],
                    ready=max(max(eye['receive_xr_ns'], eye['ready_xr_ns'] - advance) for eye in f['pair'] if eye['present'])) for f in admitted]
                deadlines = []
                for d in warm:
                    seq = d['sequence']
                    row, p = traces[seq - 1], presented[seq - 1]
                    select, submit = row['selection_end_xr_ns'] + shift, p['submit_xr_ns'] + shift
                    if supply.observed_admission(row, p, select, submit)['before_recorded_admission'] or submit >= 2**63:
                        raise ValueError('scenario violates recorded admission or local timestamp range')
                    deadlines.append(dict(sequence=seq, selection=select, submit=submit))
                original_frames = frames
                ceilings = target_ceilings(traces, deadlines, target_periods)
                frames, transform = eligibility_frames(original_frames, deadlines, ceilings)
                lines = [f'{len(frames)} {len(deadlines)} {maximum_skips} {threshold_ns}']
                lines += [' '.join(str(f[k]) for k in ('frame', 'target', 'receipt', 'ready', 'generation')) for f in frames]
                lines += [' '.join(str(d[k]) for k in ('sequence', 'selection', 'submit')) for d in deadlines]
                input_path.write_text('\n'.join(lines) + '\n')
                command = [str(solver), str(input_path)]
                run = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if run.returncode:
                    raise ValueError('cardinality solver failed: ' + run.stderr)
                result = audit_novelty_frontier(json.loads(run.stdout), original_frames, frames, deadlines, ceilings, maximum_skips, threshold_ns, targets, generations, warm_contract)
                feasible_target = [e for e in result['frontier'] if e['feasible'] and e['p95_under_threshold_attainable_in_model']
                    and e['conditional_unique_frame_cadence_hz'] is not None and e['conditional_unique_frame_cadence_hz'] >= cadence_floor_hz]
                best = max(feasible_target, key=lambda e: e['required_count']) if feasible_target else None
                keep = {0, maximum_skips} | ({best['skips']} if best else set())
                for entry in result['frontier']:
                    if entry['feasible']:
                        path = entry['certificate']
                        entry.update(unique_latched_app_sets=len({generations[frame] for _, frame in path}),
                            chosen_absent_from_original_snapshot=sum(seq not in known[frame]['observed_sequences'] for seq, frame in path),
                            chosen_before_first_recorded_observation=sum(seq < known[frame]['first_observed_sequence'] for seq, frame in path))
                    entry['certificate_retained'] = entry['skips'] in keep
                    if not entry['certificate_retained']:
                        del entry['certificate']
                result.update(target_periods=target_periods, target_admission=transform, warm_start=warm_contract, sampling_shift_ns=shift, hypothetical_publication_advance_ns=advance,
                    input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
                    highest_count_meeting_conditional_age_and_cadence=best['required_count'] if best else None)
                alternatives.append(result)
    return dict(schema=1, status='pass', analysis_kind='distinct_source_generation_exact_small_skip_frontier', target_periods=target_periods,
        warm_observed_deadlines=len(warm), observed_deadline_span_ns=span, strict_age_threshold_ns=threshold_ns,
        cadence_floor_reference_hz=cadence_floor_hz, full_window_minimum_count=minimum_count, maximum_skips=maximum_skips,
        observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
        alternatives=alternatives, measured_speedup=False, latency_gate_evaluated=False, runnable_policy=False,
        native_behavior_changed=False, promotion_eligible=False, release_ready=False,
        assumptions=[
            'At most one encoded choice from each distinct source application-frame set; all actual pre-warm generation sets excluded.',
            'Generation sets must occupy contiguous ranges in increasing encoded frame order; noncontiguous recurrence is rejected.',
            'Application frame IDs identify latched generations, not simulation/input epochs or optical freshness.',
            'Exact cardinality optimum over zero through the declared skip budget; all certificate scores, readiness and identities are independently recomputed.',
            'The full-window count is sufficient for the cadence reference, not the only way a shorter endpoint interval could meet it.',
            'Observed-submission-prefix model excludes actual pre-warm submitted IDs and preserves forward frame/target identity from the last actual pre-warm submission.',
            'Relaxed warm start can reuse old startup identities inside the hypothetical window and must not be called actual new-content cadence.',
            'The distinct-generation model retains unlimited known-pair retention and may use later-observed metadata when timestamps permit it.',
            'Publication advance is hypothetical per-participating-eye subtraction clipped at receipt; sampling shift preserves original selection-to-submit gaps.',
            'Recorded runtime admission floors hold, but GPU scheduling, runtime feedback, ownership and projection quality are unqualified.',
            'Target admission uses the declared original runtime target plus periods of the bounded-newest horizon recomputed from recorded runtime inputs; None removes that ceiling.',
            'Monotone ceiling crossing is encoded as a synthetic eligibility floor, not a measured publication timestamp; every returned choice is also checked against the original ceiling and readiness.',
            'All frontier paths are audited; only endpoint and highest qualifying-count certificates are retained, others have exact hashes for reproduction.',
            'Conditional age/cadence feasibility is not a live latency gate evaluation or proof of an executable selector.'])


def check_encoded_upper_bound(result, reference):
    if (reference.get('status') != 'pass' or reference.get('analysis_kind') != 'finite_positive_phase_exact_count_grid'
            or len(result['alternatives']) != len(reference['alternatives'])
            or any(result[k] != reference[k] for k in ('warm_observed_deadlines', 'observed_deadline_span_ns',
                'strict_age_threshold_ns', 'cadence_floor_reference_hz', 'maximum_skips'))):
        raise ValueError('encoded reference domain differs')
    for new, old in zip(result['alternatives'], reference['alternatives']):
        if any(new[k] != old[k] for k in ('sampling_shift_ns', 'hypothetical_publication_advance_ns')):
            raise ValueError('encoded reference scenario differs')
        if len(new['frontier']) != len(old['frontier']):
            raise ValueError('encoded reference count population differs')
        for a, b in zip(new['frontier'], old['frontier']):
            if (a['skips'] != b['skips'] or a['required_count'] != b['required_count']
                    or a['maximum_good'] > b['maximum_good']):
                raise ValueError('generation restriction exceeded encoded upper bound')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, output, solver = args.run.resolve(), args.out.resolve(), args.solver.resolve()
    if output.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    build_hash = cf.frozen.sha(args.build_receipt)
    build = json.loads(args.build_receipt.read_text())
    if build.get('status') != 'pass' or cf.frozen.sha(solver) != build.get('solver_sha256'):
        raise ValueError('solver does not match validated build')
    for name, digest in build['sources_sha256'].items():
        if cf.frozen.sha(cf.ROOT / name) != digest:
            raise ValueError('validated cardinality implementation changed: ' + name)
    sealed = cf.frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('sealed diagnostic evidence validation failed')
    manifest_path = run / 'hashes.json'
    manifest = json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('original manifest changed')
    names = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    if not set(names).issubset(manifest):
        raise ValueError('phase-grid input not manifest-bound')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    dependencies = [Path(__file__), Path(target.__file__), Path(phase.__file__), Path(cardinality.__file__), Path(cardinality.supply.__file__),
        Path(cardinality.supply.envelope.__file__), Path(cardinality.supply.oracle.__file__), Path(cf.__file__),
        Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__),
        HERE/'novelty_oracle.h', HERE/'novelty_probe.cpp']
    hashes = {str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies}
    reference_hash = cf.frozen.sha(args.reference)
    reference = json.loads(args.reference.read_text())
    results = []
    for periods in (None, 2):
        print(json.dumps({'starting_target_periods': periods, 'scenarios': len(phase.grid())}), flush=True)
        result = evaluate(*rows, solver, scenarios=phase.grid(), target_periods=periods)
        result = phase.summarize(result)
        result.update(analysis_kind='distinct_source_generation_exact_phase_count_grid',
            target_periods=periods, future_target_limits_removed=periods is None)
        check_encoded_upper_bound(result, reference)
        result['all_scores_at_or_below_encoded_reference'] = True
        results.append(result)
    result = dict(schema=1, status='pass', analysis_kind='distinct_source_generation_admission_sensitivity',
        target_bounds=results, reference_sha256=reference_hash, finite_tested_cells=len(phase.grid()) * len(results),
        effective_readiness_is_measured_publication=False, measured_speedup=False, runnable_policy=False,
        latency_gate_evaluated=False, promotion_eligible=False, release_ready=False,
        assumptions=[
            'None removes the target ceiling; 2 adds twice the bounded-newest horizon from original runtime inputs.',
            'Each selected encoded frame must represent a distinct latched application-frame set absent from actual pre-warm submissions.',
            'Contiguous source-group ranges are checked over the full known frame order before any warm filtering.',
            'The bounded-newest horizon matches only bounded-newest target admission, not its ownership, selection instant or whole selector behavior.',
            'Shifted selection/submission leaves the original runtime target and horizon fixed; runtime feedback and upper admission remain unqualified.',
            'Every finite cell uses unlimited known-pair retention and hypothetical per-eye publication advance, clipped at receipt.',
            'Selected encoded IDs and source application-frame sets both strictly advance without reuse; neither identity measures simulation/input epochs or input-to-photon cadence.',
            'Nonmonotone target ceilings are rejected, never sorted or approximated.'])
    if (cf.frozen.sha(args.reference) != reference_hash or any(cf.frozen.sha(run / name) != manifest[name] for name in names)
            or cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']
            or cf.frozen.sha(solver) != build['solver_sha256'] or cf.frozen.sha(args.build_receipt) != build_hash
            or any(cf.frozen.sha(cf.ROOT / name) != digest for name, digest in hashes.items())):
        raise ValueError('phase-grid evidence or implementation changed during analysis')
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'], original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False, solver_sha256=build['solver_sha256'], build_receipt_sha256=build_hash, sources_sha256=hashes)
    with output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps([dict(periods=r['target_periods'], qualifying_cells=r['conditionally_feasible_grid_cells'], best=r['best_tested_phase_per_advance']) for r in results]))


if __name__ == '__main__':
    main()
