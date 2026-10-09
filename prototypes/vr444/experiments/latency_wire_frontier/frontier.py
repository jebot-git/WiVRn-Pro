#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite component model: measured host codec tails plus bounded VXF wire service."""
import argparse
from dataclasses import dataclass
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


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result
    spec.loader.exec_module(result);return result


vxf=module('frontier_vxf',HERE.parent/'datagram_xor/transport.py')
measured=module('frontier_measured',HERE.parent/'rowblocks_fused_encoder/summarize.py')
NOW=1_000_000_000
TTL=100_000_000
SESSION=b'wire-frontier-v1'
PROFILES=('vr444_serial','rrf_up_serial','rrb32_fused_workers4','rrb128_fused_workers4')
RATES=(50,100,300,600,1000,2500,10000)
WIDTHS=(0,4,8,16)
SCENARIOS=('clean','first_source_lost')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Schedule:
    source_bytes:int
    lengths:tuple
    first:int|None
    complete:int|None
    recovered:int
    dropped:int


@lru_cache(maxsize=1024,typed=True)
def schedule(size,width,scenario):
    if type(size) is not int or not 2<=size<=vxf.MAX_PAIR:raise ValueError('pair bound')
    if type(width) is not int or width not in WIDTHS or scenario not in SCENARIOS:raise ValueError('scenario bound')
    # Total lengths are measured; contents and equal-eye split are synthetic.
    # Neither changes record sizes. The real receiver checks both synthetic CRCs.
    source=(bytes(range(256))*(size//256+1))[:size]
    left,right=source[:size//2],source[size//2:]
    packets=vxf.packetize(SESSION,1,1,1,10,NOW+TTL,left,right,width)
    clock=[NOW];receiver=vxf.Receiver(SESSION,clock=lambda:clock[0])
    receiver.issue_deadline(NOW,TTL,1,10)
    first=complete=None;dropped=0
    for i,packet in enumerate(packets):
        fields=vxf.HEADER.unpack_from(packet)
        clock[0]=NOW+(i+1)*1000
        if scenario=='first_source_lost' and fields[2]==vxf.DATA and fields[9]==0:
            dropped+=1;continue
        if first is None:first=i
        result=receiver.receive(packet,clock[0])
        if result is not None:
            if complete is not None or (result.left,result.right)!=(left,right):raise ValueError('stereo recovery differs')
            complete=i
        if (receiver.buffered>vxf.MAX_BUFFERED or receiver.orphan_bytes>vxf.MAX_ORPHAN
                or len(receiver.pending)>vxf.MAX_CANDIDATES):raise ValueError('receiver bound')
    return Schedule(size,tuple(map(len,packets)),first,complete,receiver.stats['recovered_source_shards'],dropped)


def prefix_bytes(s,ip,horizon):
    if type(ip) is not int or ip not in (28,48) or horizon not in ('full','first_record'):raise ValueError('horizon/header')
    if s.complete is None:return None
    start=0 if horizon=='full' else s.first+1
    return sum(n+ip for n in s.lengths[start:s.complete+1])


def link(s,rate,ip):
    if type(rate) is not int or not 1<=rate<=10000 or type(ip) is not int or ip not in (28,48):raise ValueError('rate/header')
    durations=[((n+ip)*8000+rate-1)//rate for n in s.lengths]
    complete=sum(durations[:s.complete+1]) if s.complete is not None else None
    first=sum(durations[:s.first+1]) if s.first is not None else None
    assembled=complete is not None and complete<TTL
    return dict(assembled=assembled,all_wire_bytes=sum(n+ip for n in s.lengths),
        all_wire_service_ms=sum(durations)/1e6,
        complete_ms=complete/1e6 if assembled else None,
        first_record_ms=(complete-first)/1e6 if assembled else None,
        model_ticket_remaining_ms=(TTL-complete)/1e6 if assembled else None,
        wire_only90hz_capacity=sum(n+ip for n in s.lengths)*8*90<=rate*1_000_000)


def boundary(cpu_delta_ms,bits_delta):
    """Ideal continuous service; exact integer-rate checks are separate."""
    if type(cpu_delta_ms) not in (int,float) or not math.isfinite(cpu_delta_ms) or type(bits_delta) is not int:raise ValueError('boundary input')
    if cpu_delta_ms==0:
        relation='always_better' if bits_delta<0 else 'always_worse' if bits_delta>0 else 'equal'
        return dict(relation=relation,critical_mbps=None)
    if bits_delta==0 or (cpu_delta_ms<0)==(bits_delta<0):
        return dict(relation='always_better' if cpu_delta_ms<0 else 'always_worse',critical_mbps=None)
    critical=-bits_delta/(cpu_delta_ms*1000)
    if not math.isfinite(critical):raise ValueError('boundary overflow')
    return dict(relation='better_above' if cpu_delta_ms<0 else 'better_below',critical_mbps=critical)


def validate(data):
    measured.validate(data)
    for case in data['cases']:
        for p in case['profiles']:
            if p['stereo_wire_bytes']>32*1024*1024:raise ValueError('source size bound')
    return data


def evaluate(data,rates=RATES,ips=(28,48),widths=WIDTHS,scenarios=SCENARIOS):
    validate(data)
    for seq in (rates,ips,widths,scenarios):
        if not isinstance(seq,(tuple,list)) or not seq or len(seq)!=len(set(seq)):raise ValueError('distinct nonempty declarations')
    if any(type(r) is not int or not 1<=r<=10000 for r in rates) or any(type(i) is not int or i not in (28,48) for i in ips) or any(type(w) is not int or w not in WIDTHS for w in widths) or any(s not in SCENARIOS for s in scenarios):raise ValueError('declaration bounds')
    rows=[];crossovers=[]
    for case in data['cases']:
        profiles={p['name']:p for p in case['profiles'] if p['name'] in PROFILES}
        for ip in ips:
            for width in widths:
                for scenario in scenarios:
                    schedules={name:schedule(p['stereo_wire_bytes'],width,scenario) if p['stereo_wire_bytes']<=vxf.MAX_PAIR else None for name,p in profiles.items()}
                    for rate in rates:
                        for name,p in profiles.items():
                            s=schedules[name]
                            row=dict(case=case['name'],synthetic=case['synthetic_duplicate_eye'],profile=name,rate_mbps=rate,ip_udp_bytes=ip,stripe_width=width,scenario=scenario,eligible=s is not None)
                            if s is not None:
                                transfer=link(s,rate,ip);row.update(transfer)
                                row.update(records=len(s.lengths),source_bytes=s.source_bytes,recovered=s.recovered,dropped=s.dropped)
                                if transfer['assembled']:
                                    for horizon,metric,delay in [('full','server_plus_client_wall_ms',transfer['complete_ms']),('first_record','client_decode_wall_ms',transfer['first_record_ms'])]:
                                        for q in ('p50','p95','max'):row[horizon+'_'+q+'_ms']=p[metric][q]+delay
                                    row['all_client_finishes_before_ticket']=p['client_decode_wall_ms']['max']<transfer['model_ticket_remaining_ms']
                            rows.append(row)
                    if case['synthetic_duplicate_eye']:continue
                    base=profiles[PROFILES[0]];bs=schedules[PROFILES[0]]
                    if bs is None or bs.complete is None:continue
                    for name in PROFILES[1:]:
                        p=profiles[name];s=schedules[name]
                        if s is None or s.complete is None:continue
                        for horizon,metric in [('full','server_plus_client_wall_ms'),('first_record','client_decode_wall_ms')]:
                            bits=(prefix_bytes(s,ip,horizon)-prefix_bytes(bs,ip,horizon))*8
                            for q in ('p50','p95'):
                                cpu=p[metric][q]-base[metric][q];b=boundary(cpu,bits)
                                checks=[];critical=b['critical_mbps']
                                if critical is not None and math.isfinite(critical):
                                    for rate in sorted({max(1,min(10000,math.floor(critical)+step)) for step in (-1,0,1,2)}):
                                        a,z=link(s,rate,ip),link(bs,rate,ip)
                                        key='complete_ms' if horizon=='full' else 'first_record_ms'
                                        checks.append(dict(rate_mbps=rate,both_assembled=a['assembled'] and z['assembled'],exact_rounded_delta_ms=cpu+a[key]-z[key] if a['assembled'] and z['assembled'] else None))
                                crossovers.append(dict(case=case['name'],profile=name,ip_udp_bytes=ip,stripe_width=width,scenario=scenario,horizon=horizon,quantile=q,cpu_delta_ms=cpu,service_bits_delta=bits,**b,integer_rate_checks=checks))
    groups=[]
    for group in ('lossless','paired-a','paired-b'):
        for ip in ips:
            for width in widths:
                for scenario in scenarios:
                    for rate in rates:
                        selected=[r for r in rows if r['case'].startswith(group+'/') and r['ip_udp_bytes']==ip and r['stripe_width']==width and r['scenario']==scenario and r['rate_mbps']==rate]
                        base={r['case']:r for r in selected if r['profile']==PROFILES[0]}
                        for name in PROFILES[1:]:
                            pairs=[(r,base[r['case']]) for r in selected if r['profile']==name and r.get('assembled') and base[r['case']].get('assembled')]
                            row=dict(group=group,profile=name,rate_mbps=rate,ip_udp_bytes=ip,stripe_width=width,scenario=scenario,comparable_pairs=len(pairs))
                            for h in ('full','first_record'):
                                for q in ('p50','p95'):
                                    key=h+'_'+q+'_ms'
                                    row[key+'_mean_delta']=mean(a[key]-b[key] for a,b in pairs) if pairs else None
                                    row[key+'_improves']=sum(a[key]<b[key] for a,b in pairs)
                            groups.append(row)
    return dict(schema=1,status='pass',analysis_kind='frozen_host_codec_plus_hypothetical_serial_wire_frontier',rows=rows,groups=groups,crossovers=crossovers,
        actual_captured_pairs=sum(not c['synthetic_duplicate_eye'] for c in data['cases']),synthetic_duplicated_eye_profiles=sum(c['synthetic_duplicate_eye'] for c in data['cases']),
        sockets_opened=False,headset_accessed=False,measured_network=False,promotion_eligible=False,
        assumptions=[
            'Packet contents and equal eye split are synthetic; total codec lengths come from exact frozen paired observations.',
            'The real bounded VXF packetizer and receiver establish completion record order at a1us synthetic spacing; the separate declared serialized service model rounds every record to integer ns.',
            'Both eyes encode before service and decode after assembly; no overlap, queue, propagation, jitter, congestion control, ACK/security/link-layer, packet CRC/recovery CPU or runtime cost is included.',
            'Every record, including dropped or late unused parity, consumes IP/UDP bytes and declared service. The hypothetical100ms ticket begins at wire service start after source encoding, not before the full horizon;90Hz is wire-only capacity.',
            'One host supplies paired stage samples. Quantiles shift by deterministic model service; these are not measured network/headset percentiles or statistical confidence bounds.',
            'Full horizon adds source transcode, service and client decode. First-record horizon omits server and pre-first-record service and is not the native post-xrEndFrame metric.',
            'Critical rates use ideal continuous service. Nearby bounded integer-Mbps checks use exact per-record ns ceilings; neither is a measured available link rate.',
            'Cases above unchanged256KiB VXF stereo cap are ineligible, without truncation. A model wire-only90Hz capacity check does not establish end-to-end throughput.',
            'Captured-content crossover selection is an offline diagnostic, not an executable content-adaptive policy or production promotion.'])


def compact(result):
    out=dict(result);rows=out.pop('rows');columns=sorted(set().union(*(r.keys() for r in rows)))
    out['row_table']=dict(columns=columns,values=[[r.get(k) for k in columns] for r in rows])
    return out


def evidence():
    source=HERE.parent/'rowblocks_fused_encoder/results/benchmark.json'
    receipt=source.parent/'measurement-provenance.json'
    provenance=json.loads(receipt.read_text())
    if provenance.get('benchmark_sha256')!=sha(source) or provenance.get('inputs_sources_binaries_unchanged') is not True:raise ValueError('measurement evidence differs')
    bound={str(source.relative_to(ROOT)):sha(source),str(receipt.relative_to(ROOT)):sha(receipt)}
    for name,digest in provenance['source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('measured source differs: '+name)
        bound[name]=digest
    for f in (Path(__file__),Path(vxf.__file__),Path(vxf.base.__file__),Path(measured.__file__)):
        bound[str(f.relative_to(ROOT))]=sha(f)
    return json.loads(source.read_text()),bound


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);a=p.parse_args()
    if a.output.exists():raise ValueError('refuse existing output')
    data,bound=evidence();result=evaluate(data)
    if any(sha(ROOT/n)!=h for n,h in bound.items()):raise ValueError('evidence/source changed during analysis')
    result['input_and_source_sha256']=bound
    a.output.write_text(json.dumps(compact(result),separators=(',',':'))+'\n')
    print(json.dumps(dict(status='pass',rows=len(result['rows']),crossovers=len(result['crossovers']),actual_captured_pairs=result['actual_captured_pairs'])))


if __name__=='__main__':main()
