#!/usr/bin/env python3
"""Fail-closed custom-codec latency promotion gate; software smoke scope only."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('latency_native_metrics', HERE/'native_metrics.py')
_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metrics)


def load_policy():
    return json.loads((HERE/'LATENCY-POLICY.json').read_text())


def evaluate(rows, provenance, policy=None):
    policy = load_policy() if policy is None else policy
    errors = []
    if not isinstance(provenance, dict):
        provenance = {}
    for key, expected in policy['profile'].items():
        if provenance.get(key) != expected or type(provenance.get(key)) is bool:
            errors.append('profile mismatch or missing: '+key)
    measured = _metrics.evaluate_present(rows, policy['warmup_seconds'])
    if measured['status'] != 'pass':
        errors.extend(measured['errors'])
    warm = measured['after_warmup']
    if warm['unique_stereo_pairs'] < policy['minimum_unique_pairs']:
        errors.append('insufficient unique stereo pairs')
    if not warm['unique_pair_interval_s'] or warm['unique_pair_interval_s'] < policy['minimum_measured_seconds']:
        errors.append('insufficient measurement duration')
    values = []
    if measured['status'] == 'pass':
        seen = set()
        for row in rows:
            if row['left'] in seen:
                continue
            seen.add(row['left'])
            if row['submit_xr_ns'] >= warm['window_start_xr_ns']:
                # Earliest eye receipt is the start of this complete stereo frame.
                values.append((row['submit_xr_ns'] - min(row['left_receive_xr_ns'], row['right_receive_xr_ns'])) / 1e6)
    values.sort()
    observed = {'unique_stereo_submission_hz': warm['unique_stereo_submission_hz']}
    for pct in (50, 95, 99):
        observed[f'stereo_receive_to_submit_ms_p{pct}'] = values[math.ceil(len(values)*pct/100)-1] if values else None
    for key, limit in policy['limits'].items():
        name, direction = key.rsplit('_', 1)
        value = observed.get(name)
        if type(value) not in (int, float) or not math.isfinite(value):
            errors.append('missing/nonfinite metric: '+name)
        elif (direction == 'max' and value > limit) or (direction == 'min' and value < limit):
            errors.append(f'{name}: {value:.6f} violates {direction} {limit}')
    return dict(status='fail' if errors else 'pass', priority=policy['priority'],
                policy_id=policy['policy_id'], errors=errors, observed=observed,
                limits=policy['limits'], unique_pairs=len(values),
                measured_seconds=warm['unique_pair_interval_s'],
                promotion_eligible=False,
                promotion_scope='One run only; repeated current-build correctness/quality and hardware evidence are also required.',
                optical_motion_to_photon_verified=False,
                metric='First successful stereo submission minus earliest packet receipt among the two eyes, headset-local clock.',
                threshold_status=policy['threshold_status'])


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def inspect_run(directory, build_receipt=None, reference_native=False):
    """Recheck immutable inputs. A reference is never candidate promotion evidence."""
    directory = Path(directory)
    hashes = {}
    manifest = {}

    def read(name, jsonl=False):
        path = directory/name
        hashes[name] = _sha(path)
        if manifest.get(name) != hashes[name]:
            raise ValueError('run manifest does not match '+name)
        text = path.read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()] if jsonl else json.loads(text)

    try:
        manifest = json.loads((directory/'hashes.json').read_text())
        provenance = read('provenance.json')
        summary = read('summary.json')
        rows = read('client/codec-test-present.jsonl', True)
        report = evaluate(rows, provenance)
        errors = report['errors']
        if summary.get('status') != 'pass':
            errors.append('run correctness/evidence summary did not pass')
        for key in ('server_sha256', 'installed_apk_sha256'):
            if not _valid_sha(provenance.get(key)):
                errors.append('missing binary provenance: '+key)
        configuration = {}
        if reference_native:
            if provenance.get('mode') not in ('native-h264', 'native-hevc'):
                errors.append('reference-native requires a native codec run')
        else:
            if provenance.get('mode') != 'custom-vr444':
                errors.append('candidate must be custom-vr444')
            expected = (build_receipt or {}).get('sha256', {})
            for key, name in (('server_sha256', 'build/codec-server/server/wivrn-pro-server'),
                              ('installed_apk_sha256', 'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk')):
                if not _valid_sha(expected.get(name)) or provenance.get(key) != expected[name]:
                    errors.append('candidate does not match explicit build receipt: '+key)
            if provenance.get('candidate_trace') is not False:
                errors.append('diagnostic candidate trace is not eligible for promotion')
            if (summary.get('candidate_trace', {}).get('status') != 'off'
                    or any('render_attempts' in row or any(k.startswith('candidate_trace_') for k in row) for row in rows)
                    or (directory/'client/codec-test/selection-trace.jsonl').exists()):
                errors.append('diagnostic trace evidence cannot masquerade as an untraced candidate')
            backend, phase = provenance.get('decoder_backend'), provenance.get('selection_phase')
            if backend == 'cpu-neon':
                target_path = HERE/'cpu_target.py'
                spec = importlib.util.spec_from_file_location('cpu_target_gate', target_path)
                target_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(target_module)
                target_result = target_module.evaluate(rows, provenance)
                if target_result['status'] != 'pass':
                    errors.extend('CPU target: '+error for error in target_result['errors'])
                if (summary.get('preliminary_cpu_target_source_sha256') != _sha(target_path)
                        or summary.get('preliminary_cpu_target_policy_sha256') != _sha(HERE/'CPU-LATENCY-TARGET.json')):
                    errors.append('CPU target policy/evaluator provenance differs')
            if (provenance.get('cpu_output') != 'cached-copy'
                    or provenance.get('selection_policy') != 'closest-target'
                    or provenance.get('source_trace') is not False):
                errors.append('experimental CPU output, selector or source trace is not eligible for promotion')
            if (summary.get('source_trace', {}).get('status') != 'off'
                    or (directory/'host/source-trace.jsonl').exists()):
                errors.append('source tracing cannot masquerade as an untraced candidate')
            if (provenance.get('pacer_trace', False) is not False
                    or summary.get('pacer_trace', {}).get('status', 'off') != 'off'
                    or (directory/'host/pacer-trace.jsonl').exists()):
                errors.append('pacer tracing cannot masquerade as an untraced candidate')
            kernel = 'direct-neon' if backend == 'cpu-neon' else 'coop128dc'
            fixed = {'custom_decoder': kernel, 'scene_reference_space': 'Local',
                     'gaze_enabled': False, 'meta_sr_mode': 'off', 'user_presence_overridden': False,
                     'native_codec_requested': None, 'native_encoder_requested': None,
                     'native_is_hevc_carrier': False, 'bit_depth_requested': 8,
                     'requested_render_node': '/dev/dri/renderD129', 'requested_gpu_index': 0,
                     'comparison_worker_disabled_by_test_only_guard': True}
            for key, value in fixed.items():
                if key not in provenance or type(provenance[key]) is not type(value) or provenance[key] != value:
                    errors.append('invalid or missing candidate configuration: '+key)
            if backend not in ('resident', 'cpu-neon') or phase not in ('early', 'after-fence'):
                errors.append('invalid or missing backend/selection phase')
            if provenance.get('custom_compression') not in ('none', 'lz4'):
                errors.append('invalid or missing compression policy')
            for key, low, high in (('custom_q', 1, 1024), ('bitrate_bps', 1_000_000, 200_000_000)):
                value = provenance.get(key)
                if type(value) is not int or not low <= value <= high:
                    errors.append('invalid or missing '+key)
            if type(provenance.get('headset_serial')) is not str or not provenance['headset_serial']:
                errors.append('missing headset identity')
            for key, filename in (('harness_sha256', 'native_compare.py'),):
                if provenance.get(key) != _sha(HERE/filename):
                    errors.append('candidate harness source differs: '+filename)
            if summary.get('latency_policy_source_sha256') != _sha(Path(__file__)):
                errors.append('candidate policy source differs')
            for key, filename in (('direct_decoder_source_sha256', 'common/codec_test/vr444/direct.cpp'),
                                  ('client_decoder_source_sha256', 'client/decoder/test_decoder.cpp')):
                if not _valid_sha(expected.get(filename)) or provenance.get(key) != expected[filename]:
                    errors.append('candidate decoder source differs from build receipt: '+filename)
            helpers = provenance.get('source_helper_sha256')
            required_helpers = {'client/scenes/stream.cpp', 'common/codec_test/selection_policy.h',
                                'common/codec_test/selection_trace.h', 'server/driver/app_pacer.cpp',
                                'server/driver/codec_source_trace.h', 'server/compositor/compositor.cpp'}
            if (type(helpers) is not dict or not helpers
                    or not required_helpers.issubset(helpers)
                    or any(not _valid_sha(value) or value != expected.get(name) for name, value in helpers.items())):
                errors.append('missing or mismatched helper source provenance')
            shaders = provenance.get('shader_sha256')
            if type(shaders) is not dict or not shaders or any(not _valid_sha(value) for value in shaders.values()):
                errors.append('missing shader provenance')
            for key in ('main_source_sha256', 'runtime_manifest_sha256'):
                if not _valid_sha(provenance.get(key)):
                    errors.append('missing source/runtime provenance: '+key)
            custom = read('custom-evidence.json')
            if custom != summary.get('custom_evidence') or custom.get('status') != 'pass':
                errors.append('missing, failed or inconsistent custom evidence')
            if custom.get('decoder_selection', {}).get('status') != 'verified':
                errors.append('decoder selection not verified')
            if custom.get('compression', {}).get('status') not in ('off', 'active', 'raw_only'):
                errors.append('compression evidence not verified')
            runtime_path = HERE/'latency_runtime.py'
            if custom.get('runtime_validation_source_sha256') != _sha(runtime_path):
                errors.append('runtime validator source differs')
            if custom.get('runtime_validation', {}).get('status') != 'pass':
                errors.append('recorded runtime validation did not pass')
            decoded = []
            for path in sorted((directory/'client/codec-test').glob('decode-*.jsonl')):
                decoded.extend(r for r in read(str(path.relative_to(directory)), True) if r.get('type') != 'summary')
            spec = importlib.util.spec_from_file_location('latency_runtime_gate', runtime_path)
            runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime)
            validation = runtime.evaluate(rows, decoded, backend, phase,
                                          cpu_output='cached-copy' if backend == 'cpu-neon' else 'not_used',
                                          selection_policy='closest-target')
            if validation['status'] != 'pass':
                errors.extend('runtime: '+error for error in validation['errors'])
            if validation.get('observed_decoder_kernels') != [kernel]:
                errors.append('raw runtime kernel differs from candidate')
            server = read('server-config.json')
            client = read('client-config.json')
            readback = read('client-config-readback.json')
            expected_readback = {'codec-test-kernel.txt': kernel, 'codec-test-backend.txt': backend,
                                'codec-test-pairing.txt': 'paired', 'codec-test-compression.txt': provenance.get('custom_compression'),
                                'codec-test-gaze-timing.txt': '0', 'codec-test-selection.txt': phase,
                                'codec-test-candidate-trace.txt': '0',
                                'codec-test-cpu-output.txt': 'cached-copy',
                                'codec-test-selection-policy.txt': 'closest-target'}
            for key, value in expected_readback.items():
                if type(readback.get(key)) is not str or readback[key].strip() != value:
                    errors.append('client readback mismatch: '+key)
            if json.loads(readback.get('client.json', 'null')) != client:
                errors.append('client readback differs from recorded config')
            client_required = {'preferred_refresh_rate': 90, 'fps_divider': 1,
                               'bitrate_bps': provenance.get('bitrate_bps'), 'resolution_scale': 1.0,
                               'stream_scale': 0.3, 'eye_gaze': False, 'face_tracking': False,
                               'body_tracking': False, 'microphone': False, 'hand_tracking': False,
                               'openxr_post_processing': {'super_sampling': 0, 'sharpening': 0}}
            for key, value in client_required.items():
                if key not in client or type(client[key]) is not type(value) or client[key] != value:
                    errors.append('client config outside policy: '+key)
            application = server.get('application', [])
            if (server.get('tcp-only') is not True or server.get('bit-depth') != 8
                    or server.get('encoder') != {'encoder': 'codec-test', 'device': '/dev/dri/renderD129'}
                    or application[-2:] != ['-s', 'Local'] or '/usr/bin/hello_xr' not in application):
                errors.append('server config outside policy')
            keys = tuple(fixed) + ('headset_serial', 'custom_q', 'custom_compression', 'decoder_backend',
                                  'selection_phase', 'bitrate_bps', 'harness_sha256', 'source_helper_sha256',
                                  'main_source_sha256', 'shader_sha256', 'direct_decoder_source_sha256',
                                  'client_decoder_source_sha256', 'runtime_manifest_sha256', 'candidate_trace',
                                  'application_source_sha256', 'application_header_sha256',
                                  'cpu_output', 'selection_policy', 'source_trace')
            configuration = {key: provenance.get(key) for key in keys}
            configuration.update(client_config=client, client_readback=readback,
                                 server_config={k: v for k, v in server.items() if k != 'port'})
        report.update(status='fail' if errors else 'pass', run=str(directory.resolve()), evidence_sha256=hashes,
                      policy_sha256=_sha(HERE/'LATENCY-POLICY.json'), mode=provenance.get('mode'),
                      server_sha256=provenance.get('server_sha256'), apk_sha256=provenance.get('installed_apk_sha256'),
                      candidate_configuration=configuration, reference_only=reference_native)
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return dict(status='fail', run=str(directory.resolve()), evidence_sha256=hashes,
                    errors=['missing/malformed/unverified run evidence: '+str(exc)], reference_only=reference_native)


def evaluate_runs(directories, build_receipt=None, reference_native=False):
    results = [inspect_run(path, build_receipt, reference_native) for path in directories]
    policy = load_policy()
    enough = len(results) >= policy['minimum_repeated_candidate_runs']
    matching = bool(results) and len({json.dumps({k: x.get(k) for k in
        ('mode', 'server_sha256', 'apk_sha256', 'candidate_configuration')}, sort_keys=True) for x in results}) == 1
    presentation_hashes = [x.get('evidence_sha256', {}).get('client/codec-test-present.jsonl') for x in results]
    unique = bool(results) and None not in presentation_hashes and len(set(presentation_hashes)) == len(results)
    candidate_pass = enough and matching and unique and all(x['status'] == 'pass' for x in results)
    passed = bool(results) and all(x['status'] == 'pass' for x in results) if reference_native else candidate_pass
    return dict(status='pass' if passed else 'fail', priority='mission_critical',
                sufficient_repeats=enough, matching_candidate=matching, unique_runs=unique,
                results=results, release_ready=False, reference_only=reference_native,
                candidate_latency_gate_passed=bool(candidate_pass and not reference_native),
                promotion_eligible=False,
                note='Reference-only reports are never candidates. A candidate pass covers repeated software latency and evidence gates only; optical, displayed visual and thermal qualification remain separate.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--build-receipt', type=Path, help='Required for custom candidates; binds current host/APK hashes.')
    parser.add_argument('--reference-native', action='store_true', help='Historical native latency reference only, never candidate eligibility.')
    args = parser.parse_args()
    if not args.reference_native and args.build_receipt is None:
        parser.error('--build-receipt is required for custom candidate evaluation')
    receipt = json.loads(args.build_receipt.read_text()) if args.build_receipt else None
    report = evaluate_runs(args.runs, receipt, args.reference_native)
    if args.build_receipt:
        report['build_receipt_sha256'] = _sha(args.build_receipt)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    raise SystemExit(0 if report['status'] == 'pass' else 2)
