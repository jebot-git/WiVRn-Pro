#!/usr/bin/env python3
"""Bracket known-source-age optima without claiming a new exact optimizer."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('source_age_bound_audit',HERE/'source_receipt_age.py')
age=importlib.util.module_from_spec(spec);spec.loader.exec_module(age)
cf=age.cf
EXACT_COMMIT='306920270d9a5b4601851e8fe3958f32e9306d5a'
EXACT_PATH='prototypes/vr444/experiments/selection_policy/phase-exact-20261009.json'


def require_pinned_exact(raw,pinned):
    if raw!=pinned:raise ValueError('exact-phase lower-bound basis differs from the qualified immutable milestone')


def optimum_bracket(encoded_minimum,encoded_witness,source_witness,threshold=15_000_000):
    if (type(encoded_minimum) is not int or encoded_minimum<0 or type(threshold) is not int or threshold<=0
            or type(encoded_witness) is not list or type(source_witness) is not list
            or not encoded_witness or len(encoded_witness)!=len(source_witness)):
        raise ValueError('exact optimum and equally sized nonempty age vectors required')
    low=age.q.percentile95(encoded_witness);high=age.q.percentile95(source_witness)
    if low!=encoded_minimum or any(a>b for a,b in zip(encoded_witness,source_witness)):
        raise ValueError('encoded optimum or pointwise source-age dominance differs')
    verdict=('impossible_in_declared_model' if low>=threshold else
        'feasible_witness_in_declared_model' if high<threshold else 'unresolved_between_bounds')
    return dict(minimum_known_source_p95_lower_bound_ns=low,minimum_known_source_p95_upper_bound_ns=high,
        bound_width_ns=high-low,exact_source_optimum_proved=high==low,
        strict_threshold_ns=threshold,threshold_verdict=verdict,
        upper_bound_is_feasible_rescored_witness=True,source_age_reoptimization_performed=False)


def evaluate(traces,presented,sources,exact,warmup_ns=2_000_000_000):
    audit=age.evaluate(traces,presented,sources,exact,warmup_ns=warmup_ns)
    _,_,generations,_,_=age.supply.envelope.population(traces,presented,sources,warmup_ns,15_000_000)
    known=age.supply.known_pairs(traces);origins=age.origins(known,generations)
    deltas=[]
    for frame,item in known.items():
        origin=origins[age.source_key(generations[frame])]
        delta=item['receipt']-origin['receipt']
        if type(delta) is not int or delta<0:raise ValueError('known-source origin violates pointwise dominance')
        deltas.append(delta)
    models={m['model']:m for m in exact['models']};rows=[]
    for witness in audit['exact_phase_winning_paths_reaudited']:
        model=models[witness['model']];samples=witness['samples']
        row=optimum_bracket(model['minimum_p95_ns'],[s['encoded_receive_age_ns'] for s in samples],
            [s['known_source_receive_age_ns'] for s in samples])
        row.update(model=witness['model'],witness_phase_ns=witness['phase_ns'],certificate_sha256=witness['certificate_sha256'],
            choices=len(samples),distinct_source_generations=model['distinct_source_generations'],target_periods=model['target_periods'],
            witness_older_known_source_origins=sum(s['additional_known_source_age_ns']>0 for s in samples))
        rows.append(row)
    return dict(schema=1,status='pass',analysis_kind='known_source_receipt_age_optimum_brackets',
        models=rows,full_window_minimum_count=exact['full_window_minimum_count'],
        first_phase_ns=0,last_phase_ns=11_111_111,known_frame_origin_dominance=dict(
            known_frames_checked=len(deltas),strictly_older_origins=sum(x>0 for x in deltas),maximum_extra_age_ns=max(deltas)),
        measured_speedup=False,runnable_policy=False,latency_gate_changed=False,promotion_eligible=False,
        proof=[
            'Every known source origin is at or before the chosen encoded receipt, so source age is pointwise at least encoded age for every feasible choice and phase.',
            'Nearest-rank p95 is monotone under componentwise age increases; minimizing over the same admitted path set preserves the encoded optimum as a lower bound.',
            'The independently reaudited encoded-optimal witness is also a legal source-age path; its rescored source p95 is an upper bound on the source optimum.',
            'No source-age reoptimization is required for these brackets. Unequal endpoints are not claimed to be exact source-age minima.',
            'The fixed exact-phase model, sufficient full-window count, zero publication delay, known supply, original receipt times and fixed target/warm-history restrictions remain unchanged.',
            'Source identity is not an input epoch or optical timestamp. No native runtime feedback, ownership, unseen-frame or earlier-receipt behavior is predicted.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    parser.add_argument('--exact-phase',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve();out=args.out.resolve()
    if out.is_relative_to(run):parser.error('output must remain outside immutable acquisition')
    exact_raw=args.exact_phase.read_bytes()
    pinned=subprocess.run(['git','-C',str(cf.ROOT),'cat-file','blob',EXACT_COMMIT+':'+EXACT_PATH],capture_output=True,timeout=30)
    if pinned.returncode:raise ValueError('qualified exact-phase milestone is unavailable')
    require_pinned_exact(exact_raw,pinned.stdout)
    exact=json.loads(exact_raw);sealed=cf.frozen.analyze(run)
    if sealed['status']!='pass' or exact.get('original_manifest_sha256')!=sealed['original_hashes_manifest_sha256']:
        raise ValueError('exact phase and original acquisition differ')
    hashes=dict(exact['sources_sha256'])
    modules=(age,age.supply,age.supply.envelope,age.supply.oracle,cf,cf.frozen,cf.trace,cf.replay,cf.source,
        age.q,age.q.novelty,age.q.target,age.q.cardinality)
    for name,digest in hashes.items():
        if cf.frozen.sha(cf.ROOT/name)!=digest:raise ValueError('exact phase source differs: '+name)
    for path in [Path(__file__)]+[Path(m.__file__) for m in modules]:hashes[str(path.relative_to(cf.ROOT))]=cf.frozen.sha(path)
    manifest_path=run/'hashes.json';manifest_raw=manifest_path.read_bytes();manifest=json.loads(manifest_raw)
    if hashlib.sha256(manifest_raw).hexdigest()!=sealed['original_hashes_manifest_sha256']:raise ValueError('original manifest differs')
    names=('client/codec-test/selection-trace.jsonl','client/codec-test-present.jsonl','host/source-trace.jsonl')
    if not set(names).issubset(manifest):raise ValueError('source-age inputs not bound')
    rows=[[json.loads(line) for line in (run/name).read_text().splitlines()] for name in names]
    result=evaluate(*rows,exact)
    if (args.exact_phase.read_bytes()!=exact_raw or manifest_path.read_bytes()!=manifest_raw
            or any(cf.frozen.sha(run/name)!=digest for name,digest in manifest.items())
            or any(cf.frozen.sha(cf.ROOT/name)!=digest for name,digest in hashes.items())):
        raise ValueError('source-age bound evidence or implementation changed')
    result.update(original_run=str(run),original_run_status=sealed['original_run_status'],
        original_manifest_sha256=sealed['original_hashes_manifest_sha256'],original_manifest_files_verified=len(manifest),
        original_result_overwritten=False,exact_phase_sha256=hashlib.sha256(exact_raw).hexdigest(),
        exact_phase_qualified_commit=EXACT_COMMIT,exact_phase_solver_rerun=False,sources_sha256=hashes)
    with out.open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(result['models']))


if __name__=='__main__':main()
