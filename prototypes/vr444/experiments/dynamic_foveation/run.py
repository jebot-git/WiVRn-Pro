#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Serial host experiment; no device, GPU, or network access."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
from controller import Config,Gaze,footprints,make_map


def dump(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def synthetic(w,h,kind,eye):
    rng=random.Random(742+eye)
    out=bytearray()
    for y in range(h):
        for x in range(w):
            sx=x+eye*7
            if kind=='text':
                gx,gy=sx%11,y%17
                line=y%43<17 and (gx==1 or gy==1 or gy==8 or (gx==8 and gy<9))
                rgb=(244,244,244) if line else (27,29,33)
            elif kind=='edges':
                rgb=(241,212,39) if ((sx+2*y)//31)%2 else (26,35,203)
                if sx%73==0 or y%61==0:rgb=(0,255,0)
            else:rgb=(rng.randrange(256),rng.randrange(256),rng.randrange(256))
            out.extend((*rgb,255))
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,default=Path.cwd());p.add_argument('--out',type=Path,required=True);p.add_argument('--probe',type=Path,required=True);args=p.parse_args()
    root=args.workspace.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    fixtures=out/'fixtures';fixtures.mkdir(exist_ok=True)
    sys.path.insert(0,str(root/'prototypes/vr444/experiments/packet_compression'))
    import wrapper
    native=wrapper.Native()
    cases=[];w,h=576,640
    for run in ('lossless','paired-a'):
        directory=root/'validation/results/wivrn-stereo-live-0.6.1'/run/'host'
        all_frames=sorted(int(f.name.split('-')[0]) for f in directory.glob('*-0.payload'))
        frames=[all_frames[len(all_frames)//3],all_frames[2*len(all_frames)//3]]
        for frame in frames:
            for eye in (0,1):
                src=directory/f'{frame}-{eye}.rgba'
                cases.append(dict(name=f'{run}-{frame}-{eye}',pair=f'{run}-{frame}',eye=eye,path=str(src),domain='pixel_proxy',source='pristine_preencode_host_warped_capture',sha256=sha(src)))
    for kind in ('text','edges','noise'):
        for eye in (0,1):
            src=fixtures/f'{kind}-{eye}.rgba';src.write_bytes(synthetic(w,h,kind,eye))
            cases.append(dict(name=f'{kind}-{eye}',pair=kind,eye=eye,path=str(src),domain='angular',source='synthetic_rectilinear_encoder_input',sha256=sha(src)))
    results=[]
    for item in cases:
        eye=item['eye'];bounds=(-48.,43.,-44.,46.) if eye==0 else (-43.,48.,-46.,44.)
        tiles=footprints(w,h,bounds,angular=item['domain']=='angular')
        g=Gaze(-1.5 if eye==0 else 1.5,.5*eye,100000000)
        previous=Gaze(g.x,g.y,90000000)
        profiles=[('uniform_q8',110000000,previous,Config(),8),
                  ('uniform_q1',110000000,previous,Config(),1),
                  ('guarded_q128',110000000,previous,Config(),0),
                  ('guarded_q32',110000000,previous,Config(peripheral_q=32),0),
                  ('uncertain_age35ms',135000000,previous,Config(),0),
                  ('saccade_age20ms',120000000,Gaze(g.x-20,g.y,90000000),Config(),0),
                  ('stale_age65ms',165000000,previous,Config(),0),
                  ('stale_age80ms',180000000,previous,Config(),0)]
        # Common current-gaze ROI allows matched quality comparisons across policies.
        reference=make_map(tiles,g,previous,110000000,eye=eye,geometry_id=item['name'],domain=item['domain'])
        mask=fixtures/(item['name']+'.mask');mask.write_bytes(struct.pack('<'+'I'*len(tiles),*reference['protected']))
        for policy,target,prev,config,uniform in profiles:
            m=make_map(tiles,g,prev,target,eye=eye,geometry_id=item['name'],domain=item['domain'],config=config)
            qmap=fixtures/(item['name']+'-'+policy+'.qmap');qmap.write_bytes(struct.pack('<'+'I'*len(tiles),*m['q']))
            wire_path=fixtures/(item['name']+'-'+policy+'.payload')
            data=json.loads(subprocess.check_output([str(args.probe.resolve()),str(w),str(h),item['path'],str(qmap),str(mask),str(uniform),str(wire_path)],text=True))
            wire=wire_path.read_bytes();compressed=wrapper.encode(wire,native,method=1)
            assert wrapper.decode(compressed,native)==wire
            data['lz4_wrapped_bytes']=len(compressed)
            data['payload_sha256']=hashlib.sha256(wire).hexdigest()
            wire_path.unlink()
            results.append(dict(case=item['name'],pair=item['pair'],eye=eye,domain=item['domain'],policy=policy,
                                age_ms=m['age_ms'],fallback=m['fallback'],saccade=m['saccade'],radius=m['radius'],
                                tile_count=len(tiles),protected_tiles=sum(m['protected']),q1_tiles=(len(tiles) if uniform==1 else 0) if uniform else m['q'].count(1),**data))
    pairs=defaultdict(list)
    for r in results:pairs[(r['pair'],r['policy'])].append(r)
    stereo=[]
    for (pair,policy),items in pairs.items():
        assert sorted(i['eye'] for i in items)==[0,1]
        n=sum(i['bytes'] for i in items);compressed=sum(i['lz4_wrapped_bytes'] for i in items)
        # Media payload only. Framing/UDP/QUIC/IP/Wi-Fi/FEC/retries are excluded.
        stereo.append(dict(pair=pair,policy=policy,bytes=n,lz4_wrapped_bytes=compressed,payload_mbps_at90hz=n*8*90/1e6,lz4_payload_mbps_at90hz=compressed*8*90/1e6,
                           fits_payload_budget_100mbps_90hz=n<=100000000/8/90,lz4_fits_payload_budget_100mbps_90hz=compressed<=100000000/8/90,
                           ideal_serialization_ms_at100mbps=n*8/100000,
                           serial_cpu_encode_pack_ms_median_sum=sum(i['encode_pack_ms_median'] for i in items)))
    dump(out/'benchmark.json',dict(schema=1,width=w,height=h,timing_repetitions=5,metrics_note='guarded_fovea means common fresh current-gaze tile mask for matched comparisons, periphery is its complement; Q1 exact checked across ALL Q1 tiles; null PSNR with zero errors means exact',cases=results,stereo=stereo))
    dump(out/'inputs.json',cases)
    hashes={str(f.relative_to(root)):sha(f) for f in sorted((root/'prototypes/vr444/experiments/dynamic_foveation').glob('*')) if f.is_file()}
    hashes['prototypes/vr444/codec.cpp']=sha(root/'prototypes/vr444/codec.cpp');hashes['prototypes/vr444/codec.hpp']=sha(root/'prototypes/vr444/codec.hpp')
    hashes['prototypes/vr444/experiments/packet_compression/wrapper.py']=sha(root/'prototypes/vr444/experiments/packet_compression/wrapper.py')
    dump(out/'provenance.json',dict(source_sha256=hashes,probe_sha256=sha(args.probe),compiler=subprocess.check_output(['clang++','--version'],text=True).splitlines()[0]))


if __name__=='__main__':main()
