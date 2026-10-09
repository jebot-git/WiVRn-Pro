"""Validate local OpenXR gaze timing provenance; never infer sensor acquisition age."""

_TIME_FIELDS = (
    'scheduled_poll_xr_ns', 'poll_begin_xr_ns', 'poll_end_xr_ns',
    'requested_pose_xr_ns', 'returned_pose_expression_xr_ns',
)
_BOOL_FIELDS = (
    'time_available', 'expression_after_poll', 'orientation_valid',
    'position_valid', 'orientation_tracked', 'position_tracked', 'poll_order_valid',
)


def _integer(value, minimum, maximum):
    # JSON booleans are Python int subclasses, but are not valid timestamps/flags.
    return type(value) is int and minimum <= value <= maximum


def gaze_timing_metrics(rows, requested):
    """Return off/unverified/pass/invalid for the bounded schema-1 diagnostic log.

    Pass proves at least one successful orientation-valid pose with an available
    expression time; it does not prove physical eye sampling latency or quality.
    Future expression times are valid, and zero denotes unavailable time.
    """
    result = dict(status='unverified', requested=requested, observations=0,
                  usable_expression_time_samples=0, unavailable_time_samples=0,
                  future_expression_time_samples=0, failed_locates=0, errors=[],
                  clock_domain='local_openxr',
                  time_semantics='pose_expression_not_camera_acquisition',
                  scope='Timing provenance only; no sensor age or motion-to-photon measurement')
    errors = set()
    if type(requested) is not bool:
        errors.add('requested must be a boolean')
    if type(rows) is not list:
        errors.add('rows must be a list')
        result.update(status='invalid', errors=sorted(errors))
        return result
    result['observations'] = len(rows)
    if len(rows) > 6000:
        errors.add('telemetry exceeds the 6000-row cap')
        result.update(status='invalid', errors=sorted(errors))
        return result
    if not requested and rows:
        errors.add('gaze telemetry present while collection was disabled')
    previous_sequence = None
    previous_begin = None
    durations = []
    for row in rows:
        if type(row) is not dict:
            errors.add('row must be an object')
            continue
        shape_valid = (
            _integer(row.get('schema'), 1, 1)
            and _integer(row.get('sequence'), 1, (1 << 64) - 1)
            and all(_integer(row.get(k), -(1 << 63), (1 << 63) - 1) for k in _TIME_FIELDS)
            and _integer(row.get('locate_result'), -(1 << 31), (1 << 31) - 1)
            and _integer(row.get('location_flags'), 0, (1 << 64) - 1)
            and _integer(row.get('output_pose_flags'), 0, 63)
            and all(type(row.get(k)) is bool for k in _BOOL_FIELDS)
            and row.get('reference') in ('view', 'world')
            and row.get('time_semantics') == result['time_semantics']
            and row.get('clock_domain') == result['clock_domain']
        )
        if not shape_valid:
            errors.add('invalid or missing schema-1 fields/types')
            continue
        sequence = row['sequence']
        begin, end = row['poll_begin_xr_ns'], row['poll_end_xr_ns']
        if previous_sequence is not None and sequence <= previous_sequence:
            errors.add('gaze sequence is not strictly increasing')
        previous_sequence = sequence
        if previous_begin is not None and begin - previous_begin < 100_000_000:
            errors.add('poll emission order/rate violates the 100-ms interval')
        previous_begin = begin
        if end < begin:
            errors.add('poll ends before it begins')
        else:
            durations.append(end - begin)
        success = row['locate_result'] >= 0
        available = success and row['returned_pose_expression_xr_ns'] != 0
        future = available and row['returned_pose_expression_xr_ns'] > end
        expected = dict(time_available=available, expression_after_poll=future,
                        poll_order_valid=end >= begin)
        for name, mask in (('orientation_valid', 1), ('position_valid', 2),
                           ('orientation_tracked', 4), ('position_tracked', 8)):
            expected[name] = success and bool(row['location_flags'] & mask)
        if any(row[name] != value for name, value in expected.items()):
            errors.add('reported booleans disagree with time/result/location flags')
        # WiVRn moves XR tracked bits 2/3 to packet bits 4/5; packet bits 2/3
        # describe velocity validity and have no recorded raw counterpart here.
        raw_flags = row['location_flags'] if success else 0
        raw_validity = (raw_flags & 3) | ((raw_flags & 12) << 2)
        output_validity = row['output_pose_flags'] & 51
        if (output_validity & ~raw_validity or
            (row['reference'] == 'view' and output_validity != raw_validity) or
            (row['reference'] == 'world' and row['output_pose_flags'] & 12)):
            errors.add('output pose flags contradict the locate/reference contract')
        result['failed_locates'] += not success
        result['unavailable_time_samples'] += not available
        result['future_expression_time_samples'] += future
        result['usable_expression_time_samples'] += available and expected['orientation_valid']
    if durations:
        result['poll_duration_ns'] = dict(count=len(durations), min=min(durations), max=max(durations))
    result['errors'] = sorted(errors)
    result['status'] = ('invalid' if errors else 'off' if not requested else
                        'pass' if result['usable_expression_time_samples'] else 'unverified')
    return result
