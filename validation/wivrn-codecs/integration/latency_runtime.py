"""Fail-closed validation of observed 0.13 selection and custom decoder telemetry.

Pass validates telemetry contracts, not latency targets, optical MTP or image quality.
`decoded` accepts frame-row lists or the existing {(frame, eye): row} mapping.
Callers must filter decoder summary records; failed frame records must remain present.
Use backend='native' with no custom decode rows for native H.264/HEVC.
"""
import math


_TIMES = ('selection_begin_xr_ns', 'selection_end_xr_ns',
          'render_fence_begin_xr_ns', 'render_fence_end_xr_ns',
          'end_frame_return_xr_ns', 'submit_xr_ns',
          'left_receive_xr_ns', 'right_receive_xr_ns',
          'left_decode_ready_xr_ns', 'right_decode_ready_xr_ns')
_DURATIONS = ('decode_gpu_ms', 'cpu_decode_ms', 'staging_copy_ms',
              'submit_fence_wait_ms', 'queue_wait_ms', 'decode_upload_ms',
              'decompression_ms')
_GPU_KERNELS = ('baseline', 'coop64', 'coop128', 'coop128dc')


def _integer(value, low=0, high=(1 << 64)-1):
    return type(value) is int and low <= value <= high


def _duration(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0}
    return dict(count=len(values), min=values[0], max=values[-1],
                **{f'p{q}': values[max(0, math.ceil(len(values)*q/100)-1)]
                   for q in (50, 95, 99)})


def evaluate(presented, decoded, backend, selection_phase, cpu_output=None, selection_policy=None):
    """Return status pass/fail, errors, counts and millisecond distributions.

    XR times are client-local positive signed-64-bit values in this runtime log.
    Fence span is CPU elapsed time waiting for the PREVIOUS render fence, not GPU
    work duration. Decoder submit_fence_wait includes queue-lock/submit/wait CPU time.
    The immediate end_frame_return timestamp precedes the older post-submit log time.
    """
    result = dict(status='fail', backend=backend, selection_phase=selection_phase,
                  errors=[], presented_rows=0, decoded_rows=0,
                  clock_domain='headset_local_openxr',
                  optical_motion_to_photon_verified=False,
                  scope='Runtime telemetry contract only; not the mission latency gate')
    errors = set()
    if backend not in ('native', 'resident', 'readback', 'cpu-neon'):
        errors.add('unsupported requested backend')
    if selection_phase not in ('early', 'after-fence'):
        errors.add('unsupported requested selection phase')
    if cpu_output not in (None, 'cached-copy', 'mapped-direct', 'not_used'):
        errors.add('unsupported requested CPU output mode')
    if selection_policy not in (None, 'closest-target', 'bounded-newest'):
        errors.add('unsupported requested selection policy')
    if cpu_output in ('cached-copy', 'mapped-direct') and backend != 'cpu-neon':
        errors.add('CPU output requires CPU NEON backend')
    if cpu_output == 'not_used' and backend == 'cpu-neon':
        errors.add('CPU NEON requires an output destination')
    if type(presented) is not list or not presented:
        errors.add('presented must be a nonempty list')
        presented = []
    if type(decoded) not in (list, dict):
        errors.add('decoded must be a frame-row list or mapping')
        decoded = []
    if type(decoded) is dict:
        for key, row in decoded.items():
            if (type(row) is not dict or type(key) is not tuple or len(key) != 2
                    or any(type(k) is not int for k in key)
                    or key != (row.get('frame'), row.get('eye'))):
                errors.add('decoded mapping identity disagrees with row')
        decoded = list(decoded.values())
    result.update(presented_rows=len(presented), decoded_rows=len(decoded))
    if backend == 'native' and decoded:
        errors.add('native run contains custom decoder telemetry')
    if backend != 'native' and not decoded:
        errors.add('custom decoder telemetry is empty')

    identities, seen_frames, previous_submit = set(), {}, None
    fence_wait, selection_to_return = [], []
    for row in presented:
        if (type(row) is not dict
                or any(not _integer(row.get(k), 1, (1 << 63)-1) for k in _TIMES)
                or any(not _integer(row.get(k)) for k in ('left', 'right', 'left_warp', 'right_warp'))):
            errors.add('missing or invalid presentation identity/timestamp fields')
            continue
        if row.get('selection_phase') != selection_phase:
            errors.add('observed selection phase differs from requested phase')
        if selection_policy is not None and row.get('selection_policy') != selection_policy:
            errors.add('observed selection policy differs from requested policy')
        if row['left'] != row['right'] or row['left_warp'] != row['right_warp']:
            errors.add('presented stereo frame/warp mismatch')
        sb, se = row['selection_begin_xr_ns'], row['selection_end_xr_ns']
        fb, fe = row['render_fence_begin_xr_ns'], row['render_fence_end_xr_ns']
        end, submit = row['end_frame_return_xr_ns'], row['submit_xr_ns']
        ordered = ((sb <= se <= fb <= fe <= end <= submit) if selection_phase == 'early'
                   else (fb <= fe <= sb <= se <= end <= submit))
        if not ordered:
            errors.add('selection/fence/end-frame/submit timestamps violate phase order')
        else:
            fence_wait.append((fe-fb)/1e6)
            selection_to_return.append((end-se)/1e6)
        if previous_submit is not None and submit <= previous_submit:
            errors.add('presentation submit timestamps do not strictly increase')
        if previous_submit is not None and min(sb, fb) < previous_submit:
            errors.add('next presentation selection/fence begins before previous submit observation')
        previous_submit = submit
        for side, eye in (('left', 0), ('right', 1)):
            receive, ready = row[side+'_receive_xr_ns'], row[side+'_decode_ready_xr_ns']
            if not receive <= ready <= se:
                errors.add('eye receive/publication occurs after selection or in reverse order')
            key = row[side], row[side+'_warp'], eye
            identities.add(key)
            identity_key = row[side], eye
            stable = row[side+'_warp'], receive, ready
            if identity_key in seen_frames and seen_frames[identity_key] != stable:
                errors.add('repeated frame changed warp or receive/publication timestamps')
            seen_frames[identity_key] = stable

    decoded_identities, actual_kernels, output_modes = set(), set(), set()
    duration_values = {k: [] for k in _DURATIONS}
    for row in decoded:
        if (type(row) is not dict or row.get('failed')
                or not _integer(row.get('frame')) or not _integer(row.get('warp'))
                or not _integer(row.get('eye'), 0, 1)):
            errors.add('failed or invalid custom decoder frame identity')
            continue
        identity = row['frame'], row['warp'], row['eye']
        if identity in decoded_identities:
            errors.add('duplicate custom decoder frame/warp/eye')
        decoded_identities.add(identity)
        if row.get('decoder_backend') != backend:
            errors.add('observed decoder backend differs from requested backend')
        if type(row.get('decoder_kernel')) is not str:
            errors.add('missing decoder kernel')
        else:
            actual_kernels.add(row['decoder_kernel'])
        if type(row.get('gpu_timing_valid')) is not bool:
            errors.add('gpu_timing_valid must be a boolean')
        valid_durations = all(_duration(row.get(k)) for k in _DURATIONS)
        if not valid_durations:
            errors.add('missing/nonfinite/negative/non-numeric decoder duration')
        else:
            for key in _DURATIONS:
                duration_values[key].append(row[key])
        if backend == 'cpu-neon':
            if (row.get('decoder_kernel') != 'direct-neon'
                    or row.get('cpu_vector_backend') != 'aarch64-neon'
                    or row.get('gpu_timing_valid') is not False
                    or row.get('decode_gpu_ms') != 0):
                errors.add('CPU NEON decoder provenance/timing flags contradict requested path')
        elif backend in ('resident', 'readback'):
            if (row.get('decoder_kernel') not in _GPU_KERNELS
                    or row.get('cpu_vector_backend') != 'not_used'
                    or row.get('gpu_timing_valid') is not True
                    or row.get('cpu_decode_ms') != 0
                    or (backend == 'resident' and row.get('staging_copy_ms') != 0)):
                errors.add('GPU decoder provenance/timing flags contradict requested path')
        # Historical callers may omit this option. Current comparisons require
        # actual memory/output evidence for every frame, including capture cost.
        if cpu_output is not None or 'cpu_output_mode' in row:
            mode = row.get('cpu_output_mode')
            if type(mode) is str:
                output_modes.add(mode)
            if mode not in (('cached-copy', 'mapped-direct') if backend == 'cpu-neon' else ('not_used',)):
                errors.add('invalid observed CPU output mode')
            if cpu_output is not None and mode != cpu_output:
                errors.add('observed CPU output differs from requested mode')
            flags = row.get('staging_memory_flags')
            if (not _integer(row.get('staging_memory_type'), 0, 31)
                    or not _integer(flags, 0, (1 << 32)-1) or flags & 6 != 6):
                errors.add('staging memory lacks verified host-visible coherent allocation')
            if mode == 'mapped-direct' and row.get('staging_copy_ms') != 0:
                errors.add('mapped-direct must not report a staging memcpy')
            snapshot_ms = row.get('capture_snapshot_ms')
            if not _duration(snapshot_ms):
                errors.add('invalid capture snapshot duration')
            captured = row['frame'] % 90 == 0
            expected_capture = ('cpu_mapped_staging_snapshot' if mode == 'mapped-direct' else
                                'cpu_cached_output' if mode == 'cached-copy' else
                                'gpu_output_readback' if backend == 'resident' else 'decoded_cpu_output')
            if row.get('capture_source') != (expected_capture if captured else 'not_captured'):
                errors.add('capture source contradicts actual output path or sample cadence')
            if not captured and snapshot_ms != 0:
                errors.add('uncaptured frame has snapshot overhead')
            if not (captured and (mode == 'mapped-direct' or backend == 'resident')) and snapshot_ms != 0:
                errors.add('output path without snapshot reports snapshot overhead')
            if _duration(snapshot_ms) and _duration(row.get('decode_upload_ms')) and snapshot_ms > row['decode_upload_ms']:
                errors.add('capture snapshot exceeds enclosing decode/upload interval')
    if len(actual_kernels) > 1:
        errors.add('mixed custom decoder kernels within run')
    if len(output_modes) > 1:
        errors.add('mixed CPU output modes within run')
    if backend != 'native' and identities - decoded_identities:
        errors.add('presented eye lacks exact frame/warp/eye decoder evidence')
    result.update(render_fence_wait_ms=_distribution(fence_wait),
                  selection_end_to_end_frame_return_ms=_distribution(selection_to_return),
                  decoder_duration_ms={k: _distribution(v) for k, v in duration_values.items()},
                  observed_decoder_kernels=sorted(actual_kernels),
                  observed_cpu_output_modes=sorted(output_modes),
                  matched_presented_eye_identities=len(identities & decoded_identities),
                  errors=sorted(errors), status='fail' if errors else 'pass')
    return result
