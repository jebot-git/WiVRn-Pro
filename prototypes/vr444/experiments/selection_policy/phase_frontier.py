#!/usr/bin/env python3
"""Finite positive-phase sensitivity grid using the exact cardinality solver."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phase_cardinality', HERE / 'cardinality.py')
cardinality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cardinality)
cf = cardinality.cf
PHASES = tuple(sorted(set(range(0, 11_000_001, 500_000)) | {5_555_555, 11_111_111}))
ADVANCES = (0, 1_000_000, 2_000_000, 4_000_000, 8_000_000)


def grid(phases=PHASES, advances=ADVANCES):
    for values in (phases, advances):
        if (type(values) not in (tuple, list) or not values or len(set(values)) != len(values)
                or any(type(value) is not int or not 0 <= value <= 15_000_000 for value in values)
                or list(values) != sorted(values)):
            raise ValueError('grid axes require sorted distinct bounded nonnegative integers')
    if len(phases) * len(advances) > 200:
        raise ValueError('phase grid exceeds explicit scenario budget')
    return [(phase, advance) for phase in phases for advance in advances]


def summarize(result, phases=PHASES, advances=ADVANCES):
    expected = grid(phases, advances)
    alternatives = result.get('alternatives')
    if type(alternatives) is not list or len(alternatives) != len(expected):
        raise ValueError('phase grid omitted scenarios')
    rows, best_per_advance = [], []
    for alternative, (phase, advance) in zip(alternatives, expected):
        if (type(alternative.get('sampling_shift_ns')) is not int or type(alternative.get('hypothetical_publication_advance_ns')) is not int
                or alternative.get('sampling_shift_ns') != phase or alternative.get('hypothetical_publication_advance_ns') != advance
                or alternative.get('warm_start', {}).get('model') != 'observed_submission_prefix'):
            raise ValueError('phase grid scenario identity differs')
        frontier = alternative['frontier']
        if len(frontier) != result['maximum_skips'] + 1:
            raise ValueError('phase grid omitted count budgets')
        feasible = [entry for entry in frontier if entry['feasible']]
        if not feasible:
            rows.append(dict(phase_ns=phase, publication_advance_ns=advance, feasible=False))
            continue
        best = max(feasible, key=lambda entry: (entry['maximum_good'] - entry['required_good_count_for_p95'], entry['required_count']))
        eligible = [entry for entry in feasible if entry['p95_under_threshold_attainable_in_model']
            and entry['conditional_unique_frame_cadence_hz'] is not None
            and entry['conditional_unique_frame_cadence_hz'] >= result['cadence_floor_reference_hz']]
        highest = max(eligible, key=lambda entry: entry['required_count']) if eligible else None
        declared = alternative['highest_count_meeting_conditional_age_and_cadence']
        if declared != (highest['required_count'] if highest else None):
            raise ValueError('phase grid conditional solution declaration differs')
        rows.append(dict(phase_ns=phase, publication_advance_ns=advance, feasible=True,
            best_good_margin=best['maximum_good'] - best['required_good_count_for_p95'],
            best_margin_count=best['required_count'], best_margin_good=best['maximum_good'],
            minimum_count_good=frontier[-1]['maximum_good'], full_count_good=frontier[0]['maximum_good'],
            highest_conditionally_qualifying_count=highest['required_count'] if highest else None,
            conditional_solution_certificate_sha256=highest['certificate_sha256'] if highest else None))
    for advance in advances:
        available = [row for row in rows if row['publication_advance_ns'] == advance and row['feasible']]
        best_per_advance.append(max(available, key=lambda row: (row['highest_conditionally_qualifying_count'] or 0,
            row['best_good_margin'], -row['phase_ns'])) if available else dict(publication_advance_ns=advance, feasible=False))
    keep = {(row['phase_ns'], row['publication_advance_ns']) for row in best_per_advance if row['feasible']}
    for alternative in alternatives:
        selected = (alternative['sampling_shift_ns'], alternative['hypothetical_publication_advance_ns']) in keep
        for entry in alternative['frontier']:
            if not selected and 'certificate' in entry:
                del entry['certificate']
                entry['certificate_retained'] = False
    result.update(analysis_kind='finite_positive_phase_exact_count_grid', phase_grid_ns=list(phases),
        publication_advance_grid_ns=list(advances), scenario_count=len(expected), grid_summary=rows,
        best_tested_phase_per_advance=best_per_advance,
        conditionally_feasible_grid_cells=sum(row.get('highest_conditionally_qualifying_count') is not None for row in rows),
        continuous_phase_optimum_proven=False, scenarios_exhaustive=False,
        future_target_limits_removed=True, observed_warm_prefix_preserved=True)
    result['assumptions'] += [
        'Finite declared positive-phase grid only; no continuous-phase optimum or untested-scenario impossibility is claimed.',
        'Every cell solves every count from the sufficient full-window cadence count through all observed deadlines.',
        'All cells preserve the actual pre-warm submission prefix; legacy startup reuse is excluded.',
        'Only best-tested-phase cells per publication advance retain endpoint/qualifying certificates; all audited paths retain hashes.',
        'Positive phase shifts preserve recorded lower admission bounds but do not prove runtime feedback, a new upper deadline, or late-submit acceptance.']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
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
    dependencies = [Path(__file__), Path(cardinality.__file__), Path(cardinality.supply.__file__),
        Path(cardinality.supply.envelope.__file__), Path(cardinality.supply.oracle.__file__), Path(cf.__file__),
        Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__),
        HERE/'cardinality_oracle.h', HERE/'cardinality_probe.cpp']
    hashes = {str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies}
    print(json.dumps({'starting_scenarios': len(grid()), 'warm_model': 'observed_submission_prefix'}), flush=True)
    result = cardinality.evaluate(*rows, solver, scenarios=grid(), warm_models=('observed_submission_prefix',))
    result = summarize(result)
    # Predicted display is descriptive and not an independently proven latest
    # admission deadline. Keep this timestamp comparison explicit nonetheless.
    cutoff = rows[1][0]['submit_xr_ns'] + 2_000_000_000
    warm = [(trace, present) for trace, present in zip(rows[0], rows[1]) if present['submit_xr_ns'] >= cutoff]
    result['shifted_submit_at_or_after_original_predicted_display'] = [dict(phase_ns=phase,
        count=sum(present['submit_xr_ns'] + phase >= trace['predicted_display_xr_ns'] for trace, present in warm)) for phase in PHASES]
    if (any(cf.frozen.sha(run / name) != manifest[name] for name in names)
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
    print(json.dumps(result['best_tested_phase_per_advance']))


if __name__ == '__main__':
    main()
