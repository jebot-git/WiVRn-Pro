"""Validate bounded selection diagnostics; observed alternatives are not speedups."""
import math


def _int(value, positive=False):
    return type(value) is int and (1 if positive else 0) <= value < 2**63


def _identity(row):
    return (row['frame'], row['warp']) if row['present'] else None


def _frame(row, selection_end):
    if type(row) is not dict or type(row.get('present')) is not bool:
        raise ValueError('invalid frame presence')
    if not row['present']:
        return
    if any(type(row.get(k)) is not int or not 0 <= row[k] < 2**64 for k in ('frame', 'warp')):
        raise ValueError('invalid frame/warp identity')
    if any(not _int(row.get(k), True) for k in ('view_display_time_xr_ns', 'receive_xr_ns', 'ready_xr_ns')):
        raise ValueError('missing/invalid frame clock')
    if not row['receive_xr_ns'] <= row['ready_xr_ns'] <= selection_end:
        raise ValueError('frame publication is reversed or later than snapshot')
    if type(row.get('alpha')) is not bool:
        raise ValueError('invalid alpha flag')


def _stats(values):
    values = sorted(values)
    if not values:
        return None
    return dict(count=len(values), **{f'p{p}': values[math.ceil(len(values)*p/100)-1] for p in (50, 95, 99)},
                min=values[0], max=values[-1])


def evaluate(traces, presented, enabled=True):
    if type(traces) is not list or type(presented) is not list:
        return dict(status='fail', errors=['trace and presentation inputs must be lists'], promotion_eligible=False)
    errors = set()
    result = dict(status='fail', records=len(traces), promotion_eligible=False,
                  scope='Observed candidate availability and target choice only; no measured alternate-policy speedup or source-content age')
    if not enabled:
        bad = bool(traces) or any(type(p) is not dict or any(k.startswith('candidate_trace_') for k in p if type(k) is str) for p in presented)
        return result | dict(status='fail' if bad else 'off', errors=['trace present while disabled'] if bad else [])
    if not traces or len(traces) > 4096 or not presented:
        errors.add('missing or oversized diagnostic trace/presentation evidence')
    by_selection = {}
    for index, p in enumerate(presented, 1):
        if type(p) is not dict:
            errors.add('invalid presentation row')
            continue
        key = p.get('selection_end_xr_ns')
        if not _int(key, True) or key in by_selection:
            errors.add('invalid or duplicate presentation selection time')
        by_selection[key] = p
        if (type(p.get('candidate_trace_records')) is not int or p['candidate_trace_records'] != min(index, 4096)
                or type(p.get('candidate_trace_dropped')) is not int or p['candidate_trace_dropped'] != max(0, index-4096)
                or p.get('candidate_trace_write_ok') is not True
                or not _int(p.get('candidate_trace_write_ns'))
                or (index > 4096 and p['candidate_trace_write_ns'] != 0)):
            errors.add('trace counter/drop/write failure')
    if len(traces) != min(len(presented), 4096):
        errors.add('trace does not cover the bounded presentation prefix')
    previous = None
    previous_frames = None
    previous_record = None
    observed_metadata = {}
    matched_times = []
    different = newer = fallback = repeats = 0
    values = {k: [] for k in ('wait_frame_ms', 'wait_return_to_selection_ms', 'render_entry_to_selection_ms',
                             'selected_target_minus_runtime_target_ms', 'newest_target_minus_selected_target_ms',
                             'trace_write_ms')}
    for index, r in enumerate(traces, 1):
        try:
            if type(r) is not dict or type(r.get('schema')) is not int or r['schema'] != 1 or type(r.get('sequence')) is not int or r['sequence'] != index:
                raise ValueError('invalid trace schema/sequence')
            fields = ('wait_frame_begin_xr_ns', 'wait_frame_end_xr_ns', 'render_entry_xr_ns',
                      'selection_begin_xr_ns', 'selection_end_xr_ns', 'end_frame_return_xr_ns')
            times = [r.get(k) for k in fields]
            if any(not _int(t, True) for t in times) or times != sorted(times):
                raise ValueError('wait/render/selection/submission clock order')
            if not _int(r.get('predicted_display_xr_ns'), True):
                raise ValueError('missing/invalid predicted target')
            if any(type(r.get(k)) is not bool for k in ('alpha_required', 'repeat_previous', 'projection_submitted')):
                raise ValueError('invalid trace flags')
            if r.get('selection_phase') not in ('early', 'after-fence'):
                raise ValueError('invalid selection phase')
            p = by_selection.get(r['selection_end_xr_ns'])
            if p is None:
                raise ValueError('trace lacks presentation join')
            for k in ('selection_phase', 'render_entry_xr_ns', 'selection_begin_xr_ns', 'selection_end_xr_ns',
                      'predicted_display_xr_ns', 'end_frame_return_xr_ns'):
                if p.get(k) != r[k]:
                    raise ValueError('trace/presentation clock or phase mismatch')
            if not _int(p.get('submit_xr_ns'), True) or r['end_frame_return_xr_ns'] > p['submit_xr_ns']:
                raise ValueError('submission observation precedes end-frame return')
            fb, fe = p.get('render_fence_begin_xr_ns'), p.get('render_fence_end_xr_ns')
            if not _int(fb, True) or not _int(fe, True) or not fb <= fe <= r['end_frame_return_xr_ns']:
                raise ValueError('invalid render fence clocks')
            if ((r['selection_phase'] == 'early' and r['selection_end_xr_ns'] > fb)
                    or (r['selection_phase'] == 'after-fence' and fe > r['selection_begin_xr_ns'])):
                raise ValueError('render fence/selection phase order')
            if previous_record is not None and (
                    r['wait_frame_begin_xr_ns'] < previous_record['end_frame_return_xr_ns']
                    or r['selection_end_xr_ns'] <= previous_record['selection_end_xr_ns']
                    or r['predicted_display_xr_ns'] <= previous_record['predicted_display_xr_ns']):
                raise ValueError('inter-record frame clocks regress or overlap')
            history, selected = r.get('history'), r.get('selected')
            if type(history) is not list or len(history) != 9 or type(selected) is not list or len(selected) != 3:
                raise ValueError('fixed history/selection bounds violated')
            for pos, f in enumerate(history):
                _frame(f, times[-2])
                if type(f.get('decoder')) is not int or type(f.get('slot')) is not int or (f['decoder'], f['slot']) != divmod(pos, 3):
                    raise ValueError('history slot/decoder ordering')
            for eye, f in enumerate(selected):
                _frame(f, times[-2])
                if type(f.get('decoder')) is not int or f['decoder'] != eye:
                    raise ValueError('selected decoder ordering')
            if r['alpha_required'] != bool(history[0]['present'] and history[0]['alpha']):
                raise ValueError('alpha participation disagrees with actual oldest-slot policy')
            metadata_keys = ('present','frame','warp','view_display_time_xr_ns','receive_xr_ns','ready_xr_ns','alpha')
            snapshots = [tuple(f.get(k) for k in metadata_keys) for f in selected]
            for f in history + selected:
                if f['present']:
                    key = f['frame'], f['warp'], f['decoder']
                    value = tuple(f.get(k) for k in metadata_keys)
                    if key in observed_metadata and observed_metadata[key] != value:
                        raise ValueError('immutable frame metadata changed across snapshots')
                    observed_metadata[key] = value
            required = 2 + int(r['alpha_required'])
            if any(not s['present'] for s in selected[:required]) or any(_identity(s) != _identity(selected[0]) for s in selected[:required]):
                raise ValueError('selected stereo/alpha identity mismatch')
            for eye, side in enumerate(('left', 'right')):
                if (p.get(side), p.get(side+'_warp')) != _identity(selected[eye]):
                    raise ValueError('selected identity disagrees with submitted pair')
                for source, suffix in (('receive_xr_ns', '_receive_xr_ns'), ('ready_xr_ns', '_decode_ready_xr_ns')):
                    if selected[eye][source] != p.get(side+suffix):
                        raise ValueError('selected publication disagrees with presentation')
            eligible = [f for f in history[:3] if f['present'] and all(
                any(g['present'] and _identity(g) == _identity(f) for g in history[eye*3:eye*3+3]) for eye in range(1, required))]
            if eligible:
                chosen = min(eligible, key=lambda f: abs(f['view_display_time_xr_ns']-r['predicted_display_xr_ns']))
                if r.get('reason') != 'closest_target' or _identity(selected[0]) != _identity(chosen):
                    raise ValueError('selected pair violates actual closest-target policy')
                for eye in range(required):
                    if not any(all(g.get(k) == selected[eye].get(k) for k in ('frame','warp','view_display_time_xr_ns','receive_xr_ns','ready_xr_ns','alpha'))
                               for g in history[eye*3:eye*3+3] if g['present']):
                        raise ValueError('selected metadata not found in complete history')
                newest = max(eligible, key=lambda f: f['frame'])
                different += _identity(newest) != _identity(chosen)
                newer += newest['frame'] > chosen['frame']
                values['newest_target_minus_selected_target_ms'].append((newest['view_display_time_xr_ns']-chosen['view_display_time_xr_ns'])/1e6)
            else:
                fallback += 1
                if r.get('reason') != 'no_complete_fallback' or previous is None or [_identity(f) for f in selected] != previous or snapshots != previous_frames:
                    raise ValueError('fallback is not a verified retained selection')
            identities = [_identity(f) for f in selected]
            if r['repeat_previous'] != (previous is not None and identities == previous):
                raise ValueError('repeat flag disagrees with prior selection')
            previous = identities
            previous_frames = snapshots
            previous_record = r
            repeats += r['repeat_previous']
            matched_times.append(r['selection_end_xr_ns'])
            values['wait_frame_ms'].append((times[1]-times[0])/1e6)
            values['wait_return_to_selection_ms'].append((times[4]-times[1])/1e6)
            values['render_entry_to_selection_ms'].append((times[4]-times[2])/1e6)
            values['selected_target_minus_runtime_target_ms'].append((selected[0]['view_display_time_xr_ns']-r['predicted_display_xr_ns'])/1e6)
            duration = p.get('candidate_trace_write_ns')
            if not _int(duration):
                raise ValueError('invalid diagnostic write duration')
            values['trace_write_ms'].append(duration/1e6)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            errors.add(str(exc))
    if matched_times != [p.get('selection_end_xr_ns') if type(p) is dict else None for p in presented[:4096]]:
        errors.add('trace joins do not preserve the full presentation prefix')
    return result | dict(status='fail' if errors else 'pass', errors=sorted(errors),
                         selected_differs_from_newest_complete=different,
                         higher_frame_id_available=newer, fallback_records=fallback, repeated_records=repeats,
                         distributions={k: _stats(v) for k, v in values.items()},
                         warning='Higher frame ID or target is not proof of a newer application scene or physical display time')
