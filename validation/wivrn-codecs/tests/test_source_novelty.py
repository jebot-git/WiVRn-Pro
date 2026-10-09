import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('source_novelty_test', Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/source_novelty.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceNoveltyTests(unittest.TestCase):
    def test_grouping_preserves_all_encoded_tradeoffs(self):
        frames = [dict(frame=i, ready=10+i, receipt=i+1) for i in range(4)]
        grouped, contract = module.generation_groups(frames, {0:((1,10),),1:((1,10),),2:((1,11),),3:((1,12),)})
        self.assertEqual([f['generation'] for f in grouped], [0,0,1,2])
        self.assertEqual([f['ready'] for f in grouped], [10,11,12,13])
        self.assertEqual(contract['distinct_generation_sets'], 3)
        self.assertNotIn('generation', frames[0])

    def test_noncontiguous_recurrence_missing_join_and_bad_order_reject(self):
        for frames, generations in [
            ([dict(frame=i) for i in range(3)], {0:((1,10),),1:((1,11),),2:((1,10),)}),
            ([dict(frame=1)], {}),
            ([dict(frame=2),dict(frame=1)], {1:((1,10),),2:((1,11),)}),
            ([dict(frame=True)], {1:((1,10),)})]:
            with self.assertRaises(ValueError): module.generation_groups(frames, generations)

    def test_sets_canonicalize_order_but_never_duplicate_pacer(self):
        self.assertEqual(module.generation_key([[2,20],[1,10]]), ((1,10),(2,20)))
        for key in ([], [[True,1]], [[1,0]], [[1,2],[1,3]], [[1,2**63]], [[1]], '12'):
            with self.assertRaises(ValueError): module.generation_key(key)

    def test_actual_warm_generation_excludes_new_encoded_alias(self):
        known = {i:dict(frame=i,target=100+i) for i in range(1,5)}
        traces = [dict(selected=[dict(frame=1,view_display_time_xr_ns=101)])]
        present = [dict(left=1,submit_xr_ns=10)]
        generations = {1:((1,1),),2:((1,1),),3:((1,2),),4:((1,3),)}
        admitted, contract = module.novel_warm_population(known,traces,present,20,'observed_submission_prefix',generations)
        self.assertEqual([f['frame'] for f in admitted], [3,4])
        self.assertEqual(contract['additional_encoded_frames_excluded'], 1)
        self.assertEqual(contract['pre_warm_generation_sets_excluded'], 1)
        with self.assertRaises(ValueError):
            module.novel_warm_population(known,traces,present,20,'relaxed_known_universe',generations)

    def test_source_identity_and_ordinal_are_independently_audited(self):
        frames = [dict(frame=i,target=i+1,receipt=1,ready=1,generation=i) for i in range(2)]
        deadlines = [dict(sequence=i+1,selection=10*(i+1),submit=10*(i+1)+1) for i in range(2)]
        result = dict(schema=1,frontier=[dict(skips=0,required_count=2,feasible=True,
            maximum_good=2,certificate=[[1,0],[2,1]])])
        keys = {0:((1,1),),1:((1,2),)}
        contract = dict(pre_warm_generation_sets=[])
        args = [frames,frames,deadlines,None,0,100,{1:1,2:2},keys,contract]
        good = module.audit_novelty_frontier(copy.deepcopy(result),*args)
        self.assertEqual(good['frontier'][0]['distinct_source_generations'], 2)
        self.assertTrue(good['frontier'][0]['direct_generation_identity_checked'])
        for mode in range(3):
            bad = copy.deepcopy(args)
            if mode == 0: bad[-2][1] = ((1,1),) # Fake distinct ordinals cannot disguise identical identities.
            elif mode == 1: bad[-1]['pre_warm_generation_sets'] = [[[1,2]]]
            else:
                bad[0][1]['generation'] = 0
                bad[1][1]['generation'] = 0
            with self.assertRaises(ValueError): module.audit_novelty_frontier(copy.deepcopy(result),*bad)
