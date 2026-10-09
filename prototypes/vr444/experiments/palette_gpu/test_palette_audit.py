import copy
from pathlib import Path
import sys
import unittest
import struct
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"direct_image_gpu"))
from test_audit import valid_result
from audit_palette import evaluate
from make_run_fixtures import make


def valid_palette():
    data=valid_result(); data.update(experiment="palette-gpu",cpu_reconstruction_in_timed_path=False,rpt_modes=6)
    for case in data["cases"]:
        w,h=map(int,case["name"].split("-")[0].split("x")); tiles=((w+15)//16)*((h+15)//16)
        case.update(width=w,height=h,stereo_index_bytes=tiles*80,stereo_rpt_bytes=2*(32+tiles*7))
    return data


class PaletteAuditTests(unittest.TestCase):
    def test_admission_and_no_performance_promotion(self):
        report=evaluate(valid_palette())
        self.assertTrue(report["correctness_pass"])
        self.assertFalse(report["performance_claim"])
        self.assertFalse(report["cpu_reconstruction_in_timed_path"])

    def test_palette_metadata_rejections(self):
        changes=[lambda d:d.update(cpu_reconstruction_in_timed_path=True),
                 lambda d:d.update(rpt_modes=5),
                 lambda d:d["cases"][0].update(stereo_index_bytes=0),
                 lambda d:d["cases"][0].update(width=0),
                 lambda d:d["cases"][0].update(stereo_rpt_bytes=10**9)]
        for change in changes:
            data=valid_palette(); change(data)
            with self.assertRaises(ValueError): evaluate(data)

    def test_independent_run_vectors(self):
        for w,h,runs in ((16,16,1),(16,16,129),(16,16,192),(17,17,192)):
            wire,pixels=make(w,h,runs)
            self.assertEqual(len(pixels),w*h*4)
            self.assertEqual(struct.unpack_from("<I",wire,28)[0],zlib.crc32(wire[:28]))
            self.assertEqual(struct.unpack_from("<I",wire,24)[0],zlib.crc32(wire[32:]))
            actual=bytearray(w*h*4); at=32
            for y in range(0,h,16):
                for x in range(0,w,16):
                    length,mode,reserved=struct.unpack_from("<HBB",wire,at); at+=4
                    self.assertEqual((mode,reserved),(5,0)); self.assertLessEqual(length,768)
                    local=0; tw,th=min(16,w-x),min(16,h-y)
                    for record in range(at,at+length,4):
                        rgb=wire[record+1:record+4]
                        for _ in range(wire[record]+1):
                            out=((y+local//tw)*w+x+local%tw)*4; actual[out:out+4]=rgb+b"\xff"; local+=1
                    self.assertEqual(local,tw*th); at+=length
            self.assertEqual(at,len(wire)); self.assertEqual(actual,pixels)


if __name__=="__main__": unittest.main()
