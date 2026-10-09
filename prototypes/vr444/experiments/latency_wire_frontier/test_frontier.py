#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy
import json
import math
import unittest
from unittest.mock import patch
import frontier as f


def fixture():
    d=json.loads((f.HERE.parent/'rowblocks_fused_encoder/results/benchmark.json').read_text())
    d['cases']=[next(c for c in d['cases'] if c['name'].startswith('lossless/'))]
    return d


class TestFrontier(unittest.TestCase):
    def test_actual_packetizer_record_formula(self):
        for size in (2,1151,1152,1153,9216,9217,f.vxf.MAX_PAIR):
            n=(size+1151)//1152
            for width in f.WIDTHS:
                s=f.schedule(size,width,'clean');g=(n+width-1)//width if width else 0
                self.assertEqual(len(s.lengths),n+2+g)
                self.assertEqual(sum(s.lengths),size+44*n+176+1196*g)
                self.assertLessEqual(max(s.lengths),1196)
                self.assertIsNotNone(s.complete);self.assertEqual(s.recovered,0)

    def test_single_record_two_eye_horizons(self):
        s=f.schedule(2,0,'clean');r=f.link(s,100,28)
        self.assertEqual(s.lengths,(88,46,88));self.assertEqual(s.complete,1)
        self.assertEqual(f.prefix_bytes(s,28,'full'),190)
        self.assertEqual(f.prefix_bytes(s,28,'first_record'),74)
        self.assertEqual(r['complete_ms'],.0152)
        self.assertEqual(r['first_record_ms'],.00592)
        self.assertGreater(r['all_wire_service_ms'],r['complete_ms'])

    def test_parity_and_dropped_record_still_consume_service(self):
        for size in (2,1152,1153,10000,f.vxf.MAX_PAIR):
            for width in f.WIDTHS:
                clean=f.schedule(size,width,'clean');lost=f.schedule(size,width,'first_source_lost')
                self.assertEqual(clean.lengths,lost.lengths)
                self.assertEqual(lost.dropped,1);self.assertEqual(lost.complete is not None,bool(width))
                self.assertEqual(lost.recovered,int(bool(width)))
                self.assertEqual(f.link(clean,1000,28)['all_wire_service_ms'],f.link(lost,1000,28)['all_wire_service_ms'])
        s=f.schedule(10000,8,'clean');r=f.link(s,100,28)
        self.assertAlmostEqual(r['all_wire_service_ms']-r['complete_ms'],1224*8/100000)

    def test_crc_recovery_order_differs_for_single_shard(self):
        clean=f.schedule(2,8,'clean');lost=f.schedule(2,8,'first_source_lost')
        self.assertEqual((clean.complete,lost.complete),(1,3))
        self.assertEqual(f.prefix_bytes(lost,28,'full')-f.prefix_bytes(clean,28,'full'),116+1224)

    def test_ticket_expiry_is_not_completion(self):
        r=f.link(f.schedule(f.vxf.MAX_PAIR,8,'clean'),1,28)
        self.assertFalse(r['assembled']);self.assertIsNone(r['complete_ms'])
        self.assertIsNone(r['first_record_ms']);self.assertFalse(r['wire_only90hz_capacity'])

    def test_integer_ns_rounding_against_independent_sum(self):
        s=f.schedule(12345,8,'clean')
        for rate in (3,17,101,999,10000):
            r=f.link(s,rate,48)
            durations=[math.ceil((n+48)*8000/rate) for n in s.lengths]
            self.assertEqual(r['complete_ms'],sum(durations[:s.complete+1])/1e6)
            self.assertEqual(r['all_wire_service_ms'],sum(durations)/1e6)
            self.assertEqual(r['first_record_ms'],sum(durations[s.first+1:s.complete+1])/1e6)

    def test_ipv6_header_bytes(self):
        s=f.schedule(12345,4,'clean');a=f.link(s,100,28);b=f.link(s,100,48)
        self.assertEqual(b['all_wire_bytes']-a['all_wire_bytes'],20*len(s.lengths))
        self.assertGreater(b['complete_ms'],a['complete_ms'])

    def test_boundary_all_sign_classes(self):
        for cpu,bits,relation,critical in [(0,0,'equal',None),(0,-8,'always_better',None),(0,8,'always_worse',None),(-1,0,'always_better',None),(1,0,'always_worse',None),(-1,-8,'always_better',None),(1,8,'always_worse',None),(-.1,80000,'better_above',800),(1,-80000,'better_below',80)]:
            self.assertEqual(f.boundary(cpu,bits),dict(relation=relation,critical_mbps=critical))

    def test_boundary_exhaustive_positive_rate_inequalities(self):
        for cpu in (-2,-.1,0,.1,2):
            for bits in (-80000,-800,0,800,80000):
                b=f.boundary(cpu,bits)
                for rate in (1,4,80,100,800,1000,10000):
                    delta=cpu+bits/(rate*1000)
                    relation=b['relation'];critical=b['critical_mbps']
                    predicted=relation=='always_better' or (relation=='better_above' and rate>critical) or (relation=='better_below' and rate<critical)
                    self.assertEqual(delta<0,predicted,(cpu,bits,rate,b))

    def test_declared_bounds(self):
        for args in [(1,0,'clean'),(True,0,'clean'),(f.vxf.MAX_PAIR+1,8,'clean'),(2,False,'clean'),(2,2,'clean'),(2,8,'random')]:
            with self.assertRaises(ValueError):f.schedule(*args)
        s=f.schedule(2,0,'clean')
        for rate,ip in [(True,28),(0,28),(10001,28),(100.0,28),(100,28.0),(100,29)]:
            with self.assertRaises(ValueError):f.link(s,rate,ip)
        for cpu,bits in [(True,1),(float('nan'),1),(math.inf,1),(.1,True),(.1,1.0)]:
            with self.assertRaises(ValueError):f.boundary(cpu,bits)
        for ip,horizon in [(28.0,'full'),(28,'unknown')]:
            with self.assertRaises(ValueError):f.prefix_bytes(s,ip,horizon)

    def test_measured_component_quantiles_shift_exactly(self):
        d=fixture();result=f.evaluate(d,rates=(100,),ips=(28,),widths=(0,),scenarios=('clean',))
        self.assertEqual(len(result['rows']),4)
        self.assertFalse(result['promotion_eligible']);self.assertFalse(result['measured_network'])
        profiles={p['name']:p for p in d['cases'][0]['profiles']}
        for row in result['rows']:
            p=profiles[row['profile']]
            self.assertEqual(row['full_p95_ms'],p['server_plus_client_wall_ms']['p95']+row['complete_ms'])
            self.assertEqual(row['first_record_p95_ms'],p['client_decode_wall_ms']['p95']+row['first_record_ms'])
        for crossover in result['crossovers']:
            for check in crossover['integer_rate_checks']:
                self.assertLessEqual(check['rate_mbps'],10000);self.assertGreaterEqual(check['rate_mbps'],1)

    def test_no_fec_source_loss_has_no_fabricated_quantiles(self):
        result=f.evaluate(fixture(),rates=(100,),ips=(28,),widths=(0,),scenarios=('first_source_lost',))
        self.assertFalse(result['crossovers'])
        for row in result['rows']:
            self.assertFalse(row['assembled']);self.assertNotIn('full_p95_ms',row)

    def test_over_cap_is_ineligible_without_truncation(self):
        d=fixture()
        for p in d['cases'][0]['profiles']:p['stereo_wire_bytes']=f.vxf.MAX_PAIR+1
        result=f.evaluate(d,rates=(100,),ips=(28,),widths=(0,),scenarios=('clean',))
        self.assertTrue(all(not r['eligible'] for r in result['rows']));self.assertFalse(result['crossovers'])

    def test_measured_provenance_shape_rejections(self):
        mutations=[lambda d:d.update(repetitions=True),lambda d:d.update(**{'pass':False}),lambda d:d['cases'].append(copy.deepcopy(d['cases'][0])),lambda d:d['cases'][0].update(synthetic_duplicate_eye=True),lambda d:d['cases'][0]['profiles'][0].update(stereo_wire_bytes=True),lambda d:d['cases'][0]['profiles'][2].update(stereo_wire_bytes=99),lambda d:d['cases'][0]['profiles'][0]['client_decode_wall_ms'].update(p95=999),lambda d:d['cases'][0]['profiles'][0]['client_decode_wall_ms']['samples'].__setitem__(0,float('nan'))]
        for mutate in mutations:
            d=fixture();mutate(d)
            with self.assertRaises(ValueError):f.validate(d)

    def test_declaration_rejections(self):
        for kwargs in [dict(rates=()),dict(rates=(100,100)),dict(rates=(100.0,)),dict(ips=(28.0,)),dict(widths=(False,)),dict(scenarios=('unknown',))]:
            with self.assertRaises(ValueError):f.evaluate(fixture(),**kwargs)

    def test_compact_table_preserves_all_present_fields(self):
        result=f.evaluate(fixture(),rates=(100,),ips=(28,),widths=(0,8),scenarios=f.SCENARIOS)
        packed=f.compact(result);table=packed['row_table']
        self.assertNotIn('rows',packed);self.assertEqual(len(result['rows']),len(table['values']))
        for expected,values in zip(result['rows'],table['values']):
            reconstructed=dict(zip(table['columns'],values))
            self.assertTrue(all(reconstructed[k]==v for k,v in expected.items()))
            self.assertTrue(all(v is None for k,v in reconstructed.items() if k not in expected))

    def test_evidence_control_and_measured_source_hash_fail_closed(self):
        real=f.sha
        for suffix in ('rowblocks_fused_encoder/results/benchmark.json','rowblocks_fused_encoder/encoder.hpp'):
            with patch.object(f,'sha',side_effect=lambda p:'0'*64 if str(p).endswith(suffix) else real(p)):
                with self.assertRaises(ValueError):f.evidence()


if __name__=='__main__':unittest.main()
