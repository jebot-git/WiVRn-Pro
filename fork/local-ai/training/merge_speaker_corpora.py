#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Merge manifests without counting duplicate PCM or augmentation as new audio."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('manifests',type=Path,nargs='+');a=p.parse_args()
    rows=[];seen=set();inputs=[];hours=Counter();speaker_splits={}
    for path in a.manifests:
        manifest=json.loads(path.read_text())
        inputs.append(dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for row in manifest['rows']:
            if row['sha256'] in seen:continue
            if row['speaker'] in speaker_splits and speaker_splits[row['speaker']]!=row['split']:
                raise ValueError('A speaker occurs in multiple splits')
            speaker_splits[row['speaker']]=row['split'];seen.add(row['sha256'])
            row=dict(row,path=str((path.parent/row['path']).resolve()))
            rows.append(row);hours[row['split']]+=row['seconds']/3600
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(schema=1,inputs=inputs,rows=rows,hours=dict(hours))))
    print(json.dumps(dict(hours=dict(hours),windows=len(rows),speakers=len(speaker_splits)),indent=2))

if __name__=='__main__':main()
