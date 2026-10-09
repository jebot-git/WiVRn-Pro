#!/usr/bin/env python3
"""Read-only audit of TWO finite probe endpoint results; no networking."""
import argparse
import hashlib
import json
import math
from pathlib import Path
MASK=(1<<64)-1

def need(b,m):
    if not b: raise ValueError(m)
def integer(v,lo,hi): return type(v) is int and lo<=v<=hi
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def mix(x):
    x=(x+0x9e3779b97f4a7c15)&MASK
    x=((x^(x>>30))*0xbf58476d1ce4e5b9)&MASK
    x=((x^(x>>27))*0x94d049bb133111eb)&MASK
    return x^(x>>31)
def pair_hashes(token,frame,n):
    b=bytes.fromhex(token);seed=int.from_bytes(b[:8],'little')^int.from_bytes(b[8:],'little')^((frame*0x100000001b3)&MASK)
    result=[]
    for length,domain in [(n//2,0x123456789abcdef0),(n-n//2,0xabcdef0123456789)]:
        h=0xcbf29ce484222325
        for start in range(0,length,8):
            word=mix(seed^domain^(start//8))
            for i in range(min(8,length-start)): h=((h^((word>>(8*i))&255))*0x100000001b3)&MASK
        result.append(format(h,'x'))
    return result

def distribution(values):
    if not values:return None
    order=sorted(values);n=len(order)
    return {'n':n,'p50_ms':order[(n-1)//2]/1e6,'p95_ms':order[math.ceil(.95*n)-1]/1e6,'max_ms':order[-1]/1e6}
def audit(sender,receiver):
    need(sender['role']=='sender' and receiver['role']=='receiver','endpoint role')
    for key in ['schema','transport','port','run_token','planned_pairs','pair_bytes','hz','data_records_per_pair','max_record_bytes','pair_deadline_ms']:
        need(sender[key]==receiver[key],'unmatched '+key)
    c=sender;need(c['schema']==1 and c['transport'] in ['tcp','udp'],'protocol')
    need(integer(c['planned_pairs'],1,180) and integer(c['pair_bytes'],2,262144) and integer(c['hz'],5,90),'configuration')
    need(c['planned_pairs']<=c['hz']*8 and c['data_records_per_pair']==(c['pair_bytes']+1159)//1160 and c['max_record_bytes']==1200 and c['pair_deadline_ms']==100,'profile bounds')
    need((c['pair_bytes']+c['data_records_per_pair']*68+84)*8*c['hz']<=100000000,'nominal rate')
    need(len(c['run_token'])==32 and len(bytes.fromhex(c['run_token']))==16 and int(c['run_token'],16)!=0,'token')
    need(sender['local_ipv4']==receiver['peer_ipv4'] and sender['peer_ipv4']==receiver['local_ipv4'],'peer pairing')
    for endpoint in [sender,receiver]:
        rows=endpoint['rows'];need(isinstance(rows,list) and len(rows)==endpoint['processed_pairs'] and len(rows)<=c['planned_pairs'],'row count')
        need([r['frame'] for r in rows]==list(range(1,len(rows)+1)),'frame order/population')
        need(type(endpoint['aborted']) is bool and endpoint['physical_wire_bytes_measured'] is False,'measurement scope')
        need(endpoint['tcp_nodelay']==(c['transport']=='tcp'),'TCP setting')
        complete=0
        for row in rows:
            need(type(row['complete']) is bool and integer(row['unique_records'],0,c['data_records_per_pair']) and integer(row['duplicates'],0,1000000),'row counters')
            if row['local_start_offset_ns'] is not None: need(integer(row['local_start_offset_ns'],0,30000000000),'local clock start')
            if row['complete']:
                need(integer(row['local_complete_offset_ns'],0,30000000000) and row['local_start_offset_ns'] is not None and row['local_complete_offset_ns']>=row['local_start_offset_ns'],'local complete time')
                duration=row['sender_rtt_ns'] if endpoint['role']=='sender' else row['receiver_assembly_ns']
                need(row['local_complete_offset_ns']-row['local_start_offset_ns']==duration,'local clock duration')
                complete+=1;need(row['unique_records']==c['data_records_per_pair'] and integer(row['receiver_assembly_ns'],0,100000000),'complete frame')
                if endpoint['role']=='receiver': need([row['left_fnv64'],row['right_fnv64']]==pair_hashes(c['run_token'],row['frame'],c['pair_bytes']),'independent whole-eye source hashes')
                else: need(integer(row['sender_rtt_ns'],0,100000000),'same-sender RTT')
            else:need(row['local_complete_offset_ns'] is None and row['sender_rtt_ns'] is None and row['receiver_assembly_ns'] is None,'incomplete timings')
        need(complete==endpoint['complete_pairs'] and len(rows)-complete==endpoint['incomplete_processed_pairs'] and c['planned_pairs']-len(rows)==endpoint['unprocessed_pairs'],'endpoint accounting')
        for key in ['application_bytes_sent','application_bytes_received','records_sent','rejected_records','stale_records']:
            need(integer(endpoint[key],0,200000000),'byte/count bound')
        wire=endpoint['counted_ipv4_udp_bytes_sent']
        need(wire==(endpoint['application_bytes_sent']+28*endpoint['records_sent'] if c['transport']=='udp' else None),'wire accounting scope')
    received={r['frame']:r for r in receiver['rows']}
    acked=[r for r in sender['rows'] if r['complete']]
    for row in acked:
        peer=received.get(row['frame']);need(peer and peer['complete'] and peer['receiver_assembly_ns']==row['receiver_assembly_ns'],'ACK lacks corresponding exact receiver completion')
    unmatched_receiver=[r['frame'] for r in receiver['rows'] if r['complete'] and not any(s['frame']==r['frame'] for s in acked)]
    missing=sum(c['data_records_per_pair']-r['unique_records'] for r in receiver['rows'])+receiver['unprocessed_pairs']*c['data_records_per_pair']
    def cadence(rows,key):
        times=[r[key] for r in rows if r[key] is not None]
        need(all(a<b for a,b in zip(times,times[1:])),'nonmonotone local event times')
        return None if len(times)<2 else (len(times)-1)*1e9/(times[-1]-times[0])
    return {'schema':1,'status':'audited','transport':c['transport'],'planned_pairs':c['planned_pairs'],'exact_receiver_pairs':receiver['complete_pairs'],'sender_complete_acks':sender['complete_pairs'],
        'receiver_completions_without_sender_ack':unmatched_receiver,'missing_unique_data_records_at_receiver_pair_end':missing,
        'observed_sender_start_hz':cadence(sender['rows'],'local_start_offset_ns'),'observed_receiver_complete_hz':cadence(receiver['rows'],'local_complete_offset_ns'),
        'cadence_scope':'Rates span their first/last observed event; planned/incomplete counts remain separate and edge holds are not qualified.',
        'sender_rtt_successes':distribution([r['sender_rtt_ns'] for r in acked]),'receiver_assembly_successes':distribution([r['receiver_assembly_ns'] for r in receiver['rows'] if r['complete']]),
        'application_bytes_sent_both_directions':sender['application_bytes_sent']+receiver['application_bytes_sent'],
        'counted_ipv4_udp_bytes_sent_both_directions':None if c['transport']=='tcp' else sender['counted_ipv4_udp_bytes_sent']+receiver['counted_ipv4_udp_bytes_sent'],
        'scope':'Physical path only as established independently by deployment; synthetic opaque pairs, stop-and-wait, no production codec/OpenXR. Receiver deficits are deadline/receipt accounting, not an estimated radio packet-loss probability. Successful-only quantiles do not hide incomplete counts.',
        'promotion_eligible':False}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('sender',type=Path);p.add_argument('receiver',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result=audit(json.loads(a.sender.read_text()),json.loads(a.receiver.read_text()));result['input_sha256']={str(path):sha(path) for path in [a.sender,a.receiver,Path(__file__)]};a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'audited','exact_pairs':result['exact_receiver_pairs'],'acked_pairs':result['sender_complete_acks']}))
