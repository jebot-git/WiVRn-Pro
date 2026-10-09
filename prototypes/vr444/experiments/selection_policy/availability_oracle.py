#!/usr/bin/env python3
"""Optimistic frozen-history upper bounds, not causal selection simulations."""
import argparse
from bisect import bisect_left, bisect_right
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('oracle_counterfactual', HERE / 'counterfactual.py')
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)


class PrefixBest:
    """Sparse 2-D Fenwick prefix maximum: strictly earlier frame, <= target."""
    def __init__(self, nodes):
        self.frames = sorted({n['frame'] for n in nodes})
        self.targets = [[] for _ in range(len(self.frames) + 1)]
        for n in nodes:
            index = bisect_left(self.frames, n['frame']) + 1
            while index < len(self.targets):
                self.targets[index].append(n['target'])
                index += index & -index
        self.targets = [sorted(set(values)) for values in self.targets]
        self.values = [[None] * (len(values) + 1) for values in self.targets]

    @staticmethod
    def better(a, b):
        if a is None:
            return b
        if b is None:
            return a
        # Score first; earliest node breaks exact ties deterministically.
        return max(a, b, key=lambda item: (item[0], -item[1]))

    def query(self, frame, target):
        index = bisect_left(self.frames, frame)
        best = None
        while index:
            inner = bisect_right(self.targets[index], target)
            while inner:
                best = self.better(best, self.values[index][inner])
                inner -= inner & -inner
            index -= index & -index
        return best

    def update(self, frame, target, value):
        index = bisect_left(self.frames, frame) + 1
        while index < len(self.targets):
            inner = bisect_left(self.targets[index], target) + 1
            while inner < len(self.values[index]):
                self.values[index][inner] = self.better(value, self.values[index][inner])
                inner += inner & -inner
            index += index & -index


def optimal_chain(nodes, objective):
    """At most one choice per observed deadline; strictly advancing frame IDs.

    Scores count unique selections and strictly under-threshold selections.
    Waiting/repeating/empty output costs nothing, and warm-start ownership is
    unconstrained. Those relaxations make this an optimistic count bound.
    """
    if objective not in ('unique_first', 'under_threshold_first'):
        raise ValueError('unknown oracle objective')
    if any(type(n) is not dict or any(type(n.get(k)) is not int for k in
            ('sequence', 'frame', 'target', 'age_ns', 'threshold_ns')) for n in nodes):
        raise ValueError('oracle nodes must contain exact integer fields')
    if any(n['sequence'] < 1 or n['frame'] < 0 or n['target'] <= 0 or n['age_ns'] < 0 or n['threshold_ns'] <= 0 for n in nodes):
        raise ValueError('oracle node range invalid')
    if any(a['sequence'] > b['sequence'] for a, b in zip(nodes, nodes[1:])):
        raise ValueError('oracle nodes must be in observed-deadline order')
    if not nodes:
        return dict(score=[0, 0], path=[], unique_count=0, under_threshold_count=0)
    tree = PrefixBest(nodes)
    scores, parents = [], []
    best = None
    start = 0
    while start < len(nodes):
        end = start + 1
        while end < len(nodes) and nodes[end]['sequence'] == nodes[start]['sequence']:
            end += 1
        for index in range(start, end):
            n = nodes[index]
            predecessor = tree.query(n['frame'], n['target'])
            prior_score, parent = predecessor if predecessor is not None else ((0, 0), -1)
            good = int(n['age_ns'] < n['threshold_ns'])
            weight = (1, good) if objective == 'unique_first' else (good, 1)
            score = tuple(a + b for a, b in zip(prior_score, weight))
            scores.append(score)
            parents.append(parent)
            best = tree.better(best, (score, index))
        # Batch insertion prevents two choices at the same deadline.
        for index in range(start, end):
            n = nodes[index]
            tree.update(n['frame'], n['target'], (scores[index], index))
        start = end
    selected = []
    index = best[1]
    while index >= 0:
        selected.append(index)
        index = parents[index]
    selected.reverse()
    path = [nodes[i] for i in selected]
    for a, b in zip(path, path[1:]):
        if not (a['sequence'] < b['sequence'] and a['frame'] < b['frame'] and a['target'] <= b['target']):
            raise ValueError('oracle certificate violates path constraints')
    return dict(score=list(best[0]), path=selected, unique_count=len(path),
                under_threshold_count=sum(n['age_ns'] < n['threshold_ns'] for n in path))



def best_quantile_at_max_count(nodes, percentile=95):
    """Exact nearest-rank quantile optimum among maximum-cardinality paths."""
    if type(percentile) is not int or not 1 <= percentile <= 100:
        raise ValueError('invalid oracle percentile')
    maximum = optimal_chain(nodes, 'unique_first')['unique_count']
    if maximum == 0:
        return dict(unique_count=0, percentile=percentile, minimum_age_ns=None, path=[])
    rank = (percentile * maximum + 99) // 100
    ages = sorted({n['age_ns'] for n in nodes})
    lo, hi = 0, len(ages) - 1
    solution = None
    while lo < hi:
        middle = (lo + hi) // 2
        candidate = optimal_chain([n | {'threshold_ns': ages[middle] + 1} for n in nodes], 'unique_first')
        if candidate['under_threshold_count'] >= rank:
            hi = middle
        else:
            lo = middle + 1
    solution = optimal_chain([n | {'threshold_ns': ages[lo] + 1} for n in nodes], 'unique_first')
    if solution['unique_count'] != maximum or solution['under_threshold_count'] < rank:
        raise ValueError('quantile optimum certificate invalid')
    observed = sorted(nodes[i]['age_ns'] for i in solution['path'])[rank - 1]
    if observed != ages[lo]:
        raise ValueError('quantile path does not attain declared optimum')
    return dict(unique_count=maximum, percentile=percentile, minimum_age_ns=ages[lo], path=solution['path'])


def complete_pairs(record):
    history = record['history']
    alpha = bool(history[0]['present'] and history[0]['alpha'])
    empty = [dict(decoder=i, present=False) for i in range(3)]
    pairs = []
    for slot, left in enumerate(history[:3]):
        if not left['present'] or left['alpha'] != alpha or left['view_display_time_xr_ns'] <= 0:
            continue
        pair = [left]
        for eye in range(1, 2 + int(alpha)):
            pair.append(next((f for f in history[eye*3:eye*3+3] if cf.replay.same(left, f, True)), empty[eye]))
        if not alpha:
            pair.append(empty[2])
        if cf.replay.coherent(pair):
            pairs.append(pair)
    # The actual retained pair is visible too. Allowing its acquisition by an
    # alternate path is deliberately optimistic: ownership is not simulated.
    if cf.replay.coherent(record['previous']):
        pairs.append(record['previous'])
    unique = {}
    for pair in pairs:
        frame = pair[0]['frame']
        if frame in unique and not cf.replay.snapshot_equal(pair, unique[frame]):
            raise ValueError('one wire frame has conflicting complete snapshots')
        unique[frame] = pair
    return list(unique.values())


def evaluate(traces, presented, source_rows, fractions=((0, 1), (1, 2), (1, 1)),
             warmup_ns=2_000_000_000, threshold_ns=15_000_000):
    if any(type(x) is not int or x < 0 for x in (warmup_ns, threshold_ns)) or threshold_ns == 0:
        raise ValueError('invalid warmup or strict age threshold')
    if (not isinstance(fractions, (tuple, list)) or not fractions or
            any(not isinstance(f, (tuple, list)) or len(f) != 2 or
                any(type(v) is not int for v in f) or not 0 <= f[0] <= f[1] <= 1024 or f[1] == 0 for f in fractions)):
        raise ValueError('invalid future-limit fractions')
    if len(set(tuple(f) for f in fractions)) != len(fractions):
        raise ValueError('duplicate future-limit fractions')
    verified = cf.trace.evaluate(traces, presented)
    if verified['status'] != 'pass' or len(traces) != len(presented):
        raise ValueError('complete valid observed candidate trace required')
    window = presented[0]['submit_xr_ns'] + warmup_ns
    warm = [(index, row, p) for index, (row, p) in enumerate(zip(traces, presented), 1) if p['submit_xr_ns'] >= window]
    if not warm:
        raise ValueError('no warm observed deadlines')
    snapshots, all_pairs = {}, []
    needed = {p['left'] for _, _, p in warm}
    for index, row, p in warm:
        pairs = complete_pairs(row)
        for pair in pairs:
            frame = pair[0]['frame']
            if frame in snapshots and not cf.replay.snapshot_equal(pair, snapshots[frame]):
                raise ValueError('wire frame metadata changes across observed snapshots')
            snapshots[frame] = pair
            needed.add(frame)
        all_pairs.append(pairs)
    joined = cf.source.evaluate(source_rows, needed)
    if joined['status'] != 'pass' or joined.get('ambiguous_projection_matches'):
        raise ValueError('unambiguous source joins required for every available complete pair')
    generations = {r['system_frame_id']: tuple((a['pacer_id'], a['app_frame_id']) for a in r['app_frames'])
                   for r in joined['system_to_app_frames']}
    if any(not ids or len(dict(ids)) != len(ids) for ids in generations.values()):
        raise ValueError('source joins have missing or ambiguous application generations')
    alternatives = []
    for numerator, denominator in fractions:
        nodes = []
        warm_any = warm_good = 0
        for (index, row, p), pairs in zip(warm, all_pairs):
            full = (min(row['predicted_display_period_xr_ns'],
                        row['predicted_display_xr_ns'] - row['previous_predicted_display_xr_ns'])
                    if row['predicted_display_period_xr_ns'] and row['previous_predicted_display_xr_ns'] else 0)
            bound = full * numerator // denominator
            deadline_nodes = []
            for pair in pairs:
                target = pair[0]['view_display_time_xr_ns']
                if target > row['predicted_display_xr_ns'] + bound:
                    continue
                if any(f['ready_xr_ns'] > row['selection_end_xr_ns'] for f in pair if f['present']):
                    raise ValueError('oracle uses frame not ready at observed selection')
                age = p['submit_xr_ns'] - min(f['receive_xr_ns'] for f in pair[:2])
                if age < 0:
                    raise ValueError('oracle frame receipt is later than observed submit')
                deadline_nodes.append(dict(sequence=index, frame=pair[0]['frame'], target=target,
                        age_ns=age, threshold_ns=threshold_ns, submit_xr_ns=p['submit_xr_ns']))
            nodes += sorted(deadline_nodes, key=lambda n: n['frame'])
            warm_any += bool(deadline_nodes)
            warm_good += any(n['age_ns'] < threshold_ns for n in deadline_nodes)
        paths = []
        for objective in ('unique_first', 'under_threshold_first'):
            solution = optimal_chain(nodes, objective)
            selected = [nodes[i] for i in solution.pop('path')]
            solution.update(objective=objective,
                conditional_first_receive_age_ms=cf.distribution([n['age_ns'] / 1e6 for n in selected]),
                unique_latched_app_sets=len({generations[n['frame']] for n in selected}),
                certificate=[[n['sequence'], n['frame']] for n in selected])
            paths.append(solution)
        quantile = best_quantile_at_max_count(nodes)
        quantile['certificate'] = [[nodes[i]['sequence'], nodes[i]['frame']] for i in quantile.pop('path')]
        alternatives.append(dict(future_limit_numerator=numerator, future_limit_denominator=denominator,
            available_nodes=len(nodes), deadlines_with_any_pair=warm_any,
            deadlines_with_any_pair_under_threshold=warm_good, oracle_paths=paths,
            best_p95_at_maximum_unique_count=quantile))
    return dict(schema=1, status='pass', analysis_kind='optimistic_frozen_availability_count_bound',
                warm_observed_deadlines=len(warm), observed_deadline_span_ns=warm[-1][2]['submit_xr_ns'] - warm[0][2]['submit_xr_ns'],
                warmup_ns=warmup_ns, strict_age_threshold_ns=threshold_ns,
                observed_trace_validation=verified, source_validation={k: v for k, v in joined.items() if k != 'system_to_app_frames'},
                alternatives=alternatives, measured_speedup=False, latency_gate_evaluated=False,
                promotion_eligible=False, release_ready=False, assumptions=[
                    'Each decision uses only complete recorded history or actually retained pairs, at original submit times.',
                    'Warm-start retained state and all ownership costs are relaxed; any first visible pair can start a path.',
                    'At most one advancing wire ID per observed deadline; frame IDs strictly advance and source targets do not regress.',
                    'Waiting, repeated content and empty output cost nothing; these are optimistic count bounds, not runnable policies.',
                    'Future source targets are limited by the declared fraction of the recorded runtime interval bound.',
                    'Closest-target and successor guards are relaxed. Their exact policy behavior is not simulated.',
                    'Original diagnostic overhead, transport, decode, skipped attempts, scheduling and feedback are frozen.',
                    'Source joins establish latched app IDs only; no source-clock subtraction or input/photon freshness is inferred.',
                    'An oracle ceiling below a desired count limits this frozen data model, not possible live improvements.',
                    'Oracle paths are separate objectives; their best counts need not be jointly attainable.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if out.is_relative_to(run):
        parser.error('output must be outside immutable acquisition')
    sealed = cf.frozen.analyze(run)
    if sealed['status'] != 'pass':
        raise ValueError('immutable acquisition diagnostic reanalysis did not pass')
    names = ('client/codec-test/selection-trace.jsonl', 'client/codec-test-present.jsonl', 'host/source-trace.jsonl')
    rows = [[json.loads(line) for line in (run / name).read_text().splitlines()] for name in names]
    result = evaluate(*rows)
    manifest = json.loads((run / 'hashes.json').read_text())
    for name in names:
        if cf.frozen.sha(run / name) != manifest.get(name):
            raise ValueError('input changed during analysis')
    dependencies = [Path(__file__), Path(cf.__file__), Path(cf.frozen.__file__),
                    Path(cf.trace.__file__), Path(cf.replay.__file__), Path(cf.source.__file__)]
    result.update(original_run=str(run), original_run_status=sealed['original_run_status'],
                  original_manifest_sha256=sealed['original_hashes_manifest_sha256'],
                  original_manifest_files_verified=sealed['original_manifest_files_verified'], original_result_overwritten=False,
                  sources_sha256={str(p.relative_to(cf.ROOT)): cf.frozen.sha(p) for p in dependencies})
    with out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(status='pass', warm_deadlines=result['warm_observed_deadlines'],
        counts=[dict(future_limit=[a['future_limit_numerator'], a['future_limit_denominator']],
            any_under15=a['deadlines_with_any_pair_under_threshold'],
            paths=[dict(objective=p['objective'], unique=p['unique_count'], under15=p['under_threshold_count']) for p in a['oracle_paths']])
                for a in result['alternatives']])))


if __name__ == '__main__':
    main()
