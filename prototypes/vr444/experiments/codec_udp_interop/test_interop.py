#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
import qualify as q

BIN=Path(os.environ.get('CODEC_UDP_INTEROP_BIN','/tmp/wivrn-codec-udp-interop-build'))


def rrf(width,height,rgb):
    assert len(rgb)==width*height*3
    header=struct.pack('<4sBBHIIIIII',b'RRF1',1,0,0,width,height,len(rgb),len(rgb),zlib.crc32(rgb),0)
    return header[:28]+struct.pack('<I',zlib.crc32(header[:28]))+rgb


def rrb(width,height,rgb):
    blocks=[rrf(width,min(16,height-row),rgb[row*width*3:min(row+16,height)*width*3]) for row in range(0,height,16)]
    cursor=32+8*len(blocks);table=b''
    for block in blocks:table+=struct.pack('<II',cursor,len(block));cursor+=len(block)
    header=struct.pack('<4sBBBBIIIIII',b'RRB1',1,4,0,0,width,height,len(blocks),cursor,zlib.crc32(table),0)
    return header[:28]+struct.pack('<I',zlib.crc32(header[:28]))+table+b''.join(blocks)


def rgba(rgb):return b''.join(rgb[i:i+3]+b'\xff' for i in range(0,len(rgb),3))


class InteropTests(unittest.TestCase):
    def rejected(self,result):
        self.assertEqual(result.returncode,1);self.assertEqual(result.stdout,b'')
        for marker in (b'Sanitizer',b'runtime error:',b'SUMMARY:'):self.assertNotIn(marker,result.stderr)

    def test_actual_received_records_all_modes(self):
        packets=[bytes([i%256])*n for i,n in enumerate([88,1196,88]+[1196]*37+[71])]
        for mode in range(3):
            with self.subTest(mode=mode):
                try:received,counters=q.relay(BIN/'relay',packets,mode)
                except q.Unsupported:
                    if mode==0:raise
                    continue
                self.assertEqual(sorted(received),sorted(packets));self.assertGreater(counters['send_calls'],0)

    def test_single_short_packet(self):
        for mode in range(3):
            try:out,_=q.relay(BIN/'relay',[b'x'],mode)
            except q.Unsupported:continue
            self.assertEqual(out,[b'x'])

    def test_vxf_opaque_pair_and_recovery_through_sockets(self):
        left=bytes(range(256))*5+b'l';right=bytes(range(255,-1,-1))*5+b'r'
        for mode in range(3):
            for scenario in q.SCENARIOS:
                p,_,_=q.packets_for(left,right,scenario)
                try:received,_=q.relay(BIN/'relay',p,mode)
                except q.Unsupported:continue
                pair,stats=q.reconstruct(received)
                if scenario=='source_and_parity_lost':self.assertIsNone(pair)
                else:
                    self.assertEqual((pair.left,pair.right),(left,right))
                    self.assertEqual(stats['recovered_source_shards'],int(scenario=='corrupt_first_fec'))

    def test_no_fec_missing_source_cannot_publish(self):
        p,_,_=q.packets_for(b'left',b'right','clean_no_fec')
        p=[packet for packet in p if q.vxf.HEADER.unpack_from(packet)[2]!=q.vxf.DATA]
        received,_=q.relay(BIN/'relay',p,0);pair,_=q.reconstruct(received);self.assertIsNone(pair)

    def test_exact_independent_raw_codec_goldens(self):
        for width,height in ((1,1),(17,19)):
            left=bytes((i*3)%256 for i in range(width*height*3));right=bytes((255-i*7)%256 for i in range(width*height*3))
            for mode,encode in ((1,rrf),(2,rrb)):
                result=q.decode(BIN/'decode',mode,encode(width,height,left),encode(width,height,right),rgba(left),rgba(right))
                self.assertEqual((result['width'],result['height']),(width,height))

    def test_actual_codec_recovery_all_modes(self):
        rgb_left=bytes((i*3)%256 for i in range(17*19*3));rgb_right=bytes((i*7)%256 for i in range(17*19*3))
        for codec,encode in ((1,rrf),(2,rrb)):
            left,right=encode(17,19,rgb_left),encode(17,19,rgb_right)
            for mode in range(3):
                packets,_,_=q.packets_for(left,right,'corrupt_first_fec')
                try:received,_=q.relay(BIN/'relay',packets,mode)
                except q.Unsupported:continue
                pair,stats=q.reconstruct(received);self.assertEqual(stats['recovered_source_shards'],1)
                self.assertTrue(q.decode(BIN/'decode',codec,pair.left,pair.right,rgba(rgb_left),rgba(rgb_right))['both_eyes_exact'])

    def test_right_codec_failure_has_no_partial_success(self):
        for mode,encode in ((1,rrf),(2,rrb)):
            left=encode(1,1,b'\x01\x02\x03');right=b'BAD!'+left[4:]
            # The transport CRC covers the malformed codec exactly. A valid
            # transport pair must not bypass the independent codec validator.
            packets,_,_=q.packets_for(left,right,'clean_fec');received,_=q.relay(BIN/'relay',packets,0)
            pair,_=q.reconstruct(received);self.assertIsNotNone(pair)
            r=subprocess.run([str(BIN/'decode')],input=q.decode_request(mode,pair.left,pair.right,b'\x01\x02\x03\xff',b'\x01\x02\x03\xff'),capture_output=True,timeout=10)
            self.rejected(r)

    def test_stereo_shape_mismatch_no_success(self):
        left=rrf(1,1,b'\x01\x02\x03');right=rrf(1,2,b'\x01\x02\x03'*2)
        r=subprocess.run([str(BIN/'decode')],input=q.decode_request(1,left,right,b'\x01\x02\x03\xff',b'\x01\x02\x03\xff'*2),capture_output=True,timeout=10)
        self.rejected(r)

    def test_wrong_expected_eye_bytes_no_success(self):
        wire=rrf(1,1,b'\x01\x02\x03')
        r=subprocess.run([str(BIN/'decode')],input=q.decode_request(1,wire,wire,b'\x01\x02\x03\xff',b'\x01\x02\x03\x00'),capture_output=True,timeout=10)
        self.rejected(r)

    def test_native_relay_framing_rejection(self):
        good=q.request([b'abc'])
        bad=[good[:i] for i in (0,3,4,7,8,11,12,14)]+[good+b'x',b'BAD!'+good[4:],b'UPX1'+struct.pack('<I',0),b'UPX1'+struct.pack('<I',513),b'UPX1'+struct.pack('<II',1,1201),b'UPX1'+struct.pack('<II',1,0)]
        for data in bad:
            r=subprocess.run([str(BIN/'relay'),'0'],input=data,capture_output=True,timeout=10)
            self.rejected(r)

    def test_native_decoder_framing_rejection(self):
        wire=rrf(1,1,b'\x01\x02\x03');good=q.decode_request(1,wire,wire,b'\x01\x02\x03\xff',b'\x01\x02\x03\xff')
        bad=[good[:i] for i in (0,3,4,7,8,11,len(good)-1)]+[good+b'x',b'BAD!'+good[4:],b'CDP1'+struct.pack('<II',1,262145),b'CDP1'+struct.pack('<I',3)]
        for data in bad:
            r=subprocess.run([str(BIN/'decode')],input=data,capture_output=True,timeout=10)
            self.rejected(r)

    def test_python_adapter_bounds(self):
        for packets in ([],[b''],[b'x'*1201],[b'x']*513):
            with self.assertRaises(ValueError):q.request(packets)
        for data in (b'',b'UPY1'+struct.pack('<IIII',1,1,1,1),b'UPY1'+struct.pack('<IIIII',513,1,1,1,1)+b'x',b'UPY1'+struct.pack('<IIIII',1,1,1,33,1)+b'x',b'UPY1'+struct.pack('<IIIII',1,1,1,1,1)+b'xz'):
            with self.assertRaises(ValueError):q.response(data)

    def test_unbound_build_receipts_reject_before_corpus_or_socket_work(self):
        original=json.loads((BIN/'build-receipt.json').read_text())
        mutations=[lambda d:d.update(configuration='unsanitized'),lambda d:d.update(source_sha256={}),
            lambda d:d.update(binary_sha256={}),lambda d:d.update(successful_compiler_argv=[['clang++']]*3)]
        with tempfile.TemporaryDirectory(prefix='codec-interop-receipt-') as name:
            directory=Path(name)
            for binary in ('relay','decode'):(directory/binary).symlink_to((BIN/binary).resolve())
            for mutate in mutations:
                data=json.loads(json.dumps(original));mutate(data);(directory/'build-receipt.json').write_text(json.dumps(data))
                r=subprocess.run([sys.executable,str(q.HERE/'qualify.py'),'--binaries',str(directory),'--corpus','/unused',
                    '--rrf-export','/unused','--rrb-export','/unused','--output',str(directory/'out.json')],capture_output=True,timeout=10)
                self.assertEqual(r.returncode,1);self.assertEqual(r.stdout,b'');self.assertFalse((directory/'out.json').exists())
                self.assertTrue(b'build receipt' in r.stderr or b'sanitizer compiler commands' in r.stderr)


if __name__=='__main__':unittest.main()
