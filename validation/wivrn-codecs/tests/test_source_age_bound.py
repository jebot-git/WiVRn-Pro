import importlib.util
from pathlib import Path
import random
import unittest
from test_selection_counterfactual import fixture,sources
from test_source_receipt_age import exact_fixture

spec=importlib.util.spec_from_file_location('source_age_bracket_test',Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/source_age_bound.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class SourceAgeBracketTests(unittest.TestCase):
    def test_2048_exhaustive_finite_path_populations(self):
        rng=random.Random(71061)
        for _ in range(2048):
            n=rng.randrange(1,41);paths=[]
            for j in range(rng.randrange(1,15)):
                encoded=[rng.randrange(100) for _ in range(n)]
                source=[a+rng.randrange(25) for a in encoded];paths.append((encoded,source))
            best=min(paths,key=lambda p:m.age.q.percentile95(p[0]));low=m.age.q.percentile95(best[0])
            result=m.optimum_bracket(low,*best,threshold=75)
            actual=min(m.age.q.percentile95(p[1]) for p in paths)
            self.assertLessEqual(result['minimum_known_source_p95_lower_bound_ns'],actual)
            self.assertGreaterEqual(result['minimum_known_source_p95_upper_bound_ns'],actual)
            if result['threshold_verdict']=='impossible_in_declared_model':self.assertGreaterEqual(actual,75)
            if result['threshold_verdict']=='feasible_witness_in_declared_model':self.assertLess(actual,75)

    def test_strict_threshold_boundary_and_unresolved_interval(self):
        for low,high,expected in ((15,16,'impossible_in_declared_model'),(14,14,'feasible_witness_in_declared_model'),(14,15,'unresolved_between_bounds')):
            r=m.optimum_bracket(low,[low],[high],15);self.assertEqual(r['threshold_verdict'],expected)
            self.assertEqual(r['exact_source_optimum_proved'],low==high)
            self.assertFalse(r['source_age_reoptimization_performed'])

    def test_invalid_dominance_score_or_exact_types_rejected(self):
        for args in ((True,[1],[1]),(1,[2],[2]),(2,[2],[1]),(1,[],[]),(1,[1],[1,2]),(1,[True],[2]),(1,[1],[False]),(1,[1],[1],True)):
            with self.subTest(args=args),self.assertRaises(ValueError):m.optimum_bracket(*args)

    def test_full_pipeline_reaudits_known_universe_and_all_models(self):
        traces,presented=fixture();result=m.evaluate(traces,presented,sources(),exact_fixture(),warmup_ns=0)
        self.assertEqual(result['known_frame_origin_dominance']['known_frames_checked'],2)
        self.assertEqual(len(result['models']),4)
        for model in result['models']:
            self.assertEqual(model['minimum_known_source_p95_lower_bound_ns'],55)
            self.assertEqual(model['minimum_known_source_p95_upper_bound_ns'],55)
        self.assertFalse(result['latency_gate_changed'])

    def test_lower_bound_requires_unchanged_previously_qualified_artifact(self):
        m.require_pinned_exact(b'qualified bytes',b'qualified bytes')
        with self.assertRaises(ValueError):m.require_pinned_exact(b'changed plausible optimum',b'qualified bytes')
