#!/usr/bin/env python3
"""Exact finite-phase age bounds with every known eye ready at receipt."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('receipt_floor_quantile',HERE/'quantile_bound.py')
quantile=importlib.util.module_from_spec(spec);spec.loader.exec_module(quantile)
cf=quantile.cf


def floor_proof(known):
    """Validate participating eye floors and retain removed recorded spans."""
    if not known:raise ValueError('nonempty known pair population required')
    maximum=0;eyes=0
    for frame in known.values():
        for eye in frame['pair']:
            if not eye['present']:continue
            receipt,ready=eye['receive_xr_ns'],eye['ready_xr_ns']
            if any(type(v) is not int or not 0<v<2**63 for v in (receipt,ready)) or ready<receipt:
                raise ValueError('exact nonregressing participating-eye receipt/readiness required')
            maximum=max(maximum,ready-receipt);eyes+=1
    if not eyes:raise ValueError('participating eyes required')
    return dict(maximum_observed_eye_receive_to_ready_ns=maximum,participating_eyes=eyes,
        every_participating_eye_equals_own_original_receipt=True)


def evaluate(traces,presented,source_rows,solvers,quiet_file=None,phases=quantile.phase.PHASES):
    proof=floor_proof(quantile.supply.known_pairs(traces))
    result=quantile.evaluate(traces,presented,source_rows,solvers,quiet_file=quiet_file,
        phases=phases,publication_advance_ns=0,ready_at_receipt=True)
    result['analysis_kind']='exact_quantile_with_zero_receive_to_publication_delay'
    result['receipt_floor']=proof
    result['assumptions']=[s for s in result['assumptions'] if not s.startswith('Larger publication advance')]
    result['assumptions'] += [
        'Every participating eye, including alpha if present, is ready at its own recorded receive timestamp; pair readiness is their maximum and age retains the original minimum stereo-eye receipt.',
        'Zero receive-to-publication delay is an optimistic bound over recorded supply, not a decoder, native policy or measured removable cost.',
        'Any hypothetical publication improvement constrained not to precede each original eye receipt has a subset of these eligible edges; its minimum attainable p95 cannot be smaller at the same phase/identity/target constraints.',
        'Original network receipts, source targets and fixed shifted runtime schedule remain unchanged; earlier source generation or transport receipt is outside this bound.']
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    for kind in ('encoded','source'):
        parser.add_argument('--'+kind+'-solver',type=Path,required=True)
        parser.add_argument('--'+kind+'-build-receipt',type=Path,required=True)
    parser.add_argument('--quiet-file',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run.resolve(),args.out.resolve()
    if out.is_relative_to(run):parser.error('output must be outside immutable acquisition')
    solvers,builds,controls={},{},{}
    for kind in ('encoded','source'):
        solver=getattr(args,kind+'_solver').resolve();receipt=getattr(args,kind+'_build_receipt').resolve()
        build=json.loads(receipt.read_text())
        if build.get('status')!='pass' or cf.frozen.sha(solver)!=build.get('solver_sha256'):
            raise ValueError('validated solver binary differs: '+kind)
        for name,digest in build['sources_sha256'].items():
            if cf.frozen.sha(cf.ROOT/name)!=digest:raise ValueError('validated solver source differs: '+name)
        solvers[kind]=solver;builds[kind]=dict(solver_sha256=cf.frozen.sha(solver),build_receipt_sha256=cf.frozen.sha(receipt))
        controls[str(solver)]=cf.frozen.sha(solver);controls[str(receipt)]=cf.frozen.sha(receipt)
    sealed=cf.frozen.analyze(run)
    if sealed['status']!='pass':raise ValueError('sealed acquisition invalid')
    manifest_path=run/'hashes.json';manifest=json.loads(manifest_path.read_text())
    if cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest changed')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):raise ValueError('receipt floor inputs not manifest-bound')
    modules=(quantile,quantile.novelty,quantile.target,quantile.phase,quantile.cardinality,quantile.supply,
        quantile.supply.envelope,quantile.supply.oracle,cf,cf.frozen,cf.trace,cf.replay,cf.source)
    paths=[Path(__file__)]+[Path(m.__file__) for m in modules]
    paths += [HERE/name for name in ('cardinality_oracle.h','cardinality_probe.cpp','novelty_oracle.h','novelty_probe.cpp')]
    hashes={str(p.relative_to(cf.ROOT)):cf.frozen.sha(p) for p in paths}
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names]
    result=evaluate(*rows,solvers,quiet_file=args.quiet_file)
    if (any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or cf.frozen.sha(manifest_path)!=sealed['original_hashes_manifest_sha256']
            or any(cf.frozen.sha(path)!=digest for path,digest in controls.items())
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('receipt floor evidence/build/implementation changed during analysis')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'],
        original_result_overwritten=False,solver_builds=builds,sources_sha256=hashes)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps([dict(model=r['model'],phase_ns=r.get('sampling_shift_ns'),minimum_p95_ns=r.get('minimum_p95_ns')) for r in result['best_tested_phase_per_model']]))


if __name__=='__main__':main()
