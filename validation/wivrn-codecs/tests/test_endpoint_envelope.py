import importlib.util
from itertools import product
from pathlib import Path
import random
import unittest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('endpoint_envelope_test',ROOT/'prototypes/vr444/experiments/selection_policy/endpoint_envelope.py')
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)


def reference(times,options,hz,span,pairs):
    margins=[];not_ruled=0;qualified=under=0
    for first in range(len(times)-1):
        for last in range(first+1,len(times)):
            duration=times[last]-times[first]
            if duration<span:continue
            qualified+=1;count=max(pairs,(hz*duration+999_999_999)//1_000_000_000+1)
            if count>last-first+1:under+=1;continue
            window=options[first:last+1]
            maximum=exact_good_chain(window)
            margin=maximum-(95*count+99)//100
            not_ruled+=margin>=0;margins.append((margin,-duration,-first))
    return not_ruled,qualified,under,max(margins) if margins else None


def exact_good_chain(options):
    best=0
    def walk(index,last,count):
        nonlocal best
        if count+len(options)-index<=best:return
        if index==len(options):best=max(best,count);return
        walk(index+1,last,count)
        for value in options[index]:
            if value>last:walk(index+1,value,count+1)
    walk(0,-1,0);return best



class EndpointEnvelopeTests(unittest.TestCase):
    def test_512_random_populations_match_exhaustive_ordered_reference(self):
        rng=random.Random(11721)
        for _ in range(512):
            n=rng.randrange(2,10);times=[1_000_000_000]
            for i in range(n-1):times.append(times[-1]+rng.randrange(100_000_000,500_000_001))
            options=[{j for j in range(6) if rng.randrange(4)==0} for i in range(n)]
            hz=rng.randrange(1,5);span=rng.randrange(1,8)*100_000_000;pairs=rng.randrange(2,5)
            r=e.window_bound(times,options,hz,span,pairs);expected=reference(times,options,hz,span,pairs)
            self.assertEqual((r['intervals_not_ruled_out'],r['duration_qualifying_intervals'],r['insufficient_deadline_count_intervals']),expected[:3])
            best=r['best_necessary_margin_interval'];self.assertEqual((best['necessary_margin'],-best['span_ns'],-best['first_index']) if best else None,expected[3])

    def test_256_small_exhaustive_chain_populations_never_false_reject(self):
        rng=random.Random(781)
        for _ in range(256):
            n=rng.randrange(2,8);times=[1_000_000_000+i*250_000_000 for i in range(n)]
            options=[{j for j in range(5) if rng.randrange(3)==0} for i in range(n)]
            report=e.window_bound(times,options,2,250_000_000,2)
            passing=False
            for left in range(n-1):
                for right in range(left+1,n):
                    count=max(2,(2*(times[right]-times[left])+999_999_999)//1_000_000_000+1)
                    if count>right-left+1:continue
                    exact=exact_good_chain(options[left:right+1])
                    if exact>=(95*count+99)//100:passing=True
            if passing:self.assertFalse(report['every_endpoint_interval_ruled_out'])

    def test_same_deadline_alternatives_cannot_be_reused_by_chain(self):
        options=[{0},{0},{0},{1,2,3}]
        r=e.window_bound([1_000_000_000+i for i in range(4)],options,1,3,4)
        best=r['best_necessary_margin_interval']
        self.assertEqual(best['distinct_good_identities'],4)
        self.assertEqual(best['deadlines_with_good_choice'],4)
        self.assertEqual(best['exact_good_only_advancing_chain_length'],2)
        self.assertTrue(r['every_endpoint_interval_ruled_out'])
        self.assertFalse(r['path_found']);self.assertFalse(r['exact_feasibility_solved'])

    def test_nearest_rank_20_choices_requires_19_strictly_good(self):
        times=[1_000_000_000+i for i in range(20)]
        for good,possible in ((19,True),(18,False)):
            r=e.window_bound(times,[{i} if i<good else set() for i in range(20)],1,19,20)
            self.assertEqual(r['best_necessary_margin_interval']['necessary_good_choices'],19)
            self.assertEqual(r['any_qualifying_path_possible_by_necessary_counts'],possible)

    def test_strict_threshold_readiness_target_and_generation_identity(self):
        frames=[dict(frame=i,target=100+i,receipt=receipt,ready=ready,generation=7) for i,receipt,ready in
            ((1,85,85),(2,86,95),(3,96,99),(4,97,97))]
        deadlines=[dict(sequence=1,selection=98,submit=100)]
        self.assertEqual(e.good_options(frames,deadlines,None,False,15),[{2,4}])
        self.assertEqual(e.good_options(frames,deadlines,[102],False,15),[{2}])
        self.assertEqual(e.good_options(frames,deadlines,None,True,15),[{7}])

    def test_integer_cadence_boundary_and_clock_translation(self):
        times=[1_000_000_000,1_500_000_000,2_000_000_000]
        options=[{0},{1},{2}]
        r=e.window_bound(times,options,2,1_000_000_000,2)
        self.assertEqual(r['best_necessary_margin_interval']['minimum_choices'],3)
        self.assertEqual(r,e.window_bound([t+10**12 for t in times],options,2,1_000_000_000,2))
        r=e.window_bound([times[0],times[1],times[2]+1],options,2,1_000_000_000,2)
        self.assertEqual(r['insufficient_deadline_count_intervals'],1)
        self.assertTrue(r['every_endpoint_interval_ruled_out'])

    def test_malformed_or_boolean_controls_reject(self):
        times=[1,2];options=[{0},{1}]
        for bad in ([1,1],[True,2],[0,2]):
            with self.assertRaises(ValueError):e.window_bound(bad,options,1,1,2)
        for bad in ([{False},{1}],[{-1},{1}],[{10000},{1}],[[],{1}]):
            with self.assertRaises(ValueError):e.window_bound(times,bad,1,1,2)
        for values in ((True,1,2),(1,True,2),(1,1,True),(1,1,1),(0,1,2)):
            with self.assertRaises(ValueError):e.window_bound(times,options,*values)
