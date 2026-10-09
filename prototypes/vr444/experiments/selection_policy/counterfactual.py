#!/usr/bin/env python3
"""Frozen-history selector hypotheses joined to actual application-frame IDs.

No scheduling, ownership, decode or feedback effects are simulated. Outputs are
conditional sample ages at the original submit times, never measured latency.
"""
import argparse
import collections
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trace = load('horizon_trace', ROOT / 'validation/wivrn-codecs/integration/candidate_trace.py')
replay = load('horizon_replay', ROOT / 'validation/wivrn-codecs/integration/selection_replay.py')
source = load('horizon_source', HERE.parent / 'compositor_freshness/analyze.py')
frozen = load('horizon_frozen', HERE / 'reanalyze_trace.py')


def choose(record, numerator, denominator, previous=None):
    """Independent metadata-only version of opt-in horizon_policy.h."""
    if (type(numerator) is not int or type(denominator) is not int
            or not 0 <= numerator <= denominator <= 1024 or denominator == 0):
        raise ValueError('horizon must be an integer ratio in [0,1] with denominator 1..1024')
    target, period, prior = (record[k] for k in ('predicted_display_xr_ns', 'predicted_display_period_xr_ns',
                                               'previous_predicted_display_xr_ns'))
    if (any(type(v) is not int or not 0 <= v < 2**63 for v in (target, period, prior))
            or target == 0 or prior >= target):
        raise ValueError('invalid runtime target/period/prior')
    full = min(period, target-prior) if period and prior else 0
    bound = full*numerator//denominator
    history = record['history']
    previous = record['previous'] if previous is None else previous
    closest = record['closest']
    alpha = bool(history[0]['present'] and history[0]['alpha'])
    empty = [dict(decoder=i, present=False) for i in range(3)]
    eligible = [False]*3
    selected, chosen_slots = None, [-1]*3

    def forward(candidate, reference):
        return (not reference['present'] or
                candidate['frame'] >= reference['frame'] and
                candidate['view_display_time_xr_ns'] >= reference['view_display_time_xr_ns'] and
                (candidate['frame'] != reference['frame'] or replay.same(candidate, reference, True)))

    if closest[0]['present']:
        for slot, left in enumerate(history[:3]):
            if not left['present'] or left['alpha'] != alpha or left['view_display_time_xr_ns'] <= 0:
                continue
            slots = [slot, -1, -1]
            for eye in range(1, 2+int(alpha)):
                slots[eye] = next((i for i, f in enumerate(history[eye*3:eye*3+3]) if replay.same(left, f, True)), -1)
            if any(slots[eye] < 0 for eye in range(2+int(alpha))):
                continue
            if not forward(left, closest[0]) or (replay.coherent(previous) and not forward(left, previous[0])):
                continue
            if left['view_display_time_xr_ns'] > target+bound or (bound == 0 and not replay.same(left, closest[0], True)):
                continue
            eligible[slot] = True
            if selected is None or left['frame'] > selected[0]['frame']:
                chosen_slots = slots
                selected = [history[eye*3+i] if i >= 0 else empty[eye] for eye, i in enumerate(slots)]
    fallback = selected is None
    if fallback:
        selected = previous if replay.coherent(previous) else empty
    return dict(selected=selected, chosen_slots=chosen_slots, eligible_left_slots=eligible,
                max_ahead_xr_ns=bound, fallback=fallback)


def distribution(values):
    values = sorted(values)
    return (dict(count=len(values), **{f'p{p}': values[math.ceil(len(values)*p/100)-1] for p in (50, 95, 99)},
                 min=values[0], max=values[-1]) if values else None)


def cadence(times):
    return ((len(times)-1)*1e9/(times[-1]-times[0]) if len(times) > 1 and times[-1] > times[0] else None)


def evaluate(traces, presented, source_rows, fractions=((0, 1), (1, 2), (3, 4), (31, 32), (1, 1)), warmup_ns=2_000_000_000):
    if type(warmup_ns) is not int or warmup_ns < 0:
        raise ValueError('invalid warmup')
    verified = trace.evaluate(traces, presented)
    if verified['status'] != 'pass' or len(traces) != len(presented):
        raise ValueError('complete observed trace required: ' + '; '.join(verified['errors']))
    if not isinstance(fractions, (list, tuple)) or not fractions:
        raise ValueError('at least one declared horizon is required')
    if any(not isinstance(f, (list, tuple)) or len(f) != 2 for f in fractions):
        raise ValueError('each horizon needs a numerator and denominator')
    fractions = tuple(tuple(f) for f in fractions)
    if any(any(type(v) is not int for v in f) for f in fractions) or len(set(fractions)) != len(fractions):
        raise ValueError('horizon declarations must be distinct integer ratios')
    predictions = {}
    needed = {p['left'] for p in presented}
    for fraction in fractions:
        for model in ('local_actual_previous', 'recurrent_hypothetical_previous'):
            previous = None
            decisions = []
            for row in traces:
                result = choose(row, *fraction, previous=previous if model.startswith('recurrent') else None)
                decisions.append(result)
                if result['selected'][0]['present']:
                    needed.add(result['selected'][0]['frame'])
                previous = result['selected']
            predictions[(fraction, model)] = decisions
    joined = source.evaluate(source_rows, needed)
    if joined['status'] != 'pass' or joined.get('ambiguous_projection_matches'):
        raise ValueError('unambiguous source-frame joins required: ' + '; '.join(joined['errors']))
    generations = {r['system_frame_id']: tuple((a['pacer_id'], a['app_frame_id']) for a in r['app_frames'])
                   for r in joined['system_to_app_frames']}
    if any(not ids or len(dict(ids)) != len(ids) for ids in generations.values()):
        raise ValueError('source joins need one unambiguous latched app generation per participating pacer')
    window = presented[0]['submit_xr_ns'] + warmup_ns
    if not any(p['submit_xr_ns'] >= window for p in presented):
        raise ValueError('no submissions remain after warmup')
    results = []
    for (fraction, model), decisions in predictions.items():
        counts = collections.Counter()
        values = collections.defaultdict(list)
        seen = set()
        last_choice = None
        first_times = []
        for row, p, decision in zip(traces, presented, decisions):
            pair = decision['selected']
            if not pair[0]['present']:
                counts['no_coherent_hypothetical_pair'] += 1
                continue
            frame = pair[0]['frame']
            first = frame not in seen
            seen.add(frame)
            regressed = last_choice is not None and frame < last_choice
            last_choice = frame
            if p['submit_xr_ns'] < window:
                continue
            counts['warm_observed_submissions'] += 1
            counts['retained_fallback'] += decision['fallback']
            counts['different_encoded_frame'] += frame != p['left']
            counts['encoded_frame_regressions'] += regressed
            old, new = generations[p['left']], generations[frame]
            counts['different_latched_app_set'] += old != new
            old_map, new_map = dict(old), dict(new)
            advances = (old_map.keys() == new_map.keys() and all(new_map[k] >= v for k, v in old_map.items())
                        and any(new_map[k] > v for k, v in old_map.items()))
            counts['strictly_advancing_latched_app_set'] += advances
            age = (p['submit_xr_ns'] - min(f['receive_xr_ns'] for f in pair[:2])) / 1e6
            if age < 0 or any(f['ready_xr_ns'] > row['selection_end_xr_ns'] for f in pair if f['present']):
                raise ValueError('hypothesis uses unavailable data at observed selection')
            values['conditional_receive_age_at_original_submit_ms'].append(age)
            values['source_target_minus_runtime_target_ms'].append((pair[0]['view_display_time_xr_ns']-row['predicted_display_xr_ns'])/1e6)
            values['source_target_advance_over_observed_ms'].append((pair[0]['view_display_time_xr_ns']-row['selected'][0]['view_display_time_xr_ns'])/1e6)
            if first:
                counts['warm_first_hypothetical_frame_submissions'] += 1
                values['conditional_first_receive_age_at_original_submit_ms'].append(age)
                first_times.append(p['submit_xr_ns'])
        results.append(dict(horizon_numerator=fraction[0], horizon_denominator=fraction[1], model=model,
                            counts=dict(counts), distributions={k: distribution(v) for k, v in values.items()},
                            conditional_unique_frame_cadence_hz=cadence(first_times)))
    actual_seen, actual_times, actual_ages = set(), [], []
    for p in presented:
        first = p['left'] not in actual_seen
        actual_seen.add(p['left'])
        if first and p['submit_xr_ns'] >= window:
            actual_times.append(p['submit_xr_ns'])
            actual_ages.append((p['submit_xr_ns']-min(p['left_receive_xr_ns'], p['right_receive_xr_ns']))/1e6)
    source_summary = {k: v for k, v in joined.items() if k != 'system_to_app_frames'}
    return dict(schema=1, status='pass', analysis_kind='frozen_history_hypothesis',
                observed_trace_records=len(traces), warmup_ns=warmup_ns,
                observed_diagnostic_control=dict(warm_unique_frames=len(actual_times),
                    unique_frame_cadence_hz=cadence(actual_times), first_receive_age_ms=distribution(actual_ages)),
                observed_trace_validation=verified, source_validation=source_summary, alternatives=results,
                measured_speedup=False, latency_gate_evaluated=False, promotion_eligible=False, release_ready=False,
                assumptions=[
                    'Original histories, receive/ready timestamps and submission times are held fixed.',
                    'Local decisions use the actual prior selection; recurrent decisions retain their hypothetical prior pair.',
                    'Alternate ownership, render workload, skipped iterations, decoder queues, transport and feedback are not simulated.',
                    'Source joins establish latched application IDs, not input/simulation age or displayed visual correctness.',
                    'No cross-device clock subtraction is performed. Conditional receive ages are not acquired latency measurements.',
                    'Original diagnostic instrumentation overhead remains in observations; it is not subtracted.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside the immutable acquisition')
    sealed = frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('observed candidate trace did not pass diagnostic reanalysis')
    paths = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in paths]
    result = evaluate(*rows)
    manifest = json.loads((run / 'hashes.json').read_text())
    for name in paths:
        if frozen.sha(run / name) != manifest.get(name):
            raise ValueError('input changed during analysis: ' + name)
    dependencies = [Path(__file__), HERE / 'horizon_policy.h', HERE / 'reanalyze_trace.py',
                    Path(trace.__file__), Path(replay.__file__), Path(source.__file__)]
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
                  original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
                  original_manifest_files_verified=sealed['original_manifest_files_verified'],
                  original_result_overwritten=False,
                  sources_sha256={str(p.relative_to(ROOT)): frozen.sha(p) for p in dependencies})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'alternatives': len(result['alternatives']), 'measured_speedup': False}))


if __name__ == '__main__':
    main()
