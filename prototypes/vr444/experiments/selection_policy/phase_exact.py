#!/usr/bin/env python3
"""Exact bounded integer-nanosecond phase search using readiness breakpoints."""
from bisect import bisect_left,bisect_right


def critical_phases(frames,deadlines,ceilings,last_phase):
    """All edge-admission changes; targets and identity order stay fixed."""
    if type(last_phase) is not int or not 0<=last_phase<=15_000_000:
        raise ValueError('bounded exact nonnegative last phase required')
    if len(deadlines)!=len(ceilings):raise ValueError('target ceiling population differs')
    if any(type(f.get(k)) is not int or not 0<f[k]<2**63 for f in frames for k in ('ready','target')):
        raise ValueError('exact positive frame readiness/target required')
    if any(c is not None and (type(c) is not int or not 0<=c<2**63) for c in ceilings):
        raise ValueError('exact nonnegative target ceiling required')
    ordered=sorted(frames,key=lambda f:f['ready']);ready=[f['ready'] for f in ordered]
    events={0,last_phase};edge_events=0
    for d,ceiling in zip(deadlines,ceilings):
        selection=d['selection']
        if type(selection) is not int or not 0<selection<2**63-last_phase:
            raise ValueError('selection phase range invalid')
        for frame in ordered[bisect_left(ready,selection):bisect_right(ready,selection+last_phase)]:
            if ceiling is not None and frame['target']>ceiling:continue
            events.add(frame['ready']-selection);edge_events+=1
    if len(events)>200_000:raise ValueError('phase event population exceeds explicit bound')
    return sorted(events),edge_events


def minimize(query,events,seeds):
    """Minimize exact F(s), where F(s)-s is nonincreasing when feasible.

    Readiness-only admission grows with phase. Every admitted path's complete
    age vector increases by s, so its p95 also increases by s. Thus on [a,b],
    F(s)>=F(b)-(b-a). Exact endpoint bounds prune candidate intervals safely.
    """
    if (type(events) not in (list,tuple) or not events or list(events)!=sorted(set(events))
            or any(type(s) is not int or s<0 for s in events)):
        raise ValueError('sorted distinct exact phase events required')
    if type(seeds) not in (list,tuple) or not seeds or any(type(s) is not int or s not in events for s in seeds):
        raise ValueError('seed phases must be exact integer candidate events')
    points=list(events);cache={};pruned=[]
    def run(s):
        if s not in cache:
            result=query(s)
            if type(result.get('feasible')) is not bool:raise ValueError('exact feasibility flag required')
            if result['feasible']:
                q=result.get('minimum_p95_ns')
                if type(q) is not int or q<0:raise ValueError('exact nonnegative optimum required')
            elif result.get('minimum_p95_ns') is not None:
                raise ValueError('infeasible phase has an optimum')
            cache[s]=result
            phases=sorted(cache)
            for a,b in zip(phases,phases[1:]):
                if cache[a]['feasible'] and (not cache[b]['feasible'] or cache[a]['minimum_p95_ns']-a<cache[b]['minimum_p95_ns']-b):
                    raise ValueError('exact phase optimum violates increasing-admission dominance')
        return cache[s]
    for s in sorted(set(seeds)|{points[0],points[-1]}):run(s)
    def best():
        feasible=[(r['minimum_p95_ns'],s) for s,r in cache.items() if r['feasible']]
        return min(feasible) if feasible else None
    anchors=sorted(points.index(s) for s in cache)
    pending=list(zip(anchors,anchors[1:]))
    while pending:
        i,j=pending.pop()
        if i+1>=j:continue
        a,b=points[i+1],points[j]
        end=run(b);chosen=best()
        if not end['feasible']:
            pruned.append(dict(first_index=i+1,last_index=j-1,upper_phase=b,reason='upper_endpoint_infeasible'))
            continue
        lower=end['minimum_p95_ns']-(b-a)
        if chosen is not None and (lower>chosen[0] or lower==chosen[0] and a>=chosen[1]):
            pruned.append(dict(first_index=i+1,last_index=j-1,upper_phase=b,
                exact_upper_endpoint_p95_ns=end['minimum_p95_ns'],minimum_interval_p95_ns=lower,
                reason='monotone_intercept_lower_bound'))
            continue
        middle=(i+j)//2;run(points[middle])
        pending.extend(((i,middle),(middle,j)))
    chosen=best();covered=set(cache)
    for item in pruned:
        if item['reason']=='monotone_intercept_lower_bound' and (chosen is None or item['minimum_interval_p95_ns']<chosen[0]):
            raise ValueError('pruned interval does not certify final optimum')
        covered.update(points[item['first_index']:item['last_index']+1])
    if covered!=set(points):raise ValueError('phase proof does not cover every candidate event')
    return dict(feasible=chosen is not None,minimum_p95_ns=chosen[0] if chosen else None,
        best_phase_ns=chosen[1] if chosen else None,candidate_phase_count=len(points),
        evaluated_phase_count=len(cache),pruned_candidate_count=len(points)-len(cache),
        evaluated={str(s):r for s,r in sorted(cache.items())},pruned_intervals=pruned,
        every_candidate_covered=True)
