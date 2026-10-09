import copy
import unittest
import audit_network as a

class Audits(unittest.TestCase):
    def fixture(self,kind='udp'):
        base=dict(schema=1,transport=kind,port=53171,run_token='0123456789abcdeffedcba9876543210',planned_pairs=2,pair_bytes=31,hz=20,data_records_per_pair=1,max_record_bytes=1200,pair_deadline_ms=100,processed_pairs=2,complete_pairs=2,incomplete_processed_pairs=0,unprocessed_pairs=0,aborted=False,physical_wire_bytes_measured=False,tcp_nodelay=kind=='tcp',records_sent=2,rejected_records=0,stale_records=0)
        sender=dict(base,role='sender',local_ipv4='192.0.2.1',peer_ipv4='192.0.2.2',application_bytes_sent=142,application_bytes_received=112,counted_ipv4_udp_bytes_sent=198 if kind=='udp' else None)
        receiver=dict(base,role='receiver',local_ipv4='192.0.2.2',peer_ipv4='192.0.2.1',application_bytes_sent=112,application_bytes_received=142,counted_ipv4_udp_bytes_sent=168 if kind=='udp' else None)
        sender['rows']=[];receiver['rows']=[]
        for frame in [1,2]:
            left,right=a.pair_hashes(base['run_token'],frame,31)
            r=dict(frame=frame,complete=True,unique_records=1,duplicates=0,sender_rtt_ns=None,receiver_assembly_ns=1000,local_start_offset_ns=frame*50000000,local_complete_offset_ns=frame*50000000+1000,left_fnv64=left,right_fnv64=right)
            receiver['rows'].append(r);sender['rows'].append(dict(r,sender_rtt_ns=2000,local_complete_offset_ns=frame*50000000+2000,left_fnv64=None,right_fnv64=None))
        return sender,receiver
    def test_complete(self):
        for kind in ['tcp','udp']:
            s,r=self.fixture(kind);result=a.audit(s,r);self.assertEqual(result['exact_receiver_pairs'],2);self.assertEqual(result['sender_complete_acks'],2)
    def test_missing_ack_is_not_missing_data(self):
        s,r=self.fixture();s['rows'][1].update(complete=False,local_complete_offset_ns=None,sender_rtt_ns=None,receiver_assembly_ns=None,unique_records=0);s.update(complete_pairs=1,incomplete_processed_pairs=1)
        result=a.audit(s,r);self.assertEqual(result['receiver_completions_without_sender_ack'],[2]);self.assertEqual(result['missing_unique_data_records_at_receiver_pair_end'],0)
    def test_missing_data_not_success(self):
        s,r=self.fixture()
        for e in [s,r]:e['rows'][1].update(complete=False,local_complete_offset_ns=None,sender_rtt_ns=None,receiver_assembly_ns=None,unique_records=0);e.update(complete_pairs=1,incomplete_processed_pairs=1)
        self.assertEqual(a.audit(s,r)['missing_unique_data_records_at_receiver_pair_end'],1)
    def test_mutations(self):
        mutations=[lambda s,r:r.update(run_token='1'*32),lambda s,r:r.update(port=53172),lambda s,r:r.update(pair_bytes=32),lambda s,r:r['rows'][0].update(right_fnv64='0'),lambda s,r:r['rows'][0].update(complete=False),lambda s,r:s['rows'][0].update(sender_rtt_ns=100000001),lambda s,r:s['rows'][0].update(frame=2),lambda s,r:r.update(complete_pairs=1),lambda s,r:s.update(counted_ipv4_udp_bytes_sent=0),lambda s,r:r.update(physical_wire_bytes_measured=True),lambda s,r:r.update(peer_ipv4='192.0.2.3')]
        for f in mutations:
            s,r=self.fixture();f(s,r)
            with self.assertRaises(ValueError):a.audit(s,r)
    def test_empty_success_quantiles(self):self.assertIsNone(a.distribution([]))
if __name__=='__main__':unittest.main()
