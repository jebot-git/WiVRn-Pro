#!/usr/bin/env python3
"""Exact small-skip frontier with explicit warm-start and timestamp assumptions."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cardinality_supply', HERE / 'supply_phase.py')
supply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supply)
cf = supply.cf
WARM_MODELS = ('relaxed_known_universe', 'observed_submission_prefix')


def warm_population(known, traces, presented, cutoff, model):
    if model not in WARM_MODELS:
        raise ValueError('unknown warm-start model')
    before = [(t, p) for t, p in zip(traces, presented) if p['submit_xr_ns'] < cutoff]
    seen = {p['left'] for _, p in before}
    last = before[-1][0]['selected'][0] if before else None
    frames = sorted(known.values(), key=lambda f: f['frame'])
    if model == 'observed_submission_prefix':
        frames = [f for f in frames if f['frame'] not in seen and (last is None or
            f['frame'] > last['frame'] and f['target'] >= last['view_display_time_xr_ns'])]
    if any(a['frame'] >= b['frame'] or a['target'] > b['target'] for a, b in zip(frames, frames[1:])):
        raise ValueError('cardinality solver requires strict frame order and globally nondecreasing source targets')
    return frames, dict(model=model, actual_pre_warm_distinct_submissions=len(seen),
        last_actual_pre_warm_frame=last['frame'] if last else None,
        last_actual_pre_warm_target=last['view_display_time_xr_ns'] if last else None,
        admitted_known_frames=len(frames), pre_warm_first_identity_preserved=model == 'observed_submission_prefix')


def audit_frontier(result, frames, deadlines, maximum_skips, threshold_ns, original_targets):
    if (not isinstance(result, dict) or type(result.get('schema')) is not int or result['schema'] != 1
            or not isinstance(result.get('frontier'), list)):
        raise ValueError('malformed solver output')
    if len(result['frontier']) != maximum_skips + 1:
        raise ValueError('solver omitted skip budgets')
    frame_map, deadline_map = {f['frame']: f for f in frames}, {d['sequence']: d for d in deadlines}
    for budget, entry in enumerate(result['frontier']):
        if (not isinstance(entry, dict) or type(entry.get('skips')) is not int or entry['skips'] != budget
                or type(entry.get('required_count')) is not int or entry['required_count'] != len(deadlines) - budget
                or type(entry.get('feasible')) is not bool or type(entry.get('maximum_good')) is not int
                or not isinstance(entry.get('certificate'), list)):
            raise ValueError('solver cardinality contract differs')
        path = entry['certificate']
        if not entry['feasible']:
            if path or entry['maximum_good'] != -1:
                raise ValueError('infeasible solver state has a path or score')
            continue
        if len(path) != entry['required_count']:
            raise ValueError('certificate count differs')
        previous = None
        ages, submits, target_leads = [], [], []
        for choice in path:
            if (type(choice) is not list or len(choice) != 2 or any(type(v) is not int for v in choice)
                    or choice[0] not in deadline_map or choice[1] not in frame_map):
                raise ValueError('certificate invented deadline/frame')
            d, f = deadline_map[choice[0]], frame_map[choice[1]]
            if f['ready'] > d['selection'] or f['receipt'] > f['ready']:
                raise ValueError('certificate uses unavailable frame')
            if previous is not None and (previous[0] >= choice[0] or previous[1] >= choice[1]
                    or frame_map[previous[1]]['target'] > f['target']):
                raise ValueError('certificate regresses identity, deadline or target')
            previous = choice
            ages.append(d['submit'] - f['receipt'])
            submits.append(d['submit'])
            target_leads.append((f['target'] - original_targets[d['sequence']]) / 1e6)
        good = sum(a < threshold_ns for a in ages)
        if any(a < 0 for a in ages) or good != entry['maximum_good']:
            raise ValueError('certificate score differs')
        required = (len(path) * 95 + 99) // 100
        entry.update(required_good_count_for_p95=required, p95_under_threshold_attainable_in_model=good >= required,
            conditional_certificate_receive_age_ms=cf.distribution([a / 1e6 for a in ages]),
            conditional_unique_frame_cadence_hz=cf.cadence(submits),
            source_target_minus_original_runtime_target_ms=cf.distribution(target_leads),
            certificate_sha256=hashlib.sha256(json.dumps(path, separators=(',', ':')).encode()).hexdigest())
    return result


def evaluate(traces, presented, source_rows, solver, scenarios=((0, 0), (0, 8_000_000), (5_555_555, 0), (5_555_555, 2_000_000), (5_555_555, 4_000_000)),
             warm_models=WARM_MODELS, warmup_ns=2_000_000_000, threshold_ns=15_000_000, cadence_floor_hz=88):
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
                lines = [f'{len(frames)} {len(deadlines)} {maximum_skips} {threshold_ns}']
                lines += [' '.join(str(f[k]) for k in ('frame', 'target', 'receipt', 'ready')) for f in frames]
                lines += [' '.join(str(d[k]) for k in ('sequence', 'selection', 'submit')) for d in deadlines]
                input_path.write_text('\n'.join(lines) + '\n')
                command = [str(solver), str(input_path)]
                run = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if run.returncode:
                    raise ValueError('cardinality solver failed: ' + run.stderr)
                result = audit_frontier(json.loads(run.stdout), frames, deadlines, maximum_skips, threshold_ns, targets)
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
                result.update(warm_start=warm_contract, sampling_shift_ns=shift, hypothetical_publication_advance_ns=advance,
                    input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
                    highest_count_meeting_conditional_age_and_cadence=best['required_count'] if best else None)
                alternatives.append(result)
    return dict(schema=1, status='pass', analysis_kind='known_supply_exact_small_skip_frontier',
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
            'All future-target limits are removed; globally nondecreasing source targets are a checked solver precondition.',
            'All frontier paths are audited; only endpoint and highest qualifying-count certificates are retained, others have exact hashes for reproduction.',
            'Conditional age/cadence feasibility is not a live latency gate evaluation or proof of an executable selector.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, out, solver = args.run.resolve(), args.out.resolve(), args.solver.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    build_hash = cf.frozen.sha(args.build_receipt)
    build = json.loads(args.build_receipt.read_text())
    if build.get('status') != 'pass' or cf.frozen.sha(solver) != build.get('solver_sha256'):
        raise ValueError('solver is not the validated build')
    for name in ('cardinality_oracle.h', 'cardinality_probe.cpp'):
        if cf.frozen.sha(HERE / name) != build['sources_sha256'].get(str((HERE / name).relative_to(cf.ROOT))):
            raise ValueError('solver source differs from build receipt')
    sealed = cf.frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('sealed diagnostic reanalysis failed')
    manifest_path = run / 'hashes.json'
    manifest = json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']:
        raise ValueError('manifest changed after verification')
    names = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    if not set(names).issubset(manifest):
        raise ValueError('input not manifest-bound')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    dependencies = [Path(__file__), HERE / 'cardinality_oracle.h', HERE / 'cardinality_probe.cpp', Path(supply.__file__),
                    Path(supply.envelope.__file__), Path(supply.oracle.__file__), Path(cf.__file__), Path(cf.frozen.__file__),
                    Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    source_hashes = {str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies}
    result = evaluate(*rows, solver)
    if (any(cf.frozen.sha(run / name) != manifest[name] for name in names)
            or cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256'] or cf.frozen.sha(solver) != build['solver_sha256']
            or cf.frozen.sha(args.build_receipt) != build_hash
            or any(cf.frozen.sha(cf.ROOT / name) != digest for name, digest in source_hashes.items())):
        raise ValueError('evidence, manifest or solver changed during analysis')
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'], original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False, solver_sha256=build['solver_sha256'], build_receipt_sha256=build_hash,
        sources_sha256=source_hashes)
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps([dict(warm_model=a['warm_start']['model'], shift_ns=a['sampling_shift_ns'], advance_ns=a['hypothetical_publication_advance_ns'],
        full_good=a['frontier'][0]['maximum_good'], budget_good=a['frontier'][-1]['maximum_good'],
        highest_conditional_count=a['highest_count_meeting_conditional_age_and_cadence']) for a in result['alternatives']]))


if __name__ == '__main__':
    main()
