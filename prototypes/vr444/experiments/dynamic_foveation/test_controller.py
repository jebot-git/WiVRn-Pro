#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import math
import random
import unittest
from controller import Config, Gaze, footprints, make_map, spherical_tile_distance, projected_dot_extrema


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tiles=footprints(576,640)
        self.gaze=Gaze(0,0,100000000)
    def make(self, **kw):
        args=dict(tiles=self.tiles,gaze=self.gaze,previous=Gaze(0,0,90000000),target_ns=110000000,eye=0,geometry_id='synthetic-left')
        args.update(kw)
        return make_map(**args)
    def test_monotonic_quantized_bounds(self):
        m=self.make()
        ordered=sorted(zip(m['distances'],m['q']))
        self.assertEqual([q for d,q in ordered],sorted(q for d,q in ordered))
        self.assertTrue(all(q in (1,2,4,8,16,32,64,128) for q in m['q']))
        self.assertTrue(all(q==1 for q,p in zip(m['q'],m['protected']) if p))
    def test_age_expands_protection(self):
        a=self.make(target_ns=100000000);b=self.make(target_ns=140000000)
        self.assertTrue(all(y<=x for x,y in zip(a['q'],b['q'])))
        self.assertGreater(sum(b['protected']),sum(a['protected']))
    def test_saccade_corridor_includes_previous(self):
        m=self.make(gaze=Gaze(20,0,100000000),previous=Gaze(-20,0,90000000))
        self.assertTrue(m['saccade'])
        for (l,r,b,t),q in zip(self.tiles,m['q']):
            if l<=-20<=r and b<=0<=t:self.assertEqual(q,1)
    def test_stale_invalid_future_fallback(self):
        for g in (Gaze(0,0,0),Gaze(0,0,120000000),Gaze(0,0,100000000,False),Gaze(math.nan,0,100000000)):
            m=self.make(gaze=g)
            self.assertTrue(m['fallback']);self.assertEqual(set(m['q']),{1})
    def test_independent_eyes(self):
        a=self.make(gaze=Gaze(-25,0,100000000),previous=None)
        b=self.make(tiles=footprints(576,640,(-40,50,-42,48)),gaze=Gaze(25,0,100000000),previous=None,eye=1,geometry_id='synthetic-right')
        self.assertNotEqual(a['q'],b['q']);self.assertNotEqual(a['geometry_id'],b['geometry_id'])
    def test_partial_tiles_and_boundary(self):
        tiles=footprints(17,19,(-45,45,-45,45),False)
        self.assertEqual(len(tiles),4)
        m=self.make(tiles=tiles,gaze=Gaze(tiles[0][1],0,100000000),previous=None)
        self.assertEqual(m['q'][0],1);self.assertEqual(m['q'][1],1)
    def test_off_axis_spherical_guard(self):
        for pitch in (-70,-40,40,70):
            # Compare exact minimum to dense independent spherical point samples.
            tile=(12,18,pitch-2,pitch+2)
            exact=spherical_tile_distance(0,pitch,tile)
            sampled=[]
            for x in (12,15,18):
                for y in (pitch-2,pitch-1,pitch,pitch+1,pitch+2):
                    u,v,g=math.tan(math.radians(x)),math.tan(math.radians(y)),math.tan(math.radians(pitch))
                    sampled.append(math.degrees(math.acos(max(-1,min(1,(g*v+1)/(math.sqrt(1+g*g)*math.sqrt(1+u*u+v*v)))))))
            self.assertLessEqual(exact,min(sampled)+1e-10)
            m=self.make(tiles=[tile],gaze=Gaze(0,pitch,100000000),previous=None)
            if exact<=m['radius']:self.assertEqual(m['q'],[1])
        self.assertLess(spherical_tile_distance(0,70,(15,16,70,71)),6)
        self.assertEqual(self.make(tiles=[(15,16,70,71)],gaze=Gaze(0,70,100000000),previous=None)['q'],[1])
        # Regression: yaw/elevation would give10.376deg and underprotect this8degROI.
        tile=(40.227754,40.227754,-9.905372,-9.905372)
        self.assertAlmostEqual(spherical_tile_distance(40.657794,.461685,tile),7.955867,places=5)
        m=self.make(tiles=[tile],gaze=Gaze(40.657794,.461685,100000000),previous=None,target_ns=100000000,config=Config(core=8,guard=0,uncertainty=0))
        self.assertEqual(m['q'],[1])
    def test_reject_bad_config_and_dimensions(self):
        for w,h in ((0,1),(4097,2),(4096,4096),(1.5,2)):
            with self.assertRaises(ValueError):footprints(w,h)
        for c in (Config(peripheral_q=0),Config(peripheral_q=7),Config(core=math.nan),Config(max_age_ms=-1),Config(predict_max_ms=100)):
            with self.assertRaises(ValueError):self.make(config=c)
        with self.assertRaises(ValueError):self.make(tiles=[(0,-1,0,1)])
        with self.assertRaises(ValueError):self.make(eye=2)
        with self.assertRaises(ValueError):self.make(target_ns=1<<70)
    def test_random_maps_bounded(self):
        rng=random.Random(999)
        tiles=footprints(113,97)
        for _ in range(1000):
            m=self.make(tiles=tiles,gaze=Gaze(rng.uniform(-70,70),rng.uniform(-70,70),100000000),previous=Gaze(rng.uniform(-70,70),rng.uniform(-70,70),90000000),target_ns=rng.randrange(90000000,190000000))
            self.assertEqual(len(m['q']),len(tiles))
            self.assertTrue(all(1<=q<=128 for q in m['q']))
    def test_spherical_extrema_dense_independent_oracle(self):
        rng=random.Random(345)
        for _ in range(150):
            x,y=(rng.uniform(-75,75) for _ in range(2))
            l,r=sorted(rng.uniform(-80,80) for _ in range(2))
            b,t=sorted(rng.uniform(-80,80) for _ in range(2))
            low,high=projected_dot_extrema(x,y,(l,r,b,t))
            g=[math.tan(math.radians(x)),math.tan(math.radians(y)),1]
            gn=math.sqrt(sum(v*v for v in g));g=[v/gn for v in g]
            for i in range(11):
                for j in range(11):
                    p=[math.tan(math.radians(l+(r-l)*i/10)),math.tan(math.radians(b+(t-b)*j/10)),1]
                    pn=math.sqrt(sum(v*v for v in p));dot=sum(a*b/pn for a,b in zip(g,p))
                    self.assertLessEqual(low,dot+1e-12);self.assertGreaterEqual(high,dot-1e-12)


if __name__=='__main__':unittest.main()
