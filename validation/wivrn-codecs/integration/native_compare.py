#!/usr/bin/env python3
"""Collect isolated native H264/HEVC versus custom VR444 comparison evidence.

Uses the existing dedicated codec-test APK/server, without rebuilding or installing.
Native means WiVRn's normal QSV video encoder and Android MediaCodec decoder, never
hevc-carrier. Shared post-xrEndFrame timestamps are comparable boundaries; native
MediaCodec wall time is not a Vulkan GPU decode timestamp. No optical claim.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tarfile
import tempfile
import time

PACKAGE = 'org.meumeu.wivrn.codectest'
CUSTOM_MODES = ('custom-vr444', 'custom-rrb32')
MODES = ('native-h264', 'native-hevc') + CUSTOM_MODES
CONFIG_FILES = ('client.json', 'codec-test-kernel.txt', 'codec-test-backend.txt',
                'codec-test-pairing.txt', 'codec-test-compression.txt', 'codec-test-gaze-timing.txt',
                'codec-test-selection.txt', 'codec-test-candidate-trace.txt',
                'codec-test-cpu-output.txt', 'codec-test-selection-policy.txt', 'codec-test-publication-trace.txt',
                'codec-test-payload.txt', 'codec-test-rrb-workers.txt', 'codec-test-rrb-output.txt',
                'codec-test-quality-capture.json', 'codec-test-quality-geometry.txt')


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def command(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, timeout=30, **kwargs)


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def pacer_tail_adapter():
    spec = importlib.util.spec_from_file_location('codec_pacer_tail_adapter', Path(__file__).with_name('pacer_tail.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rrb_settings(args):
    """Explicit payload/output selection, including resets for every control run."""
    enabled = args.mode == 'custom-rrb32'
    workers = getattr(args, 'rrb_workers', 4)
    output = getattr(args, 'rrb_output', 'cpu')
    source = getattr(args, 'rrb_source', 'vr444')
    if type(workers) is not int or workers not in (2, 4) or output not in ('cpu', 'staged-gpu') or source not in ('q1', 'vr444'):
        raise ValueError('invalid RRB workers/output/source')
    if enabled:
        if args.decoder_backend != 'cpu-neon' or getattr(args, 'cpu_output', 'cached-copy') != 'cached-copy':
            raise ValueError('RRB32 requires explicit CPU NEON coordinator with cached-copy control')
        if args.compression != 'none' or (source == 'q1' and args.q != 1):
            raise ValueError('RRB32 uses internal block compression; exact source mode requires Q1')
    elif output != 'cpu' or workers != 4 or source != 'vr444':
        raise ValueError('RRB-specific options require custom-rrb32')
    return {'codec-test-payload.txt': 'rrb32' if enabled else 'vr444',
            'codec-test-rrb-workers.txt': str(workers), 'codec-test-rrb-output.txt': output}


def rrb_source_helpers(args, source=None):
    """Pin the complete RRB adapter and native callsites before device access."""
    if args.mode != 'custom-rrb32':
        return ()
    overlay = Path(__file__).with_name('source-overlay')
    names = ['common/CMakeLists.txt', 'client/CMakeLists.txt',
             'common/codec_test/wire.h', 'common/codec_test/metadata.h',
             'common/codec_test/test_mode.h', 'common/codec_test/stereo_publication.h',
             'server/encoder/encode_batch.h', 'server/encoder/video_encoder_test.cpp',
             'server/encoder/video_encoder_test.h', 'client/decoder/test_decoder.cpp',
             'client/decoder/staged_rowblocks_image.h', 'client/decoder/staged_rowblocks_image.cpp',
             'client/decoder/staged_rowblocks_image.comp',
             'assets/codec-test/staged-rowblocks-image.spv']
    names += [str(p.relative_to(overlay)) for p in sorted((overlay/'common/codec_test/rrb').rglob('*')) if p.is_file()]
    if source is not None:
        for name in names:
            path = Path(source)/name
            if not path.is_file() or sha(path) != sha(overlay/name):
                raise ValueError('RRB native source is absent or differs from reviewed overlay: '+name)
    return tuple(names)


def experimental_settings(args):
    """Explicit experimental controls; default choices preserve existing behavior."""
    output = getattr(args, 'cpu_output', 'cached-copy')
    selection = getattr(args, 'selection_policy', 'closest-target')
    if output not in ('cached-copy', 'mapped-direct') or selection not in ('closest-target', 'bounded-newest'):
        raise ValueError('invalid CPU output/selection policy')
    if output == 'mapped-direct' and (args.mode != 'custom-vr444' or args.decoder_backend != 'cpu-neon'):
        raise ValueError('mapped-direct requires custom CPU NEON decoding')
    if selection == 'bounded-newest' and not getattr(args, 'candidate_trace', False):
        raise ValueError('bounded-newest currently requires diagnostic candidate tracing')
    publication_trace_helpers(args)
    pacer_tail_adapter().validate(args)
    quality_stream_scale(args)
    return rrb_settings(args) | quality_settings(args) | {'codec-test-cpu-output.txt': output, 'codec-test-selection-policy.txt': selection,
            'codec-test-publication-trace.txt': '1' if getattr(args, 'publication_trace', False) else '0'}


def quality_stream_scale(args):
    scale = getattr(args, 'quality_stream_scale', None)
    if scale is None:
        return 0.3
    if type(scale) not in (int, float) or scale not in (0.4, 0.5, 0.6):
        raise ValueError('quality stream scale must be 0.4, 0.5 or 0.6')
    if not (getattr(args, 'quality_capture', False) is True
            and getattr(args, 'source_trace', False) is True
            and getattr(args, 'quality_application_plan', None) is not None
            and args.mode == 'custom-rrb32' and getattr(args, 'rrb_source', None) == 'q1'
            and args.q == 1 and args.compression == 'none'):
        raise ValueError('larger stream geometry requires the explicit Q1 application quality profile')
    return float(scale)


def quality_geometry_profile(args):
    scale = quality_stream_scale(args)
    return {0.3: ('off', None), 0.4: ('q1-768x864', [768, 864]),
            0.5: ('q1-960x1056', [960, 1056]), 0.6: ('q1-1152x1280', [1152, 1280])}[scale]


def quality_geometry_helpers(args, source=None):
    token, geometry = quality_geometry_profile(args)
    if geometry is None:
        return ()
    names = ('common/codec_test/quality_geometry.h', 'client/scenes/stream.cpp',
             'server/encoder/encoder_settings.cpp')
    overlay = Path(__file__).with_name('source-overlay')
    if source is not None:
        for name in names:
            if not (Path(source)/name).is_file() or sha(Path(source)/name) != sha(overlay/name):
                raise ValueError('explicit quality geometry source is absent or changed: '+name)
    return names


def quality_settings(args):
    enabled = getattr(args, 'quality_capture', False)
    pairs = getattr(args, 'quality_max_pairs', 6)
    stride = getattr(args, 'quality_frame_stride', 90)
    if type(enabled) is not bool or type(pairs) is not int or not 1 <= pairs <= 8 \
            or type(stride) is not int or not 1 <= stride <= 900:
        raise ValueError('invalid bounded quality capture configuration')
    if enabled and args.mode not in CUSTOM_MODES:
        raise ValueError('submitted quality capture currently requires a custom RGB path')
    if enabled and stride % 90:
        raise ValueError('quality capture stride must align with the 90-frame host reference cadence')
    if not enabled and (pairs != 6 or stride != 90):
        raise ValueError('quality capture limits require --quality-capture')
    return {'codec-test-quality-capture.json': json.dumps(
        dict(enabled=enabled, max_pairs=pairs, frame_stride=stride), separators=(',', ':')),
        'codec-test-quality-geometry.txt': quality_geometry_profile(args)[0]}


def quality_source_helpers(args, source=None):
    if not getattr(args, 'quality_capture', False):
        return ()
    names = ('common/codec_test/submitted_capture.h', 'client/scenes/stream_quality_capture.h',
             'client/scenes/stream.cpp', 'client/scenes/stream_defoveator.cpp',
             'client/xr/swapchain.cpp', 'client/xr/swapchain.h')
    overlay = Path(__file__).with_name('source-overlay')
    if source is not None:
        for name in names:
            if not (Path(source)/name).is_file() or not (overlay/name).is_file() \
                    or sha(Path(source)/name) != sha(overlay/name):
                raise ValueError('submitted capture source is absent or differs from reviewed overlay: '+name)
    return names



def pacer_trace_helpers(args, source=None):
    """Pure opt-in validation; optional source preflight occurs before ADB."""
    enabled = getattr(args, 'pacer_trace', False)
    if type(enabled) is not bool:
        raise ValueError('pacer trace control must be boolean')
    if not enabled:
        return ()
    if (args.mode not in CUSTOM_MODES or args.decoder_backend != 'cpu-neon'
            or getattr(args, 'candidate_trace', False) is not True
            or getattr(args, 'source_trace', False) is not True):
        raise ValueError('pacer tracing requires custom CPU NEON plus candidate and source diagnostics')
    names = ('server/compositor/pacer.cpp', 'server/compositor/pacer.h', 'server/compositor/codec_pacer_trace.h')
    if source is not None and any(not (Path(source)/name).is_file() for name in names):
        raise ValueError('pacer diagnostic overlay is absent; native integration/build required before acquisition')
    return names


def publication_trace_helpers(args, source=None):
    """Pure preflight for the separately opt-in client publication ledger."""
    enabled = getattr(args, 'publication_trace', False)
    if type(enabled) is not bool:
        raise ValueError('publication trace control must be boolean')
    if not enabled:
        return ()
    if (args.mode not in CUSTOM_MODES or args.decoder_backend != 'cpu-neon'
            or getattr(args, 'candidate_trace', False) is not True
            or getattr(args, 'source_trace', False) is not True):
        raise ValueError('publication tracing requires custom CPU NEON plus candidate and source diagnostics')
    names = ('client/scenes/stream.cpp', 'client/scenes/stream.h', 'common/codec_test/publication_ledger.h')
    if source is not None and any(not (Path(source)/name).is_file() for name in names):
        raise ValueError('publication diagnostic overlay is absent; native integration/build required before acquisition')
    return names


def evaluate_publication_trace(output, requested):
    """File-only prefix plus configuration/presentation checkpoint validation."""
    output = Path(output)
    result = {'diagnostic_only': True, 'promotion_eligible': False, 'release_ready': False}
    if type(requested) is not bool:
        return result | {'status': 'fail', 'errors': ['publication trace request must be boolean']}
    path = output/'client/codec-test/publication-trace.jsonl'
    try:
        presented = read_rows(output/'client/codec-test-present.jsonl')
        unexpected = path.exists() or any(type(row) is dict and any(k.startswith('publication_trace_') for k in row) for row in presented)
        if not requested:
            return result | {'status': 'fail' if unexpected else 'off',
                             'errors': ['publication trace evidence present while disabled'] if unexpected else []}
        provenance = json.loads((output/'provenance.json').read_text())
        if provenance.get('publication_trace') is not True:
            raise ValueError('publication trace request differs from provenance')
        helpers = publication_trace_helpers(argparse.Namespace(**provenance))
        hashes = provenance.get('source_helper_sha256', {})
        if any(type(hashes.get(name)) is not str or not re.fullmatch('[0-9a-f]{64}', hashes[name]) for name in helpers):
            raise ValueError('publication native source provenance is missing')
        readback = json.loads((output/'client-config-readback.json').read_text())
        if type(readback.get('codec-test-publication-trace.txt')) is not str or readback['codec-test-publication-trace.txt'].strip() != '1':
            raise ValueError('publication trace config readback differs')
        analyzer = Path(__file__).with_name('publication_trace.py')
        spec = importlib.util.spec_from_file_location('codec_publication_trace', analyzer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = module.load(path)
        result = module.evaluate(rows, read_rows(output/'client/codec-test/selection-trace.jsonl'), presented)
        if result['status'] == 'pass':
            checkpoints = [row for row in rows if row['kind'] == 'checkpoint']
            for record, checkpoint in zip(presented, checkpoints):
                expected = dict(publication_trace_enabled=True, publication_trace_records=checkpoint['prefix_records'],
                    publication_trace_epoch=checkpoint['epoch'], publication_trace_dropped=0,
                    publication_trace_valid=True, publication_trace_write_ok=True)
                if any(type(record.get(key)) is not type(value) or record.get(key) != value for key, value in expected.items()):
                    raise ValueError('publication checkpoint differs from presentation ledger evidence')
            # Bind every published identity, including frames evicted between
            # candidate snapshots, to the independent complete host source trace.
            source_analyzer = Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/compositor_freshness/analyze.py'
            source_spec = importlib.util.spec_from_file_location('codec_publication_source_join', source_analyzer)
            source_module = importlib.util.module_from_spec(source_spec)
            source_spec.loader.exec_module(source_module)
            frames = sorted({row['added']['frame'] for row in rows if row['kind'] == 'publish'})
            joined = source_module.evaluate(read_rows(output/'host/source-trace.jsonl'), frames)
            result['published_source_join'] = {k: v for k, v in joined.items() if k != 'system_to_app_frames'}
            result['published_source_join_analyzer_sha256'] = sha(source_analyzer)
            if joined['status'] != 'pass':
                raise ValueError('not every published frame has a valid host source join')
        result['analyzer_sources_sha256'] = {p.name: sha(p) for p in
            (analyzer, Path(module.candidate.__file__), Path(module.candidate._replay.__file__))}
        return result
    except Exception as exc:
        return result | {'status': 'fail', 'errors': [str(exc)]}


def evaluate_pacer_trace(output, requested):
    """Pure file-based diagnostic evaluation; never starts native/device work."""
    output = Path(output)
    if type(requested) is not bool:
        return {'status': 'fail', 'errors': ['pacer trace request must be boolean']}
    path = output/'host/pacer-trace.jsonl'
    if not requested:
        return {'status': 'fail' if path.exists() else 'off'}
    try:
        analyzer = Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/pacer_timing/join_source.py'
        spec = importlib.util.spec_from_file_location('codec_pacer_source_join', analyzer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.evaluate(read_rows(path), read_rows(output/'host/source-trace.jsonl'),
                                 [r['left'] for r in read_rows(output/'client/codec-test-present.jsonl')])
        result['analyzer_sources_sha256'] = {str(p.relative_to(Path(__file__).resolve().parents[3])): sha(p)
            for p in (analyzer, Path(module.pacer.__file__), Path(module.source.__file__))}
        return result
    except Exception as exc:
        return {'status': 'fail', 'diagnostic_only': True, 'promotion_blocked': True, 'errors': [str(exc)]}


def source_trace_complete(path):
    """Bounded footer probe used only while waiting for graceful test teardown."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell()-4096))
            lines = stream.read(4096).splitlines()
        footer = json.loads(lines[-1])
        return type(footer) is dict and footer.get('type') == 'footer' and footer.get('complete') is True
    except (OSError, ValueError, IndexError):
        return False


def build_configuration(args, runtime, source, output):
    """Pure configuration builder, also usable by offline tests/review."""
    pacer_trace_helpers(args)
    publication_trace_helpers(args)
    rrb_settings(args)
    custom = args.mode in CUSTOM_MODES
    codec = 'h264' if args.mode == 'native-h264' else 'h265'
    server_config = {
        'port': args.port, 'tcp-only': True, 'publish-service': 'none',
        'wayvr': {'enabled': False}, 'bit-depth': 8,
        'encoder': {'encoder': 'codec-test' if custom else 'qsv', 'device': '/dev/dri/renderD129'},
        'application': ['/usr/bin/timeout', '-k', '2', str(args.seconds+30),
                        '/usr/bin/stdbuf', '-oL', '-eL', '/usr/bin/hello_xr',
                        '-g', 'Vulkan2', '-bm', 'Opaque', '-s', 'Local'],
    }
    if not custom:
        server_config['encoder']['codec'] = codec
    client_config = {
        'servers': [], 'preferred_refresh_rate': 90, 'fps_divider': args.fps_divider,
        'bitrate_bps': args.bitrate, 'resolution_scale': 1.0, 'stream_scale': quality_stream_scale(args),
        'eye_gaze': False, 'face_tracking': False, 'body_tracking': False,
        'microphone': False, 'hand_tracking': False, 'decoder_low_latency': True,
        'openxr_post_processing': {'super_sampling': 0, 'sharpening': 0},
    }
    # Prevent inherited codec-test activation or capture paths contaminating native.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('WIVRN_CODEC_TEST') and k not in (
               'VR444_KERNEL', 'VR444_DEVICE', 'WIVRN_DUMP_VIDEO', 'WIVRN_CODEC_SOURCE_TRACE',
               'WIVRN_CODEC_PACER_TRACE')}
    env.update({'XDG_RUNTIME_DIR': str(runtime), 'XDG_CONFIG_HOME': str(runtime/'config'),
                'XR_RUNTIME_JSON': str(source/'build/codec-server/openxr_wivrn-dev.json'),
                'XRT_COMPOSITOR_FORCE_GPU_INDEX': '0', 'WIVRN_CODEC_COMPARE': '1'})
    if getattr(args, 'source_trace', False):
        env['WIVRN_CODEC_SOURCE_TRACE'] = str(output/'host/source-trace.jsonl')
    if getattr(args, 'pacer_trace', False):
        env['WIVRN_CODEC_PACER_TRACE'] = str(output/'host/pacer-trace.jsonl')
    env.update(pacer_tail_adapter().configure(args, output))
    if quality_geometry_profile(args)[1] is not None:
        env['WIVRN_CODEC_TEST_QUALITY_GEOMETRY'] = quality_geometry_profile(args)[0]
        env['WIVRN_CODEC_TEST_QUALITY_CAPTURE'] = '1'
    if custom:
        env.update({'WIVRN_CODEC_TEST': ('rrb32-'+getattr(args, 'rrb_source', 'vr444')) if args.mode == 'custom-rrb32' else 'vulkan-vr444', 'WIVRN_CODEC_TEST_Q': str(args.q),
                    'WIVRN_CODEC_TEST_FRAME_STRIDE': '1', 'VR444_KERNEL': 'baseline',
                    'WIVRN_CODEC_TEST_COMPRESSION': args.compression,
                    'WIVRN_CODEC_TEST_SHADERS': str(source/'assets/codec-test'),
                    'WIVRN_CODEC_TEST_EVIDENCE': str(output/'host'), 'VR444_DEVICE': '0',
                    'WIVRN_CODEC_TEST_BITRATE': str(args.bitrate)})
    else:
        env['WIVRN_DUMP_VIDEO'] = str(output/'host/video')
    return server_config, client_config, env


def evaluate_output(output):
    """Optional pure metrics module; raw evidence is retained if unavailable."""
    path = Path(__file__).with_name('native_metrics.py')
    if not path.is_file():
        return {'status': 'unverified', 'errors': ['native_metrics.py unavailable'], 'quality': {'status': 'not_measured'}}
    spec = importlib.util.spec_from_file_location('codec_native_metrics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    presents = module.evaluate_present(read_rows(output/'client/codec-test-present.jsonl'))
    performance = module.evaluate_performance([r['performance'] for r in read_rows(output/'performance-samples.jsonl')])
    errors = []
    if presents.get('unique_stereo_pairs', 0) < 30:
        errors.append('fewer than 30 unique coherent stereo pairs')
    warmed = presents.get('after_warmup', {})
    if warmed.get('unique_stereo_pairs', 0) < 2 or not warmed.get('unique_pair_interval_s') or warmed['unique_pair_interval_s'] <= 0:
        errors.append('insufficient post-warmup cadence interval')
    if performance.get('status') == 'invalid':
        errors.append('invalid performance feed; encode samples cannot support comparison')
    return {'presentation': presents, 'performance': performance,
            'status': 'fail' if errors or presents['status'] != 'pass' else 'pass',
            'comparison_gate_errors': errors, 'quality': {'status': 'not_measured'},
            'metrics_source_sha256': sha(path)}


def acquisition_duration_valid(args):
    quality_profile = getattr(args, 'quality_application_plan', None) is not None and getattr(args, 'quality_capture', False) is True
    return type(args.seconds) is int and 10 <= args.seconds <= (60 if quality_profile else 30)


def run(args):
    experiment_config = experimental_settings(args)
    if args.mode not in MODES or not acquisition_duration_valid(args) or args.fps_divider not in (1, 2):
        raise ValueError('mode/bounded duration (10..30s, application quality up to60s)/fps-divider1|2 required')
    if not 1024 <= args.port <= 65535 or not 1_000_000 <= args.bitrate <= 200_000_000 or not 1 <= args.q <= 1024:
        raise ValueError('invalid port/1..200Mbps bitrate/quantizer')
    if args.decoder_backend not in ('resident', 'cpu-neon') or args.selection_phase not in ('early', 'after-fence'):
        raise ValueError('invalid decoder backend/selection phase')
    if args.mode not in CUSTOM_MODES and args.decoder_backend != 'resident':
        raise ValueError('CPU backend only applies to custom codec modes')
    kernel = 'direct-neon' if args.decoder_backend == 'cpu-neon' else 'coop128dc'
    if args.compression not in ('none', 'lz4'):
        raise ValueError('invalid custom compression')
    source, output = args.source.resolve(), args.out.resolve()
    pacer_helpers = pacer_trace_helpers(args, source)
    publication_helpers = publication_trace_helpers(args, source)
    rrb_helpers = rrb_source_helpers(args, source)
    quality_helpers = quality_source_helpers(args, source)
    geometry_helpers = quality_geometry_helpers(args, source)
    tail_helpers = pacer_tail_adapter().validate(args, source)
    quality_application_module = quality_cached = None
    if getattr(args, 'quality_application_plan', None) is not None or getattr(args, 'quality_application_plan_sha256', None) is not None:
        quality_application_path = Path(__file__).with_name('quality_application.py')
        spec = importlib.util.spec_from_file_location('codec_quality_application', quality_application_path)
        quality_application_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(quality_application_module)
        quality_cached = quality_application_module.preflight(args)
    server = source/'build/codec-server/server/wivrn-pro-server'
    manifest = source/'build/codec-server/openxr_wivrn-dev.json'
    apk = source/'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk'
    reference_binary = source/'build/codec-server/rrb-reference'
    for path in (server, manifest, apk) + ((reference_binary,) if args.mode == 'custom-rrb32' else ()):
        if not path.is_file():
            raise ValueError('existing build artifact missing: '+str(path))
    reference_sha256 = sha(reference_binary) if args.mode == 'custom-rrb32' else None
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('host', 'client', 'config-before'):
        (output/folder).mkdir()
    adb = ['adb', '-s', args.serial]
    def ac(*words, **kwargs):
        return command(adb+list(words), **kwargs)
    def read_config(name):
        result = subprocess.run(adb+['exec-out', 'run-as', PACKAGE, 'cat', 'files/'+name],
                                capture_output=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
        if b'No such file' in result.stderr or b'No such file' in result.stdout:
            return None
        raise RuntimeError('cannot back up dedicated config '+name+': '+result.stderr.decode(errors='replace'))
    def write_config(name, data):
        ac('shell', f"run-as {PACKAGE} sh -c 'cat > files/{name}'", input=data)
    physical_serial = ac('shell', 'getprop', 'ro.serialno', capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', physical_serial):
        raise ValueError('invalid physical headset serial')
    (output/'headset-serial-before.txt').write_text(physical_serial+'\n')
    installed = ac('shell', 'pm', 'path', PACKAGE, capture_output=True, text=True).stdout.strip().splitlines()
    if len(installed) != 1 or not installed[0].startswith('package:'):
        raise ValueError('a single dedicated APK installation is required')
    apk_hash = ac('shell', 'sha256sum', installed[0][8:], capture_output=True, text=True).stdout.split()[0]
    if not re.fullmatch('[0-9a-f]{64}', apk_hash) or apk_hash != sha(apk):
        raise ValueError('installed dedicated APK differs from local build; no install attempted')
    reverse = ac('reverse', '--list', capture_output=True, text=True).stdout
    if any(f'tcp:{args.port}' in line.split() for line in reverse.splitlines()):
        raise ValueError('test port already has an ADB reverse mapping')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
    backups = {name: read_config(name) for name in CONFIG_FILES}
    for name, data in backups.items():
        if data is not None:
            (output/'config-before'/name).write_bytes(data)
    save(output/'config-before.json', {name: data is not None for name, data in backups.items()})
    # The actual production package on this headset, not absent .spatial alias.
    with (output/'production-package-before.txt').open('w') as f:
        ac('shell', 'dumpsys', 'package', 'org.meumeu.wivrn', stdout=f)
    process = logcat = None
    reverse_added = app_touched = False
    handles = []
    state = {'schema': 1, 'package': PACKAGE, 'port': args.port, 'restored': False, 'cleanup_errors': []}
    failure = None
    with tempfile.TemporaryDirectory(prefix='wcn-') as temporary:
        runtime = Path(temporary)
        os.chmod(runtime, 0o700)
        config, client_config, env = build_configuration(args, runtime, source, output)
        quality_application_record = None
        if quality_cached is not None:
            quality_application_record, config['application'] = quality_application_module.configure(quality_cached, runtime, output)
            save(output/'quality-application-execution.json', quality_application_record)
        save(output/'server-config.json', config)
        save(output/'client-config.json', client_config)
        helpers = ('server/encoder/encoder_settings.cpp', 'server/encoder/ffmpeg/video_encoder_va.cpp',
                   'server/encoder/video_encoder.cpp', 'server/driver/performance_feed.cpp',
                   'client/decoder/android/android_decoder.cpp', 'client/scenes/stream.cpp',
                   'server/compositor/foveation.cpp', 'server/compositor/compositor.cpp',
                   'server/driver/app_pacer.cpp', 'server/driver/codec_source_trace.h',
                   'common/codec_test/selection_policy.h', 'common/codec_test/selection_trace.h') + pacer_helpers + publication_helpers + rrb_helpers + quality_helpers + geometry_helpers + tail_helpers
        save(output/'provenance.json', {
            'schema': 1, 'mode': args.mode, 'source': str(source), 'server_sha256': sha(server),
            'installed_apk_sha256': apk_hash, 'runtime_manifest_sha256': sha(manifest),
            'harness_sha256': sha(Path(__file__)),
            'source_helper_sha256': {name: sha(source/name) for name in helpers},
            'comparison_worker_disabled_by_test_only_guard': True,
            'main_source_sha256': sha(source/'server/main.cpp'),
            'shader_sha256': {p.name: sha(p) for p in sorted((source/'assets/codec-test').glob('*.spv'))},
            'headset_serial': args.serial, 'adb_target': args.serial,
            'headset_physical_serial': physical_serial,
            'duration_s': args.seconds,
            'geometry': None if getattr(args, 'quality_stream_scale', None) is not None else [576, 640],
            'quality_geometry_profile': getattr(args, 'quality_stream_scale', None) is not None,
            'quality_geometry_token': quality_geometry_profile(args)[0],
            'quality_geometry_requested': quality_geometry_profile(args)[1],
            'stream_scale': quality_stream_scale(args),
            'geometry_source': 'bound per-eye WCT1 envelope' if getattr(args, 'quality_stream_scale', None) is not None else 'fixed latency profile',
            'requested_display_hz': 90, 'fps_divider': args.fps_divider,
            'requested_new_content_fps': 90/args.fps_divider, 'custom_frame_stride': 1,
            'native_encoder_requested': None if args.mode in CUSTOM_MODES else 'qsv',
            'native_codec_requested': config['encoder'].get('codec'), 'bit_depth_requested': 8,
            'requested_render_node': '/dev/dri/renderD129', 'requested_gpu_index': 0,
            'bitrate_bps': args.bitrate, 'custom_q': args.q,
            'custom_compression': args.compression if args.mode in CUSTOM_MODES else None,
            'custom_decoder': kernel, 'decoder_backend': args.decoder_backend,
            'rrb_source': getattr(args, 'rrb_source', 'vr444') if args.mode == 'custom-rrb32' else None,
            'rrb_workers': getattr(args, 'rrb_workers', 4) if args.mode == 'custom-rrb32' else None,
            'rrb_output': getattr(args, 'rrb_output', 'cpu') if args.mode == 'custom-rrb32' else None,
            'rrb_reference_binary_sha256': reference_sha256,
            'rrb_reference_source_sha256': sha(Path(__file__).with_name('rrb_reference.cpp')) if args.mode == 'custom-rrb32' else None,
            'selection_phase': args.selection_phase, 'candidate_trace': args.candidate_trace,
            'cpu_output': args.cpu_output, 'selection_policy': args.selection_policy,
            'source_trace': args.source_trace, 'pacer_trace': getattr(args, 'pacer_trace', False),
            'pacer_window_frames': getattr(args, 'pacer_window_frames', 0),
            'pacer_tail_adapter_sha256': sha(Path(__file__).with_name('pacer_tail.py')),
            'publication_trace': getattr(args, 'publication_trace', False),
            'quality_capture': getattr(args, 'quality_capture', False),
            'quality_max_pairs': getattr(args, 'quality_max_pairs', 6),
            'quality_frame_stride': getattr(args, 'quality_frame_stride', 90),
            'scene_reference_space': quality_application_record['source_application_space'].title() if quality_application_record is not None else 'Local',
            'quality_application': quality_application_record is not None,
            'quality_application_plan_sha256': getattr(args, 'quality_application_plan_sha256', None),
            'quality_application_adapter_sha256': sha(quality_application_path) if quality_application_record is not None else None,
            'application_source_sha256': sha(source/'client/application.cpp'),
            'host_application_sha256': quality_application_record['executable_sha256'] if quality_application_record is not None else sha(Path('/usr/bin/hello_xr')),
            'application_header_sha256': sha(source/'client/application.h'),
            'direct_decoder_source_sha256': sha(source/'common/codec_test/vr444/direct.cpp'),
            'client_decoder_source_sha256': sha(source/'client/decoder/test_decoder.cpp'),
            'gaze_enabled': False, 'meta_sr_mode': 'off', 'user_presence_overridden': False,
            'transport': 'TCP over ADB reverse', 'native_is_hevc_carrier': False,
            'effective_target_requires_log_verification': True,
            'performance_feed_path': str(runtime/'wivrn-pro/performance.json'),
            'scope': 'Common receive/ready/submit boundaries; native decode wall is not hardware GPU time; no native pixel qualification yet',
        })
        try:
            log = (output/'wivrn-live-logcat.txt').open('w'); handles.append(log)
            logcat = subprocess.Popen(adb+['logcat', '-T', '1', '-v', 'threadtime', '-s', 'WiVRn', 'AdrenoVK-0'], stdout=log)
            ac('shell', 'am', 'force-stop', PACKAGE, stdout=subprocess.DEVNULL)
            app_touched = True
            ac('shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files')
            ac('shell', 'run-as', PACKAGE, 'rm', '-rf', 'files/codec-test', 'files/codec-test-present.jsonl')
            # Native decoder does not create the custom decoder's evidence folder.
            ac('shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files/codec-test')
            settings = {'client.json': json.dumps(client_config), 'codec-test-kernel.txt': kernel,
                        'codec-test-backend.txt': args.decoder_backend, 'codec-test-pairing.txt': 'paired',
                        'codec-test-compression.txt': args.compression if args.mode in CUSTOM_MODES else 'none',
                        'codec-test-gaze-timing.txt': '0', 'codec-test-selection.txt': args.selection_phase,
                        'codec-test-candidate-trace.txt': '1' if args.candidate_trace else '0'}
            settings.update(experiment_config)
            for name, text in settings.items():
                write_config(name, (text+'\n').encode())
            save(output/'client-config-readback.json', {name: read_config(name).decode() for name in settings})
            ac('reverse', f'tcp:{args.port}', f'tcp:{args.port}'); reverse_added = True
            log = (output/'server.log').open('w'); handles.append(log)
            process = subprocess.Popen(['/usr/bin/timeout', '-k', '5', str(args.seconds+45), 'dbus-run-session', '--', str(server),
                                        '-f', str(output/'server-config.json'), '--no-manage-active-runtime', '--no-publish-service', '--no-encrypt'],
                                       env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            time.sleep(1)
            with (output/'launch.txt').open('w') as f:
                ac('shell', 'am', 'start', '-n', PACKAGE+'/org.meumeu.wivrn.MainActivity',
                   '-a', 'android.intent.action.VIEW', '-d', f'wivrn+tcp://127.0.0.1:{args.port}', stdout=f)
            start = time.monotonic()
            pid_observed = False
            pid_attempts = 0
            feed = runtime/'wivrn-pro/performance.json'
            with (output/'performance-samples.jsonl').open('w') as f:
                while process.poll() is None and time.monotonic()-start < args.seconds:
                    if not pid_observed and pid_attempts < 10:
                        pid_attempts += 1
                        pid = subprocess.run(adb+['shell', 'pidof', PACKAGE], capture_output=True, timeout=5, text=True)
                        value = pid.stdout.strip()
                        if pid.returncode == 0 and re.fullmatch(r'[0-9]+(?: [0-9]+)*', value):
                            (output/'app-pid.txt').write_text(value+'\n')
                            save(output/'app-pid-receipt.json', {'sample_monotonic_ns': time.monotonic_ns(), 'pid': value, 'attempts': pid_attempts})
                            pid_observed = True
                    if feed.exists():
                        try:
                            if feed.stat().st_size > 1024*1024:
                                raise ValueError('performance feed exceeds 1MiB bound')
                            value = json.loads(feed.read_text())
                            f.write(json.dumps({'sample_monotonic_ns': time.monotonic_ns(), 'performance': value}, allow_nan=False)+'\n')
                            f.flush()
                        except (FileNotFoundError, json.JSONDecodeError):
                            # Feed is atomically replaced; preserve transient absence count.
                            state['performance_read_retries'] = state.get('performance_read_retries', 0)+1
                    time.sleep(0.2)
            state['server_exit_before_cleanup'] = process.poll()
            state['app_pid_observed'] = pid_observed
        except Exception as exc:
            failure = type(exc).__name__+': '+str(exc)
            state['run_error'] = failure
        finally:
            def cleanup(label, fn):
                try:
                    fn()
                except Exception as exc:
                    state['cleanup_errors'].append(label+': '+str(exc))
            if app_touched:
                cleanup('stop dedicated APK', lambda: ac('shell', 'am', 'force-stop', PACKAGE, stdout=subprocess.DEVNULL))
            if args.source_trace and process:
                # Disconnect lets the compositor export bounded in-memory records.
                # Never prolong headset wearing or turn missing teardown into PASS.
                deadline = time.monotonic()+5
                while time.monotonic() < deadline and not source_trace_complete(output/'host/source-trace.jsonl'):
                    if process.poll() is not None:
                        break
                    time.sleep(.1)
                state['source_trace_footer_before_termination'] = source_trace_complete(output/'host/source-trace.jsonl')
                if getattr(args, 'pacer_trace', False):
                    state['pacer_trace_footer_before_termination'] = source_trace_complete(output/'host/pacer-trace.jsonl')
            if process and process.poll() is None:
                cleanup('stop private server', lambda: os.killpg(process.pid, signal.SIGTERM))
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    cleanup('kill private server', lambda: os.killpg(process.pid, signal.SIGKILL))
                    cleanup('wait private server', lambda: process.wait(timeout=5))
            if logcat:
                cleanup('stop logcat', logcat.terminate)
                cleanup('wait logcat', lambda: logcat.wait(timeout=5))
            if reverse_added:
                cleanup('remove own reverse', lambda: ac('reverse', '--remove', f'tcp:{args.port}'))
            if app_touched:
                for name, data in backups.items():
                    if data is None:
                        cleanup('remove config '+name, lambda name=name: ac('shell', 'run-as', PACKAGE, 'rm', '-f', 'files/'+name))
                    else:
                        cleanup('restore config '+name, lambda name=name, data=data: write_config(name, data))
                def capture():
                    with (output/'client.tar').open('wb') as f:
                        ac('exec-out', 'run-as', PACKAGE, 'tar', '-C', 'files', '-cf', '-',
                           'codec-test', 'codec-test-present.jsonl', stdout=f, stderr=subprocess.PIPE)
                    with tarfile.open(output/'client.tar') as archive:
                        archive.extractall(output/'client', filter='data')
                cleanup('capture client', capture)
            def logs():
                with (output/'wivrn-logcat.txt').open('w') as f:
                    ac('logcat', '-d', '-s', 'WiVRn', stdout=f)
                with (output/'production-package-after.txt').open('w') as f:
                    ac('shell', 'dumpsys', 'package', 'org.meumeu.wivrn', stdout=f)
            cleanup('capture final logs', logs)
            def verify_physical_serial():
                observed = ac('shell', 'getprop', 'ro.serialno', capture_output=True, text=True).stdout.strip()
                (output/'headset-serial-after.txt').write_text(observed+'\n')
                state['physical_headset_unchanged'] = observed == physical_serial
                if not state['physical_headset_unchanged']:
                    raise ValueError('physical headset changed during acquisition')
            cleanup('verify physical headset', verify_physical_serial)
            if quality_application_record is not None:
                def verify_quality_application():
                    actual = sha(Path(quality_application_record['snapshot_path']))
                    quality_application_record['snapshot_sha256_after'] = actual
                    quality_application_record['snapshot_unchanged'] = actual == quality_application_record['executable_sha256']
                    save(output/'quality-application-execution.json', quality_application_record)
                    if not quality_application_record['snapshot_unchanged']:
                        raise ValueError('private quality application changed during execution')
                cleanup('verify private quality application', verify_quality_application)
            for f in handles:
                f.close()
            state['restored'] = not state['cleanup_errors']
            save(output/'cleanup.json', state)
    try:
        result = evaluate_output(output)
    except Exception as exc:
        result = {'status': 'fail', 'errors': ['metrics error: '+str(exc)]}
    if args.mode not in CUSTOM_MODES:
        evidence_path = Path(__file__).with_name('native_evidence.py')
        if evidence_path.is_file():
            try:
                spec = importlib.util.spec_from_file_location('codec_native_evidence', evidence_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                evidence = module.evaluate(output, args.mode)
                save(output/'native-bitstream-evidence.json', evidence)
                result['bitstream_evidence'] = evidence
                result['bitstream_evidence_source_sha256'] = sha(evidence_path)
                if evidence['status'] != 'pass':
                    result['status'] = 'fail'
            except Exception as exc:
                result['status'] = 'fail'
                result['bitstream_evidence_error'] = str(exc)
        else:
            result['status'] = 'unverified'
            result['bitstream_evidence_error'] = 'native_evidence.py unavailable'
    if args.mode in CUSTOM_MODES:
        try:
            live_path = Path(__file__).with_name('live.py')
            spec = importlib.util.spec_from_file_location('codec_custom_live', live_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            custom = module.evaluate(output, ('rrb32-'+getattr(args, 'rrb_source', 'vr444')) if args.mode == 'custom-rrb32' else 'vulkan-vr444')
            encoded = {(r['frame'], r['eye']): r for p in (output/'host').glob('encode-*.jsonl')
                       for r in read_rows(p) if 'frame' in r and r.get('type') != 'rrb_pair_rejected'}
            decoded = {(r['frame'], r['eye']): r for p in (output/'client/codec-test').glob('decode-*.jsonl')
                       for r in read_rows(p) if 'eye' in r and not r.get('failed')}
            custom['decoder_selection'] = module.decoder_selection_metrics(decoded, kernel, args.decoder_backend, args.compression)
            custom['compression'] = module.compression_metrics(encoded, decoded, args.compression)
            if custom['decoder_selection']['status'] != 'verified' or custom['compression']['status'] not in ('off', 'active', 'raw_only'):
                custom['status'] = 'fail'
            runtime_path = Path(__file__).with_name('latency_runtime.py')
            spec = importlib.util.spec_from_file_location('codec_latency_runtime', runtime_path)
            runtime_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime_module)
            custom['runtime_validation'] = runtime_module.evaluate(
                read_rows(output/'client/codec-test-present.jsonl'),
                [r for p in (output/'client/codec-test').glob('decode-*.jsonl')
                 for r in read_rows(p) if r.get('type') != 'summary'],
                args.decoder_backend, args.selection_phase,
                cpu_output=('not_used' if args.mode == 'custom-rrb32' else args.cpu_output)
                    if args.decoder_backend == 'cpu-neon' else 'not_used',
                selection_policy=args.selection_policy,
                rrb_output=getattr(args, 'rrb_output', 'cpu') if args.mode == 'custom-rrb32' else None,
                rrb_workers=getattr(args, 'rrb_workers', 4) if args.mode == 'custom-rrb32' else None)
            custom['runtime_validation_source_sha256'] = sha(runtime_path)
            if args.mode == 'custom-rrb32':
                location = 'actual GPU image readback' if getattr(args, 'rrb_output', 'cpu') == 'staged-gpu' else 'shared pair CPU output'
                custom['decode_output_reference_scope'] = f'RRB32 {location}, compared with explicit host codec reference; source equality separately scored; not final rendered/displayed pixels'
                audit_path = Path(__file__).with_name('rrb_payload_audit.py')
                spec = importlib.util.spec_from_file_location('codec_rrb_payload_audit', audit_path)
                audit_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(audit_module)
                try:
                    custom['rrb_payload_audit'] = audit_module.audit(
                        output, reference_binary, reference_sha256, getattr(args, 'rrb_source', 'vr444'))
                except Exception as exc:
                    custom['rrb_payload_audit'] = {'status': 'fail', 'errors': [str(exc)]}
                    custom['status'] = 'fail'
                custom['rrb_payload_audit_source_sha256'] = sha(audit_path)
            elif args.decoder_backend == 'cpu-neon':
                location = 'mapped staging snapshot after upload completion' if args.cpu_output == 'mapped-direct' else 'cached CPU output before upload'
                custom['decode_output_reference_scope'] = f'CPU NEON RGBA from {location}, compared byte-for-byte with host reference; not a texture readback or displayed pixel check'
            else:
                custom['decode_output_reference_scope'] = 'GPU output-buffer readback compared byte-for-byte with host reference; not a displayed pixel check'
            if custom['runtime_validation']['status'] != 'pass':
                custom['status'] = 'fail'
            save(output/'custom-evidence.json', custom)
            result['custom_evidence'] = custom
            result['custom_evidence_source_sha256'] = sha(live_path)
            if custom['status'] != 'pass':
                result['status'] = 'fail'
        except Exception as exc:
            result['status'] = 'fail'
            result['custom_evidence_error'] = str(exc)
    if not state.get('app_pid_observed'):
        result['status'] = 'fail'
        result['app_pid_error'] = 'dedicated application PID was not observed'
    if quality_cached is not None:
        try:
            audit_path = Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/quality_stimuli/audit.py'
            spec = importlib.util.spec_from_file_location('codec_quality_source_audit', audit_path)
            audit_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(audit_module)
            result['quality_application_audit'] = audit_module.application(output/'app-source')
            result['quality_application_auditor_sha256'] = sha(audit_path)
            if result['quality_application_audit'].get('status') != 'pass':
                result['status'] = 'fail'
        except Exception as exc:
            result['quality_application_audit'] = {'status': 'fail', 'errors': [str(exc)]}
            result['status'] = 'fail'
    result['hardware_selection'] = 'requires run-local encoder/decoder log review; excerpts are not proof alone'
    result.update({'mode': args.mode, 'cleanup_restored': state['restored'], 'run_error': failure,
                   'quality': {'status': 'not_measured'}, 'full_qualification': False})
    if failure or not state['restored']:
        result['status'] = 'fail'
    # Correct evidence collection can pass while the candidate remains too slow.
    # Keep latency explicitly release-blocking without discarding failed candidates.
    if args.mode in CUSTOM_MODES:
        research_path = Path(__file__).with_name('research_latency.py')
        try:
            spec = importlib.util.spec_from_file_location('codec_research_latency', research_path)
            research_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(research_module)
            result['research_latency_single_run'] = research_module.evaluate(
                read_rows(output/'client/codec-test-present.jsonl'),
                json.loads((output/'provenance.json').read_text()))
            result['research_latency_source_sha256'] = sha(research_path)
            result['research_targets_sha256'] = sha(research_path.with_name('RESEARCH-TARGETS.json'))
        except Exception as exc:
            result['research_latency_single_run'] = {'status': 'fail', 'errors': [str(exc)], 'goal_verified': False}
        policy_path = Path(__file__).with_name('latency_policy.py')
        try:
            spec = importlib.util.spec_from_file_location('codec_latency_policy', policy_path)
            policy_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(policy_module)
            result['latency_gate'] = policy_module.evaluate(
                read_rows(output/'client/codec-test-present.jsonl'),
                json.loads((output/'provenance.json').read_text()))
            result['latency_policy_source_sha256'] = sha(policy_path)
            result['promotion_status'] = ('single_run_latency_pass_pending_repeats_and_qualification'
                if result['status'] == 'pass' and result['latency_gate']['status'] == 'pass'
                else 'blocked')
        except Exception as exc:
            result['latency_gate'] = {'status': 'fail', 'errors': [str(exc)]}
            result['promotion_status'] = 'blocked'
        result['release_ready'] = False
    if args.mode == 'custom-vr444' and args.decoder_backend == 'cpu-neon':
        try:
            target_path = Path(__file__).with_name('cpu_target.py')
            spec = importlib.util.spec_from_file_location('codec_cpu_target', target_path)
            target_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(target_module)
            result['preliminary_cpu_target'] = target_module.evaluate(
                read_rows(output/'client/codec-test-present.jsonl'),
                json.loads((output/'provenance.json').read_text()))
            result['preliminary_cpu_target_source_sha256'] = sha(target_path)
            result['preliminary_cpu_target_policy_sha256'] = sha(target_path.with_name('CPU-LATENCY-TARGET.json'))
            if result['preliminary_cpu_target']['status'] != 'pass':
                result['promotion_status'] = 'blocked_preliminary_cpu_target'
        except Exception as exc:
            result['preliminary_cpu_target'] = {'status': 'fail', 'errors': [str(exc)], 'promotion_eligible': False}
            result['status'] = 'fail'
            result['promotion_status'] = 'blocked'
    try:
        trace_path = Path(__file__).with_name('candidate_trace.py')
        spec = importlib.util.spec_from_file_location('codec_candidate_trace', trace_path)
        trace_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trace_module)
        result['candidate_trace'] = trace_module.evaluate(
            read_rows(output/'client/codec-test/selection-trace.jsonl'),
            read_rows(output/'client/codec-test-present.jsonl'), args.candidate_trace, args.selection_policy)
        result['candidate_trace_source_sha256'] = sha(trace_path)
        if result['candidate_trace']['status'] not in ('pass', 'off'):
            result['status'] = 'fail'
            result['promotion_status'] = 'blocked'
    except Exception as exc:
        result['status'] = 'fail'
        result['candidate_trace'] = {'status': 'fail', 'errors': [str(exc)]}
        result['promotion_status'] = 'blocked'
    if args.candidate_trace:
        result['promotion_status'] = 'blocked_diagnostic_trace'
        result['release_ready'] = False
    source_trace_file = output/'host/source-trace.jsonl'
    if args.source_trace:
        try:
            analyzer = Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/compositor_freshness/analyze.py'
            spec = importlib.util.spec_from_file_location('codec_source_freshness', analyzer)
            source_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(source_module)
            result['source_trace'] = source_module.evaluate(
                read_rows(source_trace_file),
                [r['left'] for r in read_rows(output/'client/codec-test-present.jsonl')])
            result['source_trace_analyzer_sha256'] = sha(analyzer)
            if result['source_trace']['status'] != 'pass':
                result['status'] = 'fail'
        except Exception as exc:
            result['source_trace'] = {'status': 'fail', 'errors': [str(exc)]}
            result['status'] = 'fail'
    else:
        result['source_trace'] = {'status': 'fail' if source_trace_file.exists() else 'off'}
        if source_trace_file.exists():
            result['status'] = 'fail'
    result['pacer_trace'] = evaluate_pacer_trace(output, getattr(args, 'pacer_trace', False))
    if result['pacer_trace']['status'] not in ('pass', 'off'):
        result['status'] = 'fail'
        result['promotion_status'] = 'blocked'
    result['pacer_tail'] = pacer_tail_adapter().evaluate(output, getattr(args, 'pacer_window_frames', 0))
    if result['pacer_tail']['status'] not in ('pass', 'off'):
        result['status'] = 'fail'
        result['promotion_status'] = 'blocked'
    result['publication_trace'] = evaluate_publication_trace(output, getattr(args, 'publication_trace', False))
    if result['publication_trace']['status'] not in ('pass', 'off'):
        result['status'] = 'fail'
        result['promotion_status'] = 'blocked'
    if args.mode == 'custom-rrb32' or args.source_trace or getattr(args, 'pacer_trace', False) or getattr(args, 'publication_trace', False) or getattr(args, 'quality_capture', False) or args.cpu_output == 'mapped-direct' or args.selection_policy != 'closest-target':
        result['promotion_status'] = 'blocked_experimental_path'
        result['release_ready'] = False
    # Preserve matching log lines for review without treating a support probe or
    # requested configuration as proof of the active native backend.
    pattern = re.compile(r'encoder|decoder|qsv|vaapi|h\.?264|h\.?265|hevc|bitrate|Arc|low.latency', re.I)
    excerpt = []
    for name in ('server.log', 'wivrn-live-logcat.txt'):
        path = output/name
        if path.exists():
            excerpt.extend(name+': '+line for line in path.read_text(errors='replace').splitlines() if pattern.search(line))
    (output/'hardware-provenance.txt').write_text('\n'.join(excerpt)+'\n')
    save(output/'summary.json', result)
    save(output/'hashes.json', {str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file() and p.name != 'hashes.json'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--port', type=int, default=9767)
    parser.add_argument('--fps-divider', type=int, choices=(1, 2), default=2)
    parser.add_argument('--bitrate', type=int, default=50_000_000)
    parser.add_argument('--q', type=int, default=8)
    parser.add_argument('--compression', choices=('none', 'lz4'), default='lz4', help='custom mode only; native streams are never WPC1 wrapped')
    parser.add_argument('--decoder-backend', choices=('resident', 'cpu-neon'), default='resident')
    parser.add_argument('--rrb-source', choices=('q1', 'vr444'), default='vr444')
    parser.add_argument('--rrb-workers', type=int, choices=(2, 4), default=4)
    parser.add_argument('--rrb-output', choices=('cpu', 'staged-gpu'), default='cpu')
    parser.add_argument('--selection-phase', choices=('early', 'after-fence'), default='early')
    parser.add_argument('--candidate-trace', action='store_true', help='Bounded diagnostic candidate trace; not eligible for latency promotion')
    parser.add_argument('--cpu-output', choices=('cached-copy', 'mapped-direct'), default='cached-copy',
                        help='CPU NEON output destination; mapped-direct remains experimental')
    parser.add_argument('--selection-policy', choices=('closest-target', 'bounded-newest'), default='closest-target',
                        help='bounded-newest requires --candidate-trace and cannot be promoted')
    parser.add_argument('--source-trace', action='store_true', help='Bounded host app/compositor mapping trace; diagnostic only')
    parser.add_argument('--pacer-trace', action='store_true',
                        help='Host pacer budget diagnostics; requires custom CPU NEON plus --candidate-trace and --source-trace')
    parser.add_argument('--pacer-window-frames', type=int, default=0,
                        help='Opt-in recent feedback window, 360..1800; 0 preserves original pacing')
    parser.add_argument('--publication-trace', action='store_true',
                        help='Client publication/eviction prefix diagnostics; requires custom CPU NEON plus --candidate-trace and --source-trace')
    parser.add_argument('--quality-capture', action='store_true', help='Bounded submitted swapchain readback; diagnostic, not latency eligible')
    parser.add_argument('--quality-max-pairs', type=int, default=6)
    parser.add_argument('--quality-frame-stride', type=int, default=90)
    parser.add_argument('--quality-application-plan', type=Path,
                        help='Explicit independently generated application-source fixture plan; quality profile only')
    parser.add_argument('--quality-application-plan-sha256')
    parser.add_argument('--quality-stream-scale', type=float, choices=(0.4, 0.5, 0.6),
                        help='Separate Q1 application quality geometry; never latency eligible')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
