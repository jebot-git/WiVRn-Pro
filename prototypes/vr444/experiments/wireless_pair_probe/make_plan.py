#!/usr/bin/env python3
"""Generate a root-operated plan only. Does not inspect devices or open sockets."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--base-port',type=int,default=53171);p.add_argument('--remote',default='{remote}');a=p.parse_args()
    if not 49154<=a.base_port<=65528:raise ValueError('eight dedicated ports plus two preceding smoke ports required')
    build=Path('/tmp/wivrn-wireless-pair-build');host='192.168.1.106';quest='192.168.1.105';tokens={k:secrets.token_hex(16) for k in ['low-1','low-2','high-1','high-2','smoke']}
    def arm(name,transport,port,group,frames,pair,hz):
        common=[str(port),tokens[group],str(frames),str(pair),str(hz)]
        return dict(name=name,transport=transport,matched_token_group=group,receiver_port=port,frames=frames,pair_bytes=pair,hz=hz,
            nominal_ipv4_udp_offered_mbps=(pair+((pair+1159)//1160)*68+84)*8*hz/1e6,
            receiver_argv=[a.remote+'/probe','receive',transport,quest,host]+common,
            sender_argv=[str(build/'probe'),'send',transport,host,quest]+common,
            endpoint_timeout_s=40,receiver_start_before_sender=True)
    runs=[]
    for profile,frames,pair,hz in [('low',60,32768,20),('high',180,98304,90)]:
        for repeat,transports in [(1,['tcp','udp']),(2,['udp','tcp'])]:
            for transport in transports:runs.append(arm(f'{profile}-{transport}-{repeat}',transport,a.base_port+len(runs),f'{profile}-{repeat}',frames,pair,hz))
    plan=dict(schema=1,scope='Actual host Ethernet to Quest wireless path only if independently confirmed; opaque synthetic complete pairs; no production adapter/OpenXR.',
        host_ipv4=host,expected_host_interface='enp113s0',headset_ipv4=quest,adb_target=quest+':5555',expected_headset_serial='230YC01D8W000E',
        preparation_only=True,no_endpoint_executed_by_plan_generator=True,
        source_directory=str(Path(__file__).resolve().parent),
        files=[dict(local=str(build/n),remote=remote,sha256=sha(build/n)) for n,remote in [('probe-android','probe'),('test-android','grammar-test')]],
        host_binary=dict(path=str(build/'probe'),sha256=sha(build/'probe')),
        require_free_receiver_ports=list(range(a.base_port-2,a.base_port+8)),
        pure_device_grammar_argv=[a.remote+'/grammar-test'],
        optional_smoke=[arm('smoke-tcp','tcp',a.base_port-2,'smoke',5,32768,20),arm('smoke-udp','udp',a.base_port-1,'smoke',5,32768,20)],
        runs=runs,root_execution_notes=[
            'Verify exact device serial, bound IP/interface and each receiver port unused; root owns every socket/device operation.',
            'Launch one receiver then its sender promptly, inside five seconds; retain both stdout/stderr/statuses and wait for both before the next arm.',
            'No overlap with production latency runs; no firewall/route/sysctl/daemon changes.',
            'A shared random token within each TCP/UDP comparison gives identical bytes; ports are distinct and receivers close after finite runs.',
            'Validate every endpoint pair with audit_network.py; retain incomplete and ACK-loss counts with success-only quantiles.',
            'The high profile is nominal75Mbps for two seconds, stop-and-wait; achieved cadence may be below90Hz and is reported.',
            'TCP physical wire/retransmission bytes and UDP WiFi/MAC overhead remain unmeasured.'
        ])
    a.output.write_text(json.dumps(plan,indent=2)+'\n');print(json.dumps({'status':'plan generated only','arms':len(runs),'path':str(a.output)}))
if __name__=='__main__':main()
