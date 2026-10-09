#!/usr/bin/env python3
"""Cross-check one sealed full-count case with the earlier generic DAG solver."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('reference_full_count', HERE / 'full_count.py')
full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full)
cf = full.cf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('analysis', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    sealed = cf.frozen.analyze(run)
    analysis_hash = cf.frozen.sha(args.analysis)
    analysis = json.loads(args.analysis.read_text())
    if (sealed['status'] != 'pass' or analysis['original_run'] != str(run)
            or analysis['original_manifest_sha256'] != sealed['original_hashes_manifest_sha256']):
        raise ValueError('analysis is not bound to this sealed acquisition')
    for name, digest in analysis['sources_sha256'].items():
        path = (cf.ROOT / name).resolve()
        if not path.is_relative_to(cf.ROOT) or not path.is_file() or cf.frozen.sha(path) != digest:
            raise ValueError('analyzed source changed: ' + name)
    manifest_path = run / 'hashes.json'
    manifest = json.loads(manifest_path.read_text())
    names = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    if not set(names).issubset(manifest):
        raise ValueError('input not manifest-bound')
    traces, presented, sources = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    _, warm, _, _, _ = full.supply.envelope.population(traces, presented, sources, analysis['warmup_ns'], analysis['strict_age_threshold_ns'])
    known = sorted(full.supply.known_pairs(traces).values(), key=lambda p: p['frame'])
    shift, advance = 5_555_555, 4_000_000
    matches = [a for a in analysis['alternatives'] if a['sampling_shift_ns'] == shift and a['hypothetical_publication_advance_ns'] == advance]
    if len(matches) != 1:
        raise ValueError('unique comparison scenario missing')
    expected = matches[0]
    nodes = []
    for stage, deadline in enumerate(warm):
        sequence = deadline['sequence']
        row, present = traces[sequence - 1], presented[sequence - 1]
        selection, submit = row['selection_end_xr_ns'] + shift, present['submit_xr_ns'] + shift
        if full.supply.observed_admission(row, present, selection, submit)['before_recorded_admission']:
            raise ValueError('comparison precedes recorded admission')
        # This proven necessary rank band is independently covered by exhaustive
        # combinations in test_full_count.py; the reference DAG permits skips.
        for pair in known[stage:len(known) - len(warm) + stage + 1]:
            ready = max(max(f['receive_xr_ns'], f['ready_xr_ns'] - advance) for f in pair['pair'] if f['present'])
            if ready <= selection:
                nodes.append(dict(sequence=sequence, frame=pair['frame'], target=pair['target'],
                    age_ns=submit - pair['receipt'], threshold_ns=analysis['strict_age_threshold_ns']))
    result = full.oracle.optimal_chain(nodes, 'unique_first')
    if (not expected['full_count_feasible'] or result['unique_count'] != len(warm)
            or result['under_threshold_count'] != expected['maximum_under_threshold_count']):
        raise ValueError('independent generic DAG optimum differs from layered DP')
    if (any(cf.frozen.sha(run / name) != manifest[name] for name in names)
            or cf.frozen.sha(manifest_path) != sealed['original_hashes_manifest_sha256']
            or cf.frozen.sha(args.analysis) != analysis_hash):
        raise ValueError('evidence or analysis changed during reference check')
    receipt = dict(schema=1, status='pass', original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
        original_manifest_files_verified=sealed['original_manifest_files_verified'], analysis_sha256=analysis_hash,
        sources_sha256=analysis['sources_sha256'] | {str(Path(__file__).resolve().relative_to(cf.ROOT)): cf.frozen.sha(__file__)},
        sampling_shift_ns=shift, hypothetical_publication_advance_ns=advance,
        reference_nodes=len(nodes), full_deadline_count=len(warm), generic_dag_unique_count=result['unique_count'],
        generic_dag_maximum_good_count=result['under_threshold_count'], layered_dp_maximum_good_count=expected['maximum_under_threshold_count'],
        reference_certificate=[[nodes[i]['sequence'], nodes[i]['frame']] for i in result['path']],
        headset_operations=False, measured_speedup=False, latency_gate_evaluated=False,
        scope='Different exact optimization formulation agrees; same optimistic known-supply and timestamp assumptions remain.')
    with out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: receipt[k] for k in ('status', 'reference_nodes', 'full_deadline_count', 'generic_dag_maximum_good_count')}))


if __name__ == '__main__':
    main()
