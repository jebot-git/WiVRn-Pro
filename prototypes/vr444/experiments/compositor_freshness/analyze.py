#!/usr/bin/env python3
"""Validate test-only host source/latch traces; never infer optical latency."""
import argparse
import collections
import json
from pathlib import Path

EVENTS = {'app_predict', 'app_point', 'app_delivered', 'app_gpu_done',
          'app_discarded', 'app_latched', 'app_retired',
          'compositor_frame', 'projection_layer'}
INTS = {'sequence', 'pacer_id', 'app_frame_id', 'system_frame_id', 'when_ns',
        'observed_ns', 'display_time_ns', 'display_period_ns', 'wake_up_ns',
        'layer_target_ns', 'layer_count', 'projection_count', 'layer_index',
        'timing_point'}
BOOLS = {'direct_projection', 'projection_pose_reused'}
KEYS = INTS | BOOLS | {'type', 'event'}


def _distribution(values):
    values = sorted(values)
    if not values:
        return None
    def quantile(q):
        p = (len(values) - 1) * q
        a = int(p)
        b = min(a + 1, len(values) - 1)
        return (values[a] + (values[b] - values[a]) * (p - a)) / 1e6
    return {'count': len(values), 'p50': quantile(.50), 'p95': quantile(.95),
            'p99': quantile(.99), 'min': values[0] / 1e6, 'max': values[-1] / 1e6}


def evaluate(rows, selected_frames=None):
    """Pure evaluator. rows is parsed JSONL; optional selected_frames are wire IDs.

    A passing trace proves callback and identity consistency, not improved
    latency, fresh simulation content, visual equivalence, or display timing.
    """
    errors = []
    def check(condition, message):
        if not condition and len(errors) < 100:
            errors.append(message)
    result = {'status': 'fail', 'diagnostic_only': True, 'promotion_blocked': True,
              'errors': errors, 'limitations': [
                  'Application frame identity is not a simulation or input epoch.',
                  'Host monotonic callbacks are not photon or client clock times.',
                  'Accepted compositor input is not proof of encoded delivery.',
                  'Projection target matching does not establish layer ownership when targets coincide.',
                  'Trace adds atomic and clock-read overhead; do not use for promotion.']}
    if not isinstance(rows, list) or len(rows) < 3 or any(not isinstance(r, dict) for r in rows):
        errors.append('Expected header, nonempty event records, and footer objects.')
        return result
    header, footer = rows[0], rows[-1]
    check(set(header) == {'type', 'schema', 'clock', 'capacity', 'diagnostic_only'}, 'Header keys invalid.')
    check(header.get('type') == 'header' and type(header.get('schema')) is int and header['schema'] == 1,
          'Header schema invalid.')
    check(header.get('clock') == 'host_monotonic_ns' and header.get('diagnostic_only') is True,
          'Clock/domain or diagnostic flag invalid.')
    capacity = header.get('capacity')
    check(type(capacity) is int and 0 < capacity <= 131072, 'Capacity invalid.')
    check(set(footer) == {'type', 'records', 'dropped', 'complete'}, 'Footer keys invalid.')
    check(footer.get('type') == 'footer' and footer.get('complete') is True, 'Complete footer missing.')
    check(type(footer.get('records')) is int and footer['records'] == len(rows) - 2, 'Footer record count mismatch.')
    check(type(footer.get('dropped')) is int and footer['dropped'] == 0, 'Dropped records invalidate capture.')
    if errors:
        return result
    check(footer['records'] <= capacity, 'Record count exceeds capacity.')
    if errors:
        return result
    records = rows[1:-1]
    for i, r in enumerate(records, 1):
        check(set(r) == KEYS, f'Event {i}: keys invalid.')
        check(r.get('type') == 'event' and type(r.get('event')) is str and r['event'] in EVENTS, f'Event {i}: type/name invalid.')
        for key in INTS:
            check(type(r.get(key)) is int and -(1 << 63) <= r[key] < (1 << 63), f'Event {i}: {key} is not signed int64.')
        for key in BOOLS:
            check(type(r.get(key)) is bool, f'Event {i}: {key} is not bool.')
        if errors:
            return result
        check(r['sequence'] == i, f'Event {i}: sequence mismatch.')
        check(0 < r['when_ns'] <= r['observed_ns'], f'Event {i}: callback time order invalid.')
        for key in ('display_time_ns', 'display_period_ns', 'wake_up_ns', 'layer_target_ns'):
            check(r[key] >= 0, f'Event {i}: negative {key}.')
        for key in ('layer_count', 'projection_count', 'layer_index'):
            check(0 <= r[key] <= 64, f'Event {i}: {key} out of bounds.')
        check(0 <= r['timing_point'] <= 3, f'Event {i}: timing point out of bounds.')
        allowed = {'type', 'event', 'sequence', 'pacer_id', 'app_frame_id', 'system_frame_id', 'when_ns', 'observed_ns'}
        allowed |= {
            'app_predict': {'display_time_ns', 'display_period_ns', 'wake_up_ns'},
            'app_point': {'timing_point'},
            'app_delivered': {'display_time_ns'},
            'app_gpu_done': set(), 'app_discarded': set(),
            'app_latched': set(), 'app_retired': set(),
            'compositor_frame': {'display_time_ns', 'display_period_ns', 'layer_target_ns',
                                 'layer_count', 'projection_count', 'direct_projection',
                                 'projection_pose_reused'},
            'projection_layer': {'layer_target_ns', 'layer_index'},
        }[r['event']]
        for key in (INTS | BOOLS) - allowed:
            check(r[key] == 0, f'Event {i}: unused {key} must be zero/false.')
        if r['event'].startswith('app_'):
            check(r['pacer_id'] > 0 and r['app_frame_id'] > 0, f'Event {i}: app identity invalid.')
            check(r['system_frame_id'] >= 0 if r['event'] == 'app_latched' else r['system_frame_id'] == -1,
                  f'Event {i}: system identity invalid for app callback.')
        else:
            check(r['pacer_id'] == 0 and r['app_frame_id'] == -1 and r['system_frame_id'] >= 0,
                  f'Event {i}: compositor identity invalid.')
    if errors:
        return result

    apps = collections.defaultdict(lambda: collections.defaultdict(list))
    frames, projections, latches = {}, collections.defaultdict(list), collections.defaultdict(list)
    counts = collections.Counter(r['event'] for r in records)
    for r in records:
        kind = r['event']
        if kind.startswith('app_'):
            apps[(r['pacer_id'], r['app_frame_id'])][kind].append(r)
            if kind == 'app_latched':
                latches[r['system_frame_id']].append(r)
        elif kind == 'compositor_frame':
            check(r['system_frame_id'] not in frames, 'Duplicate compositor frame identity.')
            frames[r['system_frame_id']] = r
        else:
            projections[r['system_frame_id']].append(r)
    check(bool(frames) and counts['app_latched'] > 0, 'No compositor/latch evidence.')
    for key, events in apps.items():
        predicts = events['app_predict']
        check(len(predicts) == 1, f'App {key}: exactly one prediction required.')
        if len(predicts) != 1:
            continue
        predict = predicts[0]
        check(predict['display_time_ns'] > 0 and predict['display_period_ns'] > 0 and predict['wake_up_ns'] > 0,
              f'App {key}: predicted timing missing.')
        for kind in ('app_delivered', 'app_gpu_done', 'app_discarded', 'app_retired'):
            check(len(events[kind]) <= 1, f'App {key}: duplicate {kind}.')
        check(not (events['app_delivered'] and events['app_discarded']), f'App {key}: delivered and discarded.')
        delivered = events['app_delivered'][0] if events['app_delivered'] else None
        gpu = events['app_gpu_done'][0] if events['app_gpu_done'] else None
        if delivered:
            check(delivered['display_time_ns'] > 0 and delivered['when_ns'] >= predict['when_ns'],
                  f'App {key}: delivered timing invalid.')
        if gpu:
            check(delivered is not None and gpu['when_ns'] >= delivered['when_ns'], f'App {key}: GPU done before delivery.')
        systems = set()
        for latch in events['app_latched']:
            check(delivered is not None and gpu is not None, f'App {key}: latch lacks delivery/GPU completion.')
            if gpu:
                check(latch['when_ns'] >= gpu['when_ns'], f'App {key}: latched before GPU completion.')
            check(latch['system_frame_id'] not in systems, f'App {key}: duplicate latch for system frame.')
            systems.add(latch['system_frame_id'])
    projection_matches = 0
    ambiguous_projection_matches = 0
    for system, layer_rows in projections.items():
        check(system in frames, f'Projection {system}: lacks accepted compositor frame.')
    for system, frame in frames.items():
        check(frame['display_time_ns'] > 0 and frame['display_period_ns'] > 0, f'Frame {system}: target/period missing.')
        layer_rows = projections[system]
        check(len(layer_rows) == frame['projection_count'] <= frame['layer_count'], f'Frame {system}: projection count mismatch.')
        indexes = [r['layer_index'] for r in layer_rows]
        check(indexes == sorted(set(indexes)) and all(i < frame['layer_count'] for i in indexes), f'Frame {system}: layer indexes invalid.')
        direct = frame['layer_count'] == 1 and frame['projection_count'] == 1
        check(frame['direct_projection'] == direct, f'Frame {system}: direct projection flag invalid.')
        first_target = layer_rows[0]['layer_target_ns'] if layer_rows else 0
        check(frame['layer_target_ns'] == first_target, f'Frame {system}: first target differs.')
        reused = bool(layer_rows) and (direct or abs(frame['display_time_ns'] - first_target) <= 3 * frame['display_period_ns'])
        check(frame['projection_pose_reused'] == reused, f'Frame {system}: projection pose flag invalid.')
        if frame['layer_count']:
            check(bool(latches[system]), f'Frame {system}: lacks app latch.')
        for latch in latches[system]:
            check(latch['when_ns'] <= frame['when_ns'], f'Frame {system}: callback latch after compositor input.')
        for layer in layer_rows:
            check(layer['when_ns'] == frame['when_ns'] and layer['observed_ns'] == frame['observed_ns'],
                  f'Frame {system}: projection snapshot time differs.')
            matching = []
            for latch in latches[system]:
                app = apps[(latch['pacer_id'], latch['app_frame_id'])]
                if app['app_delivered'] and app['app_delivered'][0]['display_time_ns'] == layer['layer_target_ns']:
                    matching.append(latch)
            check(bool(matching), f'Frame {system}: projection target has no latched delivered app match.')
            projection_matches += bool(matching)
            ambiguous_projection_matches += len(matching) > 1
    selected = sorted(frames)
    if selected_frames is not None:
        if not isinstance(selected_frames, (list, tuple, set)) or any(type(x) is not int or x < 0 for x in selected_frames):
            errors.append('selected_frames must contain nonnegative integer wire frame IDs.')
        else:
            selected = sorted(set(selected_frames))
            check(bool(selected), 'No selected wire frame IDs.')
            for system in selected:
                check(system in frames, f'Selected frame {system}: absent from host compositor evidence.')
    if errors:
        return result
    generations = []
    delivery_age = []
    for system in selected:
        ids = tuple(sorted((x['pacer_id'], x['app_frame_id']) for x in latches[system]))
        generations.append(ids)
        for key in ids:
            delivery_age.append(frames[system]['when_ns'] - apps[key]['app_delivered'][0]['when_ns'])
    result.update(status='pass', records=len(records), event_counts=dict(counts),
                  app_frames=len(apps), compositor_frames=len(frames),
                  direct_projection_frames=sum(r['direct_projection'] for r in frames.values()),
                  projection_target_matches=projection_matches,
                  ambiguous_projection_matches=ambiguous_projection_matches,
                  analyzed_frames=len(selected), unique_latched_app_sets=len(set(generations)),
                  adjacent_reused_app_sets=sum(a == b for a, b in zip(generations, generations[1:])),
                  app_delivery_to_compositor_input_ms=_distribution(delivery_age),
                  callback_observation_delay_ms=_distribution([r['observed_ns'] - r['when_ns'] for r in records]),
                  system_to_app_frames=[{'system_frame_id': system,
                                         'app_frames': [{'pacer_id': p, 'app_frame_id': a} for p, a in ids]}
                                        for system, ids in zip(selected, generations)])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        result = evaluate([json.loads(line) for line in args.trace.read_text().splitlines()])
    except (OSError, ValueError) as exc:
        result = {'status': 'fail', 'errors': [str(exc)]}
    text = json.dumps(result, indent=2) + '\n'
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end='')
    return result['status'] != 'pass'


if __name__ == '__main__':
    raise SystemExit(main())
