#!/usr/bin/env python3
"""Exact stereo timeline decomposition of sealed observations; no speedup claim."""
import argparse
import collections
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]
INTEGRATION = ROOT / 'validation/wivrn-codecs/integration'
STAGES = ('receipt_to_stereo_publication_ns', 'publication_to_selection_end_ns',
          'selection_end_to_xr_end_frame_return_ns', 'return_to_log_ns')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load('timeline_runtime', INTEGRATION / 'latency_runtime.py')
metrics = load('timeline_metrics', INTEGRATION / 'native_metrics.py')
trace = load('timeline_trace', INTEGRATION / 'candidate_trace.py')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def distribution(values, divisor=1_000_000):
    values = sorted(values)
    return dict(count=len(values), **{f'p{q}': values[math.ceil(len(values)*q/100)-1] / divisor for q in (50, 95, 99)},
                min=values[0] / divisor, max=values[-1] / divisor) if values else None


def evaluate(presented, decoded, provenance, traces=None, warmup_ns=2_000_000_000, threshold_ns=15_000_000):
    if type(warmup_ns) is not int or warmup_ns < 0 or type(threshold_ns) is not int or threshold_ns <= 0:
        raise ValueError('warmup/threshold must be nonnegative/positive exact integers')
    if not isinstance(provenance, dict):
        raise ValueError('provenance must be an object')
    if type(presented) is not list or type(decoded) is not list or any(
            type(p) is not dict or type(p.get('predicted_display_xr_ns')) is not int
            or not 0 < p['predicted_display_xr_ns'] < (1 << 63) for p in presented):
        raise ValueError('presentation/decoder lists and positive runtime prediction timestamps required')
    observed = runtime.evaluate(presented, decoded, provenance.get('decoder_backend'), provenance.get('selection_phase'),
        cpu_output=provenance.get('cpu_output'), selection_policy=provenance.get('selection_policy'))
    identity = metrics.evaluate_present(presented, warmup_seconds=warmup_ns / 1e9)
    if observed['status'] != 'pass' or identity['status'] != 'pass':
        raise ValueError('runtime/identity evidence failed: ' + '; '.join(observed['errors'] + identity['errors']))
    trace_validation = None
    by_submit = {}
    if traces is not None:
        trace_validation = trace.evaluate(traces, presented, selection_policy=provenance.get('selection_policy'))
        if trace_validation['status'] != 'pass' or len(traces) != len(presented):
            raise ValueError('complete valid trace required for prediction-phase observations')
        by_submit = {p['submit_xr_ns']: t for t, p in zip(traces, presented)}
    first, seen = [], set()
    cutoff = presented[0]['submit_xr_ns'] + warmup_ns
    for p in presented:
        if p['left'] not in seen and p['submit_xr_ns'] >= cutoff:
            first.append(p)
        seen.add(p['left'])
    if not first:
        raise ValueError('no first submissions after warmup')
    stages = collections.defaultdict(list)
    late_stages = collections.defaultdict(list)
    crossing = dict.fromkeys(STAGES, 0)
    timing_rows = []
    predictions = collections.defaultdict(list)
    decodes = {(r['frame'], r['eye']): r for r in decoded}
    selected_decode = collections.defaultdict(list)
    for p in first:
        receipt = min(p['left_receive_xr_ns'], p['right_receive_xr_ns'])
        publication = max(p['left_decode_ready_xr_ns'], p['right_decode_ready_xr_ns'])
        timestamps = (receipt, publication, p['selection_end_xr_ns'], p['end_frame_return_xr_ns'], p['submit_xr_ns'])
        intervals = [b - a for a, b in zip(timestamps, timestamps[1:])]
        total = timestamps[-1] - timestamps[0]
        if any(v < 0 for v in intervals) or sum(intervals) != total:
            raise ValueError('stereo interval decomposition is not exact/nonnegative')
        row = dict(frame=p['left'], total_ns=total, intervals_ns=dict(zip(STAGES, intervals)))
        timing_rows.append(row)
        elapsed = 0
        crossed = False
        for key, value in zip(STAGES, intervals):
            stages[key].append(value)
            if total >= threshold_ns:
                late_stages[key].append(value)
            elapsed += value
            if not crossed and elapsed >= threshold_ns:
                crossing[key] += 1
                crossed = True
        stages['total_ns'].append(total)
        if total >= threshold_ns:
            late_stages['total_ns'].append(total)
        predictions['runtime_target_minus_log_ns'].append(p['predicted_display_xr_ns'] - p['submit_xr_ns'])
        if by_submit:
            selected = by_submit[p['submit_xr_ns']]['selected']
            source_target = selected[0]['view_display_time_xr_ns']
            predictions['wire_target_minus_receipt_ns'].append(source_target - receipt)
            predictions['wire_target_minus_publication_ns'].append(source_target - publication)
            predictions['runtime_target_minus_wire_target_ns'].append(p['predicted_display_xr_ns'] - source_target)
        for eye in (0, 1):
            if provenance['decoder_backend'] == 'native':
                continue
            d = decodes[(p['left'], eye)]
            for key in runtime._DURATIONS:
                selected_decode[key].append(d[key])
    ordered = sorted(timing_rows, key=lambda r: (r['total_ns'], r['frame']))
    rank95 = ordered[math.ceil(len(ordered)*.95)-1]
    return dict(schema=1, status='pass', analysis_kind='observed_stereo_timeline_decomposition',
        warmup_ns=warmup_ns, first_stereo_submissions=len(first), strict_threshold_ns=threshold_ns,
        total_at_or_above_threshold=sum(r['total_ns'] >= threshold_ns for r in timing_rows),
        cumulative_threshold_first_crossing_counts=crossing,
        stage_ms={k: distribution(v) for k, v in stages.items()},
        at_or_above_threshold_stage_ms={k: distribution(v) for k, v in late_stages.items()},
        p95_total_ranked_sample=rank95,
        prediction_phase_ms={k: distribution(v) for k, v in predictions.items()},
        selected_warm_eye_decoder_ms={k: distribution(v, 1) for k, v in selected_decode.items()},
        runtime_validation=observed, identity_validation_status=identity['status'], candidate_trace_validation=trace_validation,
        observed_unique_stereo_hz=identity['after_warmup']['unique_stereo_submission_hz'],
        measured_speedup=False, latency_gate_evaluated=False, qualification_changed=False, promotion_eligible=False,
        limitations=[
            'Receipt-to-publication includes stereo transport completion, queueing, decode/upload, evidence I/O and publication locking.',
            'Publication-to-selection is observed waiting for the selected pair, not a directly removable service cost.',
            'Individual stage percentiles must not be summed; only each exact sample decomposition sums to its total.',
            'Threshold crossing identifies when cumulative observed age reaches the threshold, not a causal blame assignment.',
            'Decoder subdurations overlap/nest and are not added to local-clock intervals.',
            'Prediction phase differences describe estimated targets in the headset-local namespace, not display photons.',
            'Repeated frames count only at their first successful submission, even if later repeats cross the warmup boundary.',
            'Existing diagnostic instrumentation and original pass/fail status are retained; no new hardware result is acquired.',
            'Matched decoder rows establish selected-frame observations only, not complete worker/process-tail accounting.'])


def analyze_run(run):
    run = Path(run).resolve()
    manifest_path = run / 'hashes.json'
    manifest_hash = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    required = {'summary.json', 'provenance.json', 'cleanup.json', 'client/codec-test-present.jsonl',
                'client/codec-test/decode-0.jsonl', 'client/codec-test/decode-1.jsonl'}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ValueError('manifest lacks required runtime evidence')
    for name, digest in manifest.items():
        path = run / name
        if (Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(run)
                or not path.is_file() or type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest) or sha(path) != digest):
            raise ValueError('unsafe or mismatched original manifest entry: ' + name)
    def rows(name):
        return [json.loads(line) for line in (run / name).read_text().splitlines()]
    provenance = json.loads((run / 'provenance.json').read_text())
    original = json.loads((run / 'summary.json').read_text())
    cleanup = json.loads((run / 'cleanup.json').read_text())
    if cleanup.get('restored') is not True or cleanup.get('cleanup_errors'):
        raise ValueError('original cleanup did not pass')
    decoded = []
    terminal_summaries = []
    for eye in (0, 1):
        values = rows(f'client/codec-test/decode-{eye}.jsonl')
        if (not values or any(type(r) is not dict for r in values)
                or any(r.get('type') == 'summary' for r in values[:-1])):
            raise ValueError('decoder rows invalid or summary is nonterminal')
        terminal = values[-1].get('type') == 'summary'
        terminal_summaries.append(terminal)
        decoded.extend(values[:-1] if terminal else values)
    traces = None
    trace_name = 'client/codec-test/selection-trace.jsonl'
    if provenance.get('candidate_trace') is True:
        if trace_name not in manifest:
            raise ValueError('candidate trace is not manifest-bound')
        required.add(trace_name)
        traces = rows(trace_name)
    elif provenance.get('candidate_trace') is not False or (run / trace_name).exists():
        raise ValueError('candidate trace provenance/artifact mismatch')
    result = evaluate(rows('client/codec-test-present.jsonl'), decoded, provenance, traces)
    for name in required:
        if sha(run / name) != manifest[name]:
            raise ValueError('original evidence changed during analysis')
    if sha(manifest_path) != manifest_hash:
        raise ValueError('original manifest changed during analysis')
    result.update(original_run=str(run), original_run_status=original.get('status'),
        original_manifest_sha256=manifest_hash, original_manifest_files_verified=len(manifest),
        original_summary_sha256=manifest['summary.json'], original_result_overwritten=False,
        observed_cpu_output=provenance.get('cpu_output'), observed_candidate_trace=provenance.get('candidate_trace'),
        decoder_terminal_summary_present=terminal_summaries, decoder_process_tail_verified=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if any(out.is_relative_to(run.resolve()) for run in args.runs):
        parser.error('output must be outside every immutable run')
    result = dict(schema=1, status='pass', analysis_kind='sealed_observed_timeline_comparison',
                  runs=[analyze_run(run) for run in args.runs], measured_speedup=False, qualification_changed=False,
                  sources_sha256={str(p.relative_to(ROOT)): sha(p) for p in
                      (Path(__file__), Path(runtime.__file__), Path(metrics.__file__), Path(trace.__file__), INTEGRATION / 'selection_replay.py')})
    with out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps([dict(run=Path(r['original_run']).name, firsts=r['first_stereo_submissions'],
        stages_p95_ms={k: v['p95'] for k, v in r['stage_ms'].items()},
        crossing=r['cumulative_threshold_first_crossing_counts']) for r in result['runs']]))


if __name__ == '__main__':
    main()
