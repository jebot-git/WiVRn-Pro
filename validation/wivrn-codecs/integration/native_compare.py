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
MODES = ('native-h264', 'native-hevc', 'custom-vr444')
CONFIG_FILES = ('client.json', 'codec-test-kernel.txt', 'codec-test-backend.txt',
                'codec-test-pairing.txt', 'codec-test-compression.txt', 'codec-test-gaze-timing.txt',
                'codec-test-selection.txt', 'codec-test-candidate-trace.txt',
                'codec-test-cpu-output.txt', 'codec-test-selection-policy.txt')


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def command(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, timeout=30, **kwargs)


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


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
    return {'codec-test-cpu-output.txt': output, 'codec-test-selection-policy.txt': selection}


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
    custom = args.mode == 'custom-vr444'
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
        'bitrate_bps': args.bitrate, 'resolution_scale': 1.0, 'stream_scale': 0.3,
        'eye_gaze': False, 'face_tracking': False, 'body_tracking': False,
        'microphone': False, 'hand_tracking': False, 'decoder_low_latency': True,
        'openxr_post_processing': {'super_sampling': 0, 'sharpening': 0},
    }
    # Prevent inherited codec-test activation or capture paths contaminating native.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('WIVRN_CODEC_TEST') and k not in (
               'VR444_KERNEL', 'VR444_DEVICE', 'WIVRN_DUMP_VIDEO', 'WIVRN_CODEC_SOURCE_TRACE')}
    env.update({'XDG_RUNTIME_DIR': str(runtime), 'XDG_CONFIG_HOME': str(runtime/'config'),
                'XR_RUNTIME_JSON': str(source/'build/codec-server/openxr_wivrn-dev.json'),
                'XRT_COMPOSITOR_FORCE_GPU_INDEX': '0', 'WIVRN_CODEC_COMPARE': '1'})
    if getattr(args, 'source_trace', False):
        env['WIVRN_CODEC_SOURCE_TRACE'] = str(output/'host/source-trace.jsonl')
    if custom:
        env.update({'WIVRN_CODEC_TEST': 'vulkan-vr444', 'WIVRN_CODEC_TEST_Q': str(args.q),
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


def run(args):
    experiment_config = experimental_settings(args)
    if args.mode not in MODES or not 10 <= args.seconds <= 30 or args.fps_divider not in (1, 2):
        raise ValueError('mode/10..30 seconds/fps-divider1|2 required')
    if not 1024 <= args.port <= 65535 or not 1_000_000 <= args.bitrate <= 200_000_000 or not 1 <= args.q <= 1024:
        raise ValueError('invalid port/1..200Mbps bitrate/quantizer')
    if args.decoder_backend not in ('resident', 'cpu-neon') or args.selection_phase not in ('early', 'after-fence'):
        raise ValueError('invalid decoder backend/selection phase')
    if args.mode != 'custom-vr444' and args.decoder_backend != 'resident':
        raise ValueError('CPU backend only applies to custom VR444')
    kernel = 'direct-neon' if args.decoder_backend == 'cpu-neon' else 'coop128dc'
    if args.compression not in ('none', 'lz4'):
        raise ValueError('invalid custom compression')
    source, output = args.source.resolve(), args.out.resolve()
    server = source/'build/codec-server/server/wivrn-pro-server'
    manifest = source/'build/codec-server/openxr_wivrn-dev.json'
    apk = source/'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk'
    for path in (server, manifest, apk):
        if not path.is_file():
            raise ValueError('existing build artifact missing: '+str(path))
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
        save(output/'server-config.json', config)
        save(output/'client-config.json', client_config)
        helpers = ('server/encoder/encoder_settings.cpp', 'server/encoder/ffmpeg/video_encoder_va.cpp',
                   'server/encoder/video_encoder.cpp', 'server/driver/performance_feed.cpp',
                   'client/decoder/android/android_decoder.cpp', 'client/scenes/stream.cpp',
                   'server/compositor/foveation.cpp', 'server/compositor/compositor.cpp',
                   'server/driver/app_pacer.cpp', 'server/driver/codec_source_trace.h',
                   'common/codec_test/selection_policy.h', 'common/codec_test/selection_trace.h')
        save(output/'provenance.json', {
            'schema': 1, 'mode': args.mode, 'source': str(source), 'server_sha256': sha(server),
            'installed_apk_sha256': apk_hash, 'runtime_manifest_sha256': sha(manifest),
            'harness_sha256': sha(Path(__file__)),
            'source_helper_sha256': {name: sha(source/name) for name in helpers},
            'comparison_worker_disabled_by_test_only_guard': True,
            'main_source_sha256': sha(source/'server/main.cpp'),
            'shader_sha256': {p.name: sha(p) for p in sorted((source/'assets/codec-test').glob('*.spv'))},
            'headset_serial': args.serial, 'duration_s': args.seconds, 'geometry': [576, 640],
            'requested_display_hz': 90, 'fps_divider': args.fps_divider,
            'requested_new_content_fps': 90/args.fps_divider, 'custom_frame_stride': 1,
            'native_encoder_requested': None if args.mode == 'custom-vr444' else 'qsv',
            'native_codec_requested': config['encoder'].get('codec'), 'bit_depth_requested': 8,
            'requested_render_node': '/dev/dri/renderD129', 'requested_gpu_index': 0,
            'bitrate_bps': args.bitrate, 'custom_q': args.q,
            'custom_compression': args.compression if args.mode == 'custom-vr444' else None,
            'custom_decoder': kernel, 'decoder_backend': args.decoder_backend,
            'selection_phase': args.selection_phase, 'candidate_trace': args.candidate_trace,
            'cpu_output': args.cpu_output, 'selection_policy': args.selection_policy,
            'source_trace': args.source_trace,
            'scene_reference_space': 'Local',
            'application_source_sha256': sha(source/'client/application.cpp'),
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
                        'codec-test-compression.txt': args.compression if args.mode == 'custom-vr444' else 'none',
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
            for f in handles:
                f.close()
            state['restored'] = not state['cleanup_errors']
            save(output/'cleanup.json', state)
    try:
        result = evaluate_output(output)
    except Exception as exc:
        result = {'status': 'fail', 'errors': ['metrics error: '+str(exc)]}
    if args.mode != 'custom-vr444':
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
    if args.mode == 'custom-vr444':
        try:
            live_path = Path(__file__).with_name('live.py')
            spec = importlib.util.spec_from_file_location('codec_custom_live', live_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            custom = module.evaluate(output, 'vulkan-vr444')
            encoded = {(r['frame'], r['eye']): r for p in (output/'host').glob('encode-*.jsonl')
                       for r in read_rows(p) if 'frame' in r}
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
                cpu_output=args.cpu_output if args.decoder_backend == 'cpu-neon' else 'not_used',
                selection_policy=args.selection_policy)
            custom['runtime_validation_source_sha256'] = sha(runtime_path)
            if args.decoder_backend == 'cpu-neon':
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
    result['hardware_selection'] = 'requires run-local encoder/decoder log review; excerpts are not proof alone'
    result.update({'mode': args.mode, 'cleanup_restored': state['restored'], 'run_error': failure,
                   'quality': {'status': 'not_measured'}, 'full_qualification': False})
    if failure or not state['restored']:
        result['status'] = 'fail'
    # Correct evidence collection can pass while the candidate remains too slow.
    # Keep latency explicitly release-blocking without discarding failed candidates.
    if args.mode == 'custom-vr444':
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
    if args.source_trace or args.cpu_output == 'mapped-direct' or args.selection_policy != 'closest-target':
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
    parser.add_argument('--selection-phase', choices=('early', 'after-fence'), default='early')
    parser.add_argument('--candidate-trace', action='store_true', help='Bounded diagnostic candidate trace; not eligible for latency promotion')
    parser.add_argument('--cpu-output', choices=('cached-copy', 'mapped-direct'), default='cached-copy',
                        help='CPU NEON output destination; mapped-direct remains experimental')
    parser.add_argument('--selection-policy', choices=('closest-target', 'bounded-newest'), default='closest-target',
                        help='bounded-newest requires --candidate-trace and cannot be promoted')
    parser.add_argument('--source-trace', action='store_true', help='Bounded host app/compositor mapping trace; diagnostic only')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
