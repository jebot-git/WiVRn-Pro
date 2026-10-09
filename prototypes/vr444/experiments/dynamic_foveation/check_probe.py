#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise codec tile edges and Q-map bounds with a sanitizer-built probe."""
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess
import tempfile
from controller import Gaze,footprints,make_map


def main():
    p=argparse.ArgumentParser();p.add_argument('probe',type=Path);a=p.parse_args()
    passed=0;rejected=0
    with tempfile.TemporaryDirectory(prefix='wivrn-roi-test-') as tmp:
        d=Path(tmp);rng=random.Random(517)
        def write(name,values):
            f=d/name;f.write_bytes(struct.pack('<'+'I'*len(values),*values));return str(f)
        for w,h in ((1,1),(17,19),(63,65),(128,96)):
            pixels=[0xff000000|rng.randrange(1<<24) for _ in range(w*h)]
            tiles=footprints(w,h);count=len(tiles)
            maps=[[1]*count,[1024]*count]
            for x in (-25,0,25):
                maps.append(make_map(tiles,Gaze(x,0,100000000),None,110000000,eye=0,geometry_id='test')['q'])
            for q in maps:
                args=[str(a.probe.resolve()),str(w),str(h),write('in.rgba',pixels),write('q',q),write('mask',[int(v==1) for v in q]),'0',str(d/'out')]
                result=json.loads(subprocess.check_output(args,text=True))
                assert result['q1_tile_rgb_errors']==0
                if set(q)=={1}:assert result['regions']['whole']['nonzero_error_samples']==0
                passed+=1
            for bad in ([0]*count,[1025]*count,[1]*(count+1)):
                args[4]=write('bad',bad)
                result=subprocess.run(args,capture_output=True,text=True)
                assert result.returncode!=0 and 'Sanitizer' not in result.stderr,result.stderr
                rejected+=1
    print(json.dumps(dict(roundtrips_passed=passed,invalid_maps_rejected=rejected,sanitizer_findings=0)))


if __name__=='__main__':main()
