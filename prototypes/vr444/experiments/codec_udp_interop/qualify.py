#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual encoded bytes through owned UDP sockets; correctness, not timing."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import build as host_build

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('interop_vxf',HERE.parent/'datagram_xor/transport.py')
vxf=importlib.util.module_from_spec(spec);sys.modules[spec.name]=vxf;spec.loader.exec_module(vxf)
SID=b'udp-codec-test!!'
NOW=1_000_000_000
TTL=100_000_000
MODES=('mmsg','gso_mmsg','gso_gro')
SCENARIOS=('clean_no_fec','clean_fec','corrupt_first_fec','source_and_parity_lost')


class Unsupported(RuntimeError):pass


def sha(data):return hashlib.sha256(data).hexdigest()


def bounded_file(path,limit):
    if not path.is_file() or not 0<path.stat().st_size<=limit:raise ValueError('file size/type bound: '+str(path))
    data=path.read_bytes()
    if not 0<len(data)<=limit:raise ValueError('changed file size bound')
    return data


def request(packets):
    if not 1<=len(packets)<=512 or any(not 1<=len(p)<=1200 for p in packets):raise ValueError('relay request bounds')
    return b'UPX1'+struct.pack('<I',len(packets))+b''.join(struct.pack('<I',len(p))+p for p in packets)


def response(data):
    if len(data)<20 or data[:4]!=b'UPY1':raise ValueError('relay response header')
    count,sends,receives,segments=struct.unpack_from('<IIII',data,4)
    if not 1<=count<=512 or not 1<=sends<=4096 or not 1<=receives<=4096 or not 0<=segments<=32:raise ValueError('relay response counters')
    packets=[];at=20
    for _ in range(count):
        if len(data)-at<4:raise ValueError('relay length missing')
        n=struct.unpack_from('<I',data,at)[0];at+=4
        if not 1<=n<=1200 or n>len(data)-at:raise ValueError('relay packet extent')
        packets.append(data[at:at+n]);at+=n
    if at!=len(data):raise ValueError('relay trailing data')
    return packets,dict(send_calls=sends,receive_calls=receives,max_gro_segments=segments)


def relay(binary,packets,mode):
    if type(mode) is not int or not 0<=mode<3:raise ValueError('relay mode')
    r=subprocess.run([str(binary),str(mode)],input=request(packets),capture_output=True,timeout=10)
    if r.returncode==77:raise Unsupported(r.stderr.decode(errors='replace'))
    if r.returncode!=0:raise ValueError('relay failed: '+r.stderr.decode(errors='replace'))
    if r.stderr:raise ValueError('unexpected relay diagnostic: '+r.stderr.decode(errors='replace'))
    received,counters=response(r.stdout)
    if Counter(received)!=Counter(packets):raise ValueError('actual received packet multiset differs')
    return received,counters


def decode_request(mode,left,right,pixels_left,pixels_right):
    if type(mode) is not int or not 0<=mode<=2 or not 2<=len(left)+len(right)<=vxf.MAX_PAIR:raise ValueError('decode request bounds')
    parts=(left,right,pixels_left,pixels_right)
    if any(not p for p in parts) or any(len(p)>4194304*4 for p in parts[2:]):raise ValueError('decode pixel bounds')
    return b'CDP1'+struct.pack('<I',mode)+b''.join(struct.pack('<I',len(p))+p for p in parts)


def decode(binary,mode,left,right,pixels_left,pixels_right):
    r=subprocess.run([str(binary)],input=decode_request(mode,left,right,pixels_left,pixels_right),capture_output=True,timeout=30)
    if r.returncode!=0:raise ValueError('complete pair decoder failed: '+r.stderr.decode(errors='replace'))
    if r.stderr:raise ValueError('unexpected decoder diagnostic: '+r.stderr.decode(errors='replace'))
    result=json.loads(r.stdout)
    if result.get('status')!='pass' or result.get('both_eyes_exact') is not True:raise ValueError('decoder exactness missing')
    return result


def baseline_wire(binary,original):
    payload=b'CEP1'+b''.join(struct.pack('<I',len(p))+p for p in original)
    r=subprocess.run([str(binary),'encode'],input=payload,capture_output=True,timeout=30)
    if r.returncode!=0 or r.stdout[:4]!=b'CEY1':raise ValueError('baseline encoder failed: '+r.stderr.decode(errors='replace'))
    if r.stderr:raise ValueError('unexpected baseline encoder diagnostic: '+r.stderr.decode(errors='replace'))
    at=4;output=[]
    for _ in range(2):
        if len(r.stdout)-at<4:raise ValueError('baseline wire length missing')
        n=struct.unpack_from('<I',r.stdout,at)[0];at+=4
        if not 1<=n<=16*1024*1024 or n>len(r.stdout)-at:raise ValueError('baseline wire length bound')
        output.append(r.stdout[at:at+n]);at+=n
    if at!=len(r.stdout):raise ValueError('baseline output trailing bytes')
    return output


def packets_for(left,right,scenario):
    if scenario not in SCENARIOS:raise ValueError('unknown deterministic scenario')
    width=0 if scenario=='clean_no_fec' else 8
    packets=vxf.packetize(SID,1,1,1,10,NOW+TTL,left,right,width)
    output=[];changed=0
    for p in packets:
        fields=vxf.HEADER.unpack_from(p);kind,index=fields[2],fields[9]
        if scenario=='source_and_parity_lost' and index==0 and kind in (vxf.DATA,vxf.PARITY):changed+=1;continue
        if scenario=='corrupt_first_fec' and index==0 and kind==vxf.DATA:
            q=bytearray(p);q[-1]^=1;p=bytes(q);changed+=1
        output.append(p)
    if changed!=(2 if scenario=='source_and_parity_lost' else 1 if scenario=='corrupt_first_fec' else 0):raise ValueError('fault not injected exactly')
    return output,width,changed


def reconstruct(packets):
    # The native owned-socket exchange has already finished. This is a distinct
    # logical arrival replay, deliberately not its measured deadline/latency.
    clock=[NOW];r=vxf.Receiver(SID,clock=lambda:clock[0]);r.issue_deadline(NOW,TTL,1,10)
    output=[]
    for i,p in enumerate(packets):
        clock[0]=NOW+(i+1)*1000
        pair=r.receive(p,clock[0])
        if pair:output.append(pair)
        if len(r.pending)>vxf.MAX_CANDIDATES or r.buffered>vxf.MAX_BUFFERED or r.orphan_bytes>vxf.MAX_ORPHAN:raise ValueError('receiver resource bound')
    if len(output)>1:raise ValueError('duplicate pair publication')
    return (output[0] if output else None),dict(recovered_source_shards=r.stats['recovered_source_shards'],highwater_payload_bytes=r.highwater_bytes)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,required=True);p.add_argument('--rrf-export',type=Path,required=True);p.add_argument('--rrb-export',type=Path,required=True)
    p.add_argument('--binaries',type=Path,default=Path('/tmp/wivrn-codec-udp-interop-build'));p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();benchmark=HERE.parent/'rowblocks_workers/results/benchmark.json';receipt=benchmark.parent/'measurement-provenance.json'
    dependencies=[Path(__file__),Path(vxf.__file__),Path(vxf.base.__file__),*host_build.inputs()]
    source_before={str(f.relative_to(ROOT)):sha(f.read_bytes()) for f in dependencies}
    binary_before={name:sha((a.binaries/name).read_bytes()) for name in ('relay','decode')}
    build_receipt=a.binaries/'build-receipt.json';build=json.loads(bounded_file(build_receipt,256*1024))
    expected_sources={str(f.relative_to(ROOT)):sha(f.read_bytes()) for f in host_build.inputs()}
    if (build.get('schema')!=1 or build.get('status')!='pass' or build.get('configuration')!='host_address_undefined'
            or build.get('source_unchanged') is not True or build.get('source_sha256')!=expected_sources or build.get('binary_sha256')!=binary_before):
        raise ValueError('native build receipt does not bind the current sanitizer binaries and sources')
    commands=build.get('successful_compiler_argv',[])
    if len(commands)!=3 or any(not isinstance(cmd,list) or '-fsanitize=address,undefined' not in cmd for cmd in commands):raise ValueError('sanitizer compiler commands missing')
    controls={str(f.resolve()):sha(f.read_bytes()) for f in (benchmark,receipt,build_receipt)}
    benchbytes=bounded_file(benchmark,8*1024*1024);data=json.loads(benchbytes);provenance=json.loads(receipt.read_text())
    if sha(benchbytes)!=provenance['benchmark_sha256']:raise ValueError('worker input provenance mismatch')
    if a.output.exists():raise ValueError('refuse existing output')
    hashes={};rows=[];capabilities={};trips=decodes=rejected=0
    def read(path,limit=16*1024*1024):
        contents=bounded_file(path,limit);digest=sha(contents);name=str(path.resolve())
        if name in hashes and hashes[name]!=digest:raise ValueError('input changed during qualification')
        hashes[name]=digest;return contents
    for ci,case in enumerate(data['cases']):
        left_name,right_name=case['left_source'],case['right_source'];synthetic=case['synthetic_duplicate_eye']
        if type(synthetic) is not bool or synthetic!=(left_name==right_name):raise ValueError('stereo source mismatch')
        original=[read(a.corpus/name) for name in (left_name,right_name)]
        for name,wire in zip((left_name,right_name),original):
            if sha(wire)!=provenance['corpus_sha256'][name]:raise ValueError('source capture differs from measured corpus')
        pixels=[read(a.rrf_export/Path(name).with_suffix('.rgba')) for name in (left_name,right_name)]
        for name,pixel in zip((left_name,right_name),pixels):
            if read(a.rrb_export/Path(name).with_suffix('.rgba'))!=pixel:raise ValueError('independent exports disagree on decoded bytes')
        for codec,mode in (('vr444',0),('rrf_up',1),('rrb32',2),('rrb128',2)):
            if codec=='vr444':wire=baseline_wire(a.binaries/'decode',original)
            elif mode==1:wire=[read(a.rrf_export/Path(name).with_suffix('.rrf')) for name in (left_name,right_name)]
            else:wire=[read(a.rrb_export/Path(name).with_suffix('.r'+codec[3:]+'.rrb')) for name in (left_name,right_name)]
            wire_size=sum(map(len,wire));profile={'vr444':0,'rrf_up':1,'rrb32':2,'rrb128':5}[codec]
            if wire_size!=case['profiles'][profile]['stereo_wire_bytes']:raise ValueError('encoded export length differs from worker evidence')
            common=dict(case=case['name'],synthetic_duplicate_eye=synthetic,codec=codec,stereo_wire_bytes=wire_size,encoded_sha256=list(map(sha,wire)),rgba_sha256=list(map(sha,pixels)))
            if wire_size>vxf.MAX_PAIR:
                rejected+=1;rows.append(common|dict(status='ineligible_over_unchanged_256KiB_pair_cap'));continue
            for socket_mode,name in enumerate(MODES):
                if capabilities.get(name,{}).get('supported') is False:continue
                for scenario in SCENARIOS:
                    packets,width,changes=packets_for(*wire,scenario)
                    try:received,counters=relay(a.binaries/'relay',packets,socket_mode)
                    except Unsupported as exc:
                        if socket_mode==0:raise
                        if capabilities.get(name,{}).get('supported') is True:raise ValueError('socket capability changed during qualification') from exc
                        capabilities[name]=dict(supported=False,reason=str(exc));break
                    capabilities[name]=dict(supported=True,domain='newly owned IPv4 loopback sockets only');trips+=1
                    pair,stats=reconstruct(received);expected=scenario!='source_and_parity_lost'
                    if bool(pair)!=expected:raise ValueError('unexpected complete/incomplete pair')
                    result=None
                    if pair:
                        if (pair.left,pair.right)!=(wire[0],wire[1]):raise ValueError('actual reconstructed encoded eyes differ')
                        result=decode(a.binaries/'decode',mode,pair.left,pair.right,*pixels);decodes+=1
                        if scenario=='corrupt_first_fec' and stats['recovered_source_shards']!=1:raise ValueError('CRC-rejected shard was not recovered')
                    rows.append(common|dict(status='pass',socket_mode=name,scenario=scenario,stripe_width=width,changed_or_removed_records=changes,
                        complete_pair=expected,actual_udp_records=len(received),actual_udp_payload_bytes=sum(map(len,received)),calls=counters,receiver=stats,decode=result))
        print(json.dumps(dict(progress=ci+1,total=len(data['cases']),owned_loopback_exchanges=trips,exact_pair_decodes=decodes)),flush=True)
    for path,digest in hashes.items():
        if sha(bounded_file(Path(path),16*1024*1024))!=digest:raise ValueError('source changed during run')
    if any(sha(Path(name).read_bytes())!=digest for name,digest in controls.items()):raise ValueError('control/build evidence changed during run')
    if any(sha((ROOT/name).read_bytes())!=digest for name,digest in source_before.items()):raise ValueError('implementation source changed during run')
    if any(sha((a.binaries/name).read_bytes())!=digest for name,digest in binary_before.items()):raise ValueError('native binary changed during run')
    result=dict(schema=1,status='pass',qualification='actual codec bytes through bounded owned UDP exchange then logical VXF arrival replay and native exact stereo decode',
        actual_captured_pairs=sum(not c['synthetic_duplicate_eye'] for c in data['cases']),synthetic_duplicated_eye_profiles=sum(c['synthetic_duplicate_eye'] for c in data['cases']),
        owned_loopback_exchanges=trips,exact_complete_stereo_decodes=decodes,over_cap_codec_cases=rejected,capabilities=capabilities,rows=rows,
        input_sha256=hashes,input_files_unchanged=True,control_sha256=controls,controls_unchanged=True,source_unchanged=True,source_sha256=source_before,
        binary_sha256=binary_before,binary_unchanged=True,measured_network_latency=False,measured_deadline_admission=False,
        headset_accessed=False,external_listener=False,production_changes=False,promotion_eligible=False,
        limitations=['All actual sockets are owned ephemeral IPv4 loopback; no physical NIC, WiFi, target OS or headset behavior is established.',
            'VXF uses a separate deterministic 1 microsecond per-record logical replay after native socket exchange; its ticket is not an elapsed-time measurement.',
            'Every successful pair is independently decoded in a native ASan/UBSan process and compared byte-for-byte to both expected RGBA eyes.',
            'A damaged source packet is deliberately sent; its VXF CRC rejection plus parity recovery preserves exact bytes. CRC/parity are not authentication.',
            'No production stereo publisher, queue, congestion controller, key/session establishment or compositor consumer is integrated.',
            'The unchanged pair cap rejects large encoded profiles without changing quality; Q8 exactness is relative to its already-decoded input.'])
    with a.output.open('x') as f:json.dump(result,f,separators=(',',':'));f.write('\n')
    print(json.dumps(dict(status='pass',owned_loopback_exchanges=trips,exact_pair_decodes=decodes,over_cap_codec_cases=rejected)))


if __name__=='__main__':main()
