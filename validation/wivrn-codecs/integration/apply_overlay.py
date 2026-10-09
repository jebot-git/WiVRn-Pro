#!/usr/bin/env python3
"""Apply the integration overlay only after checking every baseline file hash."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--apply',action='store_true');a=p.parse_args()
here=Path(__file__).resolve().parent;manifest=json.loads((here/'overlay-manifest.json').read_text());source=a.source.resolve()
for name,record in manifest.items():
 target=(source/name).resolve()
 if not target.is_relative_to(source):raise ValueError('path escapes source')
 current=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
 if current!=record['before_sha256']:raise ValueError('baseline differs: '+name)
 data=(here/'source-overlay'/name).read_bytes()
 if hashlib.sha256(data).hexdigest()!=record['after_sha256']:raise ValueError('corrupt overlay: '+name)
if a.apply:
 for name in manifest:
  target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((here/'source-overlay'/name).read_bytes())
print(('Applied' if a.apply else 'Verified')+f' {len(manifest)} integration files')
