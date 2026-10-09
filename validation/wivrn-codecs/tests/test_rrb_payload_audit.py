import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('rrb_payload_audit',ROOT/'integration/rrb_payload_audit.py')
AUDIT=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(AUDIT)


def raw_rrb(width,height,eye):
    rgb=bytes((i*17+eye*41)&255 for i in range(width*height*3))
    records=[]
    for row in range(0,height,32):
        h=min(32,height-row);body=rgb[row*width*3:(row+h)*width*3]
        header=b'RRF1'+bytes([1,0,0,0])+struct.pack('<IIIII',width,h,len(body),len(body),zlib.crc32(body))
        records.append(header+struct.pack('<I',zlib.crc32(header))+body)
    at=32+8*len(records);table=b''
    for record in records:table+=struct.pack('<II',at,len(record));at+=len(record)
    header=b'RRB1'+bytes([1,5,0,0])+struct.pack('<IIIII',width,height,len(records),at,zlib.crc32(table))
    body=header+struct.pack('<I',zlib.crc32(header))+table+b''.join(records)
    rgba=b''.join(rgb[i:i+3]+b'\xff' for i in range(0,len(rgb),3))
    envelope=struct.pack('<6I2Q2I',0x31544357,4,width,height,eye,len(body),90,7,zlib.crc32(body),0)+body
    return body,envelope,rgba


def compressed_rrb(width,height,eye,mode):
    # Independent LZ4 repeated-byte match plus canonical five-literal final
    # sequence. Mode2 reconstructs a ramp which resets at every32-row block.
    value=eye+1;records=[];rgba=bytearray()
    for row in range(0,height,32):
        h=min(32,height-row);raw=width*h*3;extra=raw-10-15
        compressed=bytes([0x1f,value,1,0])+bytes([255])*(extra//255)+bytes([extra%255])+bytes([0x50])+bytes([value])*5
        header=b'RRF1'+bytes([1,mode,0,0])+struct.pack('<IIIII',width,h,len(compressed),raw,zlib.crc32(compressed))
        records.append(header+struct.pack('<I',zlib.crc32(header))+compressed)
        for y in range(h):
            channel=value*(y+1) if mode==2 else value
            rgba+=bytes([channel,channel,channel,255])*width
    at=32+8*len(records);table=b''
    for record in records:table+=struct.pack('<II',at,len(record));at+=len(record)
    header=b'RRB1'+bytes([1,5,0,0])+struct.pack('<IIIII',width,height,len(records),at,zlib.crc32(table))
    body=header+struct.pack('<I',zlib.crc32(header))+table+b''.join(records)
    outer=struct.pack('<6I2Q2I',0x31544357,4,width,height,eye,len(body),90,7,zlib.crc32(body),0)+body
    return body,outer,bytes(rgba)


class PayloadAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory(prefix='rrb-reference-test-');cls.out=Path(cls.build.name)
        common=ROOT/'integration/source-overlay/common';vendor=common/'codec_test/compression/vendor/lz4.c'
        subprocess.run(['clang','-O1','-c',str(vendor),'-o',str(cls.out/'lz4.o')],check=True,timeout=30)
        cls.binary=cls.out/'reference'
        subprocess.run(['clang++','-std=c++20','-O1','-I'+str(common),str(ROOT/'integration/rrb_reference.cpp'),str(cls.out/'lz4.o'),'-o',str(cls.binary)],check=True,timeout=30)
        cls.sha=hashlib.sha256(cls.binary.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):cls.build.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rrb-capture-test-');self.addCleanup(self.temp.cleanup);self.directory=Path(self.temp.name)
        (self.directory/'host').mkdir();(self.directory/'client/codec-test').mkdir(parents=True)
        (self.directory/'client/codec-test-present.jsonl').write_text(json.dumps(dict(left=90,right=90,left_warp=7,right_warp=7))+'\n')
        self.populate(2,2)

    def populate(self,w,h,factory=raw_rrb):
        for eye in (0,1):
            body,envelope,rgba=factory(w,h,eye);stem=self.directory/'host'/f'90-{eye}'
            for suffix,data in (('.payload',body),('.envelope',envelope),('.rgba',rgba),('.reference.rgba',rgba)):
                stem.with_suffix(suffix).write_bytes(data)
            (self.directory/'client/codec-test'/f'90-{eye}.rgba').write_bytes(rgba)

    def run_audit(self,source='q1',sha=None):return AUDIT.audit(self.directory,self.binary,sha or self.sha,source)

    def test_real_scalar_oracle_matches_both_distinct_eyes(self):
        result=self.run_audit();self.assertEqual(result['status'],'pass');self.assertEqual(result['captured_eye_payloads'],2)
        self.assertNotEqual(result['observations'][0]['decoded_sha256'],result['observations'][1]['decoded_sha256'])
        self.assertEqual(result['captured_presented_pairs'],1)

    def test_independent_compressed_and_row_up_goldens(self):
        for mode in (1,2):
            self.populate(17,33,lambda w,h,e:compressed_rrb(w,h,e,mode))
            self.assertEqual(self.run_audit()['status'],'pass')

    def test_exec_uses_hashed_snapshot_during_original_replacement(self):
        run=subprocess.run
        def replace_at_exec(argv,**kwargs):
            original=self.binary.read_bytes()
            try:
                self.binary.write_bytes(b'temporary concurrent rebuild bytes')
                self.assertNotEqual(Path(argv[0]),self.binary)
                self.assertEqual(hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest(),self.sha)
                return run(argv,**kwargs)
            finally:self.binary.write_bytes(original)
        with mock.patch.object(AUDIT.subprocess,'run',side_effect=replace_at_exec):
            self.assertEqual(self.run_audit()['status'],'pass')

    def test_outer_identity_and_source_corruption_rejected(self):
        p=self.directory/'host/90-1.envelope';old=p.read_bytes();corrupt=bytearray(old);struct.pack_into('<Q',corrupt,32,8);p.write_bytes(corrupt)
        with self.assertRaises(ValueError):self.run_audit()
        p.write_bytes(old);p=self.directory/'host/90-0.rgba';p.write_bytes(bytes([9])+p.read_bytes()[1:])
        with self.assertRaises(ValueError):self.run_audit()
        self.assertEqual(self.run_audit('vr444')['status'],'pass')

    def test_inner_corruption_even_with_repaired_outer_crc_rejected(self):
        p=self.directory/'host/90-1.payload';body=bytearray(p.read_bytes());body[-1]^=1;p.write_bytes(body)
        e=self.directory/'host/90-1.envelope';outer=bytearray(e.read_bytes());outer[48:]=body;struct.pack_into('<I',outer,40,zlib.crc32(body));e.write_bytes(outer)
        with self.assertRaises(ValueError):self.run_audit()

    def test_reference_and_client_mismatch_rejected(self):
        for relative in ('host/90-1.reference.rgba','client/codec-test/90-1.rgba'):
            p=self.directory/relative;old=p.read_bytes();p.write_bytes(bytes([0])+old[1:])
            with self.assertRaises(ValueError):self.run_audit()
            p.write_bytes(old)

    def test_missing_mate_bad_binary_and_symlink_rejected(self):
        with self.assertRaises(ValueError):self.run_audit(sha='0'*64)
        p=self.directory/'host/90-1.payload';old=p.read_bytes();p.unlink()
        with self.assertRaises(ValueError):self.run_audit()
        target=self.directory/'saved';target.write_bytes(old);p.symlink_to(target)
        with self.assertRaises(ValueError):self.run_audit()

    def test_actual_valid_raw_pair_over_cap_is_rejected(self):
        self.populate(256,171)
        with self.assertRaisesRegex(ValueError,'stereo envelope cap'):self.run_audit()

    def test_no_presented_capture_cannot_qualify(self):
        p=self.directory/'client/codec-test-present.jsonl';p.write_text(json.dumps(dict(left=91,right=91,left_warp=7,right_warp=7))+'\n')
        with self.assertRaisesRegex(ValueError,'no complete captured presented'):self.run_audit()


if __name__=='__main__':unittest.main()
