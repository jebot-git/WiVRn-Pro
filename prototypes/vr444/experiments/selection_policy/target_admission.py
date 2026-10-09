#!/usr/bin/env python3
"""Exact phase/count grid with checked original-runtime target admission."""
import argparse
from bisect import bisect_left
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('target_phase', HERE / 'phase_frontier.py')
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)
cardinality, cf = phase.cardinality, phase.cf
supply, warm_population = cardinality.supply, cardinality.warm_population
WARM_MODELS = cardinality.WARM_MODELS


def validate_periods(periods):
    if periods is not None and (type(periods) is not int or not 0 <= periods <= 3):
        raise ValueError('target periods must be None or an integer in [0,3]')


def target_ceilings(traces, deadlines, periods):
    validate_periods(periods)
    if periods is None:
        return None
    ceilings = []
    for deadline in deadlines:
        row = traces[deadline['sequence'] - 1]
        target, period, prior = (row[k] for k in ('predicted_display_xr_ns', 'predicted_display_period_xr_ns', 'previous_predicted_display_xr_ns'))
        if (any(type(value) is not int or not 0 <= value < 2**63 for value in (target, period, prior))
                or target == 0 or prior > target):
            raise ValueError('invalid original runtime target/period/prior')
        # The recorded closest selector writes max_ahead=0. Recompute the
        # bounded-newest cap from original runtime inputs, as its native code does.
        horizon = min(period, target - prior) if period and prior else 0
        if target + periods * horizon >= 2**63:
            raise ValueError('target ceiling overflow')
        ceilings.append(target + periods * horizon)
    return ceilings


def eligibility_frames(frames, deadlines, ceilings):
    """Map monotone target ceilings to an equivalent per-frame release floor.

    This preserves every eligible edge, not just a returned optimum. For the
    first j with target <= ceiling[j], strict selection order makes selection[j]
    the first admissible deadline. A frame beyond every ceiling has no edge.
    Nonmonotone ceilings cannot be represented this way and are rejected.
    """
    if not deadlines or len(deadlines) > 10000 or len(frames) > 10000:
        raise ValueError('bounded nonempty deadline population required')
    for i, d in enumerate(deadlines):
        if (any(type(d.get(k)) is not int or not 0 < d[k] < 2**63 for k in ('sequence', 'selection', 'submit'))
                or d['selection'] > d['submit'] or i and any(deadlines[i-1][k] >= d[k] for k in ('sequence', 'selection', 'submit'))):
            raise ValueError('invalid or unordered deadline')
    if ceilings is not None and (type(ceilings) not in (tuple, list) or len(ceilings) != len(deadlines)
            or any(type(c) is not int or not 0 < c < 2**63 for c in ceilings)
            or any(a > b for a, b in zip(ceilings, ceilings[1:]))):
        raise ValueError('target ceilings must be bounded positive nondecreasing integers, one per deadline')
    converted, delayed, dropped = [], 0, 0
    for i, f in enumerate(frames):
        if (any(type(f.get(k)) is not int or not 0 <= f[k] < 2**63 for k in ('frame', 'target', 'receipt', 'ready'))
                or not 0 < f['receipt'] <= f['ready'] or f['target'] == 0
                or i and (frames[i-1]['frame'] >= f['frame'] or frames[i-1]['target'] > f['target'])):
            raise ValueError('invalid or unordered source frame')
        j = bisect_left(ceilings, f['target']) if ceilings is not None else 0
        if j == len(deadlines):
            dropped += 1
            continue
        floor = deadlines[j]['selection'] if ceilings is not None else f['ready']
        effective = max(f['ready'], floor)
        delayed += effective > f['ready']
        converted.append(dict(f, ready=effective))
    return converted, dict(original_known_frames=len(frames), solver_frames=len(converted),
        frames_never_target_admissible=dropped, frames_with_synthetic_eligibility_delay=delayed,
        monotone_ceilings_checked=ceilings is not None, synthetic_floor_is_publication_timestamp=False,
        ceiling_sha256=hashlib.sha256(json.dumps(ceilings, separators=(',', ':')).encode()).hexdigest())


def audit_target_frontier(result, original, transformed, deadlines, ceilings, maximum_skips, threshold_ns, targets):
    # Rebuild transformation instead of trusting caller-supplied effective times.
    expected, _ = eligibility_frames(original, deadlines, ceilings)
    if transformed != expected:
        raise ValueError('target eligibility transformation differs')
    result = cardinality.audit_frontier(result, transformed, deadlines, maximum_skips, threshold_ns, targets)
    originals = {f['frame']: f for f in original}
    positions = {d['sequence']: i for i, d in enumerate(deadlines)}
    for entry in result['frontier']:
        for sequence, frame in entry['certificate']:
            f, i = originals[frame], positions[sequence]
            if f['ready'] > deadlines[i]['selection'] or ceilings is not None and f['target'] > ceilings[i]:
                raise ValueError('certificate violates original readiness or target ceiling')
        if entry['feasible']:
            entry['direct_target_admission_checked'] = True
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
            admitted, warm_contract = warm_population(known, traces, presented, cutoff, model)
            for shift, advance in scenarios:
                frames = [dict(frame=f['frame'], target=f['target'], receipt=f['receipt'],
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
                lines += [' '.join(str(f[k]) for k in ('frame', 'target', 'receipt', 'ready')) for f in frames]
                lines += [' '.join(str(d[k]) for k in ('sequence', 'selection', 'submit')) for d in deadlines]
                input_path.write_text('\n'.join(lines) + '\n')
                command = [str(solver), str(input_path)]
                run = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if run.returncode:
                    raise ValueError('cardinality solver failed: ' + run.stderr)
                result = audit_target_frontier(json.loads(run.stdout), original_frames, frames, deadlines, ceilings, maximum_skips, threshold_ns, targets)
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
    return dict(schema=1, status='pass', analysis_kind='monotone_target_ceiling_exact_small_skip_frontier', target_periods=target_periods,
        warm_observed_deadlines=len(warm), observed_deadline_span_ns=span, strict_age_threshold_ns=threshold_ns,
        cadence_floor_reference_hz=cadence_floor_hz, full_window_minimum_count=minimum_count, maximum_skips=maximum_skips,
        observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
        alternatives=alternatives, measured_speedup=False, latency_gate_evaluated=False, runnable_policy=False,
        native_behavior_changed=False, promotion_eligible=False, release_ready=False,
        assumptions=[
            'Exact cardinality optimum over zero through the declared skip budget; all certificate scores, readiness and identities are independently recomputed.',
            'The full-window count is sufficient for the cadence reference, not the only way a shorter endpoint interval could meet it.',
            'Observed-submission-prefix model excludes actual pre-warm submitted IDs and preserves forward frame/target identity from the last actual pre-warm submission.',
            'Relaxed warm start can reuse old startup identities inside the hypothetical window and must not be called actual new-content cadence.',
            'Both models retain unlimited known-pair retention and may use later-observed metadata when timestamps permit it.',
            'Publication advance is hypothetical per-participating-eye subtraction clipped at receipt; sampling shift preserves original selection-to-submit gaps.',
            'Recorded runtime admission floors hold, but GPU scheduling, runtime feedback, ownership and projection quality are unqualified.',
            'Target admission uses the declared original runtime target plus periods of the bounded-newest horizon recomputed from recorded runtime inputs; None removes that ceiling.',
            'Monotone ceiling crossing is encoded as a synthetic eligibility floor, not a measured publication timestamp; every returned choice is also checked against the original ceiling and readiness.',
            'All frontier paths are audited; only endpoint and highest qualifying-count certificates are retained, others have exact hashes for reproduction.',
            'Conditional age/cadence feasibility is not a live latency gate evaluation or proof of an executable selector.'])


def check_unbounded_reference(result, reference):
    if (reference.get('status') != 'pass' or reference.get('analysis_kind') != 'finite_positive_phase_exact_count_grid'
            or len(result['alternatives']) != len(reference['alternatives'])):
        raise ValueError('invalid unbounded reference')
    for new, old in zip(result['alternatives'], reference['alternatives']):
        if any(new[k] != old[k] for k in ('sampling_shift_ns', 'hypothetical_publication_advance_ns', 'warm_start')):
            raise ValueError('unbounded scenario differs')
        if len(new['frontier']) != len(old['frontier']):
            raise ValueError('unbounded count population differs')
        for a, b in zip(new['frontier'], old['frontier']):
            for key in ('skips', 'required_count', 'feasible', 'maximum_good', 'certificate_sha256'):
                if a.get(key) != b.get(key):
                    raise ValueError('unbounded exact frontier/certificate changed')


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
    dependencies = [Path(__file__), Path(phase.__file__), Path(cardinality.__file__), Path(cardinality.supply.__file__),
        Path(cardinality.supply.envelope.__file__), Path(cardinality.supply.oracle.__file__), Path(cf.__file__),
        Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__),
        HERE/'cardinality_oracle.h', HERE/'cardinality_probe.cpp']
    hashes = {str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies}
    reference_hash = cf.frozen.sha(args.reference)
    reference = json.loads(args.reference.read_text())
    results = []
    for periods in (None, 0, 1, 2):
        print(json.dumps({'starting_target_periods': periods, 'scenarios': len(phase.grid())}), flush=True)
        result = evaluate(*rows, solver, scenarios=phase.grid(), target_periods=periods)
        result = phase.summarize(result)
        result.update(analysis_kind='monotone_target_ceiling_exact_phase_count_grid',
            target_periods=periods, future_target_limits_removed=periods is None)
        if periods is None:
            check_unbounded_reference(result, reference)
            result['preserved_unbounded_reference_exact'] = True
        results.append(result)
    result = dict(schema=1, status='pass', analysis_kind='original_runtime_target_admission_sensitivity',
        target_bounds=results, reference_sha256=reference_hash, finite_tested_cells=len(phase.grid()) * len(results),
        effective_readiness_is_measured_publication=False, measured_speedup=False, runnable_policy=False,
        latency_gate_evaluated=False, promotion_eligible=False, release_ready=False,
        assumptions=[
            'None removes the ceiling; 0 admits target<=original runtime target; 1 adds the bounded-newest horizon min(recorded period, target minus prior target); 2 adds twice that horizon.',
            'The one-period cap matches only bounded-newest target admission, not its ownership, selection instant or whole selector behavior.',
            'Shifted selection/submission leaves the original runtime target and horizon fixed; runtime feedback and upper admission remain unqualified.',
            'Every finite cell uses unlimited known-pair retention and hypothetical per-eye publication advance, clipped at receipt.',
            'All reported cadence is encoded-frame cadence, not distinct application generation or input-to-photon cadence.',
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
