#!/usr/bin/env python3
"""Join pinned native/replay audits and compare exact submitted RGB bytes.

This is a post-host-foveation, reconstructed-geometry component comparison.
The upstream native and actual-device collector receipts are explicit trust
inputs, not re-executed here. No device command or display claim is made.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import numpy as np
import research_quality as quality


def need(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict(data):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    def constant(value):
        raise ValueError('nonfinite JSON: ' + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


class Inputs:
    def __init__(self):
        self.reads = {}

    def read(self, path, expected, limit=32*1024*1024):
        path = Path(path).absolute()
        need(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected), 'invalid digest')
        need(not any(p.is_symlink() for p in [path, *path.parents]), 'symlink input')
        need(path.is_file() and path.stat().st_size <= limit, 'missing or oversized input')
        with path.open('rb') as f:
            data = f.read(limit + 1)
        need(len(data) <= limit and digest(data) == expected, 'input hash mismatch: ' + str(path))
        record = {'sha256': expected, 'bytes': len(data)}
        need(str(path) not in self.reads or self.reads[str(path)] == record, 'changed input')
        self.reads[str(path)] = record
        return data

    def json(self, path, expected):
        return strict(self.read(path, expected, 16*1024*1024))

    def child(self, root, descriptor, limit=32*1024*1024, allow_absolute=False):
        name = descriptor.get('path')
        need(type(name) is str and bool(name), 'missing product path')
        path = Path(name)
        if path.is_absolute():
            need(allow_absolute and '..' not in path.parts and path.is_relative_to(Path(root).absolute()), 'absolute product escapes explicit root')
        else:
            need(all(x not in ('', '.', '..') for x in name.split('/')), 'unsafe product path')
            path = Path(root)/path
        data = self.read(path, descriptor.get('sha256'), limit)
        if 'bytes' in descriptor:
            need(type(descriptor['bytes']) is int and descriptor['bytes'] == len(data), 'product size mismatch')
        return data

    def verify(self):
        for path, desc in list(self.reads.items()):
            self.read(path, desc['sha256'], max(desc['bytes'], 1))


def identity(value):
    need(all(type(value.get(k)) is int and 0 <= value[k] < 2**64
             for k in ('capture_id', 'frame', 'warp')), 'typed capture identity')
    need(1 <= value['capture_id'] <= 8, 'bounded capture identity')
    return tuple(value[k] for k in ('capture_id', 'frame', 'warp'))


def compare(native_audit, native_sha, job_audit, job_sha, replay_audit,
            replay_sha, replay_plan, replay_root):
    inputs = Inputs()
    native = inputs.json(native_audit, native_sha)
    job = inputs.json(job_audit, job_sha)
    replay = inputs.json(replay_audit, replay_sha)
    for value in (native, job, replay):
        need(value.get('schema') == 1 and value.get('status') == 'pass', 'passing upstream audit required')
    plan = inputs.json(replay_plan, replay.get('plan_sha256'))
    rp = plan['replay']
    # The job builder reads a superset of native source artifacts. Its embedded
    # audit must otherwise be byte-meaning equivalent, including every capture.
    embedded = job['candidate_capture_audit']
    need(set(embedded) == set(native), 'embedded native audit schema')
    need(all(embedded[k] == native[k] for k in native if k not in ('run_inputs', 'build_inputs')), 'job/native audit disagreement')
    need(all(embedded['run_inputs'].get(k) == v for k, v in native['run_inputs'].items()), 'native input binding mismatch')
    need(all(embedded['build_inputs'].get(k) == v for k, v in native['build_inputs'].items()), 'native build binding mismatch')
    need(embedded['build_inputs'] == job['build_inputs'], 'job/embedded build binding mismatch')
    need(set(embedded['build_inputs'])-set(native['build_inputs']) <= {'server/encoder/video_encoder_test.cpp', 'common/codec_test/wire.h'}, 'unexpected extra job build dependency')
    for key in ('run_hashes_sha256', 'build_receipt_sha256', 'renderer_shaders'):
        need(job[key] == native[key], 'native/job binding: ' + key)
    need(replay.get('source_adapter_sha256') == rp.get('source_adapter_sha256') == job_sha, 'replay source receipt join')
    need(replay.get('native_build_receipt_sha256') == rp.get('native_build_receipt_sha256') == job['build_receipt_sha256'], 'replay native build join')
    need(replay.get('output') == rp.get('output') == job['output'], 'replay output geometry/format join')
    need(replay.get('actual_headset_used') is True and replay.get('omission_control_verified') is True
         and replay.get('raw_format_preserved') is True, 'actual-device output/control audit required')
    need(replay.get('headset_physical_serial') == plan.get('expected_serial')
         and type(plan.get('expected_serial')) is str and bool(plan['expected_serial']), 'physical device join')
    need(replay.get('native_recorded_identity') == native['headset_serial_recorded'], 'native recorded device join')
    method = replay.get('native_identity_method')
    need(method in ('pinned_campaign_serial_bridge', 'direct_recorded_physical_serial'), 'audited physical identity method')
    if method == 'direct_recorded_physical_serial':
        need(native['headset_serial_recorded'] == plan['expected_serial'], 'direct physical device mismatch')
    else:
        need(type(rp.get('native_identity_bridge')) is dict and set(rp['native_identity_bridge']) == {'campaign', 'plan'}, 'missing audited serial bridge')
    need(job.get('source_boundary') == 'post-host-foveation-codec-input'
         and job.get('geometry_reconstructed_from_metadata') is True, 'reference scope')
    for key in ('actual_native_vertex_bytes_captured', 'actual_native_push_constant_bytes_captured',
                'aarch64_raster_geometry_bit_equivalence_verified', 'full_goal_qualified'):
        need(job.get(key) is False, 'unsupported reference scope: ' + key)
    selected = identity(job)
    w, h, fmt = (job['output'][k] for k in ('width', 'height', 'vk_format'))
    quality.shape(w, h)
    need(type(fmt) is int and fmt in (43, 50), 'only RGBA8_SRGB/BGRA8_SRGB supported')
    wire = inputs.child(Path(job_audit).parent, job['job'], 40*1024*1024)
    need(job['job'].get('format') == 'RLP1' and job['job'].get('version') == 1
         and len(wire) >= 24 and struct.unpack('<6I', wire[:24]) == (0x31504c52, 1, w, h, fmt, 2), 'RLP1 output header')
    need(rp.get('job_sha256') == digest(wire), 'plan/job byte join')
    files = plan['files']
    need(type(files) is list and len(files) == 4 and len({f['remote'] for f in files}) == 4, 'replay artifact population')
    by_name = {f['remote']: f for f in files}
    need(set(by_name) == {'replay', 'job.rlp', 'reprojection.vert.spv', 'reprojection.frag.spv'}, 'replay artifact names')
    need(by_name['job.rlp']['sha256'] == digest(wire), 'uploaded job join')
    for stage in ('vert', 'frag'):
        shader = native['renderer_shaders']['reprojection.' + stage]
        need(shader.get('literal_equivalent') is True and shader.get('literal_equivalence_recomputed') is True, 'native shader equivalence')
        artifact = by_name['reprojection.' + stage + '.spv']
        need(artifact['sha256'] == shader['sha256'], 'replay/native shader join')
        inputs.read(artifact['local'], artifact['sha256'], 8*1024*1024)
    sources = job['independent_inputs']
    need(type(sources) is list and len(sources) == 2 and [s.get('eye') for s in sources] == [0, 1], 'complete independent stereo inputs')
    for source in sources:
        need((selected[1], selected[2]) == (source['frame'], source['warp'])
             and source['source_boundary'] == job['source_boundary'], 'independent source identity')
        for flag in ('encoded_payload_pixels_used_as_reference', 'decoder_output_used_as_reference', 'original_application_rgb_claim'):
            need(source.get(flag) is False, 'reference source contamination')
        for key in ('source', 'envelope', 'encode_event_input'):
            desc = source[key]
            need(job['run_inputs'].get(desc['path'], {}).get('sha256') == desc['sha256'], 'source manifest binding')
    captures = native['captures']
    need(type(captures) is list and len(captures) == native['captured_pairs']*2, 'native stereo population')
    seen = set(); chosen = {}
    for capture in captures:
        key = identity(capture)
        eye = capture.get('eye')
        need(type(eye) is int and eye in (0, 1) and (key, eye) not in seen, 'duplicate/invalid native eye')
        seen.add((key, eye))
        if key == selected:
            chosen[eye] = capture
    need(set(chosen) == {0, 1}, 'selected complete native stereo pair required')
    eyes = replay['eyes']
    need(type(eyes) is list and len(eyes) == 2, 'complete replay stereo pair required')
    need(len({e['path'] for e in eyes}) == 2, 'replay eyes alias one file')
    results = []
    for eye in (0, 1):
        capture = chosen[eye]
        need(capture['geometry'] == [w, h] and capture['source_boundary'] == 'submitted-image'
             and capture['channel_format'] == 'RGBA8' and capture['permutation_only'] is True
             and capture['pixel_values_color_converted'] is False, 'native byte contract')
        expected_order = [0, 1, 2, 3] if fmt == 43 else [2, 1, 0, 3]
        need(capture['channel_permutation'] == expected_order, 'native format permutation')
        projection = strict(inputs.child(Path(native_audit).parent, capture['projection'], 1024*1024))
        need(projection.get('geometry') == [w, h] and projection.get('frame') == selected[1]
             and projection.get('warp') == selected[2] and projection.get('eye') == eye
             and projection.get('kind') == 'unwarped-perspective' and projection.get('source_boundary') == 'submitted-image'
             and projection.get('mapping_source') == 'actual submitted XR projection viewport/FOV'
             and projection.get('gaze_mode') == 'fixed-center' and projection.get('protected_radius_degrees') == 10, 'projection provenance/identity')
        x, y = quality.perspective_edges(w, h, projection['fov_radians'])
        mask = quality.protected_mask(w, h, x, y)
        mask_bytes = inputs.child(Path(native_audit).parent, capture['protected_mask'], quality.MAX_PIXELS)
        need(mask_bytes == mask.tobytes() and int(mask.sum()) == capture['protected_pixels'], 'recomputed mask mismatch')
        candidate = inputs.child(Path(native_audit).parent, capture['candidate'])
        raw = inputs.child(replay_root, eyes[eye], allow_absolute=True)
        need(eyes[eye].get('channel_order') == ('RGBA' if fmt == 43 else 'BGRA'), 'replay channel-order join')
        need(len(raw) == w*h*4, 'replay raw shape')
        reference = raw if fmt == 43 else np.frombuffer(raw, np.uint8).reshape(-1, 4)[:, [2, 1, 0, 3]].tobytes()
        result = quality.compare_rgba(reference, candidate, w, h, mask)
        a = np.frombuffer(reference, np.uint8).reshape(h, w, 4)[:, :, 3]
        b = np.frombuffer(candidate, np.uint8).reshape(h, w, 4)[:, :, 3]
        results.append(dict(eye=eye, **result, full_alpha_exact=bool((a == b).all()),
            protected_alpha_exact=bool((a[mask] == b[mask]).all()), raw_reference_sha256=digest(raw),
            reference_channel_permutation=expected_order, candidate_input=capture['candidate'],
            projection_input=capture['projection'], reference_input=eyes[eye]))
    inputs.verify()
    exact = all(r['protected_exact'] for r in results)
    return dict(schema=1, status='pass' if exact else 'fail', evidence_integrity_verified=True,
        capture_id=selected[0], frame=selected[1], warp=selected[2], geometry=[w, h], eyes=results,
        protected_radius_degrees=10, gaze_mode='fixed-center', rgb_code_tolerance=0,
        exact_submitted_replay_rgb=exact, native_audit_sha256=native_sha, job_audit_sha256=job_sha,
        replay_audit_sha256=replay_sha, replay_plan_sha256=replay['plan_sha256'],
        run_hashes_sha256=native['run_hashes_sha256'], build_receipt_sha256=job['build_receipt_sha256'],
        headset_physical_serial=replay['headset_physical_serial'], physical_identity_method=method,
        physical_identity_scope='Relies on explicitly pinned upstream collector serial/campaign audit',
        source_boundary=job['source_boundary'], geometry_reconstructed_from_metadata=True,
        actual_native_vertex_bytes_captured=False, actual_native_push_constant_bytes_captured=False,
        aarch64_raster_geometry_bit_equivalence_verified=False, original_application_rgb_verified=False,
        displayed_pixels_verified=False, visual_assessment='not supplied to this byte comparator',
        full_goal_qualified=False, quality_route_qualified=False, latency_eligible=False, promotion_eligible=False,
        input_artifacts=inputs.reads,
        sources_sha256={p.name: digest(p.read_bytes()) for p in (Path(__file__), Path(quality.__file__))},
        scope='Exact submitted bytes versus independently sourced post-foveation same-device replay; reconstructed geometry and upstream audited acquisition, no original-app or optical-display qualification')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('native-audit', 'job-audit', 'replay-audit'):
        p.add_argument('--' + name, type=Path, required=True)
        p.add_argument('--' + name + '-sha256', required=True)
    p.add_argument('--replay-plan', type=Path, required=True)
    p.add_argument('--replay-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    report = compare(a.native_audit, a.native_audit_sha256, a.job_audit, a.job_audit_sha256,
                     a.replay_audit, a.replay_audit_sha256, a.replay_plan, a.replay_root)
    with a.out.open('x') as f:
        json.dump(report, f, indent=2); f.write('\n')
    print(json.dumps({k: report[k] for k in ('status', 'capture_id', 'exact_submitted_replay_rgb', 'full_goal_qualified')}))
    raise SystemExit(0 if report['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()
