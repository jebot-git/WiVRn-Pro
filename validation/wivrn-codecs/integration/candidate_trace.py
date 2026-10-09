"""Validate bounded selection diagnostics; observed alternatives are not speedups."""
import math
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('selection_replay', Path(__file__).with_name('selection_replay.py'))
_replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_replay)

SKIP_REASONS = ('should_not_render', 'decoders_unavailable', 'shutdown', 'no_complete_pair', 'retained_pair')


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


def _attempt_accounting(row, previous):
    """Validate schema-3 accounting through a successful submission, not its tail."""
    a = row.get('render_attempts')
    fields = {'render_attempt', 'previous_submission_attempt', 'previous_submission_target_xr_ns',
              'aborted', 'valid', 'skipped'}
    if type(a) is not dict or set(a) != fields:
        raise ValueError('missing or malformed render-attempt accounting')
    if a['valid'] is not True or not _int(a['aborted']) or a['aborted'] != 0:
        raise ValueError('render-attempt accounting contains an aborted or unaccounted attempt')
    if not _int(a['render_attempt'], True) or not _int(a['previous_submission_attempt']):
        raise ValueError('invalid render-attempt identity')
    skipped = a['skipped']
    if type(skipped) is not dict or set(skipped) != set(SKIP_REASONS) or any(not _int(n) for n in skipped.values()):
        raise ValueError('invalid render-attempt skip reasons or counts')
    previous_attempt = previous['render_attempts']['render_attempt'] if previous else 0
    previous_target = previous['predicted_display_xr_ns'] if previous else 0
    if (a['previous_submission_attempt'] != previous_attempt
            or not _int(a['previous_submission_target_xr_ns'])
            or a['previous_submission_target_xr_ns'] != previous_target):
        raise ValueError('render-attempt previous submission link differs')
    count = sum(skipped.values())
    if count >= 2**63 or a['render_attempt'] - previous_attempt != count + 1:
        raise ValueError('render-attempt delta does not equal submission plus explicit skips')
    target, prior = row.get('predicted_display_xr_ns'), row.get('previous_predicted_display_xr_ns')
    if not _int(target, True) or not _int(prior) or not previous_target <= prior < target:
        raise ValueError('render-attempt target chain is invalid')
    if (count == 0 and prior != previous_target) or (count > 0 and prior <= previous_target):
        raise ValueError('render-attempt target gap disagrees with explicit skips')
    return skipped


def evaluate(traces, presented, enabled=True, selection_policy=None):
    if type(traces) is not list or type(presented) is not list:
        return dict(status='fail', errors=['trace and presentation inputs must be lists'], promotion_eligible=False)
    errors = set()
    result = dict(status='fail', records=len(traces), promotion_eligible=False,
                  scope='Observed candidate availability and target choice only; no measured alternate-policy speedup or source-content age')
    if not enabled:
        bad = bool(traces) or any(type(p) is not dict or 'render_attempts' in p or
                                 any(k.startswith('candidate_trace_') for k in p if type(k) is str) for p in presented)
        bad |= selection_policy == 'bounded-newest' or (selection_policy is not None and any(
            type(p) is not dict or p.get('selection_policy') != selection_policy for p in presented))
        return result | dict(status='fail' if bad else 'off', errors=['trace present while disabled'] if bad else [])
    if not traces or len(traces) > 4096 or not presented:
        errors.add('missing or oversized diagnostic trace/presentation evidence')
    by_selection = {}
    schema = traces[0].get('schema') if traces and type(traces[0]) is dict else None
    observed_policy = selection_policy
    if observed_policy is None and schema in (2, 3):
        observed_policy = traces[0].get('selection_policy')
    prior_presentation = None
    skipped_totals = dict.fromkeys(SKIP_REASONS, 0)
    for index, p in enumerate(presented, 1):
        if type(p) is not dict:
            errors.add('invalid presentation row')
            continue
        if observed_policy is not None and p.get('selection_policy') != observed_policy:
            errors.add('presentation selection policy differs within diagnostic run')
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
        if schema == 3:
            try:
                for reason, count in _attempt_accounting(p, prior_presentation).items():
                    skipped_totals[reason] += count
                prior_presentation = p
            except (ValueError, KeyError, TypeError) as exc:
                errors.add(str(exc))
        elif 'render_attempts' in p:
            errors.add('render-attempt accounting requires schema 3')
    if len(traces) != min(len(presented), 4096):
        errors.add('trace does not cover the bounded presentation prefix')
    previous = None
    previous_frames = None
    previous_record = None
    observed_metadata = {}
    matched_times = []
    different = newer = fallback = repeats = runtime_target_gaps = 0
    values = {k: [] for k in ('wait_frame_ms', 'wait_return_to_selection_ms', 'render_entry_to_selection_ms',
                             'selected_target_minus_runtime_target_ms', 'newest_target_minus_selected_target_ms',
                             'trace_write_ms', 'prior_submission_to_previous_render_target_ms')}
    for index, r in enumerate(traces, 1):
        try:
            if type(r) is not dict or type(r.get('schema')) is not int or r['schema'] not in (1, 2, 3) or r['schema'] != schema or type(r.get('sequence')) is not int or r['sequence'] != index:
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
            if not r['projection_submitted']:
                raise ValueError('selected trace did not submit a projection layer')
            if r.get('selection_phase') not in ('early', 'after-fence'):
                raise ValueError('invalid selection phase')
            if observed_policy is not None and (r['schema'] not in (2, 3) or r.get('selection_policy') != observed_policy):
                raise ValueError('trace differs from requested selection policy')
            p = by_selection.get(r['selection_end_xr_ns'])
            if p is None:
                raise ValueError('trace lacks presentation join')
            if r['schema'] == 3:
                _attempt_accounting(r, previous_record)
                if r['render_attempts'] != p.get('render_attempts'):
                    raise ValueError('trace/presentation render-attempt accounting differs')
            elif 'render_attempts' in r:
                raise ValueError('render-attempt accounting requires schema 3')
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
            if r['schema'] in (2, 3):
                for name in ('closest', 'previous'):
                    frames = r.get(name)
                    if type(frames) is not list or len(frames) != 3:
                        raise ValueError('invalid previous/closest snapshot bounds')
                    for eye, f in enumerate(frames):
                        _frame(f, times[-2])
                        if type(f.get('decoder')) is not int or f['decoder'] != eye:
                            raise ValueError('invalid previous/closest decoder ordering')
                for key in ('selection_policy', 'predicted_display_period_xr_ns', 'previous_predicted_display_xr_ns', 'max_ahead_xr_ns'):
                    if type(p.get(key)) is not type(r.get(key)) or p.get(key) != r.get(key):
                        raise ValueError('trace/presentation policy or period mismatch')
                if any(f['present'] for f in r['previous']) and not _replay.coherent(r['previous']):
                    raise ValueError('previous selection snapshot is not a coherent complete pair')
                if r['schema'] == 3 and previous_record is None and any(f['present'] for f in r['previous']):
                    raise ValueError('first render-attempt trace has an unobserved previous selection')
                if not _replay.coherent(selected) or (any(f['present'] for f in r['closest']) and not _replay.coherent(r['closest'])):
                    raise ValueError('selected/closest snapshot is not a coherent complete pair')
                repeat_snapshot = (r['previous'][0]['present'] and r['previous'][1]['present']
                                   and [_identity(f) for f in selected] == [_identity(f) for f in r['previous']])
                if r['repeat_previous'] != repeat_snapshot:
                    raise ValueError('repeat flag disagrees with explicit previous snapshot')
                prior_target = r['previous_predicted_display_xr_ns']
                if not _int(prior_target) or prior_target >= r['predicted_display_xr_ns']:
                    raise ValueError('previous runtime target is not before current target')
                if previous_record is not None:
                    if not _replay.snapshot_equal(r['previous'], previous_record['selected']):
                        raise ValueError('previous selection snapshot does not match prior trace')
                    # last_display_time advances on every render() entry, while
                    # this journal contains successful projection submissions.
                    # Empty/timewarp-only iterations need not produce a row.
                    # The previous selection must still be exactly retained:
                    # no unobserved selection transition is accepted here.
                    gap = prior_target - previous_record['predicted_display_xr_ns']
                    if gap < 0:
                        raise ValueError('previous runtime target regresses behind prior trace')
                decision = _replay.replay(r)
            if r['alpha_required'] != bool(history[0]['present'] and history[0]['alpha']):
                raise ValueError('alpha participation disagrees with actual oldest-slot policy')
            metadata_keys = ('present','frame','warp','view_display_time_xr_ns','receive_xr_ns','ready_xr_ns','alpha')
            snapshots = [tuple(f.get(k) for k in metadata_keys) for f in selected]
            for f in history + selected + (r['previous']+r['closest'] if r['schema'] in (2, 3) else []):
                if f['present']:
                    key = f['frame'], f['warp'], f['decoder']
                    value = tuple(f.get(k) for k in metadata_keys)
                    if key in observed_metadata and observed_metadata[key] != value:
                        raise ValueError('immutable frame metadata changed across snapshots')
                    observed_metadata[key] = value
            required = 2 + int(r['selected_alpha_required'] if r['schema'] in (2, 3) and r['selection_policy'] == 'bounded-newest' else r['alpha_required'])
            if any(not s['present'] for s in selected[:required]) or any(_identity(s) != _identity(selected[0]) for s in selected[:required]):
                raise ValueError('selected stereo/alpha identity mismatch')
            for eye, side in enumerate(('left', 'right')):
                if (p.get(side), p.get(side+'_warp')) != _identity(selected[eye]):
                    raise ValueError('selected identity disagrees with submitted pair')
                for source, suffix in (('receive_xr_ns', '_receive_xr_ns'), ('ready_xr_ns', '_decode_ready_xr_ns')):
                    if selected[eye][source] != p.get(side+suffix):
                        raise ValueError('selected publication disagrees with presentation')
            candidate_required = 2 + int(r['alpha_required'])
            eligible = [f for f in history[:3] if f['present'] and all(
                any(g['present'] and _identity(g) == _identity(f) for g in history[eye*3:eye*3+3]) for eye in range(1, candidate_required))]
            if r['schema'] in (2, 3):
                if decision['fallback']:
                    fallback += 1
                    if previous is None or snapshots != previous_frames:
                        raise ValueError('fallback is not a verified retained selection')
                if eligible:
                    newest = max(eligible, key=lambda f: f['frame'])
                    chosen = selected[0]
                    different += _identity(newest) != _identity(chosen)
                    newer += newest['frame'] > chosen['frame']
                    values['newest_target_minus_selected_target_ms'].append((newest['view_display_time_xr_ns']-chosen['view_display_time_xr_ns'])/1e6)
            elif eligible:
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
            if (r['schema'] == 1 or previous is not None) and r['repeat_previous'] != (previous is not None and identities == previous):
                raise ValueError('repeat flag disagrees with prior selection')
            previous = identities
            previous_frames = snapshots
            if r['schema'] in (2, 3) and previous_record is not None:
                runtime_target_gaps += gap > 0
                values['prior_submission_to_previous_render_target_ms'].append(gap / 1e6)
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
                         unrecorded_render_target_gaps=runtime_target_gaps,
                         render_attempt_coverage='scene_start_through_last_submission' if schema == 3 and not errors else 'not_recorded',
                         skipped_render_count=sum(skipped_totals.values()) if schema == 3 and not errors else None,
                         skipped_render_reasons=skipped_totals if schema == 3 and not errors else None,
                         terminal_render_tail='not_recorded',
                         continuity_scope=('Schema 3 accounts render outcomes from scene start through the last successful submission; '
                                           'individual skipped targets and the terminal tail are not recorded.' if schema == 3 else
                                           'Successful projection submissions only; schema 1/2 does not record every render attempt. '
                                           'Target gaps establish only compatibility with intervening renders, not their count or outcome.'),
                         distributions={k: _stats(v) for k, v in values.items()},
                         warning='Higher frame ID or target is not proof of a newer application scene or physical display time')
