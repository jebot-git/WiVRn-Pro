#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy
import json
import math
from pathlib import Path
import unittest
import model as m
import compact


def fixture():
    d=json.loads((m.HERE.parent/'rowblocks_workers/results/benchmark.json').read_text())
    d['cases']=[next(c for c in d['cases'] if c['name'].startswith('lossless/'))]
    d['repetitions']=5
    for p in d['cases'][0]['profiles']:
        for k in m.METRICS:
            s=p[k]['samples'][:5];p[k]=dict(samples=s,**m.distribution(s))
    return d


class TestModel(unittest.TestCase):
    def test_record_formula_boundaries(self):
        for size in (2,1151,1152,1153,9216,9217,m.vxf.MAX_PAIR):
            n=(size+1151)//1152
            for width in (0,8):
                r=m.link(size,width,1000,28,'clean')
                g=(n+7)//8 if width else 0
                self.assertTrue(r['assembled']);self.assertEqual(r['records'],n+2+g)
                self.assertEqual(r['udp_application_bytes'],size+44*n+176+1196*g)
                self.assertEqual(r['assumed_ip_udp_bytes'],r['udp_application_bytes']+28*r['records'])
                self.assertLessEqual(r['packet_payload_max'],1196)

    def test_tiny_two_eyes_and_first_record_horizon(self):
        r=m.link(2,0,100,28,'clean')
        self.assertEqual(r['records'],3)
        self.assertAlmostEqual(r['first_record_to_assembly_ms'],74*8/100000)
        self.assertEqual(r['first_data_to_assembly_ms'],0)
        self.assertGreater(r['scheduled_all_wire_service_ms'],r['sender_ready_to_assembly_ms'])

    def test_clean_parity_consumes_service_after_completion(self):
        r=m.link(10000,8,100,28,'clean')
        self.assertAlmostEqual(r['scheduled_all_wire_service_ms']-r['sender_ready_to_assembly_ms'],1224*8/100000)
        self.assertEqual(r['recovered_shards'],0)

    def test_single_source_loss(self):
        for size in (2,1152,1153,10000,m.vxf.MAX_PAIR):
            for width in (0,8):
                r=m.link(size,width,1000,28,'first_source_lost')
                self.assertEqual(r['assembled'],bool(width));self.assertEqual(r['dropped_records'],1)
                self.assertEqual(r['recovered_shards'],int(bool(width)))
                self.assertEqual(r['assumed_ip_udp_bytes'],m.link(size,width,1000,28,'clean')['assumed_ip_udp_bytes'])

    def test_ticket_expiry_is_not_success(self):
        for width in (0,8):
            r=m.link(m.vxf.MAX_PAIR,width,1,28,'clean')
            self.assertFalse(r['assembled']);self.assertIsNone(r['first_record_to_assembly_ms'])
            self.assertGreater(r['scheduled_all_wire_service_ms'],100)
            self.assertFalse(r['sustainable_at_hypothetical90hz'])

    def test_ipv6_exact_additional_wire(self):
        a=m.link(10000,8,100,28,'clean');b=m.link(10000,8,100,48,'clean')
        self.assertEqual(b['assumed_ip_udp_bytes']-a['assumed_ip_udp_bytes'],20*a['records'])
        self.assertGreater(b['sender_ready_to_assembly_ms'],a['sender_ready_to_assembly_ms'])

    def test_faster_rate_and_first_horizon(self):
        for width in (0,8):
            slow=m.link(12345,width,50,28,'clean');fast=m.link(12345,width,1000,28,'clean')
            self.assertLess(fast['first_record_to_assembly_ms'],slow['first_record_to_assembly_ms'])
            self.assertLess(fast['first_record_to_assembly_ms'],fast['sender_ready_to_assembly_ms'])

    def test_cap_rejects_without_quality_or_size_change(self):
        self.assertEqual(m.link(m.vxf.MAX_PAIR+1,0,100,28,'clean'),dict(eligible=False,reason='encoded stereo exceeds unchanged256KiB VXF profile'))
        with self.assertRaises(ValueError):m.records(m.vxf.MAX_PAIR+1,8)

    def test_declared_scenario_bounds(self):
        for args in ((1,0,100,28,'clean'),(True,0,100,28,'clean'),(2,False,100,28,'clean'),(2,4,100,28,'clean'),(2,8,0,28,'clean'),(2,8,100.0,28,'clean'),(2,8,100,28.0,'clean'),(2,8,100,28,'random')):
            with self.subTest(args=args),self.assertRaises(ValueError):m.link(*args)

    def test_component_arithmetic_and_no_gate_claim(self):
        d=fixture();r=m.evaluate(d,rates=(100,),ip_sizes=(28,),widths=(0,),scenarios=('clean',))
        self.assertEqual(len(r['rows']),8);self.assertFalse(r['headset_gate_evaluated']);self.assertFalse(r['promotion_eligible'])
        for p,row in zip(d['cases'][0]['profiles'],r['rows']):
            transfer=row['transfer']['first_record_to_assembly_ms']
            self.assertEqual(row['modeled_first_record_to_decoded_ms'],m.distribution([transfer+x for x in p['client_decode_wall_ms']['samples']]))

    def test_group_all_requires_all_cases_eligible(self):
        d=fixture();bad=copy.deepcopy(d['cases'][0]);bad['name']='lossless/over-cap'
        for p in bad['profiles']:p['stereo_wire_bytes']=m.vxf.MAX_PAIR+1
        d['cases'].append(bad)
        r=m.evaluate(d,rates=(1000,),ip_sizes=(28,),widths=(0,),scenarios=('clean',))
        for p in r['groups'][0]['profiles']:
            self.assertEqual((p['cases'],p['modeled_complete_cases']),(2,1))
            self.assertFalse(p['all_sustainable_at_hypothetical90hz'])

    def test_measured_samples_are_verified(self):
        base=fixture();m.validate_benchmark(base)
        mutations=[lambda d:d.update(repetitions=True),lambda d:d.update(**{'pass':False}),lambda d:d['cases'].append(copy.deepcopy(d['cases'][0])),
            lambda d:d['cases'][0].update(synthetic_duplicate_eye=True),lambda d:d['cases'][0]['profiles'][0].update(name='unknown'),
            lambda d:d['cases'][0]['profiles'][0].update(stereo_wire_bytes=True),lambda d:d['cases'][0]['profiles'][2].update(stereo_wire_bytes=999),
            lambda d:d['cases'][0]['profiles'][0]['client_decode_wall_ms']['samples'].__setitem__(0,float('nan')),
            lambda d:d['cases'][0]['profiles'][0]['client_decode_wall_ms']['samples'].__setitem__(0,True),
            lambda d:d['cases'][0]['profiles'][0]['client_decode_wall_ms'].update(p95=999)]
        for mutate in mutations:
            d=copy.deepcopy(base);mutate(d)
            with self.assertRaises(ValueError):m.validate_benchmark(d)
        d=copy.deepcopy(base);p=d['cases'][0]['profiles'][0];s=[x+1 for x in p['server_plus_client_wall_ms']['samples']]
        p['server_plus_client_wall_ms']=dict(samples=s,**m.distribution(s))
        with self.assertRaises(ValueError):m.validate_benchmark(d)

    def test_scenario_duplicates_and_empty_reject(self):
        for rates in ((),(100,100)):
            with self.assertRaises(ValueError):m.evaluate(fixture(),rates=rates)

    def test_distribution_order_statistics(self):
        self.assertEqual(m.distribution(list(range(1,32))),dict(p50=16,p95=30,max=31))
        for values in ([],[True],[math.inf],[-1],['1']):
            with self.assertRaises(ValueError):m.distribution(values)

    def test_lossless_evidence_table(self):
        d=fixture();r=m.evaluate(d,rates=(100,),ip_sizes=(28,),widths=(0,8),scenarios=('clean','first_source_lost'))
        p=compact.pack(r);self.assertEqual(compact.unpack(p),r)
        self.assertLess(len(p['transfer_values']),len(r['rows']))
        bad=copy.deepcopy(p);bad['row_values'][0][7]=-1
        with self.assertRaises(ValueError):compact.unpack(bad)
        bad=copy.deepcopy(r);bad['rows'][0]['new_unrepresented_field']=1
        with self.assertRaises(ValueError):compact.pack(bad)


if __name__=='__main__':unittest.main()
