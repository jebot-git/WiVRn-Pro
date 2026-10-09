#!/usr/bin/env python3
"""Close every integer-nanosecond phase gap in the recorded receipt-floor bound."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


floor=load('exact_phase_floor',HERE/'receipt_floor.py')
engine=load('exact_phase_core',HERE/'phase_exact.py')
q=floor.quantile
cf=q.cf


def verified_seed(seed,query,count):
    """Treat prior minima as hints, then independently recheck both boundaries."""
    value=seed.get('minimum_p95_ns')
    if seed.get('feasible') is not True or type(value) is not int or value<0:
        raise ValueError('finite feasible exact seed optimum required')
    needed=(count*95+99)//100
    passing=query(value+1);failing=query(value) if value else None
    if (not passing['feasible'] or passing.get('required_count')!=count or len(passing['audited_ages_ns'])!=count
            or passing['maximum_good']<needed
            or q.percentile95(passing['audited_ages_ns'])!=value
            or failing and (not failing['feasible'] or failing['maximum_good']>=needed)):
        raise ValueError('prior seed optimum failed independently rebuilt adjacent-boundary audit')
    return dict(feasible=True,required_count=count,required_good_count=needed,
        minimum_p95_ns=value,first_passing_strict_threshold_ns=value+1,last_failing_strict_threshold_ns=value,
        passing_maximum_good=passing['maximum_good'],last_failing_maximum_good=failing['maximum_good'] if failing else 0,
        certificate=passing['certificate'],certificate_sha256=passing['certificate_sha256'],
        conditional_cadence_hz=passing['conditional_unique_frame_cadence_hz'],queries=2 if value else 1,
        seed_boundary_requalified=True,seed_certificate_identical=seed['certificate_sha256']==passing['certificate_sha256'])


def evaluate(traces,presented,source_rows,solvers,seed,quiet_file=None,last_phase=11_111_111):
    if seed.get('status')!='pass' or seed.get('analysis_kind')!='exact_quantile_with_zero_receive_to_publication_delay':
        raise ValueError('validated receipt-floor seed required')
    known=q.supply.known_pairs(traces);floor_proof=floor.floor_proof(known)
    _,warm,generations,verified,joined=q.supply.envelope.population(traces,presented,source_rows,2_000_000_000,15_000_000)
    span=warm[-1]['submit_xr_ns']-warm[0]['submit_xr_ns'];count=(88*span+999_999_999)//1_000_000_000+1
    budget=len(warm)-count
    if not 0<=budget<=64 or count!=seed.get('full_window_minimum_count'):
        raise ValueError('exact phase cadence/skip scope differs')
    cutoff=presented[0]['submit_xr_ns']+2_000_000_000
    base=[dict(sequence=d['sequence'],selection=traces[d['sequence']-1]['selection_end_xr_ns'],submit=presented[d['sequence']-1]['submit_xr_ns']) for d in warm]
    targets={d['sequence']:traces[d['sequence']-1]['predicted_display_xr_ns'] for d in warm}
    for d in base:
        if q.supply.observed_admission(traces[d['sequence']-1],presented[d['sequence']-1],d['selection'],d['submit'])['before_recorded_admission']:
            raise ValueError('phase origin violates recorded admission')
    q.phase.grid(seed['phase_grid_ns'],(0,))
    seed_rows={(r['model'],r['sampling_shift_ns']):r for r in seed['alternatives']}
    expected={(name,s) for name,_,_ in q.MODELS for s in seed['phase_grid_ns']}
    if len(seed['alternatives'])!=len(expected) or set(seed_rows)!=expected or seed['phase_grid_ns'][0]!=0 or seed['phase_grid_ns'][-1]!=last_phase:
        raise ValueError('complete bounded seed grid required')
    models=[]
    with tempfile.TemporaryDirectory(prefix='sub15-exact-phase-') as directory:
        input_path=Path(directory)/'population.txt'
        for name,distinct,periods in q.MODELS:
            admitted,contract=(q.novelty.novel_warm_population(known,traces,presented,cutoff,'observed_submission_prefix',generations)
                if distinct else q.cardinality.warm_population(known,traces,presented,cutoff,'observed_submission_prefix'))
            original=[dict(frame=f['frame'],target=f['target'],receipt=f['receipt'],
                ready=max(e['receive_xr_ns'] for e in f['pair'] if e['present']),
                **(dict(generation=f['generation']) if distinct else {})) for f in admitted]
            frame_map={f['frame']:f for f in original}
            ceilings=q.target.target_ceilings(traces,base,periods)
            events,edge_events=engine.critical_phases(original,base,ceilings if ceilings is not None else [None]*len(base),last_phase)
            events=sorted(set(events)|set(seed['phase_grid_ns']))
            def phase_query(shift):
                deadlines=[dict(d,selection=d['selection']+shift,submit=d['submit']+shift) for d in base]
                transformed,transform=q.target.eligibility_frames(original,deadlines,ceilings)
                deadline_map={d['sequence']:d for d in deadlines}
                columns=('frame','target','receipt','ready')+(('generation',) if distinct else ())
                lines=[' '.join(str(f[k]) for k in columns) for f in transformed]
                lines+=[' '.join(str(d[k]) for k in ('sequence','selection','submit')) for d in deadlines]
                population='\n'.join(lines)+'\n';population_hash=hashlib.sha256(population.encode()).hexdigest()
                def query(threshold):
                    q.wait_for_quiet_file(quiet_file)
                    input_path.write_text(f'{len(transformed)} {len(deadlines)} {budget} {threshold}\n'+population)
                    run=subprocess.run([str(solvers['source' if distinct else 'encoded']),str(input_path)],capture_output=True,text=True,timeout=120)
                    if run.returncode or run.stderr:raise ValueError('exact-phase solver failed/diagnosed: '+run.stderr)
                    raw=json.loads(run.stdout);args=(raw,original,transformed,deadlines,ceilings,budget,threshold,targets)
                    audited=q.novelty.audit_novelty_frontier(*args,generations,contract) if distinct else q.target.audit_target_frontier(*args)
                    entry=audited['frontier'][-1]
                    entry['audited_ages_ns']=[deadline_map[seq]['submit']-frame_map[frame]['receipt'] for seq,frame in entry['certificate']]
                    return entry
                old=seed_rows.get((name,shift))
                if old:
                    if old['population_sha256']!=population_hash:raise ValueError('reconstructed seed population differs')
                    result=verified_seed(old,query,count)
                else:result=q.minimum_p95(query,count)
                result.update(population_sha256=population_hash,target_admission=transform)
                if result['feasible']:
                    result.update(actual_distinct_source_sets=len({generations[f] for _,f in result['certificate']}),
                        chosen_before_first_recorded_observation=sum(seq<known[f]['first_observed_sequence'] for seq,f in result['certificate']))
                print(json.dumps(dict(model=name,phase_ns=shift,minimum_p95_ns=result.get('minimum_p95_ns'),queries=result['queries'],seed=bool(old))),flush=True)
                return result
            result=engine.minimize(phase_query,events,seed['phase_grid_ns'])
            best=result['evaluated'].get(str(result['best_phase_ns']))
            if best is None or 'certificate' not in best:raise ValueError('exact phase optimum lacks an audited full certificate')
            for s,row in result['evaluated'].items():
                if int(s)!=result['best_phase_ns']:row.pop('certificate',None)
            result.update(model=name,distinct_source_generations=distinct,target_periods=periods,
                readiness_edge_events=edge_events,candidate_phases_ns=events,warm_start=contract)
            models.append(result)
            print(json.dumps(dict(completed_model=name,minimum_p95_ns=result['minimum_p95_ns'],phase_ns=result['best_phase_ns'],evaluated=result['evaluated_phase_count'],candidates=len(events))),flush=True)
    by_name={m['model']:m for m in models}
    for relaxed,strict in (('encoded_unbounded','encoded_one_horizon'),('encoded_unbounded','source_unbounded'),('source_unbounded','source_two_horizons')):
        if by_name[relaxed]['minimum_p95_ns']>by_name[strict]['minimum_p95_ns']:raise ValueError('exact constrained optimum improves relaxed bound')
    return dict(schema=1,status='pass',analysis_kind='exact_all_integer_phase_receipt_floor_bound',
        first_phase_ns=0,last_phase_ns=last_phase,integer_phase_population=last_phase+1,
        full_window_minimum_count=count,maximum_skips=budget,warm_observed_deadlines=len(warm),
        observed_deadline_span_ns=span,models=models,receipt_floor=floor_proof,
        observed_trace_validation=verified,source_validation={k:v for k,v in joined.items() if k!='system_to_app_frames'},
        measured_speedup=False,runnable_policy=False,native_behavior_changed=False,promotion_eligible=False,release_ready=False,
        assumptions=[
            'Exact minimum nearest-rank p95 over every integer-nanosecond positive shift from zero through the declared final phase; no phase beyond that interval is covered.',
            'All edge changes occur at ready minus original selection; between consecutive events every feasible path remains unchanged and its whole age vector increases by the common shift.',
            'For increasing phase, F(s)-s is nonincreasing as admission expands. Exact upper-endpoint optima certify lower bounds across pruned candidate intervals.',
            'Every candidate event is evaluated or covered by an explicit valid lower-bound/infeasibility proof; all seed minima are independently requalified at adjacent strict thresholds.',
            'The sufficient full-span count is fixed. Deleting worst ages preserves order/admission and cannot worsen p95; fewer choices over shorter endpoint spans remain outside scope.',
            'Each participating eye is ready at its own original receipt, while receipt age retains the original minimum stereo-eye receipt. No original transport receipt is improved.',
            'Warm membership, actual pre-warm identity history, target ceilings and original runtime target sequence stay fixed; only selection/submit are shifted equally.',
            'Unlimited retention and earlier use of later-observed known metadata remain optimistic; unseen frames, feedback, optics, pose quality and native scheduling are not modeled.',
            'Application frame identities do not establish distinct input/simulation epochs. Original acquisition remains failed and unmodified.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    for kind in ('encoded','source'):
        parser.add_argument('--'+kind+'-solver',type=Path,required=True);parser.add_argument('--'+kind+'-build-receipt',type=Path,required=True)
    parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--quiet-file',type=Path)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    run,out=args.run.resolve(),args.out.resolve()
    if out.is_relative_to(run):parser.error('output must be outside immutable acquisition')
    solvers,builds,controls={},{},{str(args.seed.resolve()):cf.frozen.sha(args.seed)}
    for kind in ('encoded','source'):
        solver=getattr(args,kind+'_solver').resolve();receipt=getattr(args,kind+'_build_receipt').resolve();build=json.loads(receipt.read_text())
        if build.get('status')!='pass' or cf.frozen.sha(solver)!=build.get('solver_sha256'):raise ValueError('validated solver differs: '+kind)
        for name,digest in build['sources_sha256'].items():
            if cf.frozen.sha(cf.ROOT/name)!=digest:raise ValueError('validated source differs: '+name)
        solvers[kind]=solver;builds[kind]=dict(solver_sha256=cf.frozen.sha(solver),build_receipt_sha256=cf.frozen.sha(receipt))
        controls[str(solver)]=cf.frozen.sha(solver);controls[str(receipt)]=cf.frozen.sha(receipt)
    sealed=cf.frozen.analyze(run);seed=json.loads(args.seed.read_text())
    if sealed['status']!='pass' or seed.get('original_manifest_sha256')!=sealed['original_hashes_manifest_sha256']:
        raise ValueError('exact phase acquisition/seed identity differs')
    if seed.get('solver_builds')!=builds:raise ValueError('seed solver provenance differs')
    for name,digest in seed['sources_sha256'].items():
        if cf.frozen.sha(cf.ROOT/name)!=digest:raise ValueError('seed implementation differs: '+name)
    manifest_path=run/'hashes.json';manifest=json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest changed')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):raise ValueError('exact phase inputs not bound')
    hashes=dict(seed['sources_sha256'])
    for path in (Path(__file__),Path(engine.__file__)):hashes[str(path.relative_to(cf.ROOT))]=cf.frozen.sha(path)
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names]
    result=evaluate(*rows,solvers,seed,quiet_file=args.quiet_file)
    if (any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']
            or any(cf.frozen.sha(path)!=digest for path,digest in controls.items())
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('exact phase evidence/seed/build/source changed during analysis')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False,solver_builds=builds,sources_sha256=hashes,seed_sha256=controls[str(args.seed.resolve())])
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps([dict(model=m['model'],phase_ns=m['best_phase_ns'],minimum_p95_ns=m['minimum_p95_ns'],evaluated=m['evaluated_phase_count'],candidates=m['candidate_phase_count']) for m in result['models']]))


if __name__=='__main__':main()
