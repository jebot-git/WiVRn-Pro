#!/usr/bin/env python3
import copy
import math
import unittest
from audit import ARMS, TIMES, audit, matched_variants


def fixture():
    manifest={'cases':[]};blocks={'cases':[]}
    for eye in range(2):
        row=dict(rrf=f'capture/1-{eye}.rrf',rgba=f'capture/1-{eye}.rgba',width=1,height=17,q=1,
                 wire_bytes=70+eye,exact=True,source_kind='captured_eye',rgba_sha256=str(eye)*64)
        manifest['cases'].append(row)
        for b in (16,32,64,128):
            block=dict(row);block.pop('rrf');block.update(rrb=f'capture/1-{eye}.r{b}.rrb',block_rows=b,
                blocks=math.ceil(17/b),source_kind='captured',wire_bytes=100+b+eye)
            blocks['cases'].append(block)
    variants=[]
    for v,b in enumerate((0,16,32,64,128)):
        variants.append(dict(variant=v,eye_wire_bytes=[100+b,101+b] if b else [70,71],
                             eye_block_rows=[b,b],eye_block_counts=[math.ceil(17/b) if b else 1]*2))
    iterations=[]
    for order in range(10):
        arm=(3+order)%10
        row=dict(rep=0,order=order,mode=ARMS[arm],variant=arm//2,stereo_total_ms=6.,
                 stereo_poison_ms=1.,stereo_preflight_decode_ms=1.,stereo_upload_ms=1.,
                 stereo_pipeline_wall_ms=1.,stereo_device_span_ms=.5,
                 stereo_uploaded_pixel_bytes=17*(6 if arm%2 else 8),timed_queue_submissions=2,
                 max_srgb_code_error=.2,image_bytes_exact=True,sampled_cpu_copy_baseline_exact=True,
                 malformed_pair_rejected_before_write_or_submit=True,wrong_shape_rejected_before_write_or_submit=True)
        iterations.append(row)
    case=dict(name='corpus/capture/1',synthetic_duplicated_eyes=False,device='unit-test-device',
              device_type=2,driver=123,width=1,height=17,variants=variants,iterations=iterations)
    data=dict(schema=1,experiment='rowblocks-image-timing',qualification_only=False,headset_used=False,
              promotion_blocked=True,warmups=3,repetitions=1,cpu_backend='scalar-fallback',
              checked_crc_grammar_lz4_in_every_arm=True,poison_in_total_time=True,
              oracle_outside_total_time=True,reused_workspaces=True,cases=[case],case_count=1,pass_=True)
    data['pass']=data.pop('pass_')
    return data,manifest,blocks


class AuditTests(unittest.TestCase):
    def test_valid(self):
        data,manifest,blocks=fixture();r=audit(data,manifest,blocks)
        self.assertEqual(r['measured_stereo_arm_invocations'],10)
        self.assertEqual(len(r['captured_summary_by_base_q']['1']),10)

    def test_scope_failures(self):
        changes={ 'schema':2,'experiment':'other','qualification_only':True,'headset_used':True,
                  'promotion_blocked':False,'warmups':1,'repetitions':0,'cpu_backend':'unknown',
                  'checked_crc_grammar_lz4_in_every_arm':False,'poison_in_total_time':False,
                  'oracle_outside_total_time':False,'reused_workspaces':False,'case_count':0,'pass':False}
        for k,v in changes.items():
            with self.subTest(k=k):
                d,m,b=fixture();d[k]=v
                with self.assertRaises(ValueError):audit(d,m,b)

    def test_observation_failures(self):
        changes=dict(rep=True,order=4,mode=ARMS[0],variant=True,stereo_uploaded_pixel_bytes=0,
                     timed_queue_submissions=1,max_srgb_code_error=.5,image_bytes_exact=False,
                     sampled_cpu_copy_baseline_exact=False,malformed_pair_rejected_before_write_or_submit=False,
                     wrong_shape_rejected_before_write_or_submit=False,stereo_total_ms=3.)
        changes.update({k:float('nan') for k in TIMES[1:]})
        for k,v in changes.items():
            with self.subTest(k=k):
                d,m,b=fixture();d['cases'][0]['iterations'][0][k]=v
                with self.assertRaises(ValueError):audit(d,m,b)
        d,m,b=fixture();d['cases'][0]['iterations'].pop()
        with self.assertRaises(ValueError):audit(d,m,b)

    def test_partitions(self):
        for k,v in dict(variant=True,eye_wire_bytes=[1,2],eye_block_rows=[16,32],eye_block_counts=[1,1]).items():
            with self.subTest(k=k):
                d,m,b=fixture();d['cases'][0]['variants'][1][k]=v
                with self.assertRaises(ValueError):audit(d,m,b)
        for k,v in dict(name='other',width=2,synthetic_duplicated_eyes=True,device_type=4).items():
            d,m,b=fixture();d['cases'][0][k]=v
            with self.assertRaises(ValueError):audit(d,m,b)

    def test_source_variants(self):
        for k,v in dict(rrb='other.r16.rrb',block_rows=32,width=2,height=18,q=8,rgba='other',
                        rgba_sha256='f'*64,exact=False,source_kind='synthetic',blocks=1).items():
            with self.subTest(k=k):
                d,m,b=fixture();b['cases'][0][k]=v
                with self.assertRaises(ValueError):matched_variants(m,b)
        for duplicate in (True,False):
            d,m,b=fixture()
            if duplicate:b['cases'].append(copy.deepcopy(b['cases'][0]))
            else:b['cases'].pop()
            with self.assertRaises(ValueError):matched_variants(m,b)


if __name__=='__main__':unittest.main()
