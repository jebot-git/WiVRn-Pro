#!/usr/bin/env python3
"""Audit client-local age of known source identity across encoded-frame reuse."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('source_age_supply',HERE/'supply_phase.py')
supply=importlib.util.module_from_spec(spec);spec.loader.exec_module(supply)
cf=supply.cf
spec=importlib.util.spec_from_file_location('source_age_quantile',HERE/'quantile_bound.py')
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)


def source_key(values):
    if type(values) not in (tuple,list) or not values:raise ValueError('nonempty source identity required')
    if any(type(pair) not in (tuple,list) or len(pair)!=2 or any(type(x) is not int or x<=0 for x in pair) for pair in values):
        raise ValueError('exact positive pacer/application identities required')
    key=tuple(sorted(tuple(pair) for pair in values))
    if len({p for p,_ in key})!=len(key):raise ValueError('one generation per pacer required')
    return key


def origins(known,generations):
    if not known:raise ValueError('nonempty known source population required')
    result={}
    for frame,item in known.items():
        if type(frame) is not int or frame<=0 or type(item.get('first_observed_sequence')) is not int or item['first_observed_sequence']<=0:
            raise ValueError('exact positive known frame/observation identity required')
        if frame not in generations:raise ValueError('known frame missing a source join')
        key=source_key(generations[frame]);receipt=item['receipt']
        if type(receipt) is not int or not 0<receipt<2**63:raise ValueError('exact positive source receipt required')
        previous=result.get(key)
        if previous is None or (receipt,frame)<(previous['receipt'],previous['frame']):
            result[key]=dict(frame=frame,receipt=receipt,first_observed_sequence=item['first_observed_sequence'])
    return result


def sample(sequence,frame,submit,known,generations,first_receipts):
    if any(type(v) is not int or not 0<v<2**63 for v in (sequence,frame,submit)) or frame not in known:
        raise ValueError('exact known selected identity/time required')
    key=source_key(generations[frame]);origin=first_receipts[key];encoded=known[frame]['receipt']
    if not origin['receipt']<=encoded<=submit:raise ValueError('source/encoded receipt chronology regresses')
    return dict(sequence=sequence,encoded_frame=frame,source_set=[list(pair) for pair in key],
        encoded_receive_age_ns=submit-encoded,known_source_receive_age_ns=submit-origin['receipt'],
        additional_known_source_age_ns=encoded-origin['receipt'],
        source_origin_encoded_frame=origin['frame'],source_origin_receive_xr_ns=origin['receipt'],
        origin_metadata_already_observed=origin['first_observed_sequence']<=sequence)


def summary(samples):
    return dict(count=len(samples),
        encoded_receive_age_ms=cf.distribution([s['encoded_receive_age_ns']/1e6 for s in samples]),
        known_source_receive_age_ms=cf.distribution([s['known_source_receive_age_ns']/1e6 for s in samples]),
        additional_known_source_age_ms=cf.distribution([s['additional_known_source_age_ns']/1e6 for s in samples]),
        encoded_age_under_15ms=sum(s['encoded_receive_age_ns']<15_000_000 for s in samples),
        known_source_age_under_15ms=sum(s['known_source_receive_age_ns']<15_000_000 for s in samples),
        samples_with_older_known_source_origin=sum(s['additional_known_source_age_ns']>0 for s in samples),
        origin_metadata_not_yet_observed=sum(not s['origin_metadata_already_observed'] for s in samples))


def evaluate(traces,presented,source_rows,exact_phase=None,warmup_ns=2_000_000_000):
    _,deadlines,generations,verified,joined=supply.envelope.population(traces,presented,source_rows,warmup_ns,15_000_000)
    known=supply.known_pairs(traces);first_receipts=origins(known,generations)
    cutoff=presented[0]['submit_xr_ns']+warmup_ns
    seen_frames,seen_sources=set(),set();all_warm=[];first_encoded=[];first_source=[]
    for sequence,p in enumerate(presented,1):
        frame=p['left'];key=source_key(generations[frame]);new_frame=frame not in seen_frames;new_source=key not in seen_sources
        seen_frames.add(frame);seen_sources.add(key)
        if p['submit_xr_ns']<cutoff:continue
        row=sample(sequence,frame,p['submit_xr_ns'],known,generations,first_receipts);all_warm.append(row)
        if new_frame:first_encoded.append(row)
        if new_source:first_source.append(row)
    alternative=[]
    if exact_phase is not None:
        if exact_phase.get('status')!='pass' or exact_phase.get('analysis_kind')!='exact_all_integer_phase_receipt_floor_bound':
            raise ValueError('exact phase certificate source required')
        span=deadlines[-1]['submit_xr_ns']-deadlines[0]['submit_xr_ns']
        minimum_count=(88*span+999_999_999)//1_000_000_000+1
        if (type(exact_phase.get('full_window_minimum_count')) is not int
                or exact_phase['full_window_minimum_count']!=minimum_count):
            raise ValueError('exact phase full-window cadence count differs')
        expected={name:(distinct,periods) for name,distinct,periods in q.MODELS}
        if len(exact_phase['models'])!=len(expected) or {m['model'] for m in exact_phase['models']}!=set(expected):
            raise ValueError('exact phase model population differs')
        for model in exact_phase['models']:
            phase=model['best_phase_ns'];best=model['evaluated'][str(phase)];choices=best['certificate']
            distinct,periods=expected[model['model']]
            if (type(phase) is not int or not 0<=phase<=11_111_111 or model['distinct_source_generations'] is not distinct
                    or type(model['target_periods']) is not type(periods) or model['target_periods']!=periods
                    or len(choices)!=minimum_count):
                raise ValueError('exact phase controls/count differ')
            if hashlib.sha256(json.dumps(choices,separators=(',',':')).encode()).hexdigest()!=best['certificate_sha256']:
                raise ValueError('exact phase certificate hash differs')
            admitted,contract=(q.novelty.novel_warm_population(known,traces,presented,cutoff,'observed_submission_prefix',generations)
                if distinct else q.cardinality.warm_population(known,traces,presented,cutoff,'observed_submission_prefix'))
            permitted={f['frame']:f for f in admitted}
            shifted=[dict(sequence=d['sequence'],selection=traces[d['sequence']-1]['selection_end_xr_ns']+phase,
                submit=presented[d['sequence']-1]['submit_xr_ns']+phase) for d in deadlines]
            ceilings=q.target.target_ceilings(traces,shifted,periods)
            positions={d['sequence']:i for i,d in enumerate(shifted)}
            rows=[];last_sequence=last_frame=0
            last_generation=-1
            for sequence,frame in choices:
                if (type(sequence) is not int or type(frame) is not int or sequence<=last_sequence or frame<=last_frame
                        or sequence not in positions or frame not in permitted):
                    raise ValueError('exact phase certificate order/identity invalid')
                item=permitted[frame];position=positions[sequence];deadline=shifted[position]
                ready=max(e['receive_xr_ns'] for e in item['pair'] if e['present'])
                if ready>deadline['selection'] or ceilings is not None and item['target']>ceilings[position]:
                    raise ValueError('exact phase certificate violates receipt-floor/target admission')
                if distinct:
                    if item['generation']<=last_generation:raise ValueError('exact phase source generation repeats/regresses')
                    last_generation=item['generation']
                rows.append(sample(sequence,frame,presented[sequence-1]['submit_xr_ns']+phase,known,generations,first_receipts))
                last_sequence,last_frame=sequence,frame
            if q.percentile95([r['encoded_receive_age_ns'] for r in rows])!=best['minimum_p95_ns'] or best['minimum_p95_ns']!=model['minimum_p95_ns']:
                raise ValueError('exact phase original p95 differs from directly audited ages')
            alternative.append(dict(model=model['model'],phase_ns=phase,original_p95_optimization_metric='chosen_encoded_frame_receipt',
                newly_optimized_for_source_age=False,direct_admission_and_original_p95_rechecked=True,
                summary=summary(rows),samples=rows,certificate_sha256=best['certificate_sha256']))
    return dict(schema=1,status='pass',analysis_kind='known_source_identity_receive_age_audit',
        known_encoded_frames=len(known),known_source_sets=len(first_receipts),warmup_ns=warmup_ns,
        all_warm_submissions=summary(all_warm),warm_first_encoded=summary(first_encoded),warm_first_selected_source=summary(first_source),
        actual_samples=all_warm,exact_phase_winning_paths_reaudited=alternative,
        observed_trace_validation=verified,source_validation={k:v for k,v in joined.items() if k!='system_to_app_frames'},
        measured_speedup=False,latency_gate_changed=False,promotion_eligible=False,release_ready=False,
        assumptions=[
            'Known source identity age is submit minus earliest recorded headset-local receipt among exact known encoded frames sharing the same canonical latched application set.',
            'It is a separate diagnostic metric. Original encoded-frame receipt metrics and policy limits are unchanged.',
            'All subtractions remain headset-local; host timestamps only establish identities and are never subtracted.',
            'A later encoded frame carrying the same source identity cannot reset this known-source receipt origin.',
            'The known source origin can omit earlier unobserved frames and therefore is not complete application/input age; shared source IDs also do not establish identical pixels.',
            'Origin metadata may be identified later in hindsight; this is recorded explicitly and is not a runnable online selector.',
            'First selected-source membership includes actual pre-warm selection history. It differs from first host latch or first known network receipt.',
            'Any exact-phase winning paths are rescored only; their encoded-receipt optimality does not establish source-receipt optimality.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    parser.add_argument('--exact-phase',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run.resolve(),args.out.resolve()
    if out.is_relative_to(run):parser.error('output must be outside immutable acquisition')
    sealed=cf.frozen.analyze(run);exact=json.loads(args.exact_phase.read_text())
    if sealed['status']!='pass' or exact.get('original_manifest_sha256')!=sealed['original_hashes_manifest_sha256']:
        raise ValueError('source age acquisition/certificate provenance differs')
    for name,digest in exact['sources_sha256'].items():
        if cf.frozen.sha(cf.ROOT/name)!=digest:raise ValueError('exact phase implementation differs: '+name)
    manifest_path=run/'hashes.json';manifest=json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest changed')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):raise ValueError('source age inputs not bound')
    modules=(supply,supply.envelope,supply.oracle,cf,cf.frozen,cf.trace,cf.replay,cf.source,q,q.novelty,q.target,q.cardinality)
    paths=[Path(__file__)]+[Path(m.__file__) for m in modules]
    hashes=dict(exact['sources_sha256']);hashes.update({str(p.relative_to(cf.ROOT)):cf.frozen.sha(p) for p in paths})
    certificate_hash=cf.frozen.sha(args.exact_phase)
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names]
    result=evaluate(*rows,exact)
    if (any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']
            or cf.frozen.sha(args.exact_phase)!=certificate_hash
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('source age evidence/certificate/source changed')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False,sources_sha256=hashes,exact_phase_sha256=certificate_hash)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(result['warm_first_encoded']))


if __name__=='__main__':main()
