import copy,json,struct,tempfile,unittest,zlib
from pathlib import Path
import audit
def row():
    return dict(frame=1,eye=0,warp=42,rrb_generation=1,bytes=100,effective_codec='rrb32',rrb_output='staged-gpu',rrb_block_rows=32,stereo_envelope_cap=262144,rrb_workers=0,configured_rrb_workers=4,pair_decode_scope='stereo_once_reported_per_eye',gpu_timing_valid=False,staging_copy_timing_valid=False,decode_gpu_ms=0,cpu_decode_ms=0,staging_copy_ms=0,capture_source='not_captured',**{k:0 for k in audit.STAGES})
class Audit(unittest.TestCase):
    def test_stage_scope_rejects_unsupported_gpu_claims(self):
        self.assertEqual(list(audit.rows_checked([row()],0)),[1])
        for change in [dict(gpu_timing_valid=True),dict(staging_copy_timing_valid=True),dict(pair_decode_scope='per_eye'),dict(eye=1),dict(frame=True),dict(capture_source='rrb_gpu_image_readback'),dict(staging_prepare_ms=float('nan')),dict(decode_gpu_ms=1)]:
            with self.subTest(change=change),self.assertRaises(ValueError):audit.rows_checked([row()|change],0)
        with self.assertRaises(ValueError):audit.rows_checked([row(),row()],0)
    def test_exact_envelope_identity_crc_and_shape(self):
        body=b'bounded payload';wire=struct.pack('<6I2Q2I',0x31544357,4,2,3,0,len(body),90,7,zlib.crc32(body),0)+body
        audit.envelope(wire,90,0,7,2,3)
        for args in [(90,1,7,2,3),(90,0,8,2,3),(90,0,7,3,2)]:
            with self.assertRaises(ValueError):audit.envelope(wire,*args)
        for bad in [wire[:-1],wire+b'x',wire[:-1]+b'X']:
            with self.assertRaises(ValueError):audit.envelope(bad,90,0,7,2,3)
    def test_bound_snapshot_and_changed_file(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);p=root/'input';p.write_bytes(b'original');bound=audit.Bound(root,{'input':audit.sha(b'original')})
            self.assertEqual(bound.read('input'),b'original');p.write_bytes(b'changed');self.assertEqual(bound.read('input'),b'original')
            with self.assertRaises(ValueError):bound.unchanged()
            with self.assertRaises(ValueError):bound.read('../input')
            with self.assertRaises(ValueError):bound.read('missing')
    def test_quantiles_and_strict_json(self):
        self.assertEqual(audit.stats([1,2,3,4])['median'],2.5)
        for values in [[],[True],[float('inf')],[-1]]:
            with self.assertRaises(ValueError):audit.stats(values)
        for raw in [b'{"x":1,"x":2}',b'{"x":NaN}']:
            with self.assertRaises(ValueError):audit.strict(raw)
if __name__=='__main__':unittest.main()
