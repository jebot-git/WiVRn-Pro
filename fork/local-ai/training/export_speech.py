#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export a completed pilot to a new immutable candidate directory."""
import argparse
import json
from pathlib import Path
from optimum.exporters.openvino import main_export
from optimum.intel.openvino.configuration import OVConfig
p=argparse.ArgumentParser(); p.add_argument('run',type=Path); p.add_argument('output',type=Path); a=p.parse_args()
if a.output.exists(): raise SystemExit('Refusing to overwrite a model directory')
report=json.loads((a.run/'report.json').read_text())
main_export(str(a.run/'merged'),a.output,task='automatic-speech-recognition-with-past',
    ov_config=OVConfig(dtype='fp16'),convert_tokenizer=True,trust_remote_code=False)
(a.output/'training-report.json').write_text(json.dumps(report,indent=2))
print('Candidate exported; select this directory in the Subtitles GUI only after GPU evaluation:',a.output)
