#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Insert measured host stereo-codec durations into an explicit wire-size model.

Synthetic content has the measured total encoded byte count, not captured packet
content. This is component arithmetic, NOT a combined runtime or headset test.
"""
import argparse
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('budget_vxf',HERE.parent/'datagram_xor/transport.py')
vxf=importlib.util.module_from_spec(spec);sys.modules[spec.name]=vxf;spec.loader.exec_module(vxf)
SID=b'codec-budget-v1!'
NOW=1_000_000_000
TTL=100_000_000
PROFILES=('vr444_serial','rrf_up_serial','rrb32_serial','rrb32_workers2','rrb32_workers4','rrb128_serial','rrb128_workers2','rrb128_workers4')
METRICS=('server_transcode_ms','client_decode_wall_ms','client_process_cpu_ms','server_plus_client_wall_ms')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    if not values or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in values):raise ValueError('invalid duration population')
    ordered=sorted(values);n=len(ordered)
    return dict(p50=ordered[(n-1)//2],p95=ordered[(95*n+99)//100-1],max=ordered[-1])


def validate_benchmark(data):
    if (data.get('pass') is not True or data.get('schema')!=1 or type(data.get('repetitions')) is not int
            or not 5<=data['repetitions']<=100 or not isinstance(data.get('cases'),list) or not 1<=len(data['cases'])<=128):
        raise ValueError('invalid benchmark schema')
    names=set()
    for case in data['cases']:
        if (not isinstance(case.get('name'),str) or case['name'] in names or case.get('exact_stereo') is not True
                or case.get('serial_worker_wire_equal') is not True or type(case.get('synthetic_duplicate_eye')) is not bool
                or [p.get('name') for p in case.get('profiles',[])]!=list(PROFILES)):
            raise ValueError('invalid case/profiles')
        names.add(case['name'])
        if (not all(isinstance(case.get(k),str) and case[k] for k in ('left_source','right_source'))
                or case['synthetic_duplicate_eye']!=(case['left_source']==case['right_source'])):
            raise ValueError('stereo provenance differs')
        for profile in case['profiles']:
            if type(profile.get('stereo_wire_bytes')) is not int or not 2<=profile['stereo_wire_bytes']<=32*1024*1024:
                raise ValueError('invalid total stereo wire length')
            for key in METRICS:
                metric=profile.get(key,{})
                if not isinstance(metric.get('samples'),list) or len(metric['samples'])!=data['repetitions']:
                    raise ValueError('missing duration samples')
                samples=metric['samples']
                if any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in samples):raise ValueError('invalid sample')
                expected=distribution(samples)
                if any(metric.get(k)!=v for k,v in expected.items()):raise ValueError('distribution does not match samples')
            for server,client,total in zip(profile[METRICS[0]]['samples'],profile[METRICS[1]]['samples'],profile[METRICS[3]]['samples']):
                if not math.isclose(server+client,total,rel_tol=1e-7,abs_tol=1e-7):raise ValueError('component samples are not matched')
        for first in (2,5):
            if len({p['stereo_wire_bytes'] for p in case['profiles'][first:first+3]})!=1:raise ValueError('worker count changed wire')


@lru_cache(maxsize=512)
def records(size,width):
    if type(size) is not int or not 2<=size<=vxf.MAX_PAIR or type(width) is not int or width not in (0,8):raise ValueError('transport profile bounds')
    # Split point and byte values do not affect VXF framing size; synthetic
    # values still exercise the actual receiver's both-eye CRC/recovery gates.
    source=(bytes(range(256))*(size//256+1))[:size]
    left,right=source[:size//2],source[size//2:]
    packets=vxf.packetize(SID,1,1,1,10,NOW+TTL,left,right,width)
    return left,right,tuple(packets)


@lru_cache(maxsize=8192)
def link(size,width,rate_mbps,ip_bytes,scenario):
    if type(width) is not int or width not in (0,8) or type(rate_mbps) is not int or not 1<=rate_mbps<=10000 or type(ip_bytes) is not int or ip_bytes not in (28,48) or scenario not in ('clean','first_source_lost'):
        raise ValueError('invalid declared service scenario')
    if type(size) is not int or size<2:raise ValueError('invalid pair bytes')
    if size>vxf.MAX_PAIR:return dict(eligible=False,reason='encoded stereo exceeds unchanged256KiB VXF profile')
    left,right,packets=records(size,width);clock=[NOW]
    receiver=vxf.Receiver(SID,clock=lambda:clock[0]);deadline=receiver.issue_deadline(NOW,TTL,1,10)
    assert deadline==NOW+TTL
    first=None;first_data=None;complete=None;served=NOW;dropped=0
    for packet in packets:
        fields=vxf.HEADER.unpack_from(packet);kind,index=fields[2],fields[9]
        served+=((len(packet)+ip_bytes)*8*1000+rate_mbps-1)//rate_mbps
        if scenario=='first_source_lost' and kind==vxf.DATA and index==0:
            dropped+=1;continue
        clock[0]=served
        if first is None:first=served
        if first_data is None and kind==vxf.DATA:first_data=served
        result=receiver.receive(packet,served)
        if result:
            assert complete is None and (result.left,result.right)==(left,right)
            assert result.published_receiver_ns<deadline;complete=result.published_receiver_ns
        assert len(receiver.pending)<=vxf.MAX_CANDIDATES and receiver.buffered<=vxf.MAX_BUFFERED and receiver.orphan_bytes<=vxf.MAX_ORPHAN
    total_bytes=sum(len(p)+ip_bytes for p in packets)
    return dict(eligible=True,assembled=complete is not None,source_bytes=size,source_is_synthetic_size_only=True,
        records=len(packets),udp_application_bytes=sum(map(len,packets)),assumed_ip_udp_bytes=total_bytes,
        packet_payload_max=max(map(len,packets)),dropped_records=dropped,
        scheduled_all_wire_service_ms=(served-NOW)/1e6,
        sender_ready_to_assembly_ms=(complete-NOW)/1e6 if complete is not None else None,
        first_record_to_assembly_ms=(complete-first)/1e6 if complete is not None else None,
        first_data_to_assembly_ms=(complete-first_data)/1e6 if complete is not None and first_data is not None else None,
        complete_before_model_ticket=complete is not None,recovered_shards=receiver.stats['recovered_source_shards'],
        receiver_highwater_payload_bytes=receiver.highwater_bytes,
        sustainable_at_hypothetical90hz=total_bytes*8*90<=rate_mbps*1_000_000,
        model_ticket_remaining_after_assembly_ms=(deadline-complete)/1e6 if complete is not None else None)


def evaluate(data,rates=(50,100,300,600,1000),ip_sizes=(28,48),widths=(0,8),scenarios=('clean','first_source_lost')):
    validate_benchmark(data)
    if any(not isinstance(v,(tuple,list)) or not v or len(v)!=len(set(v)) for v in (rates,ip_sizes,widths,scenarios)):
        raise ValueError('distinct nonempty scenario declarations required')
    rows=[]
    for case in data['cases']:
        for profile in case['profiles']:
            for rate in rates:
                for ip in ip_sizes:
                    for width in widths:
                        for scenario in scenarios:
                            transfer=link(profile['stereo_wire_bytes'],width,rate,ip,scenario)
                            row=dict(case=case['name'],synthetic_duplicate_eye=case['synthetic_duplicate_eye'],profile=profile['name'],
                                rate_mbps=rate,ip_udp_header_bytes=ip,stripe_width=width,scenario=scenario,transfer=transfer)
                            if transfer['eligible'] and transfer['assembled']:
                                first=[transfer['first_record_to_assembly_ms']+x for x in profile['client_decode_wall_ms']['samples']]
                                full=[transfer['sender_ready_to_assembly_ms']+x for x in profile['server_plus_client_wall_ms']['samples']]
                                row.update(modeled_first_record_to_decoded_ms=distribution(first),
                                    modeled_server_then_wire_then_client_ms=distribution(full),
                                    measured_host_client_cpu_ms=distribution(profile['client_process_cpu_ms']['samples']),
                                    all_modeled_client_finishes_before_ticket=max(profile['client_decode_wall_ms']['samples'])<transfer['model_ticket_remaining_after_assembly_ms'])
                            rows.append(row)
    groups=[]
    for group in ('lossless','paired-a','paired-b'):
        for rate in rates:
            for width in widths:
                selected=[r for r in rows if r['case'].startswith(group+'/') and r['rate_mbps']==rate and r['stripe_width']==width and r['ip_udp_header_bytes']==28 and r['scenario']=='clean']
                arms=[]
                for profile in PROFILES:
                    cases=[r for r in selected if r['profile']==profile]
                    if not cases:continue
                    complete=[r for r in cases if 'modeled_first_record_to_decoded_ms' in r]
                    arms.append(dict(profile=profile,cases=len(cases),modeled_complete_cases=len(complete),
                        mean_modeled_client_horizon_p50_ms=mean(r['modeled_first_record_to_decoded_ms']['p50'] for r in complete) if complete else None,
                        mean_modeled_full_horizon_p50_ms=mean(r['modeled_server_then_wire_then_client_ms']['p50'] for r in complete) if complete else None,
                        mean_all_wire_bytes=mean(r['transfer']['assumed_ip_udp_bytes'] for r in complete) if complete else None,
                        all_sustainable_at_hypothetical90hz=bool(complete) and len(complete)==len(cases) and all(r['transfer']['sustainable_at_hypothetical90hz'] for r in complete)))
                groups.append(dict(captured_group=group,rate_mbps=rate,stripe_width=width,ip_udp_header_bytes=28,scenario='clean',profiles=arms))
    return dict(schema=1,status='pass',analysis_kind='measured_codec_plus_synthetic_serialized_wire_component_model',
        source_cases=len(data['cases']),actual_captured_pairs=sum(not c['synthetic_duplicate_eye'] for c in data['cases']),
        synthetic_duplicated_eye_profiles=sum(c['synthetic_duplicate_eye'] for c in data['cases']),rows=rows,groups=groups,
        sockets_opened=False,headset_accessed=False,measured_network=False,headset_gate_evaluated=False,promotion_eligible=False,
        assumptions=[
            'Only total measured stereo byte counts feed the actual bounded VXF packetizer and receiver; generated payload contents and equal-eye split are synthetic.',
            'Host component durations are inserted arithmetically into a hypothetical serial link schedule; no physical host/client clocks are subtracted.',
            'Both eyes are encoded before link service starts and decoded after complete assembly; no encode/send/decode overlap is modeled.',
            'Every packet including dropped or late-unused parity consumes declared service; per-packet nanoseconds round up.',
            'IPv4/UDP28 or IPv6/UDP48 bytes are counted; link/WiFi/MAC, security, ACK/control, pacing/congestion, propagation and jitter are absent.',
            'Transport packetization/CRC/recovery processing, GPU upload, compositor/runtime waiting and optical display cost are absent.',
            'First-record-to-decoded is a component horizon, not the native earliest-receive-to-post-xrEndFrame gate.',
            'The100ms receiver ticket and90Hz offered load are declared model inputs, not live settings; encoded over-cap pairs reject without truncation.',
            'Measured CPU timings come from one uncontrolled x86 host and cannot be treated as headset durations or empirical network rates.',
            'No arrival distribution, queue history or real loss reliability is inferred from the two deterministic erasure scenarios.'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);a=p.parse_args()
    source=HERE.parent/'rowblocks_workers/results/benchmark.json';receipt=source.parent/'measurement-provenance.json'
    before={str(source.relative_to(ROOT)):sha(source),str(receipt.relative_to(ROOT)):sha(receipt)};provenance=json.loads(receipt.read_text())
    if provenance['benchmark_sha256']!=sha(source):raise ValueError('benchmark does not match measurement receipt')
    for name,digest in provenance['source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('measured implementation differs from receipt: '+name)
    data=json.loads(source.read_text());result=evaluate(data)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('evidence changed during model')
    dependencies=[Path(__file__),Path(vxf.__file__),Path(vxf.base.__file__)]
    result.update(input_sha256=before,model_source_sha256={str(f.relative_to(ROOT)):sha(f) for f in dependencies})
    a.output.write_text(json.dumps(result,separators=(',',':'))+'\n')
    print(json.dumps(dict(status='pass',modeled_rows=len(result['rows']),actual_captured_pairs=result['actual_captured_pairs'],synthetic_duplicated_eye_profiles=result['synthetic_duplicated_eye_profiles'])))


if __name__=='__main__':main()
