#!/usr/bin/env python3
"""Exact best p95 at the sufficient cadence count, on a finite phase grid."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('quantile_novelty', HERE/'source_novelty.py')
novelty = importlib.util.module_from_spec(spec)
spec.loader.exec_module(novelty)
target, phase, cardinality, cf, supply = novelty.target, novelty.phase, novelty.cardinality, novelty.cf, novelty.supply
MODELS = (('encoded_one_horizon', False, 1), ('encoded_unbounded', False, None),
          ('source_two_horizons', True, 2), ('source_unbounded', True, None))


def percentile95(ages):
    if not ages or any(type(age) is not int or age < 0 for age in ages):
        raise ValueError('nonempty exact nonnegative ages required')
    return sorted(ages)[(len(ages)*95+99)//100-1]


def minimum_p95(query, required_count, seed_threshold=15_000_000):
    """Biselect the exact integer threshold, with independently audited paths.

    query(T) maximizes count(age < T) at one fixed exact path cardinality.
    Hence the minimum attainable nearest-rank p95 is the first passing T minus
    one nanosecond. The first returned path supplies a feasible upper bound.
    """
    if type(required_count) is not int or required_count <= 0 or type(seed_threshold) is not int or seed_threshold <= 0:
        raise ValueError('positive exact count/seed threshold required')
    required_good = (required_count*95+99)//100
    cache = {}
    def run(threshold):
        if threshold not in cache:
            result = query(threshold)
            if (type(result.get('feasible')) is not bool or type(result.get('maximum_good')) is not int
                    or type(result.get('required_count')) is not int or result['required_count'] != required_count):
                raise ValueError('quantile query contract differs')
            if result['feasible']:
                ages = result.get('audited_ages_ns')
                if type(ages) is not list or len(ages) != required_count:
                    raise ValueError('quantile query lacks exact audited ages')
                percentile95(ages)
                if sum(age < threshold for age in ages) != result['maximum_good']:
                    raise ValueError('quantile query score differs from ages')
            elif result['maximum_good'] != -1 or result.get('audited_ages_ns'):
                raise ValueError('infeasible quantile query carries a path/score')
            cache[threshold] = result
        return cache[threshold]
    seed = run(seed_threshold)
    if not seed['feasible']:
        return dict(feasible=False, required_count=required_count, queries=1,
                    reason='No path at this cardinality; feasibility is independent of age threshold.')
    low, high = 0, percentile95(seed['audited_ages_ns'])+1
    upper = run(high)
    if not upper['feasible'] or upper['maximum_good'] < required_good:
        raise ValueError('seed path did not establish a feasible p95 upper bound')
    while high-low > 1:
        middle = (high+low)//2
        tested = run(middle)
        if not tested['feasible']:
            raise ValueError('cardinality feasibility changed with threshold')
        if tested['maximum_good'] >= required_good:
            high = middle
        else:
            low = middle
    passing = run(high)
    failing = run(low) if low else None
    if percentile95(passing['audited_ages_ns']) != high-1 or failing and failing['maximum_good'] >= required_good:
        raise ValueError('quantile minimality boundary differs')
    ordered = sorted(cache)
    if any(cache[a]['maximum_good'] > cache[b]['maximum_good'] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('maximum fresh count regresses as threshold increases')
    return dict(feasible=True, required_count=required_count, required_good_count=required_good,
        minimum_p95_ns=high-1, first_passing_strict_threshold_ns=high, last_failing_strict_threshold_ns=low,
        last_failing_maximum_good=failing['maximum_good'] if failing else 0,
        passing_maximum_good=passing['maximum_good'], queries=len(cache),
        certificate=passing['certificate'], certificate_sha256=passing['certificate_sha256'],
        conditional_cadence_hz=passing['conditional_unique_frame_cadence_hz'],
        query_audit=[dict(threshold_ns=t,maximum_good=cache[t]['maximum_good'],
            certificate_sha256=cache[t]['certificate_sha256']) for t in ordered])


def wait_for_quiet_file(path):
    if path is None or not path.exists():
        return
    print(json.dumps({'analysis_paused_for_team_measurement':str(path)}),flush=True)
    while path.exists():
        time.sleep(.1)
    print(json.dumps({'analysis_resumed':True}),flush=True)


def evaluate(traces, presented, source_rows, solvers, quiet_file=None, phases=phase.PHASES,
             publication_advance_ns=8_000_000, warmup_ns=2_000_000_000, cadence_floor_hz=88,
             ready_at_receipt=False):
    if type(ready_at_receipt) is not bool or ready_at_receipt and publication_advance_ns!=0:
        raise ValueError('receipt-floor mode requires an exact flag and zero unused advance')
    phase.grid(phases,(publication_advance_ns,))
    if type(cadence_floor_hz) is not int or not 0 < cadence_floor_hz <= 1000:
        raise ValueError('bounded positive cadence required')
    _, warm, generations, verified, joined = supply.envelope.population(traces,presented,source_rows,warmup_ns,15_000_000)
    known = supply.known_pairs(traces)
    span = warm[-1]['submit_xr_ns']-warm[0]['submit_xr_ns']
    minimum_count = (cadence_floor_hz*span+999_999_999)//1_000_000_000+1
    budget = len(warm)-minimum_count
    if not 0 <= budget <= 64:
        raise ValueError('cadence window requires unsupported skip budget')
    cutoff = presented[0]['submit_xr_ns']+warmup_ns
    targets = {d['sequence']:traces[d['sequence']-1]['predicted_display_xr_ns'] for d in warm}
    alternatives = []
    with tempfile.TemporaryDirectory(prefix='sub15-quantile-input-') as directory:
        path = Path(directory)/'population.txt'
        for name, distinct, periods in MODELS:
            admitted, contract = (novelty.novel_warm_population(known,traces,presented,cutoff,'observed_submission_prefix',generations)
                if distinct else cardinality.warm_population(known,traces,presented,cutoff,'observed_submission_prefix'))
            for shift in phases:
                original = [dict(frame=f['frame'],target=f['target'],receipt=f['receipt'],
                    ready=max(e['receive_xr_ns'] if ready_at_receipt else max(e['receive_xr_ns'],e['ready_xr_ns']-publication_advance_ns) for e in f['pair'] if e['present']),
                    **(dict(generation=f['generation']) if distinct else {})) for f in admitted]
                deadlines = []
                for d in warm:
                    seq=d['sequence']; row,present=traces[seq-1],presented[seq-1]
                    selection,submit=row['selection_end_xr_ns']+shift,present['submit_xr_ns']+shift
                    if supply.observed_admission(row,present,selection,submit)['before_recorded_admission']:
                        raise ValueError('shift violates original admission floor')
                    deadlines.append(dict(sequence=seq,selection=selection,submit=submit))
                ceilings=target.target_ceilings(traces,deadlines,periods)
                frames,transform=target.eligibility_frames(original,deadlines,ceilings)
                frame_map={f['frame']:f for f in original};deadline_map={d['sequence']:d for d in deadlines}
                columns=('frame','target','receipt','ready')+(('generation',) if distinct else ())
                population_lines=[' '.join(str(f[k]) for k in columns) for f in frames]
                population_lines+=[' '.join(str(d[k]) for k in ('sequence','selection','submit')) for d in deadlines]
                population_hash=hashlib.sha256(('\n'.join(population_lines)+'\n').encode()).hexdigest()
                def query(threshold):
                    wait_for_quiet_file(quiet_file)
                    path.write_text(f'{len(frames)} {len(deadlines)} {budget} {threshold}\n'+'\n'.join(population_lines)+'\n')
                    run=subprocess.run([str(solvers['source' if distinct else 'encoded']),str(path)],capture_output=True,text=True,timeout=120)
                    if run.returncode or run.stderr:
                        raise ValueError('quantile solver failed or wrote diagnostics: '+run.stderr)
                    raw=json.loads(run.stdout)
                    audit_args=(raw,original,frames,deadlines,ceilings,budget,threshold,targets)
                    audited=(novelty.audit_novelty_frontier(*audit_args,generations,contract) if distinct
                        else target.audit_target_frontier(*audit_args))
                    entry=audited['frontier'][-1]
                    entry['audited_ages_ns']=[deadline_map[seq]['submit']-frame_map[frame]['receipt'] for seq,frame in entry['certificate']]
                    return entry
                result=minimum_p95(query,minimum_count)
                result.update(model=name, distinct_source_generations=distinct,target_periods=periods,
                    sampling_shift_ns=shift,hypothetical_publication_advance_ns=None if ready_at_receipt else publication_advance_ns,
                    hypothetical_publication_mode='receipt_floor' if ready_at_receipt else 'bounded_advance',
                    population_sha256=population_hash,warm_start=contract,target_admission=transform)
                if result['feasible']:
                    choices=result['certificate']
                    result.update(actual_distinct_source_sets=len({generations[f] for _,f in choices}),
                        chosen_before_first_recorded_observation=sum(seq<known[f]['first_observed_sequence'] for seq,f in choices),
                        source_target_minus_original_runtime_target_ms=cf.distribution([(frame_map[f]['target']-targets[seq])/1e6 for seq,f in choices]))
                alternatives.append(result)
                print(json.dumps({'model':name,'phase_ns':shift,'minimum_p95_ns':result.get('minimum_p95_ns'),'queries':result['queries']}),flush=True)
    best=[]
    for name,_,_ in MODELS:
        candidates=[a for a in alternatives if a['model']==name and a['feasible']]
        best.append(min(candidates,key=lambda a:(a['minimum_p95_ns'],a['sampling_shift_ns'])) if candidates else dict(model=name,feasible=False))
    # Every additional identity/target restriction must weakly worsen the
    # independently solved quantile optimum at the same sampling phase.
    by_key={(a['model'],a['sampling_shift_ns']):a for a in alternatives}
    for shift in phases:
        for relaxed,strict in (('encoded_unbounded','encoded_one_horizon'),('encoded_unbounded','source_unbounded'),('source_unbounded','source_two_horizons')):
            a,b=by_key[relaxed,shift],by_key[strict,shift]
            if b['feasible'] and (not a['feasible'] or a['minimum_p95_ns']>b['minimum_p95_ns']):
                raise ValueError('restricted model improves exact optimum')
    keep={(r['model'],r.get('sampling_shift_ns')) for r in best}
    for a in alternatives:
        if (a['model'],a['sampling_shift_ns']) not in keep:
            a.pop('certificate',None)
    return dict(schema=1,status='pass',analysis_kind='exact_quantile_at_sufficient_cadence_count',
        phase_grid_ns=list(phases),publication_advance_ns=None if ready_at_receipt else publication_advance_ns,
        hypothetical_publication_mode='receipt_floor' if ready_at_receipt else 'bounded_advance',scenario_count=len(alternatives),
        warm_observed_deadlines=len(warm),full_window_minimum_count=minimum_count,maximum_skips=budget,
        observed_deadline_span_ns=span,cadence_floor_reference_hz=cadence_floor_hz,
        observed_trace_validation=verified,source_validation={k:v for k,v in joined.items() if k!='system_to_app_frames'},
        alternatives=alternatives,best_tested_phase_per_model=best,
        measured_speedup=False,runnable_policy=False,latency_gate_evaluated=False,promotion_eligible=False,release_ready=False,
        assumptions=[
            'Exact nearest-rank p95 over the finite declared phase grid and fixed minimum count; no continuous phase optimum.',
            'Every larger allowed count can delete its worst-age choices down to minimum count while preserving order/admission; nearest-rank p95 cannot increase.',
            'The minimum full-window count is sufficient for cadence, not necessary for shorter endpoint intervals; lower counts are outside this bound.',
            'Larger publication advance only expands eligible edges without changing receipt age; 8ms dominates the earlier 0/1/2/4ms models but remains hypothetical.',
            'All intermediate exact-count solver paths are independently audited; final passing and adjacent failing thresholds establish the integer optimum.',
            'Unlimited known-pair ownership, earlier use of later-observed metadata and fixed shifted runtime timing remain relaxed.',
            'Source application identities do not establish distinct simulation/input epochs or optical freshness.',
            'Future-target ceilings use original runtime inputs; no feedback, pose quality or actual XR acceptance is modeled.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    for kind in ('encoded','source'):
        parser.add_argument('--'+kind+'-solver',type=Path,required=True)
        parser.add_argument('--'+kind+'-build-receipt',type=Path,required=True)
    parser.add_argument('--quiet-file',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    run,out=args.run.resolve(),args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    solvers,builds,controls={},{},{}
    for kind in ('encoded','source'):
        solver=getattr(args,kind+'_solver').resolve()
        receipt=getattr(args,kind+'_build_receipt').resolve()
        build=json.loads(receipt.read_text())
        if build.get('status')!='pass' or cf.frozen.sha(solver)!=build.get('solver_sha256'):
            raise ValueError('solver differs from validated build: '+kind)
        for name,digest in build['sources_sha256'].items():
            if cf.frozen.sha(cf.ROOT/name)!=digest:
                raise ValueError('validated source changed: '+name)
        solvers[kind]=solver
        builds[kind]=dict(solver_sha256=build['solver_sha256'],build_receipt_sha256=cf.frozen.sha(receipt))
        controls[str(solver)]=cf.frozen.sha(solver);controls[str(receipt)]=cf.frozen.sha(receipt)
    sealed=cf.frozen.analyze(run)
    if sealed['status']!='pass':
        raise ValueError('sealed acquisition validation failed')
    manifest_path=run/'hashes.json';manifest=json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']:
        raise ValueError('original manifest changed')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):
        raise ValueError('quantile input not manifest-bound')
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names]
    modules=(novelty,target,phase,cardinality,supply,supply.envelope,supply.oracle,cf,cf.frozen,cf.trace,cf.replay,cf.source)
    paths=[Path(__file__)]+[Path(module.__file__) for module in modules]
    paths += [HERE/name for name in ('cardinality_oracle.h','cardinality_probe.cpp','novelty_oracle.h','novelty_probe.cpp')]
    hashes={str(p.relative_to(cf.ROOT)):cf.frozen.sha(p) for p in paths}
    result=evaluate(*rows,solvers,quiet_file=args.quiet_file)
    if (any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']
            or any(cf.frozen.sha(path)!=digest for path,digest in controls.items())
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('quantile evidence/build/implementation changed during analysis')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False,solver_builds=builds,sources_sha256=hashes)
    with out.open('x') as stream:
        json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps([dict(model=r['model'],phase_ns=r.get('sampling_shift_ns'),minimum_p95_ns=r.get('minimum_p95_ns'))
        for r in result['best_tested_phase_per_model']]))


if __name__=='__main__':
    main()
