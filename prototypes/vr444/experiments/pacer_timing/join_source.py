#!/usr/bin/env python3
"""Bind host pacer decisions to accepted compositor frames, without clock mixing."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pacer = load('joined_pacer_trace', HERE / 'analyze.py')
source = load('joined_pacer_source', HERE.parent / 'compositor_freshness/analyze.py')


def evaluate(pacer_rows, source_rows, selected_frames=None):
    predictions = pacer.evaluate(pacer_rows)
    accepted = source.evaluate(source_rows, selected_frames)
    if predictions['status'] != 'pass' or accepted['status'] != 'pass':
        return dict(status='fail', diagnostic_only=True, promotion_blocked=True,
                    errors=['Pacer/source input contract failed.'], pacer_validation=predictions,
                    source_validation=accepted)
    if accepted.get('ambiguous_projection_matches'):
        return dict(status='fail', diagnostic_only=True, promotion_blocked=True,
                    errors=['Source projection ownership is ambiguous.'])
    if any(not row['app_frames'] or len({a['pacer_id'] for a in row['app_frames']}) != len(row['app_frames'])
           for row in accepted['system_to_app_frames']):
        return dict(status='fail', diagnostic_only=True, promotion_blocked=True,
                    errors=['Selected source frames lack unambiguous latched app generations.'])
    records = pacer_rows[1:-1]
    mapping = {}
    for r in records:
        mapping.setdefault((r['frame_id'], r['display_ns']), []).append(r)
    frames = [r for r in source_rows[1:-1] if r['event'] == 'compositor_frame']
    errors, joins = [], []
    values = {key: [] for key in ('wake_to_present_ns', 'safe_decode_ns', 'render_to_display_ns',
                                 'prediction_to_accepted_input_ns', 'accepted_input_minus_desired_present_ns',
                                 'display_target_minus_accepted_input_ns')}
    selected = set(selected_frames) if selected_frames is not None else {r['system_frame_id'] for r in frames}
    for frame in frames:
        key = frame['system_frame_id'], frame['display_time_ns']
        matches = mapping.get(key, [])
        if len(matches) != 1:
            errors.append(f'Accepted frame {key[0]} has {len(matches)} exact predictor matches.')
            continue
        prediction = matches[0]
        if prediction['now_ns'] > frame['when_ns']:
            errors.append(f'Accepted frame {key[0]} predates its prediction.')
            continue
        # get_frame_duration() can change between predict and commit; the
        # accepted source period is not falsely treated as the predictor input.
        joins.append(dict(system_frame_id=key[0], pacer_id=prediction['pacer_id'],
                          prediction_sequence=prediction['sequence'], source_sequence=frame['sequence']))
        if key[0] not in selected:
            continue
        for name in ('wake_to_present_ns', 'safe_decode_ns', 'render_to_display_ns'):
            values[name].append(prediction[name])
        values['prediction_to_accepted_input_ns'].append(frame['when_ns'] - prediction['now_ns'])
        values['accepted_input_minus_desired_present_ns'].append(frame['when_ns'] - prediction['desired_present_ns'])
        values['display_target_minus_accepted_input_ns'].append(prediction['display_ns'] - frame['when_ns'])
    return dict(schema=1, status='fail' if errors else 'pass', diagnostic_only=True, promotion_blocked=True,
                errors=errors, pacer_validation=predictions, source_validation=accepted,
                accepted_source_frames=len(frames), exactly_joined_frames=len(joins), selected_source_frames=len(selected),
                selected_population='All distinct selected wire IDs across acquisition; startup is included.',
                joins=joins, selected_host_budget_and_phase_ms={k: pacer.distribution(v) if v else None for k, v in values.items()},
                measured_speedup=False, latency_gate_evaluated=False, scope=[
                    'Every accepted source frame requires exactly one matching pacer frame ID and returned host display target.',
                    'Each selected wire frame is source-validated before aggregation; ambiguous projection ownership is rejected.',
                    'Host timestamps are subtracted only from host timestamps; selected client IDs carry no client clock subtraction.',
                    'Unaccepted predictions are allowed; accepted input is not proof of encoded delivery or optical display.',
                    'Prediction-to-input and budget values are observed diagnostic estimates, not removable causal latency.'])
