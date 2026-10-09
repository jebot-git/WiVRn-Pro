import importlib.util
from pathlib import Path
import random
import unittest

spec=importlib.util.spec_from_file_location('phase_exact_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/phase_exact.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
spec=importlib.util.spec_from_file_location('phase_exact_run_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/phase_exact_run.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class ExactPhaseTests(unittest.TestCase):
    def test_seed_is_a_hint_until_both_exact_boundaries_are_requalified(self):
        ages=list(range(1,21));calls=[]
        def query(threshold):
            calls.append(threshold)
            return dict(feasible=True,required_count=20,maximum_good=sum(a<threshold for a in ages),audited_ages_ns=ages,
                certificate=[[i,i] for i in range(20)],certificate_sha256='test',conditional_unique_frame_cadence_hz=88)
        r=runner.verified_seed(dict(feasible=True,minimum_p95_ns=19,certificate_sha256='test'),query,20)
        self.assertEqual(calls,[20,19]);self.assertTrue(r['seed_boundary_requalified'])
        for guess in (18,20,True):
            with self.assertRaises(ValueError):runner.verified_seed(dict(feasible=True,minimum_p95_ns=guess,certificate_sha256='test'),query,20)

    def test_1024_exhaustive_integer_phase_populations(self):
        rng=random.Random(3199)
        for _ in range(1024):
            end=rng.randrange(1,151)
            # Each legal full path becomes available at its largest required
            # readiness shift; its age-vector p95 then rises one ns per ns.
            paths=[(rng.randrange(end+10),rng.randrange(1000)) for _ in range(rng.randrange(1,25))]
            def query(s):
                scores=[base+s for admission,base in paths if admission<=s]
                return dict(feasible=bool(scores),minimum_p95_ns=min(scores) if scores else None)
            events=sorted({0,end}|{a for a,_ in paths if a<=end})
            result=module.minimize(query,events,[0,end])
            brute=[(query(s)['minimum_p95_ns'],s) for s in range(end+1) if query(s)['feasible']]
            self.assertEqual(result['feasible'],bool(brute))
            if brute:self.assertEqual((result['minimum_p95_ns'],result['best_phase_ns']),min(brute))
            self.assertTrue(result['every_candidate_covered'])

    def test_readiness_breakpoints_cover_every_fixed_target_edge_change(self):
        rng=random.Random(8315)
        for _ in range(512):
            frames=[dict(ready=rng.randrange(1,70),target=rng.randrange(1,40)) for _ in range(rng.randrange(1,12))]
            deadlines=[dict(selection=s) for s in sorted(rng.sample(range(1,40),rng.randrange(1,8)))]
            ceilings=[rng.randrange(40) if rng.randrange(2) else None for d in deadlines]
            events,_=module.critical_phases(frames,deadlines,ceilings,20)
            def edges(s):return {(i,j) for i,f in enumerate(frames) for j,d in enumerate(deadlines)
                if f['ready']<=d['selection']+s and (ceilings[j] is None or f['target']<=ceilings[j])}
            for a,b in zip(events,events[1:]):
                for s in range(a,b):self.assertEqual(edges(a),edges(s))
            for s in range(1,21):
                if edges(s)!=edges(s-1):self.assertIn(s,events)

    def test_exact_ties_preserve_earliest_minimizing_phase(self):
        def query(s):return dict(feasible=True,minimum_p95_ns=(100+s if s<5 else 100+s-5))
        r=module.minimize(query,list(range(11)),[0,10])
        self.assertEqual((r['minimum_p95_ns'],r['best_phase_ns']),(100,0))
        r=module.minimize(lambda s:dict(feasible=True,minimum_p95_ns=10),list(range(11)),[10])
        self.assertEqual(r['best_phase_ns'],0)

    def test_invalid_events_or_nonmonotone_oracle_rejected(self):
        good=lambda s:dict(feasible=True,minimum_p95_ns=s)
        for events,seeds in (([],[0]),([0,True],[0]),([1,0],[0]),([0,0],[0]),([0,1],[2]),([0,1],[True])):
            with self.assertRaises(ValueError):module.minimize(good,events,seeds)
        for bad in (lambda s:dict(feasible=s==0,minimum_p95_ns=1 if s==0 else None),
                    lambda s:dict(feasible=True,minimum_p95_ns=2*s),
                    lambda s:dict(feasible=1,minimum_p95_ns=s),
                    lambda s:dict(feasible=True,minimum_p95_ns=True)):
            with self.assertRaises(ValueError):module.minimize(bad,[0,1],[0,1])
        for frame in (dict(ready=True,target=1),dict(ready=1,target=-1),dict(ready=2**63,target=1)):
            with self.assertRaises(ValueError):module.critical_phases([frame],[dict(selection=1)],[None],10)
