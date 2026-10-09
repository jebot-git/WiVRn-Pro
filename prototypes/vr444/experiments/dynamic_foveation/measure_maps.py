#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure Python policy generation separately; geometry footprints cached."""
import json
import statistics
import time
from controller import Config,Gaze,footprints,make_map

rows=[]
for domain in ('angular','pixel_proxy'):
    for eye in (0,1):
        tiles=footprints(576,640,(-48.,43.,-44.,46.) if eye==0 else (-43.,48.,-46.,44.),angular=domain=='angular')
        g=Gaze(-1.5 if eye==0 else 1.5,.5*eye,100000000);prev=Gaze(g.x,g.y,90000000)
        profiles=[('guarded_q128',110000000,prev,Config()),('guarded_q32',110000000,prev,Config(peripheral_q=32)),
                  ('uncertain_age35ms',135000000,prev,Config()),('saccade_age20ms',120000000,Gaze(g.x-20,g.y,90000000),Config()),
                  ('stale_age65ms',165000000,prev,Config()),('stale_age80ms',180000000,prev,Config())]
        for policy,target,previous,config in profiles:
            def calculate():return make_map(tiles,g,previous,target,eye=eye,geometry_id='timing-eye-'+str(eye),domain=domain,config=config)
            reference=calculate();times=[]
            for _ in range(5):
                start=time.perf_counter_ns();value=calculate();stop=time.perf_counter_ns()
                assert value==reference
                times.append((stop-start)/1e6)
            rows.append(dict(domain=domain,eye=eye,policy=policy,tile_count=len(tiles),median_ms=statistics.median(times),max_ms=max(times),samples_ms=times))
print(json.dumps(dict(method='Python reference policy only; includes footprint validation and q-map allocation, excludes cached footprint construction, encode/pack/LZ4/I/O/GPU/network; one warmup then5samples; no pinned/thermal control',cases=rows),indent=2))
