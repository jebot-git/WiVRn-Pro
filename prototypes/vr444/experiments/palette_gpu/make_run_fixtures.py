#!/usr/bin/env python3
"""Hand-author legal RPT1 run fixtures independently of the C++ encoder."""
import hashlib
import json
from pathlib import Path
import struct
import zlib


def make(width, height, maximum_runs):
    body=bytearray(); pixels=bytearray(width*height*4); tile=0
    for y in range(0,height,16):
        for x in range(0,width,16):
            w,h=min(16,width-x),min(16,height-y)
            n=w*h; count=min(maximum_runs,n)
            lengths=[n//count+(i<n%count) for i in range(count)]
            payload=bytearray(); local=0
            for i,length in enumerate(lengths):
                rgb=bytes(((i*17+tile*3)&255,(i*31+tile*7)&255,(i*47+tile*11)&255))
                payload.extend(bytes([length-1])+rgb)
                for _ in range(length):
                    at=((y+local//w)*width+x+local%w)*4
                    pixels[at:at+4]=rgb+b"\xff"; local+=1
            assert local==n and len(payload)<=768
            body.extend(struct.pack("<HBB",len(payload),5,0)+payload); tile+=1
    header=struct.pack("<IHHIIIII",0x31545052,1,16,width,height,tile,len(body),zlib.crc32(body))
    return header+struct.pack("<I",zlib.crc32(header))+body,bytes(pixels)


def main():
    output=Path(__file__).with_name("fixtures"); output.mkdir(exist_ok=True)
    cases=[]
    for name,w,h,runs in (("single-run-256",16,16,1),("prefix-129",16,16,129),
                          ("prefix-192",16,16,192),("partial-17x17",17,17,192)):
        wire,pixels=make(w,h,runs)
        (output/(name+".rpt")).write_bytes(wire)
        (output/(name+".rgba")).write_bytes(pixels)
        cases.append(dict(name=name,width=w,height=h,max_runs=runs,
                          rpt_sha256=hashlib.sha256(wire).hexdigest(),
                          rgba_sha256=hashlib.sha256(pixels).hexdigest()))
    (output/"manifest.json").write_text(json.dumps(dict(schema=1,independent_of_cpp_encoder=True,cases=cases),indent=2)+"\n")


if __name__=="__main__": main()
