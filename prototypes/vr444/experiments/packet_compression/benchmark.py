#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host-only stateless wrapper benchmark; no socket, GPU, or headset activity."""
import argparse
import hashlib
import json
import math
import platform
from pathlib import Path
import random
import time
from wrapper import Native, encode, decode, MAX, HEADER


def distribution(values):
    values = sorted(values)
    return dict(count=len(values), mean=sum(values)/len(values),
                p50=values[math.ceil(.5*len(values))-1],
                p95=values[math.ceil(.95*len(values))-1], max=values[-1])


def select(paths, count):
    paths = sorted(paths, key=lambda p: tuple(map(int, p.stem.split('-'))))
    if len(paths) <= count: return paths
    return [paths[round(i*(len(paths)-1)/(count-1))] for i in range(count)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not 3 <= args.repeats <= 30: parser.error('repeats must be 3..30')
    if args.out.exists(): raise FileExistsError(args.out)
    samples = []
    for run, family, count, suffix in [('paired-a','capture-vr444-q8',12,'payload'),
                                      ('paired-b','capture-vr444-q8',12,'payload'),
                                      ('lossless','capture-vr444-q1',12,'payload'),
                                      ('hevc','capture-hevc',8,'payload'),
                                      ('raw','capture-raw-rgba',6,'rgba')]:
        paths = select((args.captures/run/'host').glob('*.'+suffix), count)
        if not paths: raise ValueError('missing capture '+run)
        for path in paths:
            data = path.read_bytes()
            samples.append((str(path), family, data))
    for path in sorted(args.fixtures.glob('*.payload')):
        samples.append((str(path),'synthetic-'+path.stem,path.read_bytes()))
    if len(list(args.fixtures.glob('*.payload'))) != 6: raise ValueError('six synthetic fixtures required')
    native = Native()
    profiles = [(name, method, level, size) for name,method,level in
                [('lz4',1,1),('zstd1',2,1),('zstd3',2,3)]
                for size in (MAX,16384,1100)]
    jobs = [(i, profile) for i in range(len(samples)) for profile in profiles]
    random.Random(7007).shuffle(jobs)
    results = []
    for index, (name,method,level,size) in jobs:
        path, family, data = samples[index]
        packed = encode(data,native,method,size,level)
        if decode(packed,native) != data: raise AssertionError('warmup roundtrip')
        enc=[]; dec=[]
        for _ in range(args.repeats):
            start=time.perf_counter_ns(); wire=encode(data,native,method,size,level)
            middle=time.perf_counter_ns(); restored=decode(wire,native); end=time.perf_counter_ns()
            if restored != data or wire != packed: raise AssertionError('roundtrip/determinism')
            enc.append((middle-start)/1e6);dec.append((end-middle)/1e6)
        saved=len(data)-len(packed)
        cost=distribution([a+b for a,b in zip(enc,dec)])
        results.append(dict(sample=index, family=family, codec=name,
                            granularity='frame' if size==MAX else str(size),
                            original_bytes=len(data), wrapped_bytes=len(packed),
                            bytes_saved=saved, ratio=len(packed)/len(data),
                            whole_frame_raw_escape=HEADER.unpack_from(packed)[2]==0,
                            encode_ms=distribution(enc), decode_ms=distribution(dec),
                            roundtrip_ms=cost,
                            host_only_serial_break_even_mbps=max(0,8*saved/(cost['p50']*1000))))
    aggregates=[]
    for family in sorted({x['family'] for x in results}):
        for name,_,_,size in profiles:
            granularity='frame' if size==MAX else str(size)
            rows=[x for x in results if x['family']==family and x['codec']==name and x['granularity']==granularity]
            original=sum(x['original_bytes'] for x in rows);wrapped=sum(x['wrapped_bytes'] for x in rows)
            aggregates.append(dict(family=family,codec=name,granularity=granularity,samples=len(rows),
                                   original_bytes=original,wrapped_bytes=wrapped,
                                   byte_ratio=wrapped/original,
                                   raw_escape_count=sum(x['whole_frame_raw_escape'] for x in rows),
                                   sample_encode_p50_ms=distribution([x['encode_ms']['p50'] for x in rows]),
                                   sample_decode_p50_ms=distribution([x['decode_ms']['p50'] for x in rows])))
    output=dict(schema=1,scope='offline host compression experiment, not network or Quest timing',
                platform=platform.platform(),python=platform.python_version(),
                libraries=dict(lz4=native.lz4.LZ4_versionString().decode(),zstd=native.zstd.ZSTD_versionString().decode()),
                repeats=args.repeats,order_seed=7007,
                timing='Python/ctypes, native library, allocation/copies and CRC included; IO and exact comparison excluded; each job warmed once; small-sample p95 is descriptive only',
                framing='24-byte experimental header,12-byte chunk records, per-block and whole-frame raw escape; no WCT/UDP/QUIC/IP/AEAD framing included',
                limits=['Sparse hello_xr captures are not a VR corpus. Synthetic0=gradient,1=grid/chroma/text-like edges,2=noise.',
                        'Independent1100-byte compression chunks are not deployed packets and do not implement loss recovery.',
                        'Whole-frame raw escape bounds expansion to24bytes but does not bound wasted encoder work.',
                        'No temporal dictionary, inter-eye prediction, optical latency, GPU or Android measurement.',
                        'Break-even model assumes serial host encode+host decode plus pure payload serialization; real client cost and overlap differ.'],
                samples=[dict(path=p,family=f,bytes=len(d),sha256=hashlib.sha256(d).hexdigest()) for p,f,d in samples],
                aggregates=aggregates,results=results)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(status='pass',samples=len(samples),profiles=len(profiles),roundtrips=len(jobs)*args.repeats,libraries=output['libraries'])))


if __name__ == '__main__': main()
