"""Codec-independent local-clock timing; these metrics do not establish pixel quality."""
import math

EYES = ('left', 'right')
STAGES = ('receive_to_ready_ms', 'ready_to_submit_ms', 'receive_to_submit_ms')
TIME_LIMIT = (1 << 63) - 1
ID_LIMIT = (1 << 64) - 1


def _integer(value, minimum=0, maximum=ID_LIMIT):
    return type(value) is int and minimum <= value <= maximum


def _finite(value, minimum=0, maximum=math.inf):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _distribution(values):
    values = sorted(values)
    if not values:
        return None
    return dict(count=len(values), p50=values[math.ceil(len(values)*.50)-1],
                p95=values[math.ceil(len(values)*.95)-1],
                p99=values[math.ceil(len(values)*.99)-1], max=values[-1])


def _window(valid, first, cutoff):
    presentations = [row for row in valid if row['submit_xr_ns'] >= cutoff]
    pairs = [row for row in first if row['submit_xr_ns'] >= cutoff]
    times = [row['submit_xr_ns'] for row in pairs]
    first_ns, last_ns = (times[0], times[-1]) if times else (None, None)
    interval = (last_ns-first_ns)/1e9 if len(times) > 1 else None
    per_eye = {}
    pooled = {key: [] for key in STAGES}
    for eye in EYES:
        samples = {key: [] for key in STAGES}
        for row in pairs:
            receive = row[eye+'_receive_xr_ns']
            ready = row[eye+'_decode_ready_xr_ns']
            submit = row['submit_xr_ns']
            for stage, duration in zip(STAGES, (ready-receive, submit-ready, submit-receive)):
                samples[stage].append(duration/1e6)
        per_eye[eye] = {key: _distribution(value) for key, value in samples.items()}
        for key, value in samples.items():
            pooled[key].extend(value)
    return dict(status='measured' if pairs else 'unavailable',
                window_start_xr_ns=cutoff,
                first_unique_submit_xr_ns=first_ns, last_unique_submit_xr_ns=last_ns,
                unique_pair_interval_s=interval,
                presentation_count=len(presentations), unique_stereo_pairs=len(pairs),
                repeat_presentations=len(presentations)-len(pairs),
                unique_stereo_submission_hz=(len(times)-1)/interval if interval and interval > 0 else None,
                per_eye=per_eye, pooled={key: _distribution(value) for key, value in pooled.items()})


def evaluate_present(rows, warmup_seconds=2.0):
    """Analyze one session's existing successful xrEndFrame records.

    Every identity must be a matching frame+warp stereo pair. Only its earliest
    successful submission contributes latency/cadence. Future predicted display
    times are legal but never used for measured intervals. Receive/ready times
    must be positive and no later than submission. Invalid rows fail the report;
    partial metrics remain explicitly accompanied by errors/rejected-row counts.
    """
    errors = []
    valid, first = [], []
    identities = {}
    previous_submit = previous_frame = None
    if type(rows) is not list:
        errors.append('presentation rows must be a list')
        rows = []
    if not _finite(warmup_seconds, 0, TIME_LIMIT/1e9):
        errors.append('warmup_seconds must be finite and nonnegative')
        warmup_seconds = 2.0
    time_fields = ['submit_xr_ns'] + [eye+suffix for eye in EYES for suffix in ('_receive_xr_ns', '_decode_ready_xr_ns')]
    identity_fields = ('left', 'right', 'left_warp', 'right_warp')
    for index, row in enumerate(rows):
        reason = None
        if type(row) is not dict or not all(_integer(row.get(key)) for key in identity_fields) or not all(_integer(row.get(key), 1, TIME_LIMIT) for key in time_fields):
            reason = 'invalid/missing integer identity or positive local-clock fields'
        elif row['left'] != row['right'] or row['left_warp'] != row['right_warp']:
            reason = 'mismatched stereo frame or warp'
        elif 'predicted_display_xr_ns' in row and not _integer(row['predicted_display_xr_ns'], 1, TIME_LIMIT):
            reason = 'invalid predicted display timestamp'
        elif previous_submit is not None and row['submit_xr_ns'] <= previous_submit:
            reason = 'submission clock is not strictly increasing'
        elif previous_frame is not None and row['left'] < previous_frame:
            reason = 'presented frame regressed'
        elif any(not row[eye+'_receive_xr_ns'] <= row[eye+'_decode_ready_xr_ns'] <= row['submit_xr_ns'] for eye in EYES):
            reason = 'future or reversed receive/ready/submit clocks'
        else:
            old = identities.get(row['left'])
            if old is not None and any(row[key] != old[key] for key in identity_fields+tuple(key for key in time_fields if key != 'submit_xr_ns')):
                reason = 'repeated frame changed warp or receive/ready identity'
        if reason:
            errors.append(f'row {index}: {reason}')
            continue
        previous_submit, previous_frame = row['submit_xr_ns'], row['left']
        valid.append(row)
        if row['left'] not in identities:
            identities[row['left']] = row
            first.append(row)
    if not rows:
        errors.append('no presentation rows')
    if not first:
        errors.append('no valid coherent stereo submissions')
    start = first[0]['submit_xr_ns'] if first else 0
    all_session = _window(valid, first, start)
    after_warmup = _window(valid, first, start+int(warmup_seconds*1e9))
    return dict(status='fail' if errors else 'pass', errors=errors,
                input_rows=len(rows), rejected_rows=len(rows)-len(valid),
                # Top-level compatibility aliases describe the all-session window.
                **{key:value for key,value in all_session.items() if key != 'status'},
                all_session=all_session, after_warmup=after_warmup, warmup_seconds=warmup_seconds,
                clock_domain='headset_local_openxr',
                quality=dict(status='not_measured', frame_pair_identity_verified=bool(first) and not errors,
                             pixel_quality_verified=False,
                             scope='Matched frame/warp identity is not per-eye pixel quality; native surface sampling needs its own common capture/reference stage.'),
                scope='First successful coherent stereo submissions only. Ready means common push_blit_handle availability, not pure GPU/MediaCodec time. Receive-to-submit excludes host work and transfer before first receipt; submit is after xrEndFrame, not photon time. Predicted display and fake MediaCodec PTS are excluded.')


def evaluate_performance(rows):
    """Analyze optional raw version-1 performance.json snapshots.

    Encode durations are sampled from independently retained stage frames. Dedup
    by session/stream/encode-stage frame, not snapshot frame or first_display.
    No cross-clock delivery interval, inferred hardware-decode interval, or
    predicted-display latency is promoted into comparison metrics.
    """
    errors, seen, streams = [], {}, {}
    sessions = set()
    snapshots_seen = set()
    unavailable = duplicates = 0
    last_update = {}
    last_stage_frame = {}
    if type(rows) is not list:
        return dict(status='invalid', errors=['performance rows must be a list'])
    for index, row in enumerate(rows):
        if (type(row) is not dict or row.get('version') != 1 or type(row.get('version')) is not int
                or not _integer(row.get('session'), 1, TIME_LIMIT)
                or not _integer(row.get('updated_ns'), 1, TIME_LIMIT)
                or type(row.get('streams')) is not list):
            errors.append(f'snapshot {index}: invalid version/session/time/streams schema')
            continue
        session, updated = row['session'], row['updated_ns']
        sessions.add(session)
        if updated < session or updated < last_update.get(session, updated):
            errors.append(f'snapshot {index}: monotonic snapshot time regressed')
            continue
        last_update[session] = updated
        snapshots_seen.add((session, updated))
        ids = set()
        for stream in row['streams']:
            if type(stream) is not dict or not _integer(stream.get('id'), 0, 2) or type(stream.get('stage_frames')) is not dict:
                errors.append(f'snapshot {index}: invalid stream/stage schema')
                continue
            stream_id = stream['id']
            if stream_id in ids:
                errors.append(f'snapshot {index}: duplicate stream id')
                continue
            ids.add(stream_id)
            value = stream.get('encode_ms')
            frame = stream['stage_frames'].get('encode')
            if value is None and frame is None:
                unavailable += 1
                continue
            if not _finite(value, 0, 2000) or not _integer(frame):
                errors.append(f'snapshot {index} stream {stream_id}: invalid encode duration/stage frame')
                continue
            stage_key = session, stream_id
            if frame < last_stage_frame.get(stage_key, frame):
                errors.append(f'snapshot {index} stream {stream_id}: encode stage frame regressed')
                continue
            last_stage_frame[stage_key] = frame
            key = session, stream_id, frame
            if key in seen:
                duplicates += 1
                if seen[key] != value:
                    errors.append(f'snapshot {index} stream {stream_id}: encode duration changed for the same stage frame')
                continue
            seen[key] = value
            streams.setdefault(stream_id, []).append(value)
    values = list(seen.values())
    if len(sessions) > 1:
        errors.append('multiple performance sessions cannot form one comparison run')
    return dict(status='invalid' if errors else 'sampled' if values else 'unavailable',
                errors=errors, input_snapshots=len(rows), unique_snapshots=len(snapshots_seen),
                sessions=sorted(sessions), unique_encode_stage_frames=len(values),
                repeated_stage_observations=duplicates, unavailable_stage_observations=unavailable,
                encode_ms=_distribution(values),
                per_stream={str(key):dict(unique_encode_stage_frames=len(values), encode_ms=_distribution(values)) for key, values in sorted(streams.items())},
                scope='Optional approximately 200-ms snapshots, not every encoded frame. Encode wall-time stage duration only; stage frame IDs may differ within a snapshot. Host timing is converted to headset clock without an uncertainty bound. Decode/delivery/predicted-display fields are excluded; sent_to_decoder is not an actual hardware queue timestamp. No steady-state split: snapshots do not expose a trustworthy per-stage mapping to presentation time.')
