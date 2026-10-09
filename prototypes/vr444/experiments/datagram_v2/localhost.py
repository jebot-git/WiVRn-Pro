#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Low-volume real loopback UDP correctness tests, never a throughput/MTP benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import socket
import time
from transport import Receiver, packetize, accounting, UDP_BUDGET, HEADER

SID=b'local-session001'
LEFT=bytes(i%251 for i in range(4096))
RIGHT=bytes((i*7+5)%251 for i in range(4608))

def mutated(packet,index,value):
    fields=list(HEADER.unpack_from(packet)); fields[index]=value
    return HEADER.pack(*fields)+packet[HEADER.size:]

def run_case(name):
    r=Receiver(SID); start=time.monotonic_ns()
    ttl=3_000_000 if name=='expired' else 100_000_000
    deadline=r.issue_deadline(start,ttl,1,101)
    packets=packetize(SID,1,1,1,101,deadline,LEFT,RIGHT)
    source_account=accounting(packets)
    if name=='reorder_duplicate':
        packets=packets[1:]+[packets[1],packets[-1],packets[0]]
        random.Random(8085).shuffle(packets)
    elif name=='loss': packets=packets[:-1]
    elif name=='corrupt': packets[-1]=packets[-1][:-1]+bytes([packets[-1][-1]^1])
    elif name=='truncated': packets[-1]=packets[-1][:-1]
    elif name=='stale_epoch': packets=[mutated(p,5,0) for p in packets]
    elif name=='protect_complete':
        for f in range(2,8):
            packets+=packetize(SID,1,1,f,100+f,deadline,LEFT,RIGHT)[1:2]
    intervals=[]; received=[]
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as recv, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as send:
        recv.bind(('127.0.0.1',0)); recv.settimeout(.2); endpoint=recv.getsockname()
        for i,packet in enumerate(packets):
            if name=='expired' and i==1: time.sleep(.006)
            before=time.monotonic_ns()
            assert send.sendto(packet,endpoint)==len(packet)
            body,peer=recv.recvfrom(UDP_BUDGET+1)
            after=time.monotonic_ns()
            assert body==packet and peer[0]=='127.0.0.1'
            received.append(body); intervals.append(after-before)
            r.receive(body,after)
            time.sleep(.00025) # deliberately paced, tiny traffic; not throughput testing
    expected=name in ('baseline','reorder_duplicate','protect_complete')
    assert (r.published is not None)==expected,(name,r.stats)
    if expected:
        assert (r.published.frame,r.published.warp,r.published.left,r.published.right)==(1,101,LEFT,RIGHT)
    assert r.highwater_candidates<=2
    return dict(case=name,passed=True,clock_scope='same-host receiver monotonic_ns; real software socket timestamps only',
        clock_origin=start,deadline_receiver_ns=deadline,source_packet_accounting=source_account,
        transmitted_packet_accounting=accounting(packets),received_packet_accounting=accounting(received),
        impairment='deterministic application packet-list edit; expired case uses real receiver-clock delay',
        software_send_to_recv_ns=intervals,elapsed_ns=time.monotonic_ns()-start,
        published_frame=r.published.frame if r.published else None,stats=dict(r.stats),
        highwater_candidate_bytes=r.highwater_bytes,highwater_candidates=r.highwater_candidates,
        receiver_trace=r.trace,trace_dropped=r.trace_dropped)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    cases=[run_case(n) for n in ('baseline','reorder_duplicate','loss','corrupt','truncated','stale_epoch','expired','protect_complete')]
    root=Path(__file__).parent
    result=dict(status='PASS',transport='plain IPv4 UDP loopback, unauthenticated experimental protocol',
        measurement_limit='Socket/API software intervals are neither network-only latency, WiFi capacity, nor motion-to-photon. No external interface or system network settings changed.',
        congestion='No production congestion control. Sequential send/receive and250us pacing limit this correctness harness.',
        quic='aioquic not installed; QUIC transport/authentication not implemented or claimed',
        source_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.glob('*.py'))},cases=cases)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='PASS',cases=len(cases),sent_datagrams=sum(c['transmitted_packet_accounting']['datagrams'] for c in cases),sent_udp_payload_bytes=sum(c['transmitted_packet_accounting']['udp_payload_bytes'] for c in cases))))
if __name__=='__main__':main()
