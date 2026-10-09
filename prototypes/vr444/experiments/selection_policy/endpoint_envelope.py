#!/usr/bin/env python3
"""Necessary sub-15 p95/cadence bounds over every finite observed endpoint pair."""
import argparse
from bisect import bisect_left, bisect_right
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('endpoint_source_age',HERE/'source_receipt_age.py')
age=importlib.util.module_from_spec(spec);spec.loader.exec_module(age)
cf,q=age.cf,age.q
PHASES=(0,4_387_188,5_488_282,5_555_555,11_111_111)


def window_bound(times,options,cadence_hz=88,minimum_span_ns=10_000_000_000,minimum_pairs=180):
    """Strict-identity good-chain upper bounds; mixed-age feasibility is not solved."""
    if (type(times) not in (list,tuple) or not 2<=len(times)<=10000
            or any(type(t) is not int or not 0<t<2**63 for t in times)
            or any(a>=b for a,b in zip(times,times[1:]))
            or type(options) not in (list,tuple) or len(options)!=len(times)
            or any(type(v) not in (set,frozenset) or any(type(x) is not int or not 0<=x<10000 for x in v) for v in options)
            or any(type(v) is not int or v<=0 for v in (cadence_hz,minimum_span_ns,minimum_pairs))
            or cadence_hz>1000 or minimum_pairs<2):
        raise ValueError('bounded exact monotone times, good identity sets and positive count/span controls required')
    masks=[sum(1<<identity for identity in row) for row in options]
    descending=[sorted(row,reverse=True) for row in options]
    intervals=possible=short=undersampled=0;best=None
    for left in range(len(times)-1):
        mask=masks[left];good_deadlines=bool(mask);tails=[]
        # Descending alternatives prevent a strict LIS from taking two choices
        # from one deadline. Patience tails are an exact good-only chain length.
        for value in descending[left]:
            position=bisect_left(tails,value)
            if position==len(tails):tails.append(value)
            else:tails[position]=value
        for right in range(left+1,len(times)):
            mask|=masks[right];good_deadlines+=bool(masks[right]);span=times[right]-times[left]
            for value in descending[right]:
                position=bisect_left(tails,value)
                if position==len(tails):tails.append(value)
                else:tails[position]=value
            if span<minimum_span_ns:short+=1;continue
            intervals+=1
            required=max(minimum_pairs,(cadence_hz*span+999_999_999)//1_000_000_000+1)
            if required>right-left+1:undersampled+=1;continue
            necessary_good=(95*required+99)//100
            good_upper=len(tails)
            if good_upper>min(good_deadlines,mask.bit_count()):raise ValueError('good-chain bound exceeds independent count limits')
            margin=good_upper-necessary_good
            possible+=margin>=0
            row=dict(first_index=left,last_index=right,span_ns=span,available_deadlines=right-left+1,
                minimum_choices=required,necessary_good_choices=necessary_good,
                deadlines_with_good_choice=good_deadlines,distinct_good_identities=mask.bit_count(),
                maximum_good_count_upper_bound=good_upper,exact_good_only_advancing_chain_length=good_upper,necessary_margin=margin)
            if best is None or (margin,-span,-left)>(best['necessary_margin'],-best['span_ns'],-best['first_index']):best=row
    return dict(endpoint_pairs_examined=len(times)*(len(times)-1)//2,short_intervals=short,
        duration_qualifying_intervals=intervals,insufficient_deadline_count_intervals=undersampled,
        intervals_not_ruled_out=possible,best_necessary_margin_interval=best,
        any_qualifying_path_possible_by_necessary_counts=possible>0,
        every_endpoint_interval_ruled_out=possible==0,
        verdict='inconclusive_count_bounds' if possible else 'impossible_under_optimistic_count_bound',
        cadence_hz=cadence_hz,minimum_span_ns=minimum_span_ns,minimum_pairs=minimum_pairs,
        path_found=False,exact_feasibility_solved=False)


def good_options(frames,deadlines,ceilings,distinct,threshold_ns=15_000_000):
    """Receipt-floor readiness; targets stay those of the original runtime."""
    if type(distinct) is not bool or type(threshold_ns) is not int or threshold_ns<=0:
        raise ValueError('typed identity mode and positive strict age threshold required')
    # Reuse the independently checked admission domain; do not use its synthetic
    # readiness as an age origin or silently alter the original target ceiling.
    q.target.eligibility_frames(frames,deadlines,ceilings)
    if distinct and any(type(f.get('generation')) is not int or not 0<=f['generation']<10000 for f in frames):
        raise ValueError('bounded exact source generation required')
    ordered=sorted(frames,key=lambda f:(f['receipt'],f['frame']));receipts=[f['receipt'] for f in ordered]
    result=[]
    for i,d in enumerate(deadlines):
        first,last=bisect_right(receipts,d['submit']-threshold_ns),bisect_right(receipts,d['submit'])
        result.append({f['generation'] if distinct else f['frame'] for f in ordered[first:last]
            if f['ready']<=d['selection'] and (ceilings is None or f['target']<=ceilings[i])})
    return result


def evaluate(traces,presented,sources):
    _,warm,generations,verified,joined=age.supply.envelope.population(traces,presented,sources,2_000_000_000,15_000_000)
    known=age.supply.known_pairs(traces);cutoff=presented[0]['submit_xr_ns']+2_000_000_000
    base=[dict(sequence=d['sequence'],selection=traces[d['sequence']-1]['selection_end_xr_ns'],submit=presented[d['sequence']-1]['submit_xr_ns']) for d in warm]
    rows=[]
    for name,distinct,periods in q.MODELS:
        admitted,contract=(q.novelty.novel_warm_population(known,traces,presented,cutoff,'observed_submission_prefix',generations)
            if distinct else q.cardinality.warm_population(known,traces,presented,cutoff,'observed_submission_prefix'))
        frames=[dict(frame=f['frame'],target=f['target'],receipt=f['receipt'],ready=max(e['receive_xr_ns'] for e in f['pair'] if e['present']),
            **(dict(generation=f['generation']) if distinct else {})) for f in admitted]
        ceilings=q.target.target_ceilings(traces,base,periods)
        for shift in PHASES:
            deadlines=[dict(d,selection=d['selection']+shift,submit=d['submit']+shift) for d in base]
            if any(age.supply.observed_admission(traces[d['sequence']-1],presented[d['sequence']-1],d['selection'],d['submit'])['before_recorded_admission'] for d in deadlines):
                raise ValueError('shifted endpoint model violates recorded admission')
            options=good_options(frames,deadlines,ceilings,distinct)
            result=window_bound([d['submit'] for d in deadlines],options)
            best=result['best_necessary_margin_interval']
            if best:best.update(first_sequence=deadlines[best['first_index']]['sequence'],last_sequence=deadlines[best['last_index']]['sequence'])
            result.update(model=name,phase_ns=shift,distinct_source_generations=distinct,target_periods=periods,warm_start=contract,
                good_option_population_sha256=hashlib.sha256(json.dumps([sorted(v) for v in options],separators=(',',':')).encode()).hexdigest())
            rows.append(result)
            print(json.dumps(dict(model=name,phase_ns=shift,not_ruled_out=result['intervals_not_ruled_out'],best_margin=best['necessary_margin'] if best else None)),flush=True)
    return dict(schema=1,status='pass',analysis_kind='finite_phase_all_endpoint_necessary_count_envelope',
        phase_grid_ns=list(PHASES),warm_deadlines=len(base),strict_encoded_age_threshold_ns=15_000_000,alternatives=rows,
        observed_trace_validation=verified,source_validation={k:v for k,v in joined.items() if k!='system_to_app_frames'},
        latency_gate_changed=False,native_behavior_changed=False,headset_operations=False,promotion_eligible=False,
        assumptions=[
            'For each explicit phase and model, every pair of observed warm deadlines spanning at least ten seconds is examined; no full-window count assumption is imposed.',
            'Any path meeting 88 Hz between these endpoints needs at least ceil(88*span/1e9)+1 choices and at least ceil(0.95*count) strictly under-15ms choices.',
            'Removing bad-age choices preserves strict deadline/identity order. The exact longest good-only advancing chain therefore bounds good count of any mixed-age path. Descending alternatives per deadline prevent same-deadline reuse; globally checked monotone frame targets preserve target nonregression. Bad-choice insertion and actual ownership remain unsolved.',
            'A negative count margin rules out that interval. Nonnegative margin is inconclusive and is not a legal path, exact optimum or runtime feasibility claim.',
            'Every participating eye is optimistically ready at original receipt, known metadata may be used before first recorded observation, and all original transport receipt times stay fixed.',
            'Source-distinct identity uses the checked warm-prefix exclusion; encoded receipt age remains the metric, so a rejection also applies to the pointwise older known-source receipt age.',
            'Only the five listed phases are covered, not intervening phases or changed runtime targets, warm membership, unseen supply, earlier receipts or optical latency.',
            'Original failed acquisition and native policy are unchanged.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    run=args.run.resolve();out=args.out.resolve()
    if out.is_relative_to(run):parser.error('output must remain outside original evidence')
    sealed=cf.frozen.analyze(run)
    if sealed['status']!='pass' or sealed['original_hashes_manifest_sha256']!='38502bec943be9ad1c778a4775327dd4dbb0a32940b3fcc7946607a26b031371':
        raise ValueError('pinned original run05 acquisition required')
    manifest_path=run/'hashes.json';raw=manifest_path.read_bytes();manifest=json.loads(raw)
    if hashlib.sha256(raw).hexdigest()!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest changed before endpoint analysis')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):raise ValueError('endpoint inputs are not manifest-bound')
    source_paths=[Path(__file__)]+list(HERE.glob('*.py'))+list((cf.ROOT/'validation/wivrn-codecs/integration').glob('*.py'))
    hashes={str(p.relative_to(cf.ROOT)):cf.frozen.sha(p) for p in source_paths}
    result=evaluate(*[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names])
    if (manifest_path.read_bytes()!=raw or any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):raise ValueError('endpoint evidence or source changed')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=len(manifest),original_result_overwritten=False,sources_sha256=hashes)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')


if __name__=='__main__':main()
