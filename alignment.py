# SPDX-License-Identifier: MIT
"""Controller calibration math and coordinate projection, independent of transport."""
import math
import statistics

HANDS = ('left_hand', 'right_hand')


def add(a, b):
    return [x + y for x, y in zip(a, b)]


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def norm(a):
    return math.sqrt(sum(x*x for x in a))


def unit(q):
    n = norm(q)
    if not math.isfinite(n) or n < 1e-8:
        raise ValueError('Invalid quaternion')
    return [x/n for x in q]


def conjugate(q):
    return [-q[0], -q[1], -q[2], q[3]]


def multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def rotate(q, v):
    return multiply(multiply(q, list(v)+[0]), conjugate(q))[:3]


def angle(a, b):
    dot = abs(sum(x*y for x, y in zip(unit(a), unit(b))))
    return math.degrees(2*math.acos(min(1, dot)))


def mean_pose(poses):
    qs = [unit(p['rotation']) for p in poses]
    qs = [q if sum(x*y for x, y in zip(q, qs[0])) >= 0 else [-x for x in q] for q in qs]
    return {'position': [statistics.mean(p['position'][i] for p in poses) for i in range(3)],
            'rotation': unit([sum(q[i] for q in qs) for i in range(4)])}


def transform_from_pair(quest, pc):
    q = unit(multiply(unit(pc['rotation']), conjugate(unit(quest['rotation']))))
    return {'rotation': q, 'position': sub(pc['position'], rotate(q, quest['position']))}


def apply(transform, pose):
    return {'position': add(rotate(transform['rotation'], pose['position']), transform['position']),
            'rotation': multiply(transform['rotation'], pose['rotation'])}


def residual(a, b):
    return {'position_m': norm(sub(a['position'], b['position'])),
            'angle_deg': angle(a['rotation'], b['rotation'])}


def summarize(samples):
    if not isinstance(samples, list) or not 30 <= len(samples) <= 128:
        raise ValueError('Need 30 to 128 distinct tracked samples per phase')
    for s in samples:
        identity = s['identity']
        if (len(identity) != 3 or not all(isinstance(v, str) and v for v in identity[:2])
                or type(identity[2]) is not int or identity[2] < 0):
            raise ValueError('Invalid sample identity')
    if len({tuple(s['identity']) for s in samples}) != 1:
        raise ValueError('Tracking session or generation changed within measurement')
    times = [int(s['sample_time_ns']) for s in samples]
    if (any(b <= a or b-a > 500_000_000 for a, b in zip(times, times[1:]))
            or not 3_000_000_000 <= times[-1]-times[0] <= 10_000_000_000):
        raise ValueError('Need at least three seconds of increasing sample timestamps')
    devices, stability = {}, {}
    profiles = {}
    for hand in HANDS:
        poses = [s['devices'][hand] for s in samples]
        for p in poses:
            if (not p.get('valid') or p.get('flags', 0) & 15 != 15 or p.get('pose_space') != 'grip'
                    or len(p['position']) != 3 or len(p['rotation']) != 4
                    or not all(math.isfinite(v) for v in p['position']+p['rotation'])):
                raise ValueError('Need valid, actively tracked grip poses')
        profile_set = {p.get('interaction_profile') for p in poses}
        if len(profile_set) != 1 or None in profile_set or '' in profile_set:
            raise ValueError('Controller interaction profile missing or changed')
        profiles[hand] = next(iter(profile_set))
        devices[hand] = mean_pose(poses)
        errors = [residual(p, devices[hand]) for p in poses]
        stability[hand] = {'max_position_m': max(e['position_m'] for e in errors),
                           'max_angle_deg': max(e['angle_deg'] for e in errors)}
        if stability[hand]['max_position_m'] > .01 or stability[hand]['max_angle_deg'] > 1:
            raise ValueError(f'{hand} was not stable within 1 cm / 1 degree')
    return {'identity': samples[0]['identity'], 'sample_count': len(samples),
            'duration_s': (times[-1]-times[0])/1e9, 'devices': devices,
            'stability': stability, 'interaction_profiles': profiles}


def solve(before, quest, after):
    phases = [summarize(s) for s in (before, quest, after)]
    b, q, a = phases
    if b['identity'] != a['identity']:
        raise ValueError('PC provider/session/generation changed across app switch; repeat measurement')
    if b['identity'][0] != q['identity'][0]:
        raise ValueError('Quest samples belong to a different broker session')
    if int(before[-1]['sample_time_ns']) >= int(after[0]['sample_time_ns']):
        raise ValueError('PC return measurement must follow the initial measurement')
    if not (b['interaction_profiles'] == q['interaction_profiles'] == a['interaction_profiles']):
        raise ValueError('Interaction profiles differ; grip-pose equivalence needs verification')
    drift = {h: residual(b['devices'][h], a['devices'][h]) for h in HANDS}
    if any(e['position_m'] > .02 or e['angle_deg'] > 2 for e in drift.values()):
        raise ValueError('Controllers moved or tracking shifted across app switch (>2 cm / 2 degrees)')
    separation = norm(sub(q['devices'][HANDS[0]]['position'], q['devices'][HANDS[1]]['position']))
    if separation < .5:
        raise ValueError('Place controllers at least 50 cm apart for an independent spatial check')
    # Fit only the left controller. The right controller is held out for validation.
    full = transform_from_pair(q['devices'][HANDS[0]], a['devices'][HANDS[0]])
    yaw = 2*math.atan2(full['rotation'][1], full['rotation'][3])
    yaw_q = [0, math.sin(yaw/2), 0, math.cos(yaw/2)]
    if angle(yaw_q, full['rotation']) > 3:
        raise ValueError('Full-pose fit has >3 degree tilt between gravity-aligned stages')
    candidate = {'rotation': yaw_q, 'position': sub(a['devices'][HANDS[0]]['position'],
                 rotate(yaw_q, q['devices'][HANDS[0]]['position']))}
    checks = {h: residual(apply(candidate, q['devices'][h]), a['devices'][h]) for h in HANDS}
    if any(e['position_m'] > .03 or e['angle_deg'] > 3 for e in checks.values()):
        raise ValueError('Independent controller check failed (>3 cm / 3 degrees)')
    return {'status': 'validated_for_measured_sessions', 'method': 'stationary_grip_left_fit_right_holdout',
            'transform': dict(candidate, from_space='quest_stage', to_space='pc_stage', scale=1),
            'yaw_deg': math.degrees(math.atan2(math.sin(yaw), math.cos(yaw))),
            'controller_separation_m': separation, 'validation': checks, 'round_trip_drift': drift,
            'phases': dict(zip(('pc_before', 'quest', 'pc_after'), phases)),
            'limitations': ['Controllers must remain stationary between phases.',
                           'Checks cover these two locations; room-wide accuracy is not established.',
                           'Invalidate after source session/generation, room or runtime-origin changes.',
                           'This report does not turn cached anchors into live localized poses.']}


def projected_pose(transform, value, *, saved=False):
    """Return only a finite usable pose; never give invalid/cached input live validity."""
    if not isinstance(value, dict) or (not saved and not value.get('valid')):
        return None
    try:
        p, q = value['position'], value['rotation']
        if len(p) != 3 or len(q) != 4 or not all(math.isfinite(v) for v in p+q):
            return None
        result = apply(transform, {'position': p, 'rotation': unit(q)})
        if not saved:
            result.update(valid=True, flags=value.get('flags', 0))
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def project(transform, source, *, saved=False):
    """Pose-only view: entity-local geometry remains in the original source object."""
    output = {'reference_space': 'pc_stage', 'live': not saved,
              'source_session': source.get('source_session'), 'generation': source.get('generation'),
              'devices': {}, 'markers' if saved else 'anchors': {}, 'scene': {'entities': []},
              'boundary': None}
    field = 'saved_pose' if saved else 'pose'
    for key, value in source.get('devices', {}).items():
        pose = projected_pose(transform, value)
        if pose is not None:
            output['devices'][key] = pose
    collection = 'markers' if saved else 'anchors'
    for key, value in source.get(collection, {}).items():
        if not isinstance(value, dict):
            continue
        pose = projected_pose(transform, value.get('saved_pose') if saved else value, saved=saved)
        if pose is not None:
            output[collection][key] = {'saved_pose': pose} if saved else pose
    scene = source.get('scene') or {}
    for entity in scene.get('entities', []):
        if saved and not entity.get('localized_when_saved', False):
            continue
        pose = projected_pose(transform, entity.get(field), saved=saved)
        if pose is not None:
            output['scene']['entities'].append({'uuid': entity.get('uuid'), field: pose})
    boundary = source.get('boundary') or {}
    if boundary.get('available'):
        # Width/depth describe Quest's rectangle. Its origin/orientation must move too.
        output['boundary'] = {'kind': 'stage_rectangle', 'width': boundary.get('width'),
                              'depth': boundary.get('depth'),
                              field: {'position': transform['position'], 'rotation': transform['rotation']}}
    if saved:
        output['captured_unix_ns'] = source.get('captured_unix_ns')
    else:
        output['sample_time_ns'] = source.get('sample_time_ns')
    return output
