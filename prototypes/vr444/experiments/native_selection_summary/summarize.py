#!/usr/bin/env python3
"""Read-only replay of the fixed October 9 native selector/output campaigns."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
INTEGRATION = 'validation/wivrn-codecs/integration/'
BASE = '5ce9642eb12f50cefa9443c7d93fdf40d9da7353'
NAMES = {'selector': ['01-hevc-before', '02-closest-traced', '03-bounded-traced',
                      '04-bounded-traced', '05-closest-traced', '06-hevc-after'],
         'output': ['01-cached', '02-mapped', '03-mapped', '04-cached']}
POST = ('analysis_sources_unchanged', 'final_config_matches_initial',
        'own_reverse_absent', 'dedicated_app_stopped', 'production_package_unchanged')
CAMPAIGN_SHA = {'selector': '4113077dc2ef8b0fc730d5a04f32bb052943cd5349e5c21e11f8c6fa3d19c26f',
                'output': '04c979293ad08b6d2cac46bddf2f3ec58372ebf9fed3deba518d52d2e94f0678'}
RUN_MANIFEST_SHA = {
    'output/01-cached': '6e8dd38df674bba1a095e930dec90c309d5b818a77b7ca27f42af645e0170157',
    'output/02-mapped': 'b643ffa1bde7ee46c4bc714ea9ecaa4dd55a0847f6f590b0c36e0e8aff08e811',
    'output/03-mapped': 'aee526bfd5a75d1344e9a52de21dc65f98950f4b530f1535276005711f250e7e',
    'output/04-cached': '39a318745adc45a1623384554234e16256eff89928094b372b0d1a1151cff189',
    'selector/01-hevc-before': 'efa822ac14840e1fe9c54e2f991600b05fbd690f3aee8197517a52fa928a55a6',
    'selector/02-closest-traced': '41481526c4cc636d1f26625a8936a680efa5bb61b67cc6c77f734a5947306030',
    'selector/03-bounded-traced': 'd38c4e7116ad26c92699507f27fe6cf5bc5a41d13aae798f049e6df78541c9c4',
    'selector/04-bounded-traced': '89eaed970adb4552e049be4df3542e6c20aa42673cd0c8d8419dbea1244ac8b5',
    'selector/05-closest-traced': 'b3a1cc38ac0fb3094bbfcafd32703dc7835c910d84820527e09c23e0488f7fd6',
    'selector/06-hevc-after': 'f826290d1739c95c7df3b94435cbcc17ba6aa7dd0b932a7237ebbd0ca46fd6d8'}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Inputs:
    def __init__(self, roots):
        self.roots, self.manifest = roots, {}

    def read(self, root, name, expected=None, kind='json'):
        path = self.roots[root] / name
        require(path.resolve().is_relative_to(self.roots[root].resolve()), 'input escapes root')
        data = path.read_bytes()
        h = digest(data)
        require(expected is None or expected == h, 'hash mismatch: ' + root + '/' + name)
        item = {'path': name, 'bytes': len(data), 'sha256': h}
        key = root + '/' + name
        require(key not in self.manifest or self.manifest[key] == item, 'input changed: ' + key)
        self.manifest[key] = item
        if kind == 'bytes':
            return data
        if kind == 'text':
            return data.decode()
        if kind == 'jsonl':
            return [json.loads(line) for line in data.splitlines() if line.strip()]
        return json.loads(data)

    def unchanged(self):
        for key, item in self.manifest.items():
            root, name = key.split('/', 1)
            data = (self.roots[root] / name).read_bytes()
            require(len(data) == item['bytes'] and digest(data) == item['sha256'], 'input changed: ' + key)


def dist(values):
    v = sorted(values)
    require(v and all(type(x) in (int, float) and math.isfinite(x) for x in v), 'invalid distribution')
    return dict(count=len(v), min=v[0], max=v[-1],
                **{f'p{q}': v[math.ceil(len(v)*q/100)-1] for q in (50, 95, 99)})


def warm_first(rows, cutoff):
    seen, result = set(), []
    for row in rows:
        if row['left'] not in seen and row['submit_xr_ns'] >= cutoff:
            result.append(row)
        seen.add(row['left'])
    return result


def focus(log, pid, cutoff, end):
    events = []
    for line in log.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[2] != pid:
            continue
        match = re.search(r'Session state changed at timestamp (\d+): (XR_SESSION_STATE_\w+) => (XR_SESSION_STATE_\w+)', line)
        if match:
            t, before, after = match.groups()
            events.append({'xr_ns': int(t), 'from': before, 'to': after})
    require(events and [x['xr_ns'] for x in events] == sorted(x['xr_ns'] for x in events), 'missing/unordered same-PID focus events')
    preceding = [x for x in events if x['xr_ns'] <= cutoff]
    require(preceding, 'unknown focus state at warm start')
    warm = [x for x in events if cutoff < x['xr_ns'] <= end]
    focused = preceding[-1]['to'] == 'XR_SESSION_STATE_FOCUSED' and all(x['to'] == 'XR_SESSION_STATE_FOCUSED' for x in warm)
    return {'pid': pid, 'state_at_warm_start': preceding[-1]['to'], 'warm_transitions': warm,
            'continuously_focused_observed': focused,
            'scope': 'Same application PID session events, XR timestamp compared with warm submission window; no cause attribution.'}


def load_module(inputs, name, path):
    inputs.read('repo', path, kind='bytes')
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def campaign(inputs, group, modules, receipt):
    plan = inputs.read('inputs', group + '-plan.json')
    c = inputs.read(group, 'campaign.json', CAMPAIGN_SHA[group])
    require(c['status'] == 'complete' and c['final_check_errors'] == [], 'campaign incomplete')
    require(all(c[k] is True for k in POST), 'campaign postcondition failed')
    require(c['analysis_source_sha256_before'] == c['analysis_source_sha256_after'], 'source snapshot changed')
    require(c['initial_config_sha256'] == c['final_config_sha256'] and len(c['initial_config_sha256']) == 10, 'configuration receipt mismatch')
    require(c['build_files_verified'] == c['build_files_verified_after'] == len(receipt['sha256']) == 70, 'build receipt population mismatch')
    require(c['plan_sha256'] == inputs.manifest['inputs/' + group + '-plan.json']['sha256'], 'plan binding mismatch')
    require(c['runner_sha256'] == inputs.manifest['inputs/native_campaign.py.txt']['sha256'], 'runner binding mismatch')
    require(plan['build_receipt_sha256'] == inputs.manifest['inputs/build-receipt.json']['sha256'], 'build receipt binding mismatch')
    require([r['name'] for r in c['runs']] == [r['name'] for r in plan['runs']] == NAMES[group], 'run population/order mismatch')
    for path, sha in c['analysis_source_sha256_before'].items():
        inputs.read('repo', path, sha, 'bytes')
    command = next(x for x in c['commands'] if x['label'] == 'serial')
    require(command['returncode'] == 0, 'serial command failed')
    serial = inputs.read(group, 'preflight/serial.stdout', command['stdout_sha256'], 'text').strip()
    require(serial == plan['expected_physical_serial'] == '230YC01D8W000E', 'physical serial mismatch')
    inputs.read(group, 'preflight/serial.stderr', command['stderr_sha256'], 'bytes')
    reports = []
    for recorded, planned in zip(c['runs'], plan['runs']):
        name = recorded['name']
        require(recorded['returncode'] == 0 and recorded['evidence_status'] == 'pass'
                and recorded['config_readback_matches_initial'] is True, 'run failed: ' + name)
        manifest = inputs.read(group, name + '/hashes.json', RUN_MANIFEST_SHA[group + '/' + name])

        def read(path, kind='json'):
            require(path in manifest, 'unbound run input: ' + path)
            return inputs.read(group, name + '/' + path, manifest[path], kind)

        summary, provenance = read('summary.json'), read('provenance.json')
        require(recorded['summary_sha256'] == manifest['summary.json'], 'summary campaign binding mismatch')
        require(summary['status'] == 'pass' and summary['run_error'] is None, 'run evidence failed')
        cleanup = read('cleanup.json')
        require(cleanup['restored'] is True and cleanup['cleanup_errors'] == [], 'cleanup failed')
        for key in ('mode', 'decoder_backend', 'cpu_output', 'selection_policy', 'candidate_trace', 'source_trace'):
            require(provenance[key] == planned[key], 'planned profile differs: ' + key)
        for key, path in [('server_sha256', 'build/codec-server/server/wivrn-pro-server'),
                          ('installed_apk_sha256', 'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk'),
                          ('direct_decoder_source_sha256', 'common/codec_test/vr444/direct.cpp'),
                          ('client_decoder_source_sha256', 'client/decoder/test_decoder.cpp')]:
            require(provenance[key] == receipt['sha256'][path], 'build provenance mismatch: ' + key)
        for path, sha in provenance['source_helper_sha256'].items():
            require(receipt['sha256'].get(path) == sha, 'native helper provenance mismatch')
        rows = read('client/codec-test-present.jsonl', 'jsonl')
        measured = modules['policy']._metrics.evaluate_present(rows, modules['policy'].load_policy()['warmup_seconds'])
        require(measured['status'] == 'pass', 'presentation integrity: ' + str(measured['errors']))
        metrics = modules['policy'].evaluate(rows, provenance)
        if provenance['mode'] == 'custom-vr444':
            require(metrics['observed'] == summary['latency_gate']['observed'], 'saved/replayed metrics disagree')
        window = measured['after_warmup']
        warm = warm_first(rows, window['window_start_xr_ns'])
        require(len(warm) == metrics['unique_pairs'], 'warm population mismatch')
        decoded = []
        for path in sorted(manifest):
            if re.fullmatch(r'client/codec-test/decode-\d+\.jsonl', path):
                decoded.extend(r for r in read(path, 'jsonl') if r.get('type') != 'summary')
        native = provenance['mode'] == 'native-hevc'
        runtime = modules['runtime'].evaluate(rows, decoded, 'native' if native else provenance['decoder_backend'],
                        provenance['selection_phase'], cpu_output='not_used' if native else provenance['cpu_output'],
                        selection_policy=provenance['selection_policy'])
        require(runtime['status'] == 'pass', 'runtime integrity: ' + str(runtime['errors']))
        pid = read('app-pid.txt', 'text').strip()
        require(pid == read('app-pid-receipt.json')['pid'] and pid.isdigit(), 'PID receipt mismatch')
        state = focus(read('wivrn-live-logcat.txt', 'text'), pid, window['window_start_xr_ns'], rows[-1]['submit_xr_ns'])
        sizes = [list(map(int, x)) for x in re.findall(r'^\s*size: (\d+)x(\d+)\s*$', read('server.log', 'text'), re.M)]
        require(sizes == [[576, 640], [576, 640], [576, 320]], 'actual stream geometry mismatch')
        warm_all = [r for r in rows if r['submit_xr_ns'] >= window['window_start_xr_ns']]
        periods = [r['predicted_display_period_xr_ns'] for r in warm_all]
        outside = [r for r in warm_all if not 11_000_000 <= r['predicted_display_period_xr_ns'] <= 11_200_000]
        stages = {'receipt_to_ready_ms': [], 'ready_to_selection_ms': [], 'selection_to_submit_ms': []}
        for row in warm:
            receive = min(row[e + '_receive_xr_ns'] for e in ('left', 'right'))
            ready = max(row[e + '_decode_ready_xr_ns'] for e in ('left', 'right'))
            selection, submit = row['selection_end_xr_ns'], row['submit_xr_ns']
            require(receive <= ready <= selection <= submit, 'invalid joined stage order')
            for key, value in zip(stages, (ready - receive, selection - ready, submit - selection)):
                stages[key].append(value / 1e6)
        report = dict(campaign=group, run=name, mode=provenance['mode'], cpu_output=provenance['cpu_output'],
                      selection_policy=provenance['selection_policy'], traced=provenance['candidate_trace'],
                      acquisition_evidence_status=summary['status'], replayed_runtime_status=runtime['status'],
                      full_stereo_metrics=metrics['observed'], warm_unique_pairs=len(warm),
                      warm_seconds=metrics['measured_seconds'], provisional_latency_gate=metrics['status'],
                      sub15_and_88hz_observed=metrics['observed']['stereo_receive_to_submit_ms_p95'] < 15 and
                          metrics['observed']['unique_stereo_submission_hz'] >= 88,
                      stage_ms={key: dist(value) for key, value in stages.items()},
                      warm_period_ns=dist(periods), warm_presentations=len(periods), non_90hz_warm_presentations=len(outside),
                      first_non_90hz_after_warm_s=(outside[0]['submit_xr_ns'] - window['window_start_xr_ns'])/1e9 if outside else None,
                      focus=state, fixed_90hz_comparison_eligible=not outside and state['continuously_focused_observed'],
                      actual_stream_sizes=sizes, start_utc=recorded['start_utc'], finish_utc=recorded['finish_utc'])
        if native:
            bitstream = read('native-bitstream-evidence.json')
            require(bitstream['errors'] == [] and len(bitstream['eyes']) == 2, 'bitstream evidence failed')
            report['hevc_reference'] = {'scope': bitstream['scope'], 'eyes': [
                {k: eye[k] for k in ('codec', 'profile', 'pixel_format', 'width', 'height', 'decoded_frames')}
                for eye in bitstream['eyes']], 'quality_equivalent_to_rgb444': False}
            require(all(e['codec'] == 'hevc' and e['pixel_format'] == 'yuvj420p' and
                        [e['width'], e['height']] == [576, 640] for e in bitstream['eyes']), 'HEVC geometry/format differs')
        else:
            custom = read('custom-evidence.json')
            require(custom == summary['custom_evidence'] and custom['status'] == 'pass', 'custom evidence differs')
            index = {(r['frame'], r['eye'], r['warp']): r for r in decoded}
            require(len(index) == len(decoded), 'duplicate decoded identity')
            eye_rows = [index[(row[eye], n, row[eye + '_warp'])] for row in warm for n, eye in enumerate(('left', 'right'))]
            report['warm_selected_eye_decoder_ms'] = {key: dist([r[key] for r in eye_rows]) for key in
                ('cpu_decode_ms', 'staging_copy_ms', 'decompression_ms', 'decode_upload_ms', 'submit_fence_wait_ms', 'queue_wait_ms', 'capture_snapshot_ms')}
            report['decoder_observed'] = {key: sorted({r[key] for r in eye_rows}) for key in
                ('cpu_output_mode', 'cpu_vector_backend', 'staging_memory_type', 'staging_memory_flags')}
        if provenance['candidate_trace']:
            traces = read('client/codec-test/selection-trace.jsonl', 'jsonl')
            candidate = modules['candidate'].evaluate(traces, rows, True, provenance['selection_policy'])
            require(candidate['status'] == 'pass', 'candidate replay failed: ' + str(candidate['errors']))
            source = modules['source'].evaluate(read('host/source-trace.jsonl', 'jsonl'), [r['left'] for r in rows])
            require(source['status'] == 'pass', 'source replay failed')
            trace_map = {t['selection_end_xr_ns']: t for t in traces}
            require(len(trace_map) == len(traces), 'duplicate selection time')
            selected = [trace_map[r['selection_end_xr_ns']] for r in warm]
            report['candidate'] = {'status': 'pass', 'render_attempt_coverage': candidate['render_attempt_coverage'],
                'warm_differs_from_closest': sum(t['selected'] != t['closest'] for t in selected),
                'selected_target_minus_runtime_ms': dist([(t['selected'][0]['view_display_time_xr_ns'] - t['predicted_display_xr_ns'])/1e6 for t in selected]),
                'selected_target_minus_ready_ms': dist([(t['selected'][0]['view_display_time_xr_ns'] - max(x['ready_xr_ns'] for x in t['selected'] if x['present']))/1e6 for t in selected]),
                'runtime_target_minus_submit_ms': dist([(t['predicted_display_xr_ns'] - r['submit_xr_ns'])/1e6 for t, r in zip(selected, warm)])}
            mapping = {x['system_frame_id']: tuple(sorted((a['pacer_id'], a['app_frame_id']) for a in x['app_frames'])) for x in source['system_to_app_frames']}
            first_receipt = {}
            for r in rows:
                key = mapping[r['left']]
                receive = min(r['left_receive_xr_ns'], r['right_receive_xr_ns'])
                first_receipt[key] = min(receive, first_receipt.get(key, receive))
            report['source_selected_history'] = {'replayed_status': 'pass',
                'older_known_receipt_count': sum(first_receipt[mapping[r['left']]] < min(r['left_receive_xr_ns'], r['right_receive_xr_ns']) for r in warm),
                'earliest_known_receipt_to_submit_ms': dist([(r['submit_xr_ns'] - first_receipt[mapping[r['left']]])/1e6 for r in warm]),
                'scope': 'Hindsight earliest receipt among previously or later selected frames sharing validated app-frame set, including prewarm. Not complete arrival supply, simulation age or host-to-display latency.'}
        else:
            require(summary['candidate_trace']['status'] == summary['source_trace']['status'] == 'off', 'untraced status differs')
            require('client/codec-test/selection-trace.jsonl' not in manifest and
                    'host/source-trace.jsonl' not in manifest, 'untraced manifest contains diagnostic traces')
        reports.append(report)
    return {'status': 'complete', 'physical_serial': serial, 'postconditions': {k: c[k] for k in POST},
            'recorded_build_files_verified_before_and_after': 70,
            'build_verification_scope': 'Saved acquisition receipts; replay does not rebuild or rehash external binaries/native checkout.',
            'runs': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selector-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, default=HERE/'inputs')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--text', type=Path, required=True)
    args = parser.parse_args()
    inputs = Inputs({'selector': args.selector_root, 'output': args.output_root, 'inputs': args.inputs, 'repo': REPO})
    inputs.read('repo', str(Path(__file__).relative_to(REPO)), kind='bytes')
    inputs.read('repo', INTEGRATION+'native_metrics.py', kind='bytes')
    inputs.read('repo', INTEGRATION+'LATENCY-POLICY.json', kind='bytes')
    inputs.read('inputs', 'native_campaign.py.txt', kind='bytes')
    receipt = inputs.read('inputs', 'build-receipt.json')
    modules = {name: load_module(inputs, 'native_report_' + name, path) for name, path in {
        'policy': INTEGRATION+'latency_policy.py', 'runtime': INTEGRATION+'latency_runtime.py',
        'candidate': INTEGRATION+'candidate_trace.py', 'source': 'prototypes/vr444/experiments/compositor_freshness/analyze.py'}.items()}
    result = {'schema': 1, 'status': 'pass', 'scope': 'Read-only integrity replay and development triage of ten fixed native headset runs; no new acquisition, optical latency or promotion.',
              'analyzer_base_commit': BASE, 'promotion_eligible': False,
              'metric': 'Nearest-rank p50/p95/p99 of first successful encoded stereo submission minus earliest eye receipt; two-second warmup; global prewarm seen-frame history retained.',
              'period_classification': 'Descriptive 90 Hz band 11000000..11200000 ns; no original policy alteration.',
              'decoder_stage_scope': 'Pooled per-eye events joined by exact frame/eye/warp to warm first stereo use. Durations are not summed into stereo latency. Quantiles of telescoping presentation stages are not additive.',
              'transport': 'TCP over ADB reverse over wireless ADB; not direct codec Wi-Fi transport.',
              'campaigns': {group: campaign(inputs, group, modules, receipt) for group in NAMES}}
    result['recommendations'] = [
        {'choice': 'Retain closest-target cached-copy as control; do not promote bounded-newest.', 'reason': 'One bounded run contains focus loss and 72 Hz periods and is excluded from fixed-90 comparisons. The other stays 90 Hz but misses 88 unique stereo Hz. Closest brackets drift substantially; no stable causal selector win is established.'},
        {'choice': 'Continue native mapped-direct as an opt-in decoder/output investigation, keep cached-copy control.', 'reason': 'Both output arms have substantial session drift; these four runs do not establish an end-to-end winner. Compare joined decode/copy stages separately. Native VR444 NEON mapped RGBA output is distinct from RRF/RRB LZ4-to-mapped-RGB prototypes.'},
        {'choice': 'Prioritize default-off publication/pacer diagnostics for the next native integration.', 'commit': 'be5fd0bddfc471c8f2243f93b22a85283e618a32', 'reason': 'Closest bracket age increase is observed chiefly between publication and selection, with target/supply phase changing. This does not establish cause or removability. Diagnostic branch needs synchronized overlay/patch and a fresh build; not present in this APK. Do not take the early pacer trace without its initialization fix.'},
        {'choice': 'Retain repaired schema-2 trace analysis and preserve all original evidence statuses.', 'commit': '92e8f3f26f7b8fc16f2f3f92fe1e3f388929f85c', 'reason': 'Replay validates successful-submission traces; intervening/terminal render attempts remain unrecorded. No original failed historical capture is relabeled.'},
        {'choice': 'Keep HEVC as a native hardware compression reference, not RGB444 quality equivalence.', 'reason': 'Actual per-eye streams are 576x640 HEVC Main yuvj420p. No run reaches p95 strictly below 15 ms; traces, samples and these short repeats cannot establish release readiness.'}]
    closest_before, closest_after = result['campaigns']['selector']['runs'][1], result['campaigns']['selector']['runs'][4]
    result['drift_analysis'] = {
        'closest_bracket_ready_to_selection_median_delta_ms': closest_after['stage_ms']['ready_to_selection_ms']['p50'] - closest_before['stage_ms']['ready_to_selection_ms']['p50'],
        'closest_bracket_receipt_to_ready_median_delta_ms': closest_after['stage_ms']['receipt_to_ready_ms']['p50'] - closest_before['stage_ms']['receipt_to_ready_ms']['p50'],
        'closest_bracket_selection_to_submit_median_delta_ms': closest_after['stage_ms']['selection_to_submit_ms']['p50'] - closest_before['stage_ms']['selection_to_submit_ms']['p50'],
        'interpretation': 'Descriptive stage shifts, not additive percentile attribution or a causal scheduling diagnosis. Selected-source reuse is small relative to the bracket-wide age change. Runtime target/submit phase and selected target/readiness phase are separate in each candidate record.'}
    inputs.unchanged()
    result['input_manifest'] = inputs.manifest
    result['input_roots'] = {'selector': '--selector-root', 'output': '--output-root', 'inputs': '--inputs', 'repo': 'repository containing this script'}
    result['all_read_inputs_unchanged'] = True
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    lines = ['Native selector/output development triage (2026-10-09)', '', result['metric'], '',
             'run                           p50 ms    p95 ms    p99 ms    unique Hz   fixed90 focused']
    for group in NAMES:
        for r in result['campaigns'][group]['runs']:
            m = r['full_stereo_metrics']
            lines.append(f"{group+'/'+r['run']:29s} {m['stereo_receive_to_submit_ms_p50']:8.3f} {m['stereo_receive_to_submit_ms_p95']:9.3f} {m['stereo_receive_to_submit_ms_p99']:9.3f} {m['unique_stereo_submission_hz']:10.3f}   {r['fixed_90hz_comparison_eligible']}")
    lines += ['', 'All ten acquisition evidence summaries and replayed runtime contracts pass. No sub-15 result.']
    lines += ['', 'Warm first-use stereo stage medians (receipt->ready / ready->selection / selection->submit), ms:']
    for group in NAMES:
        for r in result['campaigns'][group]['runs']:
            stage = r['stage_ms']
            lines.append(f"{group+'/'+r['run']:29s} " + ' / '.join(f"{stage[k]['p50']:.3f}" for k in ('receipt_to_ready_ms', 'ready_to_selection_ms', 'selection_to_submit_ms')))
    lines += ['', 'Warm selected-eye output-stage medians (CPU decode / staging copy / decode_upload), ms:']
    for r in result['campaigns']['output']['runs']:
        d = r['warm_selected_eye_decoder_ms']
        lines.append(f"{r['run']:14s}" + ' / '.join(f"{d[k]['p50']:.3f}" for k in ('cpu_decode_ms', 'staging_copy_ms', 'decode_upload_ms')))
    lines += ['', 'Closest bracket: ready->selection median rises from 11.245 to 25.777 ms, while receipt->ready falls from 2.431 to 1.888 ms and selection->submit stays near 1.46 ms.',
              'This localizes the observed age change after publication; it does not establish cause or removability. Target/readiness phase and selected-source-history details are retained in JSON.',
              'The first bounded run has 124 warm presentations outside the 90 Hz band plus same-PID focus loss/recovery. Original evidence pass is preserved, but fixed-90 ranking excludes that run.']
    for rec in result['recommendations']:
        lines += ['', rec['choice'], rec['reason']]
    lines += ['', result['decoder_stage_scope'], result['transport'],
              'The JSON binds every file read by portable root/path, byte count and SHA256. External build/binary verification is retained acquisition evidence, not repeated by this replay.',
              'No observed interruption cause is assigned. No displayed-image or optical motion-to-photon qualification.']
    args.text.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status': 'pass', 'runs': 10, 'input_files': len(inputs.manifest), 'promotion_eligible': False}))


if __name__ == '__main__':
    main()
