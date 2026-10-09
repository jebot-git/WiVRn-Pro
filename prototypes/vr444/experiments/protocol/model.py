#!/usr/bin/env python3
"""Deterministic IID loss/accounting model, NOT measured transport or latency.
One QUIC DATAGRAM per packet; no ACK, manifest, link-layer or pacing cost.
Ideal systematic MDS erasure model recovers missing data iff >=k symbols arrive.
"""
import json, math

def recovered(k, r, p):
    # All data received works even when every repair is lost. Otherwise sufficient
    # repair symbols must arrive. This handles r>k and the shortened last group.
    return sum(math.comb(k,d)*p**d*(1-p)**(k-d) * sum(math.comb(r,j)*(1-p)**j*p**(r-j) for j in range(d,r+1)) for d in range(min(k,r)+1))

def row(size, p, r, k=10, udp=1200, ip=40):
    overhead=1+8+2+16+3+48 # short header, CID, PN, AEAD, DATAGRAM, app
    payload=udp-overhead
    n=math.ceil(size/payload)
    groups=[min(k,n-i) for i in range(0,n,k)]
    parity=len(groups)*r
    success=math.prod(recovered(g,r,p) for g in groups)
    # Repair symbols padded to one media slot; actual last data fragment short.
    wire=size+n*(overhead+8+ip)+parity*(udp+8+ip)
    return dict(stereo_encoded_bytes=size, iid_packet_loss=p, source_packets=n,
      fec_data_group=k, fec_repairs_per_group=r, repair_packets=parity,
      pair_media_complete_probability=success, ip_bytes=wire,
      overhead_fraction=wire/size-1, ip_mbps_at_90_hz=wire*8*90/1e6)

def main():
    assert abs(recovered(10,0,.01)-.99**10)<1e-12
    assert abs(recovered(10,1,.01)-.99**10*(1+.1))<1e-12
    assert recovered(10,2,0)==1 and recovered(10,2,1)==0
    assert row(1122,0,0)['source_packets']==1
    assert row(1123,0,0)['source_packets']==2
    rows=[row(s,p,r) for s in (16384,65536,262144,737280,2949120) for p in (0,.001,.01,.03) for r in (0,1,2)]
    print(json.dumps({'kind':'analytical_assumptions_only','assumptions':{
      'udp_payload_bytes':1200,'ipv6_header_bytes':40,'udp_header_bytes':8,
      'quic_header_and_aead_bytes':27,'datagram_frame_bytes':3,'application_header_bytes':48,
      'media_bytes_per_packet':1122,'packet_loss':'independent identical data/repair loss',
      'fec':'ideal systematic MDS; r=1 is XOR-equivalent; no decoder cost',
      'excluded':['manifest','ACK/control','handshake','L2/WiFi retransmission','burst loss','scheduling/deadlines','CPU/GPU','optical MTP'],
      'interpretation':'Media-only success is an upper bound on complete pair success; Mbps is accounting, not measured capacity.'},'rows':rows},indent=2))
if __name__=='__main__':main()
