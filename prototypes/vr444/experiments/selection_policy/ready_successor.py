#!/usr/bin/env python3
"""Require an observed ready successor before advancing a frozen selection."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('successor_counterfactual', HERE / 'counterfactual.py')
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)


def choose(record, numerator=1, denominator=1, previous=None):
    result = cf.choose(record, numerator, denominator, previous)
    prior = record['previous'] if previous is None else previous
    history = record['history']
    alpha = bool(history[0]['present'] and history[0]['alpha'])
    eligible = result['eligible_left_slots'][:]
    successors = [-1]*3
    target, bound = record['predicted_display_xr_ns'], result['max_ahead_xr_ns']

    def slots_for(slot):
        left = history[slot]
        if not left['present'] or left['alpha'] != alpha or left['view_display_time_xr_ns'] <= 0:
            return None
        slots = [slot, -1, -1]
        for eye in range(1, 2+int(alpha)):
            slots[eye] = next((i for i, f in enumerate(history[eye*3:eye*3+3]) if cf.replay.same(left, f, True)), -1)
        return slots if all(slots[eye] >= 0 for eye in range(2+int(alpha))) else None

    for slot, allowed in enumerate(eligible):
        if not allowed:
            continue
        candidate = history[slot]
        if candidate['frame'] <= record['closest'][0]['frame']:
            continue
        for index, successor in enumerate(history[:3]):
            if (not successor['present'] or successor['frame'] <= candidate['frame']
                    or successor['view_display_time_xr_ns'] <= candidate['view_display_time_xr_ns']
                    or successor['view_display_time_xr_ns'] > target+2*bound or slots_for(index) is None):
                continue
            successors[slot] = index
            break
        if successors[slot] < 0:
            eligible[slot] = False
    chosen = max((i for i in range(3) if eligible[i]), key=lambda i: history[i]['frame'], default=None)
    empty = [dict(decoder=i, present=False) for i in range(3)]
    slots = slots_for(chosen) if chosen is not None else [-1]*3
    selected = ([history[eye*3+i] if i >= 0 else empty[eye] for eye, i in enumerate(slots)] if chosen is not None
                else prior if cf.replay.coherent(prior) else empty)
    return result | dict(selected=selected, chosen_slots=slots, eligible_left_slots=eligible,
                         fallback=chosen is None, successor_left_slots=successors,
                         supporting_left_slots=sorted({i for i in successors if i >= 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside the immutable acquisition')
    sealed = cf.frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('observed candidate trace did not pass reanalysis')
    paths = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in paths]
    report = cf.evaluate(*rows, fractions=((1, 1),), selector=choose, selector_name='ready_successor')
    report['full_horizon_without_successor'] = cf.evaluate(*rows, fractions=((1, 1),))['alternatives']
    report['assumptions'].append('A ready successor is only observed in the snapshot; no resource is reserved or kept alive for it.')
    manifest = json.loads((run / 'hashes.json').read_text())
    for name in paths:
        if cf.frozen.sha(run / name) != manifest.get(name):
            raise ValueError('input changed during analysis: ' + name)
    sources = [Path(__file__), HERE / 'ready_successor_policy.h', HERE / 'horizon_policy.h', Path(cf.__file__),
               Path(cf.frozen.__file__), Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    report.update(original_run=str(run), original_run_status=sealed['original_run_status'],
                  original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
                  original_manifest_files_verified=sealed['original_manifest_files_verified'], original_result_overwritten=False,
                  sources_sha256={str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in sources})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'selector': report['selector'], 'measured_speedup': False}))


if __name__ == '__main__':
    main()
