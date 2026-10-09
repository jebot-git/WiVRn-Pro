#!/usr/bin/env python3
"""Expose first/last-new-frame coverage without changing latency policy."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
INTEGRATION=ROOT/'validation/wivrn-codecs/integration'
spec=importlib.util.spec_from_file_location('coverage_native_metrics',INTEGRATION/'native_metrics.py')
metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)
BASELINE_RECEIPT_COMMIT='0296127389e997137e3cece46f4d295c151dc4c1'
RECORDED_MANIFESTS={
    '01-cached':('e73b9d059f67b42863ad46ff81ceb458d8a3b88463aa9c96d0a64539491f750a',298),
    '02-mapped':('0ab8828fb4ff5e8d7a26ea0b1dfbd1ded12dfb43acbbf344993249b8eb75ff99',298),
    '03-mapped':('bb2d24476ad40aaf66b530d3565c0307564d8bbbb6d92b989e80a12806bf4bae',298),
    '04-cached':('26da311a0b022761359515878cece849f28f4aecceaef8fe73a7283e8fecf15f',298),
    '05-closest-traced':('38502bec943be9ad1c778a4775327dd4dbb0a32940b3fcc7946607a26b031371',300),
}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze(rows,warmup_seconds=2):
    native=metrics.evaluate_present(rows,warmup_seconds)
    if native['status']!='pass':raise ValueError('complete coherent presentation history required: '+str(native['errors']))
    warm=native['after_warmup'];cutoff=warm['window_start_xr_ns']
    visible=[r for r in rows if r['submit_xr_ns']>=cutoff]
    if not visible:raise ValueError('nonempty warm observation population required')
    first=[];seen=set()
    for row in rows:
        novel=row['left'] not in seen;seen.add(row['left'])
        if novel and row['submit_xr_ns']>=cutoff:first.append(row)
    times=[r['submit_xr_ns'] for r in first]
    start,end=visible[0]['submit_xr_ns'],visible[-1]['submit_xr_ns'];span=end-start
    unique_span=times[-1]-times[0] if len(times)>1 else 0
    if len(first)!=warm['unique_stereo_pairs'] or (len(times)>1 and unique_span/1e9!=warm['unique_pair_interval_s']):
        raise ValueError('independently rebuilt first-use population differs')
    prefix=times[0]-start if times else span
    suffix=end-times[-1] if times else span
    ages=[r['submit_xr_ns']-min(r['left_receive_xr_ns'],r['right_receive_xr_ns']) for r in visible]
    if any(a<0 for a in ages):raise ValueError('invalid observed stereo age')
    full_rate=max(0,len(first)-1)*1e9/span if span else None
    return dict(schema=1,status='pass',analysis_kind='warm_cadence_coverage_diagnostic',
        warmup_seconds=warmup_seconds,warm_cutoff_xr_ns=cutoff,
        warm_presentations=len(visible),warm_first_encoded_frames=len(first),warm_repeat_presentations=len(visible)-len(first),
        first_warm_presentation_xr_ns=start,last_warm_presentation_xr_ns=end,full_observed_warm_span_ns=span,
        cutoff_to_first_warm_presentation_ns=start-cutoff,
        first_new_frame_xr_ns=times[0] if times else None,last_new_frame_xr_ns=times[-1] if times else None,
        new_frame_endpoint_span_ns=unique_span,observed_prefix_without_new_frame_ns=prefix,
        observed_suffix_without_new_frame_ns=suffix,
        endpoint_coverage_fraction=unique_span/span if span else None,
        original_first_to_last_new_frame_hz=warm['unique_stereo_submission_hz'],
        conservative_full_observation_transition_hz=full_rate,
        maximum_between_new_frames_ns=max((b-a for a,b in zip(times,times[1:])),default=None),
        maximum_observed_submission_gap_ns=max((b['submit_xr_ns']-a['submit_xr_ns'] for a,b in zip(visible,visible[1:])),default=None),
        all_warm_presented_stereo_age_ms=metrics._distribution([a/1e6 for a in ages]),
        original_first_use_metrics=warm,
        native_metric_unchanged=True,latency_gate_changed=False,promotion_eligible=False,headset_operations=False,
        assumptions=[
            'The original metric deliberately uses only global first encoded-frame submissions after warmup and the interval between their first and last timestamps.',
            'The conservative comparison divides count minus one by the full span from first to last observed warm presentation; it is a diagnostic, not a replacement gate.',
            'Prefix and suffix describe observed submissions only; missing process tails or intervals before the first captured row remain unknown.',
            'The cutoff-to-first-warm-submission gap is reported separately; absence of captured submissions is not labeled a repeated-frame hold.',
            'With no new warm frame, prefix and suffix both describe the same whole observation span and must not be summed.',
            'All ages and intervals use the headset-local submission/receipt clocks. Encoded novelty is not source/input novelty and submit is not photon time.'])


def cohort(root):
    root=Path(root).resolve();paths=sorted(root.glob('*/hashes.json'))
    if len(paths)!=5 or {p.parent.name for p in paths}!=set(RECORDED_MANIFESTS):
        raise ValueError('the preserved five-run acquisition population is required')
    controls={str(Path(__file__).relative_to(ROOT)):sha(__file__),
        str(Path(metrics.__file__).relative_to(ROOT)):sha(metrics.__file__)}
    reports=[];count=0
    for manifest_path in paths:
        raw=manifest_path.read_bytes();manifest=json.loads(raw);run=manifest_path.parent
        if (hashlib.sha256(raw).hexdigest(),len(manifest))!=RECORDED_MANIFESTS[run.name]:
            raise ValueError('manifest identity differs from the pinned original acquisition: '+run.name)
        name='client/codec-test-present.jsonl'
        if name not in manifest or 'summary.json' not in manifest:raise ValueError('presentation trace/summary is not acquisition-bound')
        for relative,digest in manifest.items():
            path=(run/relative).resolve()
            if not path.is_relative_to(run) or sha(path)!=digest:raise ValueError('original acquisition differs: '+relative)
        rows=[json.loads(line) for line in (run/name).read_text().splitlines()]
        original=json.loads((run/'summary.json').read_text())
        if original.get('status') not in ('pass','fail'):raise ValueError('original qualification status missing')
        report=analyze(rows)
        if manifest_path.read_bytes()!=raw or any(sha(run/name)!=digest for name,digest in manifest.items()):
            raise ValueError('acquisition changed during coverage audit')
        report.update(run=run.name,original_run_status=original['status'],original_result_overwritten=False,
            original_manifest_sha256=hashlib.sha256(raw).hexdigest(),original_manifest_files_verified=len(manifest))
        reports.append(report);count+=len(manifest)
    if count!=1492:raise ValueError('original manifest-file population differs')
    if any(sha(ROOT/name)!=digest for name,digest in controls.items()):raise ValueError('coverage analyzer changed')
    return dict(schema=1,status='pass',analysis_kind='preserved_five_run_cadence_coverage',runs=reports,
        original_root=str(root),original_manifest_files_verified=count,sources_sha256=controls,
        original_manifest_baseline_receipt_commit=BASELINE_RECEIPT_COMMIT,
        original_files_written=False,latency_gate_evaluated=False,latency_gate_changed=False,headset_operations=False,promotion_eligible=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve()
    if out.is_relative_to(args.root.resolve()):parser.error('output must remain outside immutable acquisition')
    result=cohort(args.root)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps([dict(run=r['run'],prefix_ns=r['observed_prefix_without_new_frame_ns'],suffix_ns=r['observed_suffix_without_new_frame_ns'],
        original_hz=r['original_first_to_last_new_frame_hz'],full_observation_hz=r['conservative_full_observation_transition_hz']) for r in result['runs']]))


if __name__=='__main__':main()
