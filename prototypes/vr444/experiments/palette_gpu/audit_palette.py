#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"direct_image_gpu"))
from audit import audit


def evaluate(data):
    if data.get("cpu_reconstruction_in_timed_path") is not False or data.get("rpt_modes")!=6:
        raise ValueError("palette path/mode coverage")
    report=audit(data,expected_experiment="palette-gpu")
    for case in data["cases"]:
        w,h=case.get("width"),case.get("height")
        if type(w) is not int or type(h) is not int or not(1<=w<=4096 and 1<=h<=4096 and w*h<=4194304):
            raise ValueError("palette dimensions")
        if case.get("stereo_index_bytes")!=((w+15)//16)*((h+15)//16)*40*2:
            raise ValueError("palette index traffic")
        if type(case.get("stereo_rpt_bytes")) is not int or not 64<case["stereo_rpt_bytes"]<=2*(3*w*h+4*((w+15)//16)*((h+15)//16)+32):
            # Legal hand-authored runs may be larger than the smallest encoder
            # choice for tiny images; the grammar still caps each record at768.
            tiles=((w+15)//16)*((h+15)//16)
            if type(case.get("stereo_rpt_bytes")) is not int or not 64<case["stereo_rpt_bytes"]<=2*(tiles*772+32):
                raise ValueError("palette packet bound")
    report.update(cpu_reconstruction_in_timed_path=False,rpt_modes=6)
    return report


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("result",type=Path); args=parser.parse_args()
    raw=args.result.read_bytes(); report=evaluate(json.loads(raw)); report["result_sha256"]=hashlib.sha256(raw).hexdigest()
    print(json.dumps(report,indent=2))


if __name__=="__main__": main()
