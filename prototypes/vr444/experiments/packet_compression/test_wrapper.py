import random
import struct
import unittest
from wrapper import Native, encode, decode, HEADER, MAX


class WrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.native = Native()

    def test_roundtrip_and_bounded_expansion(self):
        rng = random.Random(73)
        for length in (1, 255, 256, 1100, 1101, 16384, 32769):
            for data in (bytes(length), rng.randbytes(length)):
                for method in (1, 2):
                    for block in (1100, 16384, MAX):
                        wire = encode(data, self.native, method, block)
                        self.assertEqual(decode(wire, self.native), data)
                        self.assertLessEqual(len(wire), len(data)+HEADER.size)

    def test_truncation_and_trailing_bytes(self):
        for method in (1, 2):
            wire = encode(b'abcd'*1024, self.native, method, 1100)
            for size in range(len(wire)):
                with self.assertRaises(ValueError): decode(wire[:size], self.native)
            with self.assertRaises(ValueError): decode(wire+b'x', self.native)

    def test_bad_header_lengths_and_counts_rejected_before_native(self):
        class NoNative:
            def decompress(self, *args): raise AssertionError('native reached')
        wire = encode(bytes(4096), self.native)
        for index, value in ((0,b'BAD!'),(1,2),(2,9),(3,1),(4,MAX+1),(5,0),(6,0),(6,0xffffffff)):
            fields=list(HEADER.unpack_from(wire)); fields[index]=value
            with self.assertRaises(ValueError): decode(HEADER.pack(*fields)+wire[HEADER.size:],NoNative())

    def test_checksum_and_block_corruption(self):
        for method in (1, 2):
            wire = encode(b'abcd'*1024,self.native,method,1100)
            for pos in (20,24,25,28,32,len(wire)-1):
                bad=bytearray(wire);bad[pos]^=0xff
                with self.assertRaises(ValueError):decode(bytes(bad),self.native)

    def test_seeded_malformed_inputs_never_change_output_silently(self):
        rng=random.Random(707);data=b'VR444abcd'*1024
        for method in (1,2):
            wire=encode(data,self.native,method,1100)
            for _ in range(1000):
                bad=bytearray(wire);bad[rng.randrange(len(bad))]^=1<<rng.randrange(8)
                try: result=decode(bytes(bad),self.native)
                except ValueError: continue
                self.assertEqual(result,data)


if __name__ == '__main__': unittest.main()
