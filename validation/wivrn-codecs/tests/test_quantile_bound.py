import copy
import importlib.util
from pathlib import Path
import random
import unittest

spec=importlib.util.spec_from_file_location('quantile_bound_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/quantile_bound.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def query_for(paths):
    def query(threshold):
        best=max(enumerate(paths),key=lambda pair:sum(age<threshold for age in pair[1]))
        i,ages=best
        return dict(feasible=True,required_count=len(ages),maximum_good=sum(age<threshold for age in ages),
            audited_ages_ns=ages,certificate=[[j,i] for j in range(len(ages))],certificate_sha256=str(i),
            conditional_unique_frame_cadence_hz=90)
    return query


class QuantileBoundTests(unittest.TestCase):
    def test_exact_integer_boundary_against_512_exhaustive_path_populations(self):
        rng=random.Random(2026100909)
        for _ in range(512):
            n=rng.randrange(1,41)
            paths=[[rng.randrange(0,100) for _ in range(n)] for _ in range(rng.randrange(1,31))]
            expected=min(sorted(path)[(n*95+99)//100-1] for path in paths)
            actual=module.minimum_p95(query_for(paths),n,seed_threshold=rng.randrange(1,100))
            self.assertEqual(actual['minimum_p95_ns'],expected)
            self.assertEqual(actual['first_passing_strict_threshold_ns'],expected+1)
            self.assertEqual(actual['last_failing_strict_threshold_ns'],expected)

    def test_zero_age_and_strict_fifteen_millisecond_boundary(self):
        self.assertEqual(module.minimum_p95(query_for([[0]]),1)['minimum_p95_ns'],0)
        result=module.minimum_p95(query_for([[15_000_000]*20]),20)
        self.assertEqual(result['minimum_p95_ns'],15_000_000)
        self.assertEqual(result['last_failing_maximum_good'],0)
        self.assertEqual(result['passing_maximum_good'],20)

    def test_removing_worst_ages_cannot_worsen_nearest_rank_p95(self):
        rng=random.Random(0x95)
        for _ in range(512):
            ages=sorted(rng.randrange(1000) for _ in range(rng.randrange(2,100)))
            count=rng.randrange(1,len(ages))
            self.assertLessEqual(module.percentile95(ages[:count]),module.percentile95(ages))

    def test_bad_query_types_scores_or_negative_ages_reject(self):
        base=query_for([[1,2]])(10)
        for mode in range(6):
            bad=copy.deepcopy(base)
            if mode==0: bad['feasible']=1
            elif mode==1: bad['required_count']=True
            elif mode==2: bad['maximum_good']=True
            elif mode==3: bad['audited_ages_ns']=[-1,2]
            elif mode==4: bad['audited_ages_ns']=[1]
            else: bad['maximum_good']=0
            with self.assertRaises(ValueError): module.minimum_p95(lambda _:bad,2,10)
        result=module.minimum_p95(lambda _:dict(feasible=False,maximum_good=-1,required_count=2),2)
        self.assertFalse(result['feasible'])
