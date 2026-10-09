#!/usr/bin/env python3
"""Bounded WiVRn/OpenXR correctness smoke tests, separate from full qualification."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import struct
import tarfile
import tempfile
import time
import zlib

if __package__:
    from .gaze_metrics import gaze_timing_metrics
else:
    import importlib.util
    _gaze_spec=importlib.util.spec_from_file_location('codec_gaze_metrics',Path(__file__).with_name('gaze_metrics.py'))
    _gaze_module=importlib.util.module_from_spec(_gaze_spec); _gaze_spec.loader.exec_module(_gaze_module)
    gaze_timing_metrics=_gaze_module.gaze_timing_metrics

PACKAGE = 'org.meumeu.wivrn.codectest'
MODES = ('raw-rgba', 'hevc-carrier', 'vulkan-vr444')
KERNELS = ('baseline', 'coop64', 'coop128')
DECODER_KERNELS = KERNELS + ('coop128dc',)

META_SR_FLAGS = {'off': 0, 'normal': 4, 'quality': 8}

def meta_sr_config(mode):
    # Explicit zeros clear previous test-run sharpening/supersampling settings.
    return {'super_sampling': 0, 'sharpening': META_SR_FLAGS[mode]}

def meta_sr_metrics(rows, requested_mode):
    requested = META_SR_FLAGS[requested_mode]
    result = dict(requested_mode=requested_mode, requested_flags=requested,
                  observations=rows, runtime_effect_verified=False,
                  scope='OpenXR projection-layer hints; no proof of MQSR activation or quality')
    if not rows:
        result['status'] = 'unverified'
    elif any(not r.get('extension_enabled', False) for r in rows) and requested:
        result['status'] = 'unsupported'
    elif any(r.get('requested_flags') != requested or
             (r.get('projection_submitted') and r.get('submitted_flags') != requested) or
             (not r.get('projection_submitted') and r.get('submitted_flags') != 0) for r in rows):
        result['status'] = 'mismatch'
    elif not any(r.get('projection_submitted') for r in rows):
        result['status'] = 'unverified'
    else:
        result['status'] = 'submitted' if requested else 'off'
    return result


def decoder_selection_metrics(decoded, kernel, backend, compression):
    expected={'decoder_kernel':kernel,'decoder_backend':backend,'compression_policy':compression}
    if not decoded or any(any(k not in r for k in expected) for r in decoded.values()):
        return dict(status='unverified', expected=expected, errors=['missing effective decoder telemetry'])
    mismatches=sum(any(r[k] != v for k,v in expected.items()) for r in decoded.values())
    return dict(status='mismatch' if mismatches else 'verified', expected=expected,
                checked_eye_frames=len(decoded), mismatched_eye_frames=mismatches,
                errors=['effective decoder selection mismatch'] if mismatches else [])


def compression_metrics(encoded, decoded, requested):
    errors = []
    required = ('compression_requested', 'compression_attempted', 'compression_applied', 'uncompressed_payload_bytes', 'wire_payload_bytes')
    if not encoded or any(any(k not in r for k in required) for r in encoded.values()):
        return dict(status='unverified', requested=requested, errors=['missing compression telemetry'])
    raw_bytes = wire_bytes = compressed = 0
    for key, row in encoded.items():
        raw, wire = row['uncompressed_payload_bytes'], row['wire_payload_bytes']
        applied = row['compression_applied']
        if any(type(row[k]) is not bool for k in ('compression_requested','compression_attempted','compression_applied')):
            errors.append('invalid compression flags'); continue
        if applied and not row['compression_attempted']:
            errors.append('compression applied without attempt')
        if row['compression_attempted'] and requested != 'lz4':
            errors.append('compression attempted while disabled')
        if row['compression_requested'] != (requested == 'lz4'):
            errors.append('sender compression configuration mismatch')
        if not (type(raw) is int and type(wire) is int and 0 < wire <= raw <= 16*1024*1024):
            errors.append('invalid compression sizes'); continue
        if (applied and (requested != 'lz4' or raw-wire < 64)) or (not applied and wire != raw):
            errors.append('invalid compression admission')
        if row.get('payload_bytes') != wire+48:
            errors.append('outer envelope length mismatch')
        raw_bytes += raw; wire_bytes += wire; compressed += bool(applied)
        if key in decoded:
            client = decoded[key]
            if type(client.get('compression_applied')) is not bool or client.get('compression_applied') != applied or type(client.get('decoded_payload_bytes')) is not int or client.get('decoded_payload_bytes') != raw or type(client.get('bytes')) is not int or client.get('bytes') != wire+48:
                errors.append('sender/receiver compression mismatch')
    for key in decoded:
        if key not in encoded: errors.append('decoded frame lacks compression provenance')
    return dict(status='mismatch' if errors else ('active' if compressed else ('off' if requested=='none' else 'raw_only')),
                requested=requested, encoded_eye_frames=len(encoded), compressed_eye_frames=compressed,
                raw_eye_frames=len(encoded)-compressed, original_payload_bytes=raw_bytes, wire_payload_bytes=wire_bytes,
                saved_bytes=raw_bytes-wire_bytes, wire_ratio=wire_bytes/raw_bytes if raw_bytes else None,
                encode_ms=distribution([r['compression_ms'] for r in encoded.values() if 'compression_ms' in r]),
                decode_ms=distribution([r['decompression_ms'] for r in decoded.values() if 'decompression_ms' in r]),
                errors=sorted(set(errors)), scope='payload byte savings; excludes envelope, transport and display latency')


def distribution(values):
    values = sorted(values)
    if not values: return None
    return dict(count=len(values), p50=values[math.ceil(len(values)*.50)-1],
                p95=values[math.ceil(len(values)*.95)-1], p99=values[math.ceil(len(values)*.99)-1], max=values[-1])

def timing_metrics(encoded, decoded, presented):
    result = {}
    for label, rows, fields in [('host', encoded.values(), ('encode_cpu_wall_ms','encode_gpu_ms')),
                               ('client', decoded.values(), ('decode_upload_ms','decode_gpu_ms','queue_wait_ms','cpu_decode_ms','staging_copy_ms','submit_fence_wait_ms'))]:
        for field in fields:
            result[label+'_'+field] = distribution([r[field] for r in rows if field in r
                and (field != 'decode_gpu_ms' or r.get('gpu_timing_valid', True) is True)
                and (field != 'cpu_decode_ms' or r.get('decoder_backend') == 'cpu-neon')])
    # First successful submission per stereo frame only. Reprojection repeats must
    # not inflate sample count. All three timestamps use the headset XrTime clock.
    first = {}
    for row in presented:
        if row['left'] == row['right']: first.setdefault(row['left'], row)
    for stage, name in [('receive', 'receive_to_submit_ms'), ('decode_ready', 'ready_to_submit_ms')]:
        values = []
        for row in first.values():
            for eye in ('left','right'):
                start = row.get(eye+'_'+stage+'_xr_ns',0)
                end = row.get('submit_xr_ns',0)
                if start > 0 and end >= start: values.append((end-start)/1e6)
        result['headset_'+name] = distribution(values)
    times = sorted(row['submit_xr_ns'] for row in first.values() if 'submit_xr_ns' in row)
    result['unique_stereo_submission_hz'] = ((len(times)-1)*1e9/(times[-1]-times[0])) if len(times)>1 and times[-1]>times[0] else None
    result['scope'] = 'Recorded samples include startup; headset receive-to-submit excludes host and transport-before-receipt, and is not motion-to-photon latency.'
    return result

def stereo_metrics(decoded, presented, summaries=()):
    eyes = [{frame for (frame, eye) in decoded if eye == i} for i in (0, 1)]
    common = eyes[0] & eyes[1]
    coherent = {frame for frame in common if decoded[frame, 0]['warp'] == decoded[frame, 1]['warp']}
    submitted = {r['left'] for r in presented if r['left'] == r['right']} & coherent
    total = sum(map(len, eyes))
    counters = {}
    for row in list(decoded.values()) + list(summaries):
        for key, value in row.items():
            # Both eyes report the same shared cumulative counters; never sum.
            if key.startswith('pair_') and isinstance(value, int):
                counters[key] = max(counters.get(key, 0), value)
    return dict(decoded_matching_id_pairs=len(common), decoded_coherent_pairs=len(coherent),
                decoded_eyes_without_coherent_mate=total-2*len(coherent),
                decoded_eye_pair_utilization=2*len(coherent)/total if total else None,
                coherent_pairs_submitted=len(submitted),
                coherent_pair_submission_fraction=len(submitted)/len(coherent) if coherent else None,
                observed_pairing_policies=sorted({r['pairing'] for r in decoded.values() if 'pairing' in r}),
                shared_pair_counter_maxima=counters)


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def command(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, timeout=30, **kwargs)

def records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).exists() else []

def scene_content(rgba):
    channels=(rgba[0::16],rgba[1::16],rgba[2::16])
    colors=len(set(zip(*channels)))
    span=max((max(c)-min(c) for c in channels if c),default=0)
    # hello_xr uses flat-shaded cubes: two separated colors can be legitimate.
    return dict(source_sampled_rgb_colors=colors,source_rgb_span=span,
                source_has_scene_content=colors>=2 and span>=16)

def rrb_metrics(encoded, decoded, rejected, mode, provenance):
    """Check the selected native RRB path and joint envelope cap, not latency."""
    errors = []
    contract = 'post_foveation_rgb_exact' if mode == 'rrb32-q1' else 'decoded_vr444_rgb_exact'
    workers = provenance.get('rrb_workers')
    output = provenance.get('rrb_output')
    if type(workers) is not int or workers not in (2, 4) or output not in ('cpu', 'staged-gpu'):
        errors.append('missing requested RRB worker/output provenance')
    rejected_frames = set()
    for row in rejected:
        if (type(row.get('frame')) is not int or row['frame'] < 0
                or type(row.get('eye')) is not int or row['eye'] not in (0, 1) or row.get('pair_cap') != 262144
                or type(row.get('staged_pair_bytes')) is not int or row['staged_pair_bytes'] < 0
                or row.get('reason') not in ('cap_exceeded', 'incomplete_or_invalid_pair', 'batch_failed_or_unsubmitted')
                or (row.get('reason') == 'cap_exceeded' and row['staged_pair_bytes'] <= 262144)):
            errors.append('invalid RRB pair rejection')
        else:
            rejected_frames.add(row['frame'])
    pairs = {}
    for (frame, eye), row in encoded.items():
        expected = {'effective_codec': 'rrb32', 'source_contract': contract,
                    'rrb_block_rows': 32, 'stereo_envelope_cap': 262144}
        if any(row.get(k) != v for k, v in expected.items()):
            errors.append('sender RRB selection mismatch')
        if mode == 'rrb32-q1' and row.get('quantizer') != 1:
            errors.append('exact source RRB requires effective Q1')
        if mode == 'rrb32-vr444' and (type(provenance.get('custom_q')) is not int
                or row.get('quantizer') != provenance['custom_q']):
            errors.append('RRB VR444 source quantizer differs from request')
        size = row.get('payload_bytes')
        if type(size) is not int or size < 48:
            errors.append('invalid RRB envelope size')
        else:
            pairs.setdefault(frame, {})[eye] = size
    for frame, eyes in pairs.items():
        if set(eyes) == {0, 1} and sum(eyes.values()) > 262144 and frame not in rejected_frames:
            errors.append('unrejected over-cap stereo pair')
    decode_times = {}
    frame_generations = {}
    decoded_eyes = {}
    for (frame, eye), row in decoded.items():
        expected = {'effective_codec': 'rrb32', 'rrb_workers': workers if output == 'cpu' else 0,
                    'configured_rrb_workers': workers, 'rrb_output': output,
                    'rrb_block_rows': 32, 'stereo_envelope_cap': 262144}
        if any(row.get(k) != v for k, v in expected.items()):
            errors.append('receiver RRB selection mismatch')
        if frame in rejected_frames:
            errors.append('rejected stereo pair reached decoder')
        if set(pairs.get(frame, {})) != {0, 1} or sum(pairs.get(frame, {}).values()) > 262144:
            errors.append('decoded RRB lacks admitted bounded stereo source')
        duration = row.get('pair_decode_ms')
        identity = (row.get('rrb_generation'), frame)
        decoded_eyes.setdefault(frame, set()).add(eye)
        if row.get('pair_decode_scope') != 'stereo_once_reported_per_eye':
            errors.append('shared pair duration scope differs')
        if type(identity[0]) is not int or identity[0] <= 0:
            errors.append('missing RRB generation identity')
        elif frame in frame_generations and frame_generations[frame] != identity[0]:
            errors.append('two eyes disagree on shared pair generation')
        else:
            frame_generations[frame] = identity[0]
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            errors.append('invalid shared pair decode duration')
        elif identity in decode_times and decode_times[identity] != duration:
            errors.append('two eyes disagree on shared decode work')
        else:
            decode_times[identity] = duration
    if not encoded or not decoded:
        errors.append('missing actual RRB sender/receiver observations')
    return dict(status='mismatch' if errors else 'verified', errors=sorted(set(errors)),
                source_contract=contract, workers=workers, requested_output=output,
                complete_encoded_pairs=sum(set(v) == {0, 1} for v in pairs.values()),
                partial_private_upload_pairs=sum(eyes != {0, 1} for eyes in decoded_eyes.values()),
                rejected_stereo_pairs=len(rejected_frames), stereo_envelope_cap=262144,
                shared_pair_decode_ms=distribution(list(decode_times.values())),
                scope='Effective RRB codec and joint wire admission; shared pair decode appears in both eyes and is counted once; no displayed quality or latency qualification')


def capture_geometry(output, mode, path, encoded):
    """Keep the fixed latency shape; admit larger explicit Q1 quality captures only."""
    provenance_path = output/'provenance.json'
    provenance = json.loads(provenance_path.read_text()) if provenance_path.is_file() else {}
    if provenance.get('quality_geometry_profile', False) is not True:
        return 576, 640
    if not (mode == 'rrb32-q1' and provenance.get('quality_application') is True
            and provenance.get('quality_capture') is True and provenance.get('source_trace') is True
            and provenance.get('rrb_source') == 'q1' and provenance.get('custom_q') == 1
            and provenance.get('custom_compression') == 'none'
            and type(provenance.get('stream_scale')) in (int, float)
            and provenance['stream_scale'] in (0.4, 0.5, 0.6)
            and provenance.get('geometry') is None):
        raise ValueError('invalid larger Q1 quality geometry profile')
    payload_path = path.with_suffix('.envelope')
    expected = {0.4: [768, 864], 0.5: [960, 1056], 0.6: [1152, 1280]}[provenance['stream_scale']]
    token = 'q1-'+str(expected[0])+'x'+str(expected[1])
    if provenance.get('quality_geometry_requested') != expected or provenance.get('quality_geometry_token') != token:
        raise ValueError('explicit native quality geometry binding missing')
    with payload_path.open('rb') as source:
        raw = source.read(262145)
    if not 48 < len(raw) <= 262144:
        raise ValueError('bounded Q1 envelope required for capture geometry')
    magic, kind, width, height, eye, size, frame, warp, crc, flags = struct.unpack('<6I2Q2I', raw[:48])
    identity = tuple(map(int, path.stem.split('-')))
    event = encoded.get(identity, {})
    if not (magic == 0x31544357 and kind == 4 and eye in (0, 1) and flags == 0
            and (frame, eye) == identity and event.get('warp') == warp
            and event.get('payload_bytes') == len(raw) and event.get('effective_codec') == 'rrb32'
            and event.get('source_contract') == 'post_foveation_rgb_exact'
            and size == len(raw)-48 and zlib.crc32(raw[48:]) == crc
            and 2 <= width <= 4096 and 2 <= height <= 4096 and width*height <= 4194304):
        raise ValueError('invalid bound Q1 capture geometry or identity')
    if [width, height] != expected:
        raise ValueError('requested exact quality geometry did not take effect')
    return width, height


def evaluate(output, mode):
    output = Path(output)
    encoder_rows = [r for p in (output/'host').glob('encode-*.jsonl') for r in records(p)]
    rejected = [r for r in encoder_rows if r.get('type') == 'rrb_pair_rejected']
    encoded = {(r['frame'], r['eye']): r for r in encoder_rows if 'frame' in r and r.get('type') != 'rrb_pair_rejected'}
    decoded = {(r['frame'], r['eye']): r for p in (output/'client/codec-test').glob('decode-*.jsonl') for r in records(p) if 'eye' in r}
    errors = []
    failed = [r for p in (output/'client/codec-test').glob('decode-*.jsonl') for r in records(p) if r.get('failed')]
    if failed: errors.append(f'{len(failed)} decoder errors')
    for key, value in decoded.items():
        if key not in encoded or value['warp'] != encoded[key]['warp']:
            errors.append(f'decode identity mismatch {key}')
    presented = records(output/'client/codec-test-present.jsonl')
    pairs = set()
    for p in presented:
        if p['left'] != p['right']:
            errors.append('stereo mismatch'); continue
        for eye, name in enumerate(('left', 'right')):
            key = (p[name], eye)
            if key not in decoded or decoded[key]['warp'] != p[name+'_warp']:
                errors.append(f'presentation identity mismatch {key}')
        if p.get('left_warp') != p.get('right_warp'):
            errors.append('stereo warp mismatch')
        pairs.add(p['left'])
    samples = []
    for path in sorted((output/'host').glob('[0-9]*-[01].rgba')):
        other = output/'client/codec-test'/path.name
        if not other.exists(): continue
        a, b = path.read_bytes(), other.read_bytes()
        try:
            width, height = capture_geometry(output, mode, path, encoded)
        except (OSError, ValueError, struct.error) as exc:
            errors.append('capture geometry: '+str(exc)); continue
        if len(a) != width*height*4 or len(b) != len(a):
            errors.append('invalid capture size'); continue
        # RGB-only codec comparison. No lens-space or source-to-presented claim.
        squared = sum((x-y)**2 for i,(x,y) in enumerate(zip(a,b)) if i%4 != 3)
        hardware_reference_exact = None
        reference_sha256 = None
        if mode=='hevc-carrier':
            payload=path.with_suffix('.payload')
            if not payload.is_file(): errors.append('missing HEVC payload reference')
            else:
                software=command(['ffmpeg','-v','error','-f','hevc','-i',payload,'-vf','extractplanes=y','-f','rawvideo','-pix_fmt','gray','-'],capture_output=True).stdout
                hardware_reference_exact = software == b[0::4]+b[1::4]+b[2::4]
                if not hardware_reference_exact: errors.append('HEVC hardware/software luma mismatch '+path.name)
        if mode in ('vulkan-vr444', 'rrb32-q1', 'rrb32-vr444'):
            reference=path.with_suffix('.reference.rgba')
            if not reference.is_file(): errors.append('missing explicit codec RGB reference')
            else:
                reference_bytes = reference.read_bytes()
                reference_sha256 = hashlib.sha256(reference_bytes).hexdigest()
                hardware_reference_exact=reference_bytes==b
                if not hardware_reference_exact: errors.append('client/reference decode mismatch '+path.name)
        pixels = len(a)//4
        # A byte-exact black/no-layer frame cannot establish scene correctness.
        # Sample every fourth pixel; this is a scene-content sanity gate, not
        # the full corpus/protected-zone quality metric.
        samples.append(dict(file=path.name, geometry=[width, height], submitted_as_stereo=int(path.stem.split('-')[0]) in pairs, **scene_content(a), exact=a==b, hardware_reference_exact=hardware_reference_exact, rgb_psnr_db=None if squared==0 else 10*math.log10(255**2/(squared/(pixels*3))), host_sha256=hashlib.sha256(a).hexdigest(), client_sha256=hashlib.sha256(b).hexdigest(), reference_sha256=reference_sha256))
    if not encoded: errors.append('no encoded frames')
    if len({tuple(s['geometry']) for s in samples}) > 1:
        errors.append('captured geometry changed within one native run')
    if len(pairs)<5: errors.append('fewer than five distinct stereo frames submitted to OpenXR')
    if len({int(s['file'].split('-')[1].split('.')[0]) for s in samples})<2:
        errors.append('missing paired-eye RGB quality samples')
    if len({int(s['file'].split('-')[1].split('.')[0]) for s in samples if s['submitted_as_stereo']})<2:
        errors.append('missing quality samples from submitted stereo frames for both eyes')
    if len({int(s['file'].split('-')[1].split('.')[0]) for s in samples if s['submitted_as_stereo'] and s['source_has_scene_content']})<2:
        errors.append('missing nondegenerate RGB scene captures for both submitted eyes')
    if mode!='raw-rgba' and any(s['rgb_psnr_db'] is not None and s['rgb_psnr_db']<30 for s in samples): errors.append('RGB quality below 30 dB smoke threshold')
    if mode=='raw-rgba' and any(not s['exact'] for s in samples): errors.append('raw RGBA changed pixels')
    rrb = None
    if mode in ('rrb32-q1', 'rrb32-vr444'):
        provenance = json.loads((output/'provenance.json').read_text())
        rrb = rrb_metrics(encoded, decoded, rejected, mode, provenance)
        if rrb['status'] != 'verified': errors.extend(rrb['errors'])
        if mode == 'rrb32-q1' and any(not s['exact'] for s in samples):
            errors.append('RRB Q1 changed original codec-source RGBA')
    return dict(schema=1, status='pass' if not errors else 'fail', profile='wivrn-correctness-smoke', mode=mode,
                encoded_eye_frames=len(encoded), decoded_eye_frames=len(decoded), unique_stereo_submissions=len(pairs),
                presentation_calls=len(presented), payload_bytes=sum(x['payload_bytes'] for x in encoded.values()),
                quality_samples=samples, errors=errors, timings=timing_metrics(encoded,decoded,presented),
                rrb_selection=rrb,
                stereo=stereo_metrics(decoded,presented,[r for p in (output/'client/codec-test').glob('decode-*.jsonl') for r in records(p) if r.get('type')=='summary']),
                observed_capture_geometries=sorted({tuple(s['geometry']) for s in samples}),
                limits=['Per-eye capture geometry and profile are explicit; TCP over ADB', 'Bounded scene or quality fixture, not full VR corpus',
                        'submission to OpenXR is not measured photon time', 'wall times include CPU copies, startup and capture overhead',
                        'no optical, impairment, thermal soak, or dynamic gaze qualification'])

def run(args):
    if not 1 <= args.frame_stride <= 90: raise ValueError('frame stride must be 1..90')
    if args.unattended: args.scene_space='View'
    if args.compression != 'none' and args.mode != 'vulkan-vr444': raise ValueError('LZ4 wrapping requires vulkan-vr444')
    output = args.out.resolve(); output.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve(); build = source/'build/codec-server'
    server = build/'server/wivrn-pro-server'; manifest = build/'openxr_wivrn-dev.json'
    if not server.is_file() or not manifest.is_file(): raise ValueError('build server and openxr_wivrn first')
    adb = ['adb', '-s', args.serial]
    def ac(*words, **kwargs): return command(adb+list(words), **kwargs)
    installed = ac('shell', 'pm', 'path', PACKAGE, capture_output=True, text=True).stdout
    if not installed.strip(): raise ValueError('install the dedicated test APK first')
    apk_path = installed.strip().splitlines()[0].removeprefix('package:')
    apk_hash = ac('shell','sha256sum',apk_path,capture_output=True,text=True).stdout.split()[0]
    if not re.fullmatch('[0-9a-f]{64}',apk_hash): raise ValueError('invalid installed APK digest')
    reverse = ac('reverse', '--list', capture_output=True, text=True).stdout
    if any(f'tcp:{args.port}' in row.split() for row in reverse.splitlines()): raise ValueError('test port already has an ADB reverse mapping')
    with __import__('socket').socket() as sock: sock.bind(('127.0.0.1', args.port))
    (output/'host').mkdir(); (output/'client').mkdir()
    ac('shell','dumpsys','package','org.meumeu.wivrn.spatial', stdout=(output/'production-package-before.txt').open('w'))
    process = None; logcat = None; reversed_port = False
    state = {'schema':1, 'package':PACKAGE, 'port':args.port, 'restored':False}
    # Short path is necessary for the Monado AF_UNIX socket.
    with tempfile.TemporaryDirectory(prefix='wct-') as runtime:
        runtime = Path(runtime); os.chmod(runtime, 0o700)
        config = {'port':args.port, 'tcp-only':True, 'publish-service':'none', 'wayvr':{'enabled':False},
                  'encoder':{'encoder':'codec-test','device':'/dev/dri/renderD129'},
                  'application':['/usr/bin/timeout','-k','2',str(args.seconds+30),'/usr/bin/stdbuf','-oL','-eL','/usr/bin/hello_xr','-g','Vulkan2','-bm','Opaque','-s',args.scene_space]}
        save(output/'server-config.json',config)
        env = os.environ | {'XDG_RUNTIME_DIR':str(runtime), 'XDG_CONFIG_HOME':str(runtime/'config'),
              'XR_RUNTIME_JSON':str(manifest),'WIVRN_CODEC_TEST':args.mode, 'WIVRN_CODEC_TEST_Q':str(args.q), 'WIVRN_CODEC_TEST_FRAME_STRIDE':str(args.frame_stride), 'VR444_KERNEL':args.kernel,
              'WIVRN_CODEC_TEST_COMPRESSION':args.compression, 'WIVRN_CODEC_TEST_SHADERS':str(source/'assets/codec-test'), 'WIVRN_CODEC_TEST_EVIDENCE':str(output/'host'),
              'XRT_COMPOSITOR_FORCE_GPU_INDEX':'0','VR444_DEVICE':'0','WIVRN_CODEC_TEST_BITRATE':'100000000'}
        save(output/'provenance.json', {'schema':1,'mode':args.mode,'source':str(source),'server_sha256':sha(server),'installed_apk_sha256':apk_hash,
             'shader_sha256':{p.name:sha(p) for p in sorted((source/'assets/codec-test').glob('*.spv'))},
             'runtime_manifest_sha256':sha(manifest),'duration_s':args.seconds,'q':args.q,'geometry':[576,640],'scene_reference_space':args.scene_space,'unattended_head_relative_scene':args.unattended,'user_presence_overridden':False,
             'requested_gpu_index':0,'requested_render_node':'/dev/dri/renderD129','transport':'TCP over ADB reverse',
             'compression_mode':args.compression,'gaze_timing_requested':args.gaze_timing,'meta_sr_mode':args.meta_sr,'meta_sr_requested_flags':META_SR_FLAGS[args.meta_sr],'frame_stride':args.frame_stride,'requested_new_content_fps':90/args.frame_stride,'kernel':args.kernel,'decoder_kernel':args.decoder_kernel or args.kernel,'decoder_backend':args.decoder_backend,'pairing':args.pairing,'headset_serial':args.serial,'effective_target_requires_log_verification':True})
        try:
            logcat=subprocess.Popen(adb+['logcat','-v','threadtime','-s','WiVRn','AdrenoVK-0'],stdout=(output/'wivrn-live-logcat.txt').open('w'))
            ac('shell','am','force-stop',PACKAGE,stdout=subprocess.DEVNULL)
            ac('shell','run-as',PACKAGE,'mkdir','-p','files')
            ac('shell','run-as',PACKAGE,'rm','-rf','files/codec-test','files/codec-test-present.jsonl')
            # Applies only to our dedicated APK. Disable optional biometric capture for this basic profile.
            client_config={'servers':[], 'preferred_refresh_rate':90,'resolution_scale':1.0,'stream_scale':0.3, 'eye_gaze':args.gaze_timing, 'face_tracking':False, 'body_tracking':False, 'microphone':False, 'hand_tracking':False, 'openxr_post_processing':meta_sr_config(args.meta_sr)}
            save(output/'client-config.json',client_config)
            payload=json.dumps(client_config).encode()
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/client.json\'',input=payload)
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-kernel.txt\'',input=((args.decoder_kernel or args.kernel)+'\n').encode())
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-backend.txt\'',input=(args.decoder_backend+'\n').encode())
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-pairing.txt\'',input=(args.pairing+'\n').encode())
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-compression.txt\'',input=(args.compression+'\n').encode())
            ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-gaze-timing.txt\'',input=('1\n' if args.gaze_timing else '0\n').encode())
            ac('reverse',f'tcp:{args.port}',f'tcp:{args.port}'); reversed_port=True
            with (output/'server.log').open('w') as log:
                # Both server and test scene have external deadlines, including if this runner dies.
                process=subprocess.Popen(['/usr/bin/timeout','-k','5',str(args.seconds+45),'dbus-run-session','--',str(server),
                     '-f',str(output/'server-config.json'),'--no-manage-active-runtime','--no-publish-service','--no-encrypt'],
                     env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                time.sleep(1)
                ac('shell','am','start','-n',PACKAGE+'/org.meumeu.wivrn.MainActivity','-a','android.intent.action.VIEW',
                   '-d',f'wivrn+tcp://127.0.0.1:{args.port}',stdout=(output/'launch.txt').open('w'))
                start=time.monotonic()
                while process.poll() is None and time.monotonic()-start<args.seconds: time.sleep(0.5)
                state['server_exit_before_cleanup']=process.poll()
        finally:
            cleanup_errors=[]
            if logcat:
                logcat.terminate(); logcat.wait(timeout=5)
            for words in [('shell','am','force-stop',PACKAGE)]:
                try: ac(*words,stdout=subprocess.DEVNULL)
                except Exception as e: cleanup_errors.append(str(e))
            try:
                ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/codec-test-gaze-timing.txt\'',input=b'0\n')
                if args.gaze_timing and 'client_config' in locals():
                    ac('shell',f'run-as {PACKAGE} sh -c \'cat > files/client.json\'',input=json.dumps(client_config | {'eye_gaze':False}).encode())
            except Exception as e: cleanup_errors.append('reset gaze telemetry: '+str(e))
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL); process.wait()
            if reversed_port:
                try: ac('reverse','--remove',f'tcp:{args.port}')
                except Exception as e: cleanup_errors.append(str(e))
            try:
                with (output/'client.tar').open('wb') as capture:
                    ac('exec-out','run-as',PACKAGE,'tar','-C','files','-cf','-','codec-test','codec-test-present.jsonl',stdout=capture,stderr=subprocess.PIPE)
            except Exception as e: state['capture_error']=str(e)
            archive=output/'client.tar'
            if archive.exists():
                try:
                    with tarfile.open(archive) as tar: tar.extractall(output/'client',filter='data')
                except tarfile.TarError as e: state['capture_error']=str(e)
            ac('logcat','-d','-t','2500',stdout=(output/'logcat.txt').open('w'))
            ac('logcat','-d','-s','WiVRn',stdout=(output/'wivrn-logcat.txt').open('w'))
            ac('shell','dumpsys','package','org.meumeu.wivrn.spatial',stdout=(output/'production-package-after.txt').open('w'))
            state['restored']=not cleanup_errors
            state['cleanup_errors']=cleanup_errors
            save(output/'cleanup.json',state)
    try:
        result=evaluate(output,args.mode)
    except (ValueError, KeyError) as e:
        result={'status':'fail','mode':args.mode,'errors':['invalid evidence: '+str(e)]}
    result['meta_sr'] = meta_sr_metrics(records(output/'client/codec-test/layer-filter.jsonl'), args.meta_sr)
    encoded={(r['frame'],r['eye']):r for p in (output/'host').glob('encode-*.jsonl') for r in records(p) if 'frame' in r}
    decoded={(r['frame'],r['eye']):r for p in (output/'client/codec-test').glob('decode-*.jsonl') for r in records(p) if 'frame' in r and not r.get('failed')}
    result['decoder_selection']=decoder_selection_metrics(decoded,args.decoder_kernel or args.kernel,args.decoder_backend,args.compression)
    if result['decoder_selection']['status'] != 'verified':
        result['status']='fail'; result['errors'].extend(result['decoder_selection']['errors'])
    result['compression']=compression_metrics(encoded,decoded,args.compression)
    if result['compression']['status'] not in ('off','active','raw_only'):
        result['status']='fail'; result['errors'].extend(result['compression']['errors'])
    result['gaze_timing']=gaze_timing_metrics(records(output/'client/codec-test/gaze-timing.jsonl'),args.gaze_timing)
    if result['gaze_timing']['status'] not in ('off','pass'):
        result['status']='fail'; result['errors'].append('gaze timing '+result['gaze_timing']['status']); result['errors'].extend(result['gaze_timing']['errors'])
    if args.meta_sr != 'off' and result['meta_sr']['status'] != 'submitted':
        result['status']='fail'; result['errors'].append('requested Meta filter was not verified as submitted: '+result['meta_sr']['status'])
    if not state['restored']: result['status']='fail'; result['errors'].append('cleanup failed')
    save(output/'summary.json',result)
    save(output/'hashes.json',{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file() and p.name!='hashes.json'})
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
    p.add_argument('--serial',required=True); p.add_argument('--mode',choices=MODES,required=True)
    p.add_argument('--seconds',type=int,default=30); p.add_argument('--port',type=int,default=9767); p.add_argument('--q',type=int,default=8)
    p.add_argument('--frame-stride',type=int,default=12); p.add_argument('--kernel',choices=KERNELS,default='baseline')
    p.add_argument('--decoder-kernel',choices=DECODER_KERNELS,help='override headset kernel independently of host')
    p.add_argument('--decoder-backend',choices=('resident','readback'),default='resident')
    p.add_argument('--pairing',choices=('paired','independent'),default='paired')
    p.add_argument('--meta-sr',choices=tuple(META_SR_FLAGS),default='off',help='request compositor off/normal/quality sharpening hint; quality does not prove MQSR activation')
    p.add_argument('--compression',choices=('none','lz4'),default='none',help='explicit test-only lossless payload wrapping; both ends require this build')
    p.add_argument('--gaze-timing',action='store_true',help='enable eye-gaze feature and bounded timing telemetry, subject to headset permission')
    p.add_argument('--scene-space',choices=('Local','View'),default='Local')
    p.add_argument('--unattended',action='store_true',help='select head-relative View scene for an unworn headset; no display/optical claim')
    args=p.parse_args()
    if not 10<=args.seconds<=120 or not 1024<=args.port<=65535 or not 1<=args.q<=1024: p.error('invalid bounded test parameters')
    result=run(args); print(json.dumps(result,indent=2)); return 0 if result['status']=='pass' else 2
if __name__=='__main__': raise SystemExit(main())
