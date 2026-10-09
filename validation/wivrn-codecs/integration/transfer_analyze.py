#!/usr/bin/env python3
"""Offline size/cost exploration on sampled RGB captures; no real-time claims."""
import argparse
import json
from pathlib import Path
import time
import zlib

def analyze(folder):
    rows=[]
    for eye in (0,1):
        previous=None
        for path in sorted(Path(folder).glob(f'*-{eye}.rgba'),key=lambda p:int(p.stem.split('-')[0])):
            raw=path.read_bytes();width,height=576,640
            if len(raw)!=width*height*4:raise ValueError('expected 576x640 RGBA')
            started=time.perf_counter();whole=zlib.compress(raw,1);full_ms=(time.perf_counter()-started)*1000
            changed=[];started=time.perf_counter()
            for y in range(0,height,16):
                for x in range(0,width,16):
                    tile=b''.join(raw[(r*width+x)*4:(r*width+x+16)*4] for r in range(y,y+16))
                    old=None if previous is None else b''.join(previous[(r*width+x)*4:(r*width+x+16)*4] for r in range(y,y+16))
                    if tile!=old:changed.append(tile)
            # Four-byte tile index plus a two-byte record size, followed by compressed data.
            records=[zlib.compress(tile,1) for tile in changed]
            delta_ms=(time.perf_counter()-started)*1000
            rows.append(dict(file=path.name,raw_bytes=len(raw),full_zlib_bytes=len(whole),full_zlib_host_wall_ms=full_ms,
                             tiles=1440,changed_tiles=len(changed),changed_tile_zlib_bytes=sum(len(r)+6 for r in records),
                             tile_compare_and_zlib_host_wall_ms=delta_ms,requires_base=previous is not None))
            previous=raw
    return dict(schema=1,scope='offline sampled captures, Python/zlib on host; no network or Quest execution',
                assumptions=['16x16 tiles, lossless RGBA', 'size estimate excludes frame/base ACK/recovery metadata',
                             'samples are separated by actual capture interval, not consecutive 90 Hz frames',
                             'ignores warp-generation invalidation: optimistic size exploration only'],samples=rows)
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();args.out.write_text(json.dumps(analyze(args.folder),indent=2)+'\n')
