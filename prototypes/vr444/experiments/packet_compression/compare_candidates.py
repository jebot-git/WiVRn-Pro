#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Same-input comparison: legacy/DC-only wire followed by stateless LZ4.
Measures wrapper cost only; excludes custom-tile encoding/restoration/GPU cost.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import time
from benchmark import distribution
from wrapper import Native, encode, decode


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    records=json.loads(a.manifest.read_text());native=Native();rows=[];rng=random.Random(7444)
    for record in records:
        variants={key:Path(record[key]).read_bytes() for key in ('original','candidate')}
        for key,data in variants.items():
            if hashlib.sha256(data).hexdigest()!=record[key+'_sha256']:raise ValueError('input hash mismatch')
        names=list(variants);rng.shuffle(names);result=dict(original=record['original'],candidate=record['candidate'],original_bytes=len(variants['original']),candidate_bytes=len(variants['candidate']))
        for key in names:
            data=variants[key];packed=encode(data,native)
            if decode(packed,native)!=data:raise AssertionError('warmup')
            enc=[];dec=[]
            for _ in range(5):
                start=time.perf_counter_ns();packed=encode(data,native);middle=time.perf_counter_ns()
                raw=decode(packed,native);end=time.perf_counter_ns()
                if raw!=data:raise AssertionError('roundtrip')
                enc.append((middle-start)/1e6);dec.append((end-middle)/1e6)
            result[key+'_lz4']=dict(bytes=len(packed),encode_ms=distribution(enc),decode_ms=distribution(dec))
        rows.append(result)
    totals={key:sum(r[key] for r in rows) for key in ('original_bytes','candidate_bytes')}
    totals.update({key+'_lz4_bytes':sum(r[key+'_lz4']['bytes'] for r in rows) for key in ('original','candidate')})
    output=dict(scope=__doc__,samples=len(rows),roundtrips=len(rows)*2*5,lz4=native.lz4.LZ4_versionString().decode(),totals=totals,
                candidate_ratio=totals['candidate_bytes']/totals['original_bytes'],
                original_lz4_ratio=totals['original_lz4_bytes']/totals['original_bytes'],
                candidate_lz4_ratio=totals['candidate_lz4_bytes']/totals['original_bytes'],
                combined_gain_over_lz4=1-totals['candidate_lz4_bytes']/totals['original_lz4_bytes'],
                rows=rows)
    a.out.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='rows'}))


if __name__=='__main__':main()
