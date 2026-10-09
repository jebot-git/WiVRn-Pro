import importlib.util
import json
from pathlib import Path
import struct
import unittest
import zlib
import test_submitted_capture as fixtures

SPEC=importlib.util.spec_from_file_location('reference_job',Path(__file__).parents[1]/'integration/submitted_reference_job.py')
job=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(job)


class ReferenceJob(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CaptureAudit();self.f.setUp();self.addCleanup(self.f.doCleanups)
        for name in ['server/encoder/video_encoder_test.cpp','common/codec_test/wire.h']:
            data=('independent source writer '+name).encode();self.f.put(self.f.build,name,data);self.f.receipt['sha256'][name]=job.capture.digest(data)
        self.f.seal_receipt()
        self.source={}
        for eye in (0,1):
            rows=[]
            for cid in (1,2):
                frame=cid*90;warp=cid+20;raw=bytes([18+eye,73,119,255])*16;self.source[cid,eye]=raw
                self.f.put(self.f.run,f'host/{frame}-{eye}.rgba',raw)
                payload=b'wire-body-bound-for-identity-only';wire=struct.pack('<6I2Q2I',0x31544357,4,4,4,eye,len(payload),frame,warp,zlib.crc32(payload),0)+payload
                self.f.put(self.f.run,f'host/{frame}-{eye}.envelope',wire)
                rows.append({'frame':frame,'warp':warp,'eye':eye,'payload_bytes':len(wire),'effective_codec':'rrb32','quantizer':1,'source_contract':'post_foveation_rgb_exact'})
            self.f.put(self.f.run,f'host/encode-{eye}.jsonl',b''.join(fixtures.encoded(row) for row in rows))
        self.f.rehash()
    def evaluate(self,cid=1):return job.make_job(self.f.run,self.f.receipt_path,self.f.receipt_sha,cid)
    def test_layout_and_independent_source_bytes(self):
        report,products=self.evaluate();data=products[report['job']['path']]
        self.assertEqual(struct.unpack('<6I',data[:24]),(0x31504c52,1,4,4,43,2));offset=24
        for eye in (0,1):
            self.assertEqual(struct.unpack('<6I',data[offset:offset+24]),(4,4,1,1,0,0));offset+=24
            pc=struct.unpack('<8i20f',data[offset:offset+112]);self.assertEqual(pc[:8],(0,0,4,4,0,0,0,0));offset+=112
            self.assertEqual(struct.unpack('<2I',data[offset:offset+8]),(4,4));offset+=8
            self.assertEqual(data[offset:offset+64],self.source[1,eye]);offset+=64
        self.assertEqual(offset,len(data));self.assertEqual(report['job']['sha256'],job.capture.digest(data))
        self.assertFalse(report['quality_pass']);self.assertFalse(report['reference_pixels_rendered']);self.assertTrue(report['geometry_reconstructed_from_metadata'])
        self.assertFalse(report['aarch64_raster_geometry_bit_equivalence_verified'])
        self.assertEqual(report['independent_inputs'][0]['source_boundary'],'post-host-foveation-codec-input')
        self.assertFalse(report['independent_inputs'][0]['decoder_output_used_as_reference'])
    def test_no_fallback_to_decoded_reference(self):
        source=self.f.run/'host/90-0.rgba';data=source.read_bytes();source.unlink();self.f.put(self.f.run,'host/90-0.reference.rgba',data);self.f.rehash()
        with self.assertRaises(ValueError):self.evaluate()
    def test_source_envelope_identity_crc_and_geometry(self):
        path=self.f.run/'host/90-1.envelope';original=path.read_bytes()
        for offset,value in [(8,2),(16,0),(24,180),(32,999),(40,0),(44,1)]:
            with self.subTest(offset=offset):
                data=bytearray(original);struct.pack_into('<I',data,offset,value);path.write_bytes(data);self.f.rehash()
                with self.assertRaises(ValueError):self.evaluate()
        path.write_bytes(original);self.f.rehash()
    def test_source_alpha_and_size(self):
        path=self.f.run/'host/90-1.rgba';original=path.read_bytes()
        for value in [original[:-1],original[:-1]+b'\x00']:
            path.write_bytes(value);self.f.rehash()
            with self.assertRaises(ValueError):self.evaluate()
    def test_host_event_misjoin_and_duplicate(self):
        path=self.f.run/'host/encode-1.jsonl';original=path.read_bytes();rows=[json.loads(x) for x in original.splitlines()]
        for key,value in [('warp',999),('eye',True),('frame',True),('quantizer',True),('payload_bytes',1),('effective_codec','vr444')]:
            changed=[dict(x) for x in rows];changed[0][key]=value;path.write_bytes(b''.join(fixtures.encoded(x) for x in changed));self.f.rehash()
            with self.assertRaises(ValueError):self.evaluate()
        path.write_bytes(original+fixtures.encoded(rows[0]));self.f.rehash()
        with self.assertRaises(ValueError):self.evaluate()
    def test_replay_specific_scope_rejections(self):
        name=job.capture.PREFIX+'capture-1-eye-0.json';original=self.f.getj(name)
        for mutate in [lambda m:m.update(rect_rgb=[1,0,4,4]),
                       lambda m:m.update(output_alpha=True),
                       lambda m:m.update(sampled_rgb_is_linear=False,shader_srgb_decode=True)]:
            meta=dict(original);mutate(meta);self.f.putj(name,meta);self.f.rehash()
            with self.assertRaises(ValueError):self.evaluate()
        self.f.putj(name,original);self.f.rehash()
    def test_typed_capture_id_and_source_helper_hash(self):
        for cid in [True,0,3,9]:
            with self.assertRaises(ValueError):self.evaluate(cid)
        (self.f.build/'server/encoder/video_encoder_test.cpp').write_bytes(b'drift')
        with self.assertRaises(ValueError):self.evaluate()
    def test_same_cached_capture_bytes_are_used(self):
        # Context exposes the already verified immutable read buffers, rather
        # than reparsing an audit report or treating candidate pixels as source.
        context={};job.capture.audit(self.f.run,self.f.receipt_path,self.f.receipt_sha,_context=context)
        key=job.capture.PREFIX+'capture-1-eye-0.json';saved=context['run'].read(key)
        self.f.put(self.f.run,key,b'changed after consumption')
        self.assertEqual(context['run'].read(key),saved)
        with self.assertRaises(ValueError):job.capture.audit(self.f.run,self.f.receipt_path,self.f.receipt_sha,_context=context)

if __name__=='__main__':unittest.main()
